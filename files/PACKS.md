# The pack profile

Everything specific to one set of server files lives in a **pack profile** — a
POSIX `sh` file in `packs/`. There is one:

```
packs/tmp4-r40250.pack     the [40250] Reference Serverfile by TMP4
```

## Read this first: what still runs, and what does not

The pack format was designed for a FreeBSD installer engine that used to live in
this directory. **That engine is no longer part of this repository.** The
profile is, because two Docker images copy it in verbatim and call into it, and
because the same file is in use unchanged on a server outside this repository.

So the file has two halves. Only one of them executes here:

| Hook | Used by the Docker stack? |
|---|---|
| `pack_apply_rates` | **yes** — the `game` image, when the panel's *Server rates* page is saved |
| `pack_prepare_client` | **yes** — the `client-builder` image |
| `pack_apply_ip` (and `p_write_serverinfo`) | **yes** — called from `pack_prepare_client` |
| everything else | no |

"Everything else" is `pack_locate`, `pack_preflight`, `pack_deploy_db`,
`pack_deploy_game`, `pack_apply_db_password`, `pack_install_quest`,
`pack_patch_source`, `pack_secure_admin_accounts`, `pack_start_server`,
`pack_stop_server`, `pack_ports`, `pack_ready_ports`, `pack_db_names` and
`pack_start_hint`. Sourcing the file does not run any of them, so their presence
costs nothing at runtime.

Two of those absences matter to anyone using the panel:

- **`pack_install_quest` is never called**, so `web_admin.quest` never reaches
  the game. That is why the panel's teleport and running-speed buttons do
  nothing on a Docker deployment.
- **`pack_secure_admin_accounts` is never called**, so the admin accounts that
  ship in the server files keep their published passwords until you change them
  yourself.

The variables `PACK_URL`, `PACK_NAME` and `PACK_SERVER_NAME` are read by the
client builder; the rest of the metadata is only descriptive here.

**Do not edit `packs/tmp4-r40250.pack`.** See the warning in
[README.md](README.md#the-pack-profile).

---

## The rules

A `.pack` file is POSIX `sh` and is *sourced*, never executed. So:

- **It must have no side effects.** Only variable assignments and function
  definitions at the top level. No downloads, no `mkdir`, no output. Two
  container images source this file every time they start; anything with a side
  effect fires on both.
- **Do not use bashisms.** No `[[`, no arrays, no `local` unless you have
  checked the target `/bin/sh`. The containers run Debian, where that is dash.
- Prefix anything of your own with `p_` so it cannot collide with a caller.

## Metadata

```sh
PACK_ID="tmp4-r40250"                              # slug; must match the filename
PACK_NAME="[40250] Reference Serverfile (TMP4)"    # the technical name of the files
PACK_SERVER_NAME="Singleplayer Official Metin2"    # what players see in the server list
PACK_DESC="... — 4 channels x 3 cores"
PACK_URL="https://mega.nz/file/..."                # where the package comes from
PACK_OLD_DB_PASS="..."                             # factory DB password; "" if none
```

`PACK_URL` is what the client builder falls back to when
`M2_CLIENT_ARCHIVE_URL` is not set — it is the link the client is extracted
from. It may be a MEGA link or an ordinary direct one.

The remaining optional variables (`PACK_DB_PKGS`, `PACK_PKGS`,
`PACK_GAME_DB_USER`, `PACK_CLIENT_NAME`) were the FreeBSD engine's and are
inert here.

---

## The three hooks that run

### `pack_apply_rates`

Called in the `game` container when someone saves the panel's *Server rates*
page. It scales the server-wide experience, item-drop and yang numbers to
`$RATE_EXP` / `$RATE_DROP` / `$RATE_YANG` — percentages, where `100` means
exactly like the original game — and returns non-zero if these server files
cannot do it. The panel reads that answer back and says so plainly rather than
pretending it worked.

There is no generic way to do this. Every package keeps those numbers somewhere
else, and the direction matters:

- In r40250 the database core **rebuilds `player.mob_proto` from
  `mob_proto.txt` at every boot**, so experience and yang are a change to the
  *text* file and an SQL-only edit silently reverts on the next restart.
- Other packages invert this: a `NO_TXT = 1` in the database configuration tells
  the core to read monsters out of the table and ignore the text file, and then
  the same edit has to be made in SQL.
- Item drop chances are never in the database — always in
  `share/*drop_item*.txt`.

Two things to get right:

- **Keep a pristine baseline and always calculate from it**, never from what is
  on disk right now. Otherwise 200% then 300% silently becomes 600%. The r40250
  profile keeps that baseline on disk: the first rate change copies the
  untouched `mob_proto.txt` to `mob_proto.txt.m2orig` and every later change
  recalculates from the copy. Drop tables are backed up the same way.
- **Treat rates you cannot make sense of as 100.** Empty, text, a minus sign and
  zero all mean "leave this alone"; anything above 10000 is capped. A drop
  chance is capped at 100 as well — nothing can be more likely than certain.

All of it is read once, while the server starts, which is why the caller
restarts the game afterwards and why the panel warns that saving drops anyone
online for under a minute.

### `pack_prepare_client` and `pack_apply_ip`

Called in the `client-builder` container. `pack_prepare_client` unpacks the
Windows client, removes what players must not receive, adds the start-here
readme, calls `pack_apply_ip` on the copy and repacks it.
`pack_apply_ip` — through `p_write_serverinfo` — knows which file this
particular client reads its server list from and which names that file has to
define.

`$CLIENT_WORK` is the client directory being prepared. Note this is normally
`$CLIENT_DIR` itself, edited in place: do not stage a copy under `/tmp`, which
is often a tmpfs, because game clients run to gigabytes.

`p_write_serverinfo` reads `$PUB_IP`, `$P_AUTH_PORT` and `$PACK_SERVER_NAME`.

### What a hook can rely on

The callers set these before calling in:

| | |
|---|---|
| `$GAME_DIR` | the game runtime directory |
| `$RATE_EXP` / `$RATE_DROP` / `$RATE_YANG` | the wanted percentages (during `pack_apply_rates`) |
| `$PUB_IP` | the address players will connect to |
| `$CLIENT_WORK` | the client directory being prepared |
| `$LOG` | where output should go |

And these helpers, which both callers define as small stand-ins for the original
installer's versions:

```sh
step "Doing a thing"        # section header
ok   "It worked"            # tick
warn "Not fatal, but note"  # bang
fail "It broke"             # cross
run  some command           # runs it, output to $LOG
ask  "Question" "default"   # answer lands in $ANSWER -- nothing here can ask, so
                            #   the stand-ins always take the default
```

The client builder additionally defines `pack_call name ARGS`, which calls
another hook while respecting an override.

A hook that expects to be able to ask a question will silently get the default.
That is deliberate: neither container has anyone to ask.

### Return values

`0` means success. `pack_apply_rates` returning non-zero means "these server
files cannot do this", which is reported to the operator and is not an error.
For anything the caller can live without, `warn` and return `0`.

---

## If you are adding support for different server files

Be aware that a new profile is **not enough on its own**, which was true when
the FreeBSD engine existed and is not true now. The Linux port is a patch
generated against one exact source tree and checked against a recorded SHA-256
before it is applied; different server files mean a different port, not just a
different profile. Start at
[linux-port/README.md](../linux-port/README.md), not here.

If you are writing a profile anyway — for the two hooks the containers call, or
for the FreeBSD engine elsewhere — the checklist is:

1. Write `packs/<slug>.pack` with the metadata variables.
2. Confirm `sh -n packs/<slug>.pack` is clean.
3. Confirm it has no side effects: `sh -c '. packs/<slug>.pack'` must produce no
   output and create nothing.
4. Try a real rate change and a real client build against it.

### One warning worth repeating

If your archives are `.tar.gz`, do **not** "improve" `tar -xf` into `tar -xzf`.
Server packs are notorious for shipping bzip2 data under a `.gz` name; `tar -xf`
lets tar detect the real format, `-z` forces gzip and breaks every install.
`packs/tmp4-r40250.pack` carries the same warning at the top of the file: its
archives really are gzip today, but a single re-upload in another format would
break everything that hard-coded `-z`.
