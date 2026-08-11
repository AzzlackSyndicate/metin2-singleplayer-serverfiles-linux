#!/usr/bin/env python3
"""Ask the auth server the same question hsprobe.py asks -- but through the
WebSocket bridge, the way the browser client does.

hsprobe.py proves the server answers. This proves the bridge does not change
the answer: same handshake, same phases, one more hop. If this disagrees with
hsprobe.py against the same server, the bridge is the difference and nothing
else has to be considered.

The URL shape is the browser client's own and is not ours to choose: one port,
with the real destination in the path, percent-encoded --
`ws://bridge:7789/to/<host>:<port>` (tools/wasm/pre.js in the client tree, and
the shim survives into the built dist/browser/index.js).

    python3 ws_probe.py ws://127.0.0.1:7789/to/127.0.0.1:11000
    python3 ws_probe.py wss://example.org:443/to/example.org:11000

With no arguments it also checks GET /ping, which is what the client page's
connection gate probes before it will start at all.

Standard library only, so it runs anywhere the panel runs and needs no client
build, no browser and no packages. WSClient below is also what ws_selftest.py
uses, which is why it is a class and not four lines in main().
"""
import os
import socket
import ssl
import struct
import sys
import time
from base64 import b64encode
from hashlib import sha1
from urllib.parse import urlsplit

WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WSError(Exception):
    pass


class WSClient:
    """The client half of RFC 6455, in as few lines as correctness allows."""

    def __init__(self, url, timeout=8, origin=None, subprotocol="binary"):
        parts = urlsplit(url)
        if parts.scheme not in ("ws", "wss"):
            raise WSError("not a WebSocket URL: %s" % url)
        secure = parts.scheme == "wss"
        host = parts.hostname or "127.0.0.1"
        port = parts.port or (443 if secure else 80)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query

        self.sock = socket.create_connection((host, port), timeout=timeout)
        if secure:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=host)
        self.sock.settimeout(timeout)

        key = b64encode(os.urandom(16)).decode()
        head = [
            "GET %s HTTP/1.1" % path,
            "Host: %s" % (parts.netloc or host),
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Key: " + key,
            "Sec-WebSocket-Version: 13",
        ]
        if subprotocol:
            head.append("Sec-WebSocket-Protocol: " + subprotocol)
        if origin:
            head.append("Origin: " + origin)
        self.sock.sendall(("\r\n".join(head) + "\r\n\r\n").encode())

        self.rfile = self.sock.makefile("rb")
        status = self.rfile.readline().decode("latin-1").strip()
        headers = {}
        while True:
            line = self.rfile.readline().decode("latin-1").strip()
            if not line:
                break
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        self.status = status
        self.headers = headers
        if "101" not in status:
            raise WSError("the bridge answered %r instead of upgrading" % status)
        want = b64encode(sha1(key.encode() + WS_GUID).digest()).decode()
        if headers.get("sec-websocket-accept") != want:
            raise WSError("Sec-WebSocket-Accept does not match the key we sent")
        self.buf = b""

    # -- sending
    def send(self, payload, opcode=0x2):
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self.sock.sendall(head + mask + masked)

    # -- receiving
    def _read(self, n):
        data = self.rfile.read(n)
        if not data or len(data) < n:
            raise WSError("the connection ended mid-frame")
        return data

    def recv(self):
        """Payload of the next data frame. None when the peer closed."""
        while True:
            b0, b1 = self._read(2)
            opcode = b0 & 0x0F
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read(8))[0]
            if b1 & 0x80:
                raise WSError("the server masked a frame, which it must not")
            payload = self._read(length) if length else b""
            if opcode in (0x0, 0x1, 0x2):
                return payload
            if opcode == 0x8:
                self.close_code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else None
                self.close_reason = payload[2:].decode("utf-8", "replace")
                return None
            if opcode == 0x9:
                self.send(payload, opcode=0xA)
            # pong: ignore

    def close(self):
        try:
            self.send(struct.pack("!H", 1000), opcode=0x8)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


NAMES = {0xff: "GC_HANDSHAKE", 0xfd: "GC_PHASE", 0xfc: "GC_TIME_SYNC/HANDSHAKE_OK",
         0xfb: "GC_KEY_AGREEMENT", 0xfa: "GC_KEY_AGREEMENT_COMPLETED",
         0xfe: "GC_BINDUDP"}


def probe(url, seconds=12):
    """Answer handshakes for a while and report what came back, in order."""
    ws = WSClient(url)
    seen = []
    buf = b""
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            try:
                chunk = ws.recv()
            except (WSError, socket.timeout):
                break
            if chunk is None:
                seen.append(("VERBINDUNG GESCHLOSSEN", getattr(ws, "close_reason", "")))
                break
            buf += chunk
            # Deliberately the same walk as hsprobe.py: two packets parsed by
            # length, everything else reported by its header byte. The point is
            # that this file and that one can be compared line by line.
            while buf:
                h = buf[0]
                if h == 0xFD and len(buf) >= 2:
                    seen.append(("GC_PHASE", buf[1]))
                    buf = buf[2:]
                elif h == 0xFF and len(buf) >= 13:
                    ws.send(buf[:13])          # echo it back, as the client does
                    seen.append(("GC_HANDSHAKE", None))
                    buf = buf[13:]
                elif h == 0xFB:
                    seen.append(("GC_KEY_AGREEMENT", len(buf)))
                    buf = b""
                else:
                    seen.append((NAMES.get(h, "0x%02x" % h), len(buf)))
                    buf = b""
                    break
    finally:
        ws.close()
    return seen


def ping(url):
    """What the page's gate does before it starts: GET /ping over http(s).

    The gate refuses on anything whose body does not BEGIN with "m2-ws2tcp"
    (shell.html: text.indexOf('m2-ws2tcp') !== 0), so this checks exactly that
    and nothing more forgiving.
    """
    parts = urlsplit(url)
    scheme = "https" if parts.scheme == "wss" else "http"
    base = "%s://%s/ping" % (scheme, parts.netloc)
    try:
        import urllib.request
        with urllib.request.urlopen(base, timeout=5) as r:
            body = r.read(400).decode("utf-8", "replace")
    except Exception as e:
        print("  /ping: nicht erreichbar (%s)" % e)
        return False
    ok = body.startswith("m2-ws2tcp")
    print("  /ping: %s" % body.strip())
    print("  -> %s" % ("das Gate wuerde starten" if ok
                       else "DAS GATE WUERDE ABLEHNEN -- die Antwort beginnt nicht mit m2-ws2tcp"))
    return ok


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:7789/to/127.0.0.1:11000"
    print("=== durch die Bruecke: %s ===" % url)
    ping(url)
    print()
    try:
        seen = probe(url)
    except WSError as e:
        print("  Die Bruecke hat die Verbindung nicht angenommen: %s" % e)
        return 3
    except OSError as e:
        print("  Die Bruecke war nicht erreichbar: %s" % e)
        return 3

    for name, extra in seen:
        print("  %-24s %s" % (name, "" if extra is None else extra))

    heads = [n for n, _ in seen]
    print()
    if not heads:
        print("  ERGEBNIS: nichts empfangen -- die Bruecke steht, der Spielserver antwortet nicht")
        return 2
    if "GC_HANDSHAKE" in heads and ("GC_KEY_AGREEMENT" in heads or heads.count("GC_PHASE") >= 2):
        print("  ERGEBNIS: die Bruecke traegt den Handshake unveraendert durch")
        return 0
    print("  ERGEBNIS: unklar -- zu wenig gesehen, siehe oben")
    return 2


if __name__ == "__main__":
    sys.exit(main())
