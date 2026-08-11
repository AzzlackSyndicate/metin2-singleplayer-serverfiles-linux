#!/usr/bin/env python3
# =============================================================================
#  ws2tcp -- WebSocket in, TCP out, for the browser client.
#
#  A browser cannot open a TCP socket. The game speaks TCP and nothing else, so
#  a page that wants to play has to hand its bytes to something that can. That
#  is this: a WebSocket server that copies payload bytes into a TCP connection
#  to the game container and copies whatever comes back into WebSocket frames.
#
#  It knows nothing about the protocol it carries. The Diffie-Hellman exchange
#  and the block cipher happen between the client and the game core exactly as
#  they do for a native client; this sees ciphertext and moves it.
#
#  THE URL SHAPE IS THE CLIENT'S, NOT OURS
#  ---------------------------------------
#  The browser client (The Old Metin2 Project's wasm build) speaks a fixed
#  contract, and it was read off the built artefact rather than guessed:
#
#    * one port for every game connection, with the real destination in the
#      path -- `ws://host:port/to/<host>:<port>', percent-encoded
#      (tools/wasm/pre.js, and the shim survives into dist/browser/index.js)
#    * the page's connection gate validates the address with `GET /ping' and
#      refuses to start unless the body BEGINS with the string "m2-ws2tcp"
#      (tools/wasm/shell.html: text.indexOf('m2-ws2tcp') !== 0)
#    * the page is told where to dial through its own URL:
#      ?serverHost=<host>&serverPort=<port>[&serverTLS=1]. serverHost is
#      matched as [A-Za-z0-9.-]+ (mainPosix.cpp), which deliberately excludes
#      '/', so THE BRIDGE CANNOT LIVE UNDER A PATH PREFIX of its own -- it is
#      always host:port, and behind TLS that means port 443 with nginx routing
#      /to/ and /ping to here.
#
#  So this speaks their wire. It is not their proxy: `tools/ws2tcp' dials
#  whatever host and port the path names, which is right on a player's own
#  machine and is an open proxy anywhere else -- and its -allow list checks the
#  HOST only, so `-allow myserver' still leaves /to/myserver:15000, the db core,
#  reachable from any web page. That is why the implementation here is ours and
#  the URL shape is theirs.
#
#  WHAT IT WILL AND WILL NOT CONNECT TO
#  ------------------------------------
#  The host is fixed at startup (M2_BRIDGE_TARGET_HOST, default `game') and
#  cannot be influenced by a request. The host in the path is read, checked for
#  shape, logged -- and then discarded. Whatever a page asks for, the socket
#  goes to the game container and nowhere else, by construction rather than by
#  filtering.
#
#  The port from the path IS used, and it must be on a list computed at startup
#  from the number of channels. A port that is not on it is refused before any
#  socket is opened. Two sets are refused even when named explicitly: 12000 and
#  14000-14009 are the cores' peer-to-peer channel, and 15000 is the db core,
#  which speaks an unauthenticated protocol -- anyone who can reach it can
#  rewrite any character on the server.
#
#  ROUTES
#  ------
#      GET /ping                 200, plain text beginning "m2-ws2tcp".
#                                The page's gate probes this before it starts.
#      GET /health               200, plain text. The container healthcheck.
#      GET /ports                200, JSON. What this will dial; the panel reads
#                                it to decide whether to offer the button.
#      GET /to/<host>:<port>     WebSocket -> the game container on that port,
#                                if the port is on the list.
#
#  Anything else is a 404 and costs nothing.
#
#  MESSAGE BOUNDARIES ARE NOT PACKET BOUNDARIES. WebSocket is framed, TCP is a
#  stream. What arrives in one frame may leave in three writes and vice versa.
#  The client side must parse the game protocol out of a stream, the way it
#  already does over a socket -- it must not assume one frame is one packet.
#
#  Python standard library only, on purpose: this runs beside a stack that
#  already carries Python for the panel, and a byte pump between two sockets is
#  not worth a dependency that has to be fetched, pinned and audited.
# =============================================================================
import json
import os
import re
import socket
import struct
import sys
import threading
import time
from base64 import b64encode
from hashlib import sha1
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# The identity /ping answers with. The leading token is not decoration: the
# client page's gate does `text.indexOf('m2-ws2tcp') !== 0` and refuses to start
# on anything else, so this string is part of the contract. The rest says
# plainly which implementation this is -- it is not their Go proxy.
PING_NAME = "m2-ws2tcp"
PING_VERSION = "1.4.0-compatible"

# Ports that are never reachable through here, whatever the configuration says.
# See the header: p2p between the cores, and the db core.
FORBIDDEN_PORTS = {12000, 15000} | set(range(14000, 14010))


def env(name, default=""):
    return os.environ.get(name, "").strip() or default


def env_int(name, default):
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def log(msg):
    sys.stdout.write("%s [wsbridge] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()), msg))
    sys.stdout.flush()


_seen_lock = threading.Lock()
_seen = set()


def log_once(key, msg):
    """Log a line the first time its key appears, and never again.

    Borrowed from their Go proxy, which is right about this: a running client
    opens and closes dozens of connections -- login, a channel, a warp -- and
    one line each buries the only fact worth having on the screen, which is
    which destinations were reached at all.
    """
    with _seen_lock:
        if key in _seen:
            return
        _seen.add(key)
    log(msg)


# ---- configuration, read once at startup ------------------------------------
BIND = env("M2_BRIDGE_BIND", "0.0.0.0")
PORT = env_int("M2_BRIDGE_LISTEN_PORT", 7789)

TARGET_HOST = env("M2_BRIDGE_TARGET_HOST", "game")

# Inside the compose network the game always listens on its own numbers: the
# published host port (M2_AUTH_PORT) is a mapping and does not apply here.
AUTH_PORT = env_int("M2_BRIDGE_AUTH_PORT", 11000)
CHANNELS = env_int("M2_BRIDGE_CHANNELS", 1)

MAX_CONNECTIONS = env_int("M2_BRIDGE_MAX_CONNECTIONS", 200)
MAX_PER_IP = env_int("M2_BRIDGE_MAX_PER_IP", 8)
MAX_FRAME = env_int("M2_BRIDGE_MAX_FRAME", 262144)
CONNECT_TIMEOUT = env_int("M2_BRIDGE_CONNECT_TIMEOUT", 10)

# Behind nginx every connection arrives from the same address, so the per-address
# limit would cap the whole server at MAX_PER_IP unless the forwarded address is
# believed. Only switch this on when nothing but the proxy can reach this port.
TRUST_PROXY = env("M2_BRIDGE_TRUST_PROXY", "0").lower() in ("1", "true", "yes", "on")

# Empty means any origin, which is the honest default: the game ports these
# connections reach are published to the internet anyway for native clients, so
# refusing a foreign page here would protect nothing. Set it to the panel's own
# origin(s) on a server where that is not true.
ALLOWED_ORIGINS = [o.strip() for o in env("M2_BRIDGE_ORIGINS", "").replace(";", ",").split(",") if o.strip()]

# The host the client names in the path is used for NOTHING but the log: the
# socket goes to TARGET_HOST whatever a page asks for. This list, when set,
# additionally refuses a name this server does not answer to -- a diagnostic
# rather than a defence, and empty by default because the public address of a
# server changes and a client built before the change would then be refused for
# naming the old one, having reached the right box anyway.
ALLOWED_HOSTS = {h.strip().lower() for h in env("M2_BRIDGE_HOST_ALIASES", "").replace(";", ",").split(",") if h.strip()}


def computed_ports():
    """The set of game ports this bridge will dial.

    M2_BRIDGE_PORTS replaces the computed list outright, for a stack whose
    channels are laid out differently. Either way the forbidden ones are taken
    back out afterwards -- an explicit setting cannot open the db core.
    """
    raw = env("M2_BRIDGE_PORTS", "")
    if raw:
        wanted = set()
        for part in raw.replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                wanted.add(int(part))
    else:
        wanted = {AUTH_PORT}
        for ch in range(max(1, CHANNELS)):
            base = 13000 + 10 * ch
            wanted.update({base, base + 1, base + 2})
    refused = wanted & FORBIDDEN_PORTS
    if refused:
        log("refusing to serve %s -- those are the cores' own ports, not the game's"
            % ", ".join(str(p) for p in sorted(refused)))
    return {p for p in wanted if 1 <= p <= 65535} - FORBIDDEN_PORTS


ALLOWED_PORTS = computed_ports()

# ---- connection accounting --------------------------------------------------
_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
_per_ip = {}
_per_ip_lock = threading.Lock()


def take_ip_slot(ip):
    with _per_ip_lock:
        if _per_ip.get(ip, 0) >= MAX_PER_IP:
            return False
        _per_ip[ip] = _per_ip.get(ip, 0) + 1
        return True


def free_ip_slot(ip):
    with _per_ip_lock:
        n = _per_ip.get(ip, 0) - 1
        if n > 0:
            _per_ip[ip] = n
        else:
            _per_ip.pop(ip, None)


# ---- WebSocket framing ------------------------------------------------------
class ProtocolError(Exception):
    pass


def frame(payload, opcode=0x2):
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return head + payload


def unmask(data, key):
    if not data:
        return data
    pad = (key * (len(data) // 4 + 1))[:len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(pad, "big")).to_bytes(len(data), "big")


def read_exactly(rfile, n):
    """n bytes, or None at a clean end of stream."""
    if n == 0:
        return b""
    data = rfile.read(n)
    if not data or len(data) < n:
        return None
    return data


def read_frame(rfile):
    """(fin, opcode, payload), or None when the peer stopped sending."""
    head = read_exactly(rfile, 2)
    if head is None:
        return None
    b0, b1 = head[0], head[1]
    if b0 & 0x70:
        raise ProtocolError("reserved bits set")
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        ext = read_exactly(rfile, 2)
        if ext is None:
            return None
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = read_exactly(rfile, 8)
        if ext is None:
            return None
        length = struct.unpack("!Q", ext)[0]
    # RFC 6455: a client MUST mask. An unmasked frame here is either a broken
    # client or something that is not a browser, and both are worth refusing.
    if not masked:
        raise ProtocolError("client frame was not masked")
    if length > MAX_FRAME:
        raise ProtocolError("frame of %d bytes exceeds the %d byte limit" % (length, MAX_FRAME))
    if opcode in (0x8, 0x9, 0xA) and (length > 125 or not fin):
        raise ProtocolError("malformed control frame")
    key = read_exactly(rfile, 4)
    if key is None:
        return None
    payload = read_exactly(rfile, length) if length else b""
    if payload is None:
        return None
    return fin, opcode, unmask(payload, key)


class Pipe:
    """One browser connection and the TCP connection it is joined to."""

    def __init__(self, ws_sock, rfile, tcp_sock, label):
        self.ws = ws_sock
        self.rfile = rfile
        self.tcp = tcp_sock
        self.label = label
        self.send_lock = threading.Lock()
        self.closed = threading.Event()

    def ws_send(self, payload, opcode=0x2):
        with self.send_lock:
            self.ws.sendall(frame(payload, opcode))

    def shutdown(self):
        if self.closed.is_set():
            return
        self.closed.set()
        for sock in (self.tcp, self.ws):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # -- game -> browser
    def pump_tcp_to_ws(self):
        try:
            while not self.closed.is_set():
                chunk = self.tcp.recv(16384)
                if not chunk:
                    break
                self.ws_send(chunk)
        except OSError:
            pass
        finally:
            try:
                if not self.closed.is_set():
                    self.ws_send(struct.pack("!H", 1000), opcode=0x8)
            except OSError:
                pass
            self.shutdown()

    # -- browser -> game
    def pump_ws_to_tcp(self):
        try:
            while not self.closed.is_set():
                got = read_frame(self.rfile)
                if got is None:
                    break
                fin, opcode, payload = got
                if opcode in (0x0, 0x2):
                    if payload:
                        self.tcp.sendall(payload)
                elif opcode == 0x9:
                    self.ws_send(payload, opcode=0xA)
                elif opcode == 0xA:
                    pass
                elif opcode == 0x8:
                    break
                elif opcode == 0x1:
                    # Text would have to be re-encoded to be sent on, and any
                    # client sending it has misunderstood what this carries.
                    raise ProtocolError("text frame -- this bridge carries binary only")
                else:
                    raise ProtocolError("unknown opcode 0x%x" % opcode)
        except ProtocolError as e:
            log("%s closed: %s" % (self.label, e))
            try:
                self.ws_send(struct.pack("!H", 1002) + str(e).encode()[:100], opcode=0x8)
            except OSError:
                pass
        except OSError:
            pass
        finally:
            self.shutdown()


class Handler(BaseHTTPRequestHandler):
    # Enough for a browser to finish its request line and headers, not enough
    # for an idle socket to hold a connection slot indefinitely.
    timeout = 15
    protocol_version = "HTTP/1.1"
    server_version = "m2wsbridge"
    sys_version = ""

    # BaseHTTPRequestHandler logs every request to stderr; we log the ones that
    # matter ourselves, with the target they reached.
    def log_message(self, fmt, *args):
        pass

    # ---- plain HTTP -------------------------------------------------------
    def _text(self, code, body, ctype="text/plain; charset=utf-8", cors=False):
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def client_ip(self):
        peer = self.client_address[0] if self.client_address else "?"
        if TRUST_PROXY:
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[0].strip() or peer
        return peer

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        trimmed = path.rstrip("/") or "/"

        if trimmed == "/ping":
            # Cross-origin on purpose: the page is served by the panel and this
            # answers on the bridge's own address, so the gate's fetch is a
            # cross-origin one. Their proxy opens it for the same reason.
            self._text(200, "%s %s pong (relay :%d -> %s, ports %s)\n" % (
                PING_NAME, PING_VERSION, PORT, TARGET_HOST,
                ",".join(str(p) for p in sorted(ALLOWED_PORTS))),
                cors=True)
            return
        if trimmed == "/health":
            self._text(200, "ok\n")
            return
        if trimmed == "/ports":
            self._text(200, json.dumps({
                "host": TARGET_HOST,
                "auth": AUTH_PORT,
                "ports": sorted(ALLOWED_PORTS),
                "path": "/to/{host}:{port}",
            }) + "\n", ctype="application/json", cors=True)
            return

        named_host, port = self.resolve_target(path)
        if port is None:
            # Their proxy's own wording, near enough: this is what a developer
            # sees when the page dials something the bridge will not open.
            self._text(404, "no destination -- the client must dial /to/<host>:<port>,"
                            " and the port must be one this server's game runs on\n")
            return
        if not self.is_upgrade():
            self._text(426, "this endpoint speaks WebSocket\n")
            return
        self.serve_websocket(named_host, port)

    def do_HEAD(self):
        self.do_GET()

    def resolve_target(self, path):
        """`/to/<host>:<port>' -> (host as named, port) or (name, None).

        The ONLY place a target is chosen, and the host it returns is used for
        nothing but the log. The socket goes to TARGET_HOST whatever a page
        asks for -- that is what makes an open proxy impossible here rather
        than merely unlikely.
        """
        if not path.startswith("/to/"):
            return "", None
        raw = unquote(path[4:])
        if not raw or len(raw) > 300:
            return "", None
        # Split on the LAST colon so a name or an IPv4 literal keeps its port.
        # An IPv6 literal would need brackets, which the client never emits.
        head, sep, tail = raw.rpartition(":")
        if not sep or not head or not tail.isdigit():
            return "", None
        if not re.match(r"^[A-Za-z0-9.\-]{1,253}$", head):
            return "", None
        port = int(tail)
        if port not in ALLOWED_PORTS:
            return head, None
        if ALLOWED_HOSTS and head.lower() not in ALLOWED_HOSTS:
            return head, None
        return head, port

    def is_upgrade(self):
        conn = (self.headers.get("Connection", "") or "").lower()
        upg = (self.headers.get("Upgrade", "") or "").lower()
        return "upgrade" in conn and upg == "websocket"

    # ---- the upgrade, and then the pumping --------------------------------
    def serve_websocket(self, named_host, port):
        key = self.headers.get("Sec-WebSocket-Key", "")
        version = self.headers.get("Sec-WebSocket-Version", "")
        if not key or version != "13":
            self._text(400, "bad WebSocket handshake\n")
            return

        origin = self.headers.get("Origin", "")
        if ALLOWED_ORIGINS and origin not in ALLOWED_ORIGINS:
            log("refused origin %r" % origin[:120])
            self._text(403, "origin not allowed\n")
            return

        ip = self.client_ip()
        if not _slots.acquire(blocking=False):
            self._text(503, "too many connections\n")
            return
        if not take_ip_slot(ip):
            _slots.release()
            self._text(429, "too many connections from your address\n")
            return

        tcp = None
        try:
            try:
                tcp = socket.create_connection((TARGET_HOST, port), timeout=CONNECT_TIMEOUT)
            except OSError as e:
                log_once("fail:%s:%d:%s" % (TARGET_HOST, port, e),
                         "-> %s:%d FAILED: %s" % (TARGET_HOST, port, e))
                self._text(502, "the game server did not answer\n")
                return

            tcp.settimeout(None)
            tcp.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            tcp.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self.connection.settimeout(None)
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            accept = b64encode(sha1(key.encode() + WS_GUID).digest()).decode()
            resp = [
                "HTTP/1.1 101 Switching Protocols",
                "Upgrade: websocket",
                "Connection: Upgrade",
                "Sec-WebSocket-Accept: " + accept,
            ]
            offered = [p.strip() for p in (self.headers.get("Sec-WebSocket-Protocol", "") or "").split(",")]
            if "binary" in offered:
                resp.append("Sec-WebSocket-Protocol: binary")
            self.connection.sendall(("\r\n".join(resp) + "\r\n\r\n").encode())

            # The page asked for `named_host'; it got TARGET_HOST. Both are in
            # the line, once per distinct route rather than once per connection
            # -- a client opens dozens (login, a channel, a warp) and logging
            # each one buries the only thing worth seeing, which is which
            # destinations were reached at all.
            route = "%s:%d -> %s:%d" % (named_host, port, TARGET_HOST, port)
            log_once("up:" + route, "route %s" % route)
            label = "%s %s" % (ip, route)
            started = time.time()

            pipe = Pipe(self.connection, self.rfile, tcp, label)
            reader = threading.Thread(target=pipe.pump_tcp_to_ws, daemon=True)
            reader.start()
            pipe.pump_ws_to_tcp()
            reader.join(timeout=5)
            # Not logged per connection -- see log_once above; a warp that
            # reaches a new destination still gets its own line, once.
            _ = (label, time.time() - started)
        finally:
            self.close_connection = True
            if tcp is not None:
                try:
                    tcp.close()
                except OSError:
                    pass
            free_ip_slot(ip)
            _slots.release()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """One line, not fifteen.

        socketserver prints a full traceback for every exception, and the
        commonest one here is a player closing the tab: the browser resets the
        connection and the read fails. That is not an error, and a container log
        full of tracebacks for it hides the ones that are. Anything unexpected
        still gets a line saying what it was.
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError,
                            TimeoutError, socket.timeout)):
            return
        log("unexpected error from %s: %r"
            % (client_address[0] if client_address else "?", exc))


def main():
    if not ALLOWED_PORTS:
        log("FATAL: no reachable game port was worked out. Check M2_BRIDGE_CHANNELS.")
        sys.exit(1)
    log("target %s, ports %s" % (TARGET_HOST, ", ".join(str(p) for p in sorted(ALLOWED_PORTS))))
    log("limits: %d connections, %d per address, %d byte frames"
        % (MAX_CONNECTIONS, MAX_PER_IP, MAX_FRAME))
    log("origins: %s" % (", ".join(ALLOWED_ORIGINS) if ALLOWED_ORIGINS else "any"))
    log("listening on %s:%d" % (BIND, PORT))
    Server((BIND, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
