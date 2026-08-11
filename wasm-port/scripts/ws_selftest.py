#!/usr/bin/env python3
"""Check the WebSocket bridge without a game server, a browser or a client.

The bridge is the one piece of the browser port that can be finished and proved
before the WebAssembly client exists, so it is worth proving properly. This
starts a stub that answers the way the auth core does, starts the real bridge
against it, and then asks the questions that matter:

  * does it answer /ping the way the client page's gate demands, or does the
    page refuse to start
  * does it upgrade on the client's own URL shape, /to/<host>:<port>
  * does it carry the game's handshake unchanged, byte for byte, both ways
  * does it IGNORE the host the page names -- the whole anti-open-proxy claim
  * does it refuse every port it was not pinned to, including the db core
  * does it refuse text frames, oversized frames and unmasked frames
  * does the per-address limit actually limit

No network, no Docker, no game data. Standard library only.

    python3 ws_selftest.py

Exit code 0 means every check passed.
"""
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ws_probe import WSClient, WSError            # noqa: E402

BRIDGE = os.path.normpath(os.path.join(
    HERE, "..", "..", "linux-port", "docker", "wsbridge", "bin", "ws2tcp.py"))

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + detail) if detail else ""))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# -----------------------------------------------------------------------------
# The stub. It speaks the first three packets of the auth core -- phase 1, a
# handshake to echo, then phase 10 (PHASE_AUTH) -- and after that echoes
# whatever it is sent, which is what makes the relay measurable.
# -----------------------------------------------------------------------------
class StubAuth(threading.Thread):
    daemon = True

    def __init__(self, port):
        super().__init__()
        self.port = port
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(16)
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

    def serve(self, conn):
        try:
            conn.settimeout(10)
            conn.sendall(b"\xfd\x01")                       # HEADER_GC_PHASE, phase 1
            hs = b"\xff" + bytes(range(12))                 # HEADER_GC_HANDSHAKE
            conn.sendall(hs)
            echoed = b""
            while len(echoed) < 13:
                chunk = conn.recv(13 - len(echoed))
                if not chunk:
                    return
                echoed += chunk
            if echoed != hs:
                return
            conn.sendall(b"\xfd\x0a")                       # PHASE_AUTH
            while True:
                data = conn.recv(65536)
                if not data:
                    return
                conn.sendall(data)                          # echo, for the relay check
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


def http_get(url, timeout=4):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except OSError as e:
        return None, str(e)


def wait_for_bridge(port, seconds=15):
    deadline = time.time() + seconds
    while time.time() < deadline:
        code, _ = http_get("http://127.0.0.1:%d/health" % port, timeout=2)
        if code == 200:
            return True
        time.sleep(0.2)
    return False


def read_exactly_ws(ws, n, seconds=8):
    """n payload bytes off the WebSocket, however they happen to be framed."""
    got = b""
    deadline = time.time() + seconds
    while len(got) < n and time.time() < deadline:
        chunk = ws.recv()
        if chunk is None:
            break
        got += chunk
    return got


def finish_game_handshake(ws):
    """Answer the stub's handshake and return the phase it lands in."""
    data = read_exactly_ws(ws, 15)                  # fd 01 + ff + 12
    if len(data) < 15 or data[0:2] != b"\xfd\x01" or data[2] != 0xFF:
        return None, data
    ws.send(data[2:15])
    tail = read_exactly_ws(ws, 2)
    if len(tail) < 2 or tail[0] != 0xFD:
        return None, data + tail
    return tail[1], data + tail


def main():
    stub_port = free_port()
    bridge_port = free_port()

    stub = StubAuth(stub_port)
    stub.start()

    env = dict(os.environ)
    env.update({
        "M2_BRIDGE_AUTH_PORT": str(stub_port),
        "M2_BRIDGE_PORTS": "%d,15000" % stub_port,   # 15000 must be dropped anyway
        "M2_BRIDGE_TARGET_HOST": "127.0.0.1",
        "M2_BRIDGE_LISTEN_PORT": str(bridge_port),
        "M2_BRIDGE_BIND": "127.0.0.1",
        "M2_BRIDGE_MAX_PER_IP": "3",
        "M2_BRIDGE_MAX_FRAME": "4096",
        "PYTHONUNBUFFERED": "1",
    })
    proc = subprocess.Popen([sys.executable, BRIDGE], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")

    # Drained in a thread, and not optional: a pipe nobody reads fills after a
    # few kilobytes on Windows, and the bridge then blocks inside its own log
    # call while accepting a connection. That looks exactly like a bridge that
    # accepts and then says nothing, and it cost an hour to see.
    bridge_log = []

    def drain():
        for line in proc.stdout:
            bridge_log.append(line.rstrip())

    threading.Thread(target=drain, daemon=True).start()

    print("bridge on 127.0.0.1:%d, stub game core on 127.0.0.1:%d\n" % (bridge_port, stub_port))
    base = "ws://127.0.0.1:%d" % bridge_port
    try:
        if not wait_for_bridge(bridge_port):
            print("  FAIL the bridge did not come up")
            for line in bridge_log[-20:]:
                print("  | %s" % line)
            return 1

        # --- plain HTTP -----------------------------------------------------
        code, body = http_get("http://127.0.0.1:%d/health" % bridge_port)
        check("/health answers", code == 200 and body.strip() == "ok", "%s %r" % (code, body[:40]))

        code, body = http_get("http://127.0.0.1:%d/ports" % bridge_port)
        try:
            info = json.loads(body)
        except ValueError:
            info = {}
        check("/ports lists the game port", code == 200 and stub_port in info.get("ports", []),
              body.strip()[:120])
        # 15000 was put in M2_BRIDGE_PORTS on purpose: an explicit setting must
        # not be able to open the db core.
        check("/ports never lists the db core", 15000 not in info.get("ports", []),
              "list is %s" % info.get("ports"))

        # The gate's own test, verbatim: shell.html refuses to start unless the
        # body BEGINS with this string. Nothing about it is cosmetic.
        code, body = http_get("http://127.0.0.1:%d/ping" % bridge_port)
        check("/ping satisfies the page's connection gate",
              code == 200 and body.startswith("m2-ws2tcp"), "%s %r" % (code, body[:60]))

        code, _ = http_get("http://127.0.0.1:%d/to/anywhere:%d" % (bridge_port, stub_port))
        check("a game path without an upgrade is refused, not opened", code == 426, "got %s" % code)

        # --- the game handshake, through the bridge -------------------------
        # The host in the path is a name that CANNOT resolve. If the bridge
        # dialled what the page names, this fails; it succeeds only because the
        # name is read, logged and discarded and the socket goes to the fixed
        # target. That is the anti-open-proxy claim, tested rather than argued.
        ws = WSClient(base + "/to/no-such-host.invalid:%d" % stub_port)
        phase, raw = finish_game_handshake(ws)
        check("the game handshake survives the bridge", phase == 0x0A,
              "phase %r, raw %s" % (phase, raw.hex(" ")))
        check("the host the page names is never dialled", phase == 0x0A,
              "a bridge that resolved no-such-host.invalid could not have answered")
        check("the subprotocol is agreed", ws.headers.get("sec-websocket-protocol") == "binary",
              repr(ws.headers.get("sec-websocket-protocol")))

        # --- bytes in, the same bytes out ------------------------------------
        blob = os.urandom(3000)
        ws.send(blob)
        back = read_exactly_ws(ws, len(blob))
        check("3000 bytes come back byte for byte", back == blob,
              "%d bytes back" % len(back))

        # A payload split across several small frames must arrive as one stream:
        # the game parses a stream, not messages, and this is the property the
        # client is allowed to rely on.
        parts = [os.urandom(37) for _ in range(20)]
        for p in parts:
            ws.send(p)
        joined = b"".join(parts)
        back = read_exactly_ws(ws, len(joined))
        check("many small frames arrive as one stream", back == joined,
              "%d of %d bytes" % (len(back), len(joined)))
        ws.close()

        # pre.js sends encodeURIComponent("host:port"), so the colon arrives as
        # %3A. A bridge that only handled the raw form would work in every test
        # written by hand and fail against the real client.
        ws = WSClient(base + "/to/game%%3A%d" % stub_port)
        phase, _ = finish_game_handshake(ws)
        check("the percent-encoded colon the client really sends works", phase == 0x0A,
              "phase %r" % phase)
        ws.close()

        # --- the pinning ------------------------------------------------------
        for path, why in (("/to/game:15000", "the db core"),
                          ("/to/game:12000", "the cores' p2p port"),
                          ("/to/game:9999", "a port that is not the game's"),
                          ("/to/game%3A15000", "the db core, percent-encoded"),
                          ("/to/game", "a path with no port at all"),
                          ("/to/", "a path with no destination at all"),
                          ("/anything", "an endpoint that does not exist")):
            try:
                WSClient(base + path).close()
                check("refuses %s (%s)" % (path, why), False, "it connected")
            except WSError as e:
                check("refuses %s (%s)" % (path, why), "404" in str(e), str(e)[:80])

        # --- frames it must not accept ----------------------------------------
        ws = WSClient(base + "/to/game:%d" % stub_port)
        finish_game_handshake(ws)
        ws.send(b"hello", opcode=0x1)
        closed = ws.recv() is None
        check("a text frame closes the connection", closed,
              "close %r" % getattr(ws, "close_code", None))
        ws.close()

        ws = WSClient(base + "/to/game:%d" % stub_port)
        finish_game_handshake(ws)
        ws.send(os.urandom(5000))                     # over M2_BRIDGE_MAX_FRAME
        closed = ws.recv() is None
        check("an oversized frame closes the connection", closed,
              "close %r" % getattr(ws, "close_code", None))
        ws.close()

        # An unmasked client frame is a protocol violation; a browser never
        # sends one, so anything that does is not the client we are serving.
        s = socket.create_connection(("127.0.0.1", bridge_port), timeout=5)
        s.sendall(("GET /to/game:%d HTTP/1.1\r\n" % stub_port).encode() +
                  b"Host: x\r\nUpgrade: websocket\r\n"
                  b"Connection: Upgrade\r\nSec-WebSocket-Key: AAAAAAAAAAAAAAAAAAAAAA==\r\n"
                  b"Sec-WebSocket-Version: 13\r\n\r\n")
        time.sleep(0.3)
        s.recv(4096)
        s.sendall(struct.pack("!BB", 0x82, 4) + b"abcd")     # no mask bit
        time.sleep(0.5)
        s.settimeout(3)
        try:
            reply = s.recv(4096)
        except socket.timeout:
            reply = b""
        check("an unmasked frame is refused", reply[:1] == b"\x88" or reply == b"",
              reply[:16].hex(" "))
        s.close()

        # --- the per-address limit --------------------------------------------
        held = []
        try:
            for _ in range(3):
                held.append(WSClient(base + "/to/game:%d" % stub_port))
            limited = False
            try:
                held.append(WSClient(base + "/to/game:%d" % stub_port))
            except WSError as e:
                limited = "429" in str(e)
            check("the fourth connection from one address is refused", limited)
        finally:
            for w in held:
                w.close()

        # And the slot comes back, or a player who reconnects is locked out.
        time.sleep(0.5)
        try:
            w = WSClient(base + "/to/game:%d" % stub_port)
            w.close()
            check("a closed connection frees its slot", True)
        except WSError as e:
            check("a closed connection frees its slot", False, str(e)[:80])

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        stub.stop.set()
        try:
            stub.sock.close()
        except OSError:
            pass

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        for name in FAIL:
            print("  failed: %s" % name)
        print("\nthe bridge said:")
        for line in bridge_log[-25:]:
            print("  | %s" % line)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
