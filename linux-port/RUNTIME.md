# 40250 runtime environment

**Record of preparing the r40250 runtime tree and database by hand**, before
there were any Linux binaries to run against it. Written at that point, so it is
in the present tense of a machine that no longer exists — a WSL2 install with
the tree unpacked at `/opt/m2port/server40250/`.

It is kept because everything the Docker images do to the runtime tree was
worked out here: the port map, the MariaDB settings, the layout of the cores,
and four defects in the source that this preparation is what uncovered. Where
something below was later superseded, it says so inline.

**None of it is a set of instructions.** To run the server, see
[docker/README.md](docker/README.md).

## Why this tree and not the other one

The port originally targeted the **Fliege V3 - Fratello** source. That was the
wrong target: it is a genuinely different Metin2 fork, and this project runs
**[40250]** everywhere — production server, installer pack, and client.

The mismatch was proven at runtime, not by diffing. Booting the Fliege-built
`db` against 40250 data connected to MySQL fine and then aborted:

```
db: CsvReader.cpp:273: Assertion `index < row->size()' failed.
#7  Set_Proto_Mob_Table (...) at ProtoReader.cpp:975
```

It read mob column **71** from a file with **71 columns** (0-70) — short by one.
Fliege's `service.h` defines `ENABLE_WOLFMAN`, which adds `MOB_RESIST_CLAW` and
`MOB_RESIST_BLEEDING`, so its reader needs 74 columns while 40250's data has 71.
**The 40250 data predates the Wolfman class.** Items likewise: 33 columns vs 35.

Structurally the two share almost nothing — `common/tables.h` differs in 1015 of
1488 lines, `game/src/packet.h` in 1000 of 2392, and the two `service.h` files
share **not one define**. Fliege self-identifies as `CLIENT_VERSION "20181010"`.

## Ports

**What a client needs:**

| Purpose | Port |
|---|---|
| auth (login) | **11000** |
| game channel 1 | **13000** |

**Internal only:** db `15000` (all cores dial `127.0.0.1:15000`), P2P auth
`12000`, P2P first `14000`, MariaDB `3306`.

Full shipped map, for scaling up later — note channels 2-4 are **13010 / 13020 /
13030**, not 13001+:

| Core | CH | PORT | P2P |
|---|---|---|---|
| auth | 1 | 11000 | 12000 |
| channel1 first / game1 / game2 | 1 | 13000 / 13001 / 13002 | 14000 / 14001 / 14002 |
| channel2 first / game1 / game2 | 2 | 13010 / 13011 / 13012 | 14010 / 14011 / 14012 |
| channel3 … | 3 | 13020 / 13021 / 13022 | 14020 / 14021 / 14022 |
| channel4 … | 4 | 13030 / 13031 / 13032 | 14030 / 14031 / 14032 |
| game99 | 99 | 13099 | 14099 |

## Layout

- Working tree `/opt/m2port/server40250/` (220 MB), `cp -a` from the pristine
  extraction, md5-verified identical, relative symlinks intact.
- **Minimal layout for bring-up: 3 processes** — `db` + `auth` +
  `channel1/first`. Everything else moved to `_disabled/`. That was a bring-up
  shortcut only; the Docker stack runs the full five (`db`, `auth`, and the
  three cores of channel 1), which is what the server files ship with.
- **The port only ever delivers two files.** Every core symlinks its binary out
  of `share/bin/{game,db}`; `auth/auth` → `../share/bin/game` (auth *is* the game
  binary under another name), `channel1/first/game` → `../../share/bin/game`.
  Still true, and it is why the Docker image's builder stage produces exactly
  two artifacts.
- `install-linux.sh`, `start-minimal.sh`, `stop-minimal.sh` were added to that
  tree (boot order db → 5 s → auth → 3 s → channel, then prints listening
  ports); the shipped `install.sh`/`close.sh` were left untouched as reference.
  Those scripts lived on that machine and are **not in this repository** — the
  boot ordering they worked out is now in `docker/game/bin/m2-supervise`, which
  waits for each core's port to open instead of sleeping.

## Database

Ubuntu's default `sql_mode` (`STRICT_TRANS_TABLES,…`) rejects what r40250 writes.
`/etc/mysql/mariadb.conf.d/99-m2port.cnf` was given
`sql_mode = NO_ENGINE_SUBSTITUTION` — matching the `my.cnf` shipped inside the
dump zip — plus `character-set-server = latin1` and `bind-address = 127.0.0.1`.
The same three settings are now `docker/mariadb/conf.d/99-metin2.cnf`, mounted
into the MariaDB container rather than baked in, so they stay visible.

Imported from `metin2_mysql_dump.zip`; the raw datadir tarball was not needed.
**No CONFIG credentials were edited** — the tree already asks for
`127.0.0.1 metin2 password <db>`, so the user was created to match.

Verified over TCP as the CONFIG's own credentials:

| Table | Rows | | Table | Rows |
|---|---|---|---|---|
| `player.item_proto` | 5743 | | `player.player` | 2 |
| `player.mob_proto` | 1334 | | `player.item` | 71 |
| `player.shop_item` | 290 | | `account.account` | 2 |
| `player.skill_proto` | 77 | | `common.gmlist` | 1 |
| `player.refine_proto` | 407 | | `common.locale` | 13 |

**Test logins:** `admin` / `123456789` and `test` / `123456789` (plaintext
confirmed against `PASSWORD()`); `admin` is IMPLEMENTOR in `common.gmlist`.
These ship in the distributed dump, so the hashes are public. Change both before
a server is reachable from the internet.

The 128 import warnings are benign — `Data truncated` on `mob_proto.size` and
`skill_proto.setAffectFlag*`, which are `enum` columns the dump itself stores as
`''`. The import is faithful, not lossy; production's data is in the same state.

### The MAP_ALLOW change — and why it was the wrong answer

At the time, `channel1/first/CONFIG`'s `MAP_ALLOW` was merged to the union of
first + game1 + game2 (41 maps; original kept as `CONFIG.orig`). Both shipped
characters live on **map 41**, which belongs to `game2` — with only one core
running, on `first`'s stock 15-map list, nobody could log in. It was verified as
far as it goes: 0 duplicates (`map_allow_add()` calls `exit(1)` on a duplicate),
all 41 maps have directories, and the 141-byte line fits the parser's 256-byte
`fgets` buffer. The open question recorded here was whether 41 maps fit in one
32-bit core's address space (818 MB resident against a ~3 GB ceiling).

**Superseded — do not do this.** Address space was not the limit that bit.
Serving all 41 maps from one core pushes its main loop past the 50 ms window the
client allows during the initial handshake, and then *no* client can connect at
all, while the auth server keeps answering perfectly — which looks exactly like
a protocol bug and is not one. The right answer is the one the server files
already shipped with: three cores per channel, maps split between them. That is
what the Docker stack runs, and the map lists it generates are the stock ones.

## Port defects this preparation uncovered

These fed straight into the source port. All four are settled; the patch is
where each one landed.

1. **`game/src/config.cpp` — `GetIPInfo()` crashes or lies on Linux.**
   It dereferenced `ifa_addr` before any NULL test and never checked
   `sa_family`. On Linux `getifaddrs()` returns an `AF_PACKET` entry per
   interface plus NULL-`ifa_addr` entries, and AF_INET6 read through a
   `sockaddr_in*` yields `sin6_flowinfo` where the address should be. **Mandatory
   path**: `config.cpp` calls it unconditionally and `exit(1)`s with
   "Can not get public ip address", *before* CONFIG parsing — no setting can
   route around it. Measured on this host: 2 AF_PACKET + 2 AF_INET6 entries
   alongside 3 AF_INET; it survived only by luck.
   → **Fixed.** The Linux branch skips anything that is not `AF_INET`, and an
   explicitly configured public IP now wins over detection instead of being
   clobbered by it. The FreeBSD loop is preserved verbatim in the `#else` half.
2. **`db/src/Makefile` is missing `-m32`** (`game`'s has it). On an x86_64 host
   that silently builds a **64-bit `db` against a 32-bit `game`**.
   → **Fixed**, with a comment in the Makefile saying `-m32` is load bearing.
3. **`db` builds without `-DNDEBUG`, `game` with it** — so `db`'s `assert`s stay
   live. That is what aborted the mis-targeted `db` on mismatched data: expect
   `db` to abort rather than warn on any data problem.
   → **Kept deliberately.** It is upstream's choice and a good one: an assert
   abort in `db` is almost always a data problem, not a code problem. The
   Docker troubleshooting section says so in as many words.
4. Both Makefiles need **`-std=c++23`** (GCC 13+). → **Done.**

**Not a blocker, confirmed absent:** `Metin2Server_Check()` is a no-op —
`_SERVER_CHECK_` and `_USE_SERVER_KEY_` are undefined. No licence or IP-whitelist
gate. `ENABLE_PORT_SECURITY` gates only P2P and the admin text page, not client
traffic, and with one core there is no P2P at all.

## The WSL2 client-reachability problem — and the fix it forced

`game/src/main.cpp` bound to `g_szPublicIP`, **not `0.0.0.0`**, and
`BIND_IP: 0.0.0.0` was impossible because the code sentinel-tests
`g_szPublicIP[0] == '0'`. So in WSL2's NAT mode the cores listened only on eth0
(`172.27.84.75` at the time), and Windows `127.0.0.1` forwarding missed them.
40250 classifies `192.168.*` and `10.*` as internal and everything else as
public, so WSL's `172.27.x` became the public IP — usable, but **it changes on
every WSL restart**.

The workarounds available then were: point the client at the current WSL IP, or
add a `netsh interface portproxy` rule. Neither would do for a container, where
binding every interface is not optional.

→ **Fixed in the port.** Listening and advertising are now separate. A new
`g_szBindIP` holds the listen address only; `BIND_IP: 0.0.0.0` is accepted as a
wildcard (and logs that it left `PUBLIC_IP` alone); a new `LISTEN_IP` key sets
the same thing without `BIND_IP`'s legacy double meaning; and `M2_BIND_IP` in
the environment overrides both, which is how the Docker entrypoint sets it. The
three addresses a container needs to keep apart — listen, own identity,
advertised — are documented under
[docker/README.md → The addressing model](docker/README.md#the-addressing-model).
