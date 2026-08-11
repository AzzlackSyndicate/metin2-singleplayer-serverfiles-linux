# ws2tcp — the WebSocket end of the browser client

A browser cannot open a TCP socket. The game speaks TCP and nothing else. This
service is the join between the two: a WebSocket server that copies payload
bytes into a TCP connection to the game container, and copies what comes back
into WebSocket frames.

It understands nothing about what it carries. The Diffie-Hellman exchange and
the block cipher happen between the browser client and the game core exactly as
they do for a native client — this sees ciphertext and moves it.

It is in the `browser` compose profile, so `docker compose up -d` neither starts
it nor builds it. A server that does not offer a browser client never runs it
and is not affected by it in any way.

    docker compose --profile browser up -d wsbridge

---

## The URL shape is the client's, not ours

The browser client ships its own WebSocket proxy, `tools/ws2tcp` — a Go binary
the player runs on their own machine. **This service speaks that proxy's wire
protocol**, because the built client speaks it and a client is not a thing you
negotiate with. Every line below was read off the built artefact, not guessed:

| What | Where it is fixed |
|---|---|
| One port for every connection, destination in the path: `ws://host:port/to/<host>:<port>`, percent-encoded | `tools/wasm/pre.js`; the shim survives into `dist/browser/index.js` |
| The page's gate probes `GET /ping` and refuses to start unless the body **begins with** `m2-ws2tcp` | `tools/wasm/shell.html` — `text.indexOf('m2-ws2tcp') !== 0` |
| The page is told where to dial through its own URL: `?serverHost=<host>&serverPort=<port>[&serverTLS=1]` | `src/UserInterface/mainPosix.cpp` |
| `serverHost` is matched as `[A-Za-z0-9.\-]+` — **no `/`**, deliberately, "so a value cannot smuggle a path" | same file, in as many words |

That last row is the one with consequences. **The bridge cannot live under a
path prefix of its own.** It is always `host:port`, so behind TLS the only
arrangement that works is the panel's own domain on port 443, with nginx routing
the two prefixes the client actually uses — `/to/` and `/ping` — through to
here. `install.sh` writes exactly that, and the panel builds the link.

## Why this is not their proxy

Their Go proxy dials whatever host and port the path names. On a player's own
machine that is correct and this document would be pointless. Beside a game
server, reachable from the internet, it is an open proxy — and its `-allow`
list does not close it:

```go
func hostAllowed(host string, allow []string) bool   // ← host. Not port.
```

So `-allow myserver.example` still leaves `/to/myserver.example:15000` — **the
db core**, which speaks an unauthenticated protocol: anyone who can reach it can
rewrite any character on the server. It also has no connection limit, no
per-address limit, no frame-size limit and no idle timeout, all of which are
right for one player on one machine and none of which are right here.

The wire is theirs. The implementation is ours, and it is pinned:

* **The host is fixed at startup** (`M2_BRIDGE_TARGET_HOST`, default `game`).
  The host in the path is read, checked for shape, logged — and discarded. The
  socket goes to the game container whatever a page asks for. Not filtering:
  construction. `ws_selftest.py` proves it by dialling
  `/to/no-such-host.invalid:<port>` and getting a working game connection.
* **The port from the path must be on a list** computed at startup from the
  channel count. Anything else is refused before a socket is opened.
* **12000, 14000–14009 and 15000 are refused** even when named explicitly in
  `M2_BRIDGE_PORTS` — the cores' peer-to-peer channel and the db core.
* Total and per-address connection limits, a frame-size limit, no root, no
  writable path, and a healthcheck.

If the port allowlist ever lands upstream in `tools/ws2tcp`, this service
becomes replaceable by their binary and should be replaced by it. It is one
function: `hostAllowed` gains a `ports []int` and the caller checks `dport`.

---

## Routes

| Route | What it does |
|---|---|
| `GET /ping` | The gate's probe. Body begins `m2-ws2tcp`; `Access-Control-Allow-Origin: *`. |
| `GET /health` | The container healthcheck. |
| `GET /ports` | JSON: what this will dial. The panel reads it to decide whether to offer the button. |
| `GET /to/<host>:<port>` | WebSocket → the game container on that port, if the port is on the list. |

Anything else is a 404.

### Framing

Binary frames. A text frame is refused with close code 1002, because carrying it
would mean re-encoding bytes that are already ciphertext.

**Message boundaries are not packet boundaries.** WebSocket is framed, TCP is a
stream: what the client sends in one frame may arrive at the game in three
writes, and what the game sends as one packet may arrive as two frames. Parse
the game protocol out of a stream, the way the native client already does over a
socket. A client that assumes one frame is one packet works in testing and fails
under load.

Ping is answered with pong. A frame larger than `M2_BRIDGE_MAX_FRAME`
(256 KiB by default) closes the connection — game packets are three orders of
magnitude below that.

---

## Settings

All optional; the compose file supplies the ones that matter.

| Variable | Default | What it does |
|---|---|---|
| `M2_BRIDGE_TARGET_HOST` | `game` | The one host it will ever dial. |
| `M2_BRIDGE_AUTH_PORT` | `11000` | The *internal* auth port, not the published one. |
| `M2_BRIDGE_CHANNELS` | `1` | How many channels to work out ports for. |
| `M2_BRIDGE_PORTS` | — | Replaces the computed list outright. The forbidden ones are still removed. |
| `M2_BRIDGE_HOST_ALIASES` | — | Names this server answers to. Set, an unknown name is refused; empty, any name is accepted and still only the game is dialled. |
| `M2_BRIDGE_LISTEN_PORT` | `7789` | Inside the container. |
| `M2_BRIDGE_MAX_CONNECTIONS` | `200` | Total. |
| `M2_BRIDGE_MAX_PER_IP` | `8` | Per address. |
| `M2_BRIDGE_MAX_FRAME` | `262144` | Largest frame accepted. |
| `M2_BRIDGE_TRUST_PROXY` | `0` | Believe `X-Forwarded-For`. Only ever with nothing but nginx able to reach the port — otherwise the per-address limit is a suggestion. |
| `M2_BRIDGE_ORIGINS` | — | Comma-separated allow-list. Empty means any, which is the honest default: these game ports are published to the internet for native clients regardless, so refusing a foreign page here protects nothing. |

---

## Checking it without a browser

`wasm-port/scripts/ws_selftest.py` needs no game server, no browser and no
client build: it starts a stub that answers like the auth core, starts this
bridge against it, and checks the gate contract, the relay, the pinning, the
frame rules and the limits.

    python3 wasm-port/scripts/ws_selftest.py

`wasm-port/scripts/ws_probe.py` asks a **real** server the same question
`hsprobe.py` asks over plain TCP, one hop further:

    python3 wasm-port/scripts/ws_probe.py ws://127.0.0.1:7789/to/127.0.0.1:11000
