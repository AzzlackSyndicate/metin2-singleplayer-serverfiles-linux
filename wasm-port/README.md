# Metin2 in the browser — working notes

Scratch work for the `wasm-port` branch. **Nothing here is part of the server
product**; it is the record of an investigation, kept so the next session does
not repeat it.

Everything runs against three trees that live **outside** this repository:

| Path (inside WSL) | What it is |
|---|---|
| `/opt/m2wasm` | The Old Metin2 Project's client, branch `feat/bgfx-renderer` |
| `/opt/m2origclient` | The stock 2014 client source, from `ClientVS22.zip` |
| `/opt/m2port/port40250/server` | Our own server source, the patched r40250 tree |

> **None of those may ever be committed here.** They carry Ymir/Webzen material,
> and the Old Metin2 tree additionally ships ~2.3 GB of game data in `bin/`.
> The rule is the one this project has always had: build tools in git, content
> out of it.

---

## What was established

**The port is real and it already exists.** The Old Metin2 Project has a
`feat/bgfx-renderer` branch, 546 commits ahead of their master, that builds
`dist/browser` (wasm32) alongside native Linux and cross-compiled Windows
clients. It carries a Go WebSocket↔TCP bridge (`ws2tcp`) so a browser page can
reach an unmodified game server.

**The three blockers I had scoped as hardest were already solved by them** —
Granny 3D, SpeedTree and Miles Sound System are consumed as *source* now
(`extern/sdk/fetch.sh`), not as 32-bit Windows DLLs.

**The client builds and runs here.** 194,070 lines of C++, CPython 3.14, bgfx on
Vulkan, in a window on Windows through WSLg. See `scripts/setup.sh` onward.

**It cannot log in to our server, and the reason is now known.** Our r40250
enables `_IMPROVED_PACKET_ENCRYPTION_` (`common/service.h:8`); after the
handshake the server sends `0xfb` HEADER_GC_KEY_AGREEMENT and waits. The Old
Metin2 client has no such packet in its enum at all, so it discards it and waits
for a phase change that has already been and gone.

**Turning it off on the server is not an option.** Measured, not assumed: with
the define removed, the Old Metin2 client reaches the auth phase — and the stock
Windows client stops working, because *it* requires the key agreement. The two
clients are mutually exclusive on that switch, so the fix has to go into the
client.

---

## The method that worked, and the one that did not

Two source-level "proofs" that the encryption was off were both wrong:

* `grep` for the `#define` found nothing, because `common/service.h` carries a
  Korean comment and grep treats the file as binary without `-a`.
* `strings | grep "key agree"` on the running binary returned zero, because the
  code path in question logs nothing.

The packet capture settled it in one step. **For protocol questions: measure
first, read the source second.** `scripts/capture.sh` → `scripts/tshark.sh` →
`scripts/follow.sh` is that path.

`scripts/hsprobe.py` and `scripts/login_probe.py` speak the protocol directly,
which means most questions can be answered without a human clicking through a
client — and, since `login_probe.py` uses an invented account, without a real
password crossing the wire. The login packet is **not encrypted at this stage**;
a capture of a real login contains the account password in clear text.

---

## Scripts

Run them as files, never inline: `wsl -d Ubuntu-24.04 -u root -- sh /mnt/c/…/x.sh`.
Passing a script as `bash -c '…'` from this host loses `$variables` and `$(…)`,
which produced two rounds of nonsense readings before it was noticed. WSL also
wipes `/tmp` between invocations, so anything that extracts then inspects has to
do both in one run (`measure.sh`, `extract_orig.sh`).

Twenty-six are kept. The investigation used about seventy; the rest were one-shot
probes whose answers are the findings above, and a script whose question is
answered is just a file to read past.

| Group | Scripts |
|---|---|
| Build the client from nothing | `setup.sh` → `deps2.sh` → `fetchsdk.sh` → `buildpy.sh` → `fetchbin.sh` → `buildclient.sh`; `mkuser.sh` for a login user, `state.sh` to check the checkout |
| Run it and read it | `launch.sh` `clientlog.sh` |
| Ask the server directly, no client needed | `hsprobe.py` (how far does the handshake get?) `login_probe.py` (which login shape is accepted?) |
| Ask it through the WebSocket bridge | `ws_probe.py` (the same handshake, one hop further) `ws_selftest.py` (the bridge alone, no game server needed) |
| Read the client tree's own bridge and what the built client dials | `ws2tcp_read.sh` `ws2tcp_read2.sh` `ws2tcp_client.sh` `ws2tcp_client2.sh` `ws2tcp_client3.sh` `ws2tcp_gate.sh` |
| Measure the wire | `capture.sh` → `tshark.sh` → `follow.sh` |
| The server's own account of it | `recheck.sh` |
| Change the server, and put it back | `disable_enc.sh` / `revert.sh` |
| Port from the stock client | `extract_orig.sh` `port_cipher.sh` |

`disable_enc.sh` and `revert.sh` touch the **live VPS**. `revert.sh` is the one
that puts it back; the original `service.h` is kept beside the file as
`service.h.m2orig`.

---

## Where it stands

Done: `cipher.cpp` / `cipher.h` ported from the stock client into
`/opt/m2wasm/src/NetworkLib` (`port_cipher.sh`) — copied rather than rewritten,
because it has to be bit-identical to the server's counterpart. The nine
`#ifdef __THEMIDA__` blocks are removed whole; removing only their `#ifdef`
lines left nine orphaned `#endif`, which is how that script failed first time.

**DONE.** All five steps are in the tree and the client plays against the VPS.
The exchange runs on **both** channels — the auth connector and the game stream
each need their own handlers, and the game stream additionally needs both
headers in `CMainPacketHeaderMap`, because `CheckPacket()` resolves sizes from
that table *before* the phase switch ever sees the header.

What actually cost the day was not the cipher but four packet layouts, and the
lesson is the same each time: **the wire is the authority, not the header file.**

| packet | wire | this tree had | why |
|---|---|---|---|
| `TPacketKeyAgreementCompleted` | 4 | 1 | dropped `BYTE data[3]` when transcribing |
| `TPacketGCPointChange` | 17 | 14 | `header` narrowed from `int` to `BYTE` |
| `TPacketGCTime` | 5 | 9 | `time_t` replaced by `int64_t`; the server is a **32-bit** build |
| `TPlayerSkill` | 6 | 10 | same, which made the skill packet 2551 instead of 1531 |
| `TPacketGCViewEquip` | 1221 | 423 | this serverfile raised `WEAR_MAX_NUM` to 32; the client has 11 |

Each was silent: `__AnalyzePacket` answers "fine" to a header it does not know,
so a mis-sized packet looks exactly like a server that stopped talking.

**The parked sequence-byte divergence was a wrong diagnosis.** No `SEQUENCE
mismatch` was ever logged, and the server closes the connection when one occurs,
which never happened. "UNKNOWN HEADER: 162" came from framing that had already
slipped, not from the sequence table. The client sends its sequence byte after
the packet and the server reads it as the last byte inside the declared length —
which is the *same byte in the same place*. There was nothing to fix.

Read the sizes out of both compilers rather than out of the headers:
`scripts/wirediff.py` (gdb against the server binary, which carries debug info,
vs. a generated program compiled against the client's `Packet.h`) and
`scripts/headermatch.py`, which does it **per header number** — the unit that
actually decides whether the stream desyncs. Names mislead: the client carries
`ItemSet2`, `TargetCreateNew` and `MainCharacter2_EMPIRE` under the numbers the
server calls `ItemSet`, `TargetCreate` and `MainCharacter`, and each of those
looked like a divergence until the number was checked.

One genuine defect remains, and it is **server-side**: header 9
(`CHARACTER_CREATE_FAILURE`) is sent with two different widths — 10 bytes in two
branches of `input_login.cpp:442-465`, 2 bytes in the `LC_IsCanada`/`LC_IsEurope`
ones. `LC_IsCanada()` is hard-wired to `false` in this build, so an **invalid
character name** takes the 10-byte path while every client expects 2. That
breaks the stock Windows client too; it just has never met an invalid name.

---

## The other half: getting a browser to the server at all

**A browser cannot open a TCP socket.** However well the client builds, it
cannot reach the game without something beside the server that turns a
WebSocket into a TCP connection.

The client tree already has one: `tools/ws2tcp`, a Go binary the player runs on
their own machine, which also serves the page. Its wire is what the built client
speaks, and it was read off the artefact rather than assumed:

* one port for every connection, destination percent-encoded in the path —
  `ws://proxy:PORT/to/<host>:<port>` (`tools/wasm/pre.js`, and the shim survives
  into `dist/browser/index.js`)
* the page's gate probes `GET /ping` and refuses to start unless the body
  **begins with** `m2-ws2tcp` (`tools/wasm/shell.html`)
* the page learns the address from its own URL,
  `?serverHost=&serverPort=[&serverTLS=1]`, and `serverHost` is matched as
  `[A-Za-z0-9.\-]+` — no `/`, deliberately, so **the bridge can never live under
  a path**

It does **not** parse the game protocol. It has no `HEADER_GC_WARP` handling and
reads no payload byte; what follows a warp is the *client*, which stashes the
new destination in `globalThis.__m2ProxyDest` one line before each `connect()`
(`NetStream.cpp` under `__EMSCRIPTEN__`) and the shim moves it into the path.
The proxy just obeys.

For the server side of this project that binary cannot be used as it stands:
its `-allow` list checks the **host only** (`hostAllowed(host, allow)`), so a
proxy pinned to the server's own name still exposes `/to/thatname:15000` — the
db core, unauthenticated — to any web page, and it has no connection, address or
frame limits. Right for one player on one machine; not for a public server.

So `linux-port/docker/wsbridge/` speaks the same wire and pins the target: one
fixed host, a port allowlist, the db core and the p2p ports refused outright.
`ws_selftest.py` proves the pinning by dialling `/to/no-such-host.invalid:<port>`
and getting a working game connection — a bridge that resolved what the page
named could not have answered.

If a port allowlist ever lands in `tools/ws2tcp`, their binary becomes the
better answer and should replace ours. It is one function.

---

## The login screen's Connect button, and why it looked dead

Reported as: the Connect button does not react to a mouse click — it lights up
on hover, but neither a normal click nor a press and release ten seconds apart
does anything, not even with empty fields where an error message is due. Enter
in the password field logs in fine. The message box's OK, the server dialog's
OK and the server list's rows all react normally.

**The browser is not at fault, and neither is the button.** What is at fault is
**one pixel**, and it is the pixel the driver was aiming at.

`ui.py`'s `Button.SetText` parents a `TextLine` to the button, places it at the
button's **centre**, and never gives it a size — `CWindow`'s width and height
stay 0. `CWindow::IsIn` tests both edges with `<=`, so a zero-size rect is not
empty: it matches exactly one pixel. `CWindow::PickWindow` walks children before
it considers itself, so on that one pixel it descends into the label and returns
**the label**; the button underneath gets neither `OnMouseOverIn` nor
`OnMouseLeftButtonUp`.

Measured on the Connect button (`large_button_01.sub`, 88×21, at 423,423 in a
1024×768 canvas): every pixel of it hovers and clicks except **(468,434)**, and
its four neighbours do not. `wb09-login.sh` and `wb10-connect.sh` both drive
`468,434`, because that is what "click the button" computes. `wb15`…`wb17`
map it; `wb17` also clicks one pixel to the left and gets the *Enter the ID.*
box the report said was missing.

This is **not** a port regression. `/opt/m2origclient`'s `PythonWindow.cpp` has
the same `PickWindow`, the same `IsIn` and the same `CWindow` constructor, and
stock `ui.py` builds the same unsized centred label. The stock 2014 client has
the same dead pixel; nobody ever hit it, because a hand on a mouse does not.

It is also **not specific to the browser build.** Everything above is in PyLib
and in `root/ui.py`, both of which the Linux desktop client runs unchanged. The
desktop client has the identical dead pixel — and, for the same reason, the
identical practical behaviour: the button works.

`wb18-fix-anchor-pick.sh` closes it in `CWindow::PickWindow`: a window that has
a parent and no extent of its own is an **anchor**, not a pick surface, so it
stops answering the hit test. Layer roots are excluded — `__PickWindow` compares
a layer's result against the layer by identity — and `FLAG_IGNORE_SIZE` is
honoured. `wb19` syntax-checks it against both `compile_commands.json` trees;
`wb20` re-runs the click at `468,434` after a wasm build and gets the box.
