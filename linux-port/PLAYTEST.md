# Playtesting the Linux port

Two things, in order: **how to play the server today**, and **the record of the
first time a real client logged into it**. The second is history — it describes
a hand-built WSL setup that no longer exists and that nobody should try to
recreate — but it is the evidence the whole port rests on, so it is kept.

---

## Playing it today

The Docker stack builds the client for you, with your address already inside it:

```sh
cd linux-port/docker
docker compose run --rm client-builder
```

Then the panel's **Download the game** button hands out a `client.zip` that
needs no editing — the address, the port and the channel list are already
correct. Details, including what to do when the MEGA link the archive comes from
is refusing to serve, are in
[docker/README.md → Giving players the game client](docker/README.md#giving-players-the-game-client).

On Windows, `installer/install.ps1` does all of it in one command and binds
everything to `127.0.0.1`, so the client it produces points at your own PC.

If you are building a client by hand instead, copy
[`client/serverinfo.py`](client/serverinfo.py) **next to `Metin2Release.exe`**
and put your address in it. The client reads the copy beside the `.exe`; a
`root/serverinfo.py` is never read. On a `_DISTRIBUTE` client — which this one
is — `pack/root.epk` is searched *before* loose files, so the pack has to be
moved aside for the loose `serverinfo.py` to win. `client-builder` does that
step for you; by hand it is easy to forget and looks exactly like the address
not taking effect.

### Accounts

The shipped database contains two accounts:

| Account | Password | Note |
|---|---|---|
| `admin` | `123456789` | IMPLEMENTOR in `common.gmlist` |
| `test` | `123456789` | ordinary player |

Both plaintext passwords were verified against the stored hashes, not guessed.
They are also public knowledge — the hashes are inside a distributed database
dump. **Change them before the server is reachable from the internet.**

Two characters already exist, both on **map 41**, which channel 1's third core
serves on port 13002. All three cores of a channel must be running for those
characters to be reachable; the Docker stack starts all three.

---

# History — the first successful login

Everything below happened in WSL2 on the maintainer's Windows machine, against a
tree at `/opt/m2port/` that was assembled by hand. It is not reproducible from
this repository and is not meant to be. It is here because it is what proved the
port worked, and because the WSL-specific obstacles are real and would be hit
again by anyone testing this way.

At that point three cores were up:

| Core | Address | Memory | Status |
|---|---|---|---|
| `db` | `127.0.0.1:15000` | 21 MB | connected to MariaDB, protos loaded |
| `auth` | `172.27.84.75:11000` | 9 MB | "AUTH_SERVER: I am the master" |
| `game` (channel 1) | `172.27.84.75:13000` | 818 MB | 41 maps loaded |

`syserr` contained no errors — only normal boot markers.

## Obstacle 1 — the WSL firewall

WSL2's Hyper-V firewall defaults to `DefaultInboundAction: Block`, so Windows
could not reach the server at all. The rule that opened it, from an
administrator PowerShell:

```powershell
New-NetFirewallHyperVRule -Name "Metin2-Linux-Test" `
  -DisplayName "Metin2 Linux port test (auth 11000, game 13000)" `
  -Direction Inbound `
  -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' `
  -Protocol TCP -LocalPorts 11000,13000 -Action Allow
```

That opens only those two ports and only for the WSL virtual machine.
`Remove-NetFirewallHyperVRule -Name "Metin2-Linux-Test"` undoes it, and
`Test-NetConnection <wsl-ip> -Port 11000` confirms it took.

Deliberately **not** done instead: switching WSL to mirrored networking mode. It
would have given a stable `127.0.0.1` and avoided the firewall entirely, but it
is a machine-wide change that can break Docker Desktop's networking — and Docker
Desktop was installed on that machine.

## Obstacle 2 — the address moves

`172.27.84.75` was the WSL virtual machine's address, and it **changes whenever
WSL restarts**. Re-reading it (`wsl -d Ubuntu-24.04 -- ip -4 addr show eth0`)
and hand-editing `SERVER_IP` in `serverinfo.py` was part of every session.

The reason it could not simply bind everything: `game/src/main.cpp` binds to
`g_szPublicIP`, not `0.0.0.0`, and the config code actively rejects
`BIND_IP: 0.0.0.0` because it sentinel-tests `g_szPublicIP[0] == '0'`. That was
tolerable for a test and unacceptable for a container, so the port fixed it —
the Docker stack sets `BIND_IP: 0.0.0.0` and it works. This obstacle is the
direct reason that change exists.

## Obstacle 3 — nobody could log in until MAP_ALLOW was changed

Both shipped characters live on map 41, which belongs to channel 1's *third*
core. Only one core was running, with its stock 15-map list, so there was
nothing for those characters to log into. The workaround at the time was to
merge all 41 maps into that single core's `MAP_ALLOW`.

**Do not copy that.** It worked well enough to get a login and it is the wrong
answer: 41 maps in one core pushes its main loop past the 50 ms window the
client allows during the initial handshake, and then no client can connect at
all — while the login server keeps answering perfectly, which makes it look like
a protocol bug. The Docker stack runs the three cores per channel that the
server files ship with, and splits the maps as they were meant to be split.

## What this playtest proved

The login itself, and everything downstream of it. It is also what confirmed the
fix in [FDWATCH-BUG.md](FDWATCH-BUG.md) — before that one-line change the client
reached the key agreement and then hung forever, with no error anywhere in the
server's logs.

Already proven before the client was ever pointed at it: all modules compile and
link 32-bit; `db` connects to MariaDB on all four handles and serves; `auth`
becomes master; `game` loads its maps and listens; the epoll event loop passed
42 API checks and a 250-client soak (70,000,000 bytes echoed, 0 spurious events,
0.1 % idle CPU); `libsql` passed 29 live database checks including the async
worker thread.

Everything that was open at the end of this playtest — sustained load, whether
the port survives a reboot, whether it works away from one particular WSL
install — was closed later on a real VPS. That write-up is
[VPS-DEPLOYMENT.md](VPS-DEPLOYMENT.md).
