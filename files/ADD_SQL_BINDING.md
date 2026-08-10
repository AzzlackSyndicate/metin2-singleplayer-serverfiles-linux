# `mysql_direct_query` — how the panel reaches into the game

**What this is:** the one feature this project adds to the r40250 C++ game source. It
teaches the game server how to let a quest read from and write to one table in your MySQL
database — the table the web panel puts its commands in. That is what makes the panel's
**Teleport** and **Running speed** buttons work, and what makes items, yang and levels
arrive immediately instead of at the player's next login.

**Do I have to do anything?** On the Docker stack, no. It is built in. This document is
here so you know what is in your server, why, and what it can and cannot do — and so that
anyone building on FreeBSD, or from a different set of server files, can do the same.

---

## 0. Where this stands today

**It is in, and it has been run.**

- `linux-port/patches/0001-r40250-linux-port.patch` contains the C++ change. It is the
  only thing in that patch that is not part of the Linux port, and it says so: the
  patch's header has a section called *WHAT IS NOT PART OF THE PORT*, and every line the
  change adds is marked `M2_FEATURE quest-sql-binding`. `grep -n M2_FEATURE` on the patch
  is the whole audit.
- The Docker game image compiles `files/web_admin.quest` and installs it, in stage 2b of
  `linux-port/docker/game/Dockerfile`. It builds the quest compiler `qc` from the same
  source tree the cores are built from, because the `qc` that ships with the server files
  is a FreeBSD binary and cannot run on Linux.
- The cores start the helper's poll timer themselves, at boot.

What has actually been observed, on a throwaway copy of the stack on a Windows Docker
Desktop host:

* the patched source compiles and `game` and `db` still link, 32-bit, `time_t` still 4
  bytes;
* all five cores start and the container reports healthy;
* `syslog` shows `QUEST: Register 238 web_admin`, the three compiled objects loading, and
  `QUEST: web_admin helper installed -- starting its 5s poll timer`;
* a row put into `player.web_admin_queue` by hand for a character who was **not** logged
  in went from `pending` to `player_offline` about 32 seconds later, with nobody playing.

What has **not** been observed, for lack of a game client and a person to sit at it: an
actual character in the world receiving an item, being teleported, or being sped up. The
chain is proven up to the point where the quest picks the row up and decides what to do
with it; the last step — `pc.warp`, `pc.give_item2`, `affect.add_collect` — is ordinary
quest code that this project did not write and did not change.

---

## 1. Why this is needed

The web panel puts a note into a database table whenever you click a button ("give Player
X 5 000 yang"). A small script inside the game, `files/web_admin.quest`, reads those notes
every five seconds and carries them out on the live character.

The problem it had to solve: **the r40250 game server cannot read the database from a
quest.** There is no function for it. Every `questlua_*.cpp` in
`Source/game-src/source/game/src/` was checked — no SQL function is offered to quests
under any name. (`mysql_query` is *listed* in `share/quest/quest_functions`, but that file
is only a list of names for the quest compiler; nothing implements it.)

The database machinery itself is already there in C++ — `DBManager::instance().DirectQuery`,
used for example by `pc.change_name`. It had simply never been handed to the quest
language. This is that hand-over, plus a doorman in front of it, plus the few lines that
start the helper at boot.

---

## 2. What the change is

Two files, both under `Source/game-src/source/game/src/`.

### `questlua_global.cpp` — the binding

1. `#include "db.h"` after the existing include block. That pulls in `AsyncSQL.h` and
   with it `<mysql/mysql.h>`, so `SQLMsg`, `SQLResult`, `MYSQL_ROW`, `MYSQL_FIELD`,
   `mysql_fetch_row`, `mysql_num_fields` and `mysql_fetch_fields` all become available.

2. `_mysql_direct_query()`, plus the small helper `wa_query_is_allowed()` described in
   section 5. The function runs one statement on the core's synchronous MySQL connection
   and hands the answer back to Lua:

   | statement | Lua gets |
   | --- | --- |
   | `SELECT` | a table of rows; each row is a table keyed by column name, e.g. `rows[1].player_name`. Values are always strings — use `tonumber()` where you need a number. `rows.n` is set, so `table.getn()` works under the Lua 5.0.3 this source ships with. |
   | `UPDATE` | a number: how many rows changed |
   | refused, or a MySQL error | `nil` |

   The query text is passed to `DirectQuery` as an **argument**, never as the format
   string — `DirectQuery` is printf-style, so a `%` anywhere in a query used as the format
   would corrupt it.

3. One line in the registration table, so Lua knows the name exists:

   ```cpp
   {	"mysql_direct_query",			_mysql_direct_query				},
   ```

### `questmanager.cpp` — starting the helper

A quest can only start a server timer from inside an event, and the only event that fits
is `login`. That would leave a core nobody has logged into yet with no helper running at
all — indistinguishable, from the outside, from a build where the helper is missing or
broken. It would also mean the one check an operator can make without a game client (put a
row in the queue, watch it move) proves nothing.

So the timer is started from C++, once, at the end of `CQuestManager::Initialize()`, and
only when the quest is actually installed:

```cpp
if (GetQuestIndexByName("web_admin") != 0)
{
	extern int _set_server_loop_timer(lua_State * L);
	...
}
```

**It has to be at the end of `Initialize()` and not in `InitializeLua()`.**
`server_loop_timer` reaches `LoadTimerScript` → `NPC::Set`, which walks
`m_mapEventName` — and that map is filled *after* `InitializeLua()` returns. Started from
the wrong place the timer is created with no script attached to it and ticks forever doing
nothing, silently. That was a real hour of this project's life; the comment in the source
says so too.

Nothing else is touched. `questlua_global.cpp` and `questmanager.cpp` are already in the
core `Makefile`, and no new `.cpp` file is needed.

---

## 3. Building it

### 3a. On Linux, in the Docker build — nothing to do

`linux-port/fetch-sources.sh` applies the port patch, which contains this change, and
`linux-port/docker/prepare-context.sh` stages `files/web_admin.quest` into the game build
context. Then:

```sh
cd linux-port/docker
docker compose build game
docker compose up -d game
```

Stage 2b of the game Dockerfile does the quest side:

1. builds `qc` from `game/src/quest/qc.cc` (`-m32`, against the 32-bit liblua the same
   build produced). qc.cc calls `strlen`/`strcmp` without including `<cstring>` — that
   compiled in 2019 and does not under GCC 13, so the headers are force-included from the
   command line rather than the upstream file being edited;
2. adds `mysql_direct_query` to a copy of `quest_functions` — `qc` refuses any name that
   is not listed there, with "Calls undeclared function!";
3. compiles the quest in a scratch directory with an empty `object/`, so the result is
   only web_admin's own output — the 237 stock quests keep the object files they came
   with, byte for byte;
4. checks the three files that must exist (`object/state/web_admin`,
   `object/notarget/login/web_admin.start`, `object/wa_tick/server_timer/web_admin.start`),
   because `qc` exits 0 whether or not it wrote anything;
5. makes the output world-readable and asserts it. **This one matters more than it looks:**
   `qc` creates `object/state` with mode 0700, and that directory lands on top of the stock
   one — an unfixed copy replaces the pack's readable directory with a root-only one and
   the server, which runs unprivileged, then loads **no quests at all** while still
   starting, listening and looking perfectly healthy;
6. appends the file to `locale_list`, so an operator who later recompiles the tree by hand
   keeps the helper instead of quietly dropping it.

To build the image **without** the helper, delete `game/quest/web_admin.quest` from the
staged context and rebuild. Everything above becomes a no-op, the image ships no extra
quest, and the panel behaves as described in section 6.

### 3b. On FreeBSD 14 (how the original was built)

The change is not platform-specific and carries no `#ifdef` — the same tree still builds
on FreeBSD. The source ships real `Makefile`s, one per library plus one for the core:

| what | Makefile | produces |
| --- | --- | --- |
| libthecore | `source/libthecore/src/Makefile` | `source/libthecore/libthecore.a` |
| libpoly | `source/libpoly/src/Makefile` | `source/libpoly/libpoly.a` |
| libsql | `source/libsql/src/Makefile` | `source/libsql/libsql.a` |
| libgame | `source/libgame/src/Makefile` | `source/libgame/libgame.a` |
| **the game core** | `source/game/src/Makefile` | `source/game/game` |

These are GNU-make files, so you need `gmake`, not FreeBSD's built-in `make`:

```sh
pkg install gmake
```

Then, from the folder that contains `Server.sln` (i.e. `Source/game-src/`):

```sh
# 1) the four static libraries (only needed once, or after you change them)
cd source/libthecore/src && gmake
cd ../../libpoly/src     && gmake
cd ../../libsql/src      && gmake
cd ../../libgame/src     && gmake

# 2) the game core itself
cd ../../game/src
gmake clean
gmake
```

Notes that will save you a support ticket:

* The Makefiles use `CC = CC`. On FreeBSD, `CC` (upper case) is the **C++** compiler driver
  — that is intentional, do not "fix" it to `cc`.
* All of them build **32-bit** binaries (`-m32`), matching the pre-built libraries under
  `Source/game-src/extern/FreeBSD/`. Do not remove `-m32` unless you rebuild everything
  else 32→64-bit too.
* `gmake clean` in `game/src` runs a `touch` over the tree and deletes `.obj/` — the first
  build afterwards is a full one and takes a while. That is normal.
* If the build stops with `cannot find -lmysqlclient` or similar, the matching 32-bit
  library under `extern/FreeBSD/mysql/lib` is missing — that is an environment problem, not
  a problem with this change.

**This FreeBSD route has not been run since the change was made.** The Linux one has.

### Where the binary goes (FreeBSD)

`gmake` writes the result to `Source/game-src/source/game/game`
(the Makefile sets `TARGET = ../game`).

Your running server keeps one copy of that binary per core:

```
/usr/home/game/Channel1/game
/usr/home/game/Channel2/game
/usr/home/game/Channel3/game
/usr/home/game/Channel99/game
/usr/home/game/Loginserver/game
```

So the deploy is:

```sh
# 1) stop the server:  cd /usr/home/game && sh index.sh   -> option 2
# 2) keep a way back
for d in Channel1 Channel2 Channel3 Channel99 Loginserver; do
    cp /usr/home/game/$d/game /usr/home/game/$d/game.backup
done
# 3) roll the new binary out
for d in Channel1 Channel2 Channel3 Channel99 Loginserver; do
    cp /path/to/Source/game-src/source/game/game /usr/home/game/$d/game
    chmod 755 /usr/home/game/$d/game
done
# 4) start again:      cd /usr/home/game && sh index.sh   -> option 1
```

**Do not touch `/usr/home/game/Datenbank/db`.** That is a different program (built from
`source/db/src/`) and it has nothing to do with quests.

On FreeBSD you must also install the quest yourself — the Docker stage above has no
counterpart there:

```sh
cp files/web_admin.quest /usr/home/game/share/locale/<lang>/quest/
echo mysql_direct_query >> /usr/home/game/share/locale/<lang>/quest/quest_functions
echo web_admin.quest    >> /usr/home/game/share/locale/<lang>/quest/locale_list
cd /usr/home/game/share/quest && python make.py     # or index.sh option 4
```

---

## 4. How to tell it is working

**Without a game client, and without anybody playing** — this is the check worth knowing,
because it needs nothing but a database prompt:

```sql
INSERT INTO player.web_admin_queue (player_name, cmd, arg1, arg2)
  VALUES ('AnyCharacterName', 'ITEM', '27003', '1');
```

Then watch it:

```sql
SELECT id, status, TIMESTAMPDIFF(SECOND, created, NOW()) AS age FROM player.web_admin_queue;
```

Within about 35 seconds the status must change from `pending` to something else — for a
character who is not logged in, `player_offline`. The 30 seconds are deliberate: the quest
only takes a row early if it can really serve it, so that it never races the panel's own
7-second wait.

**A row still `pending` a minute later means the helper is not running**, whatever else
looks right. In that case:

* `docker compose logs game` and the container's `syslog` should contain
  `QUEST: web_admin helper installed -- starting its 5s poll timer`. If it does not, the
  quest is not in the image — check that `game/quest/web_admin.quest` was staged.
* the core's `syserr` (`/opt/metin2/var/channel1/first/syserr` in the container,
  `/usr/home/game/Channel1/syserr` on FreeBSD) should contain nothing about
  `mysql_direct_query`. A line saying `refused` means the doorman in section 5 turned a
  query away.

**With a client**, the real test is Teleport: it is one of the two things that cannot be
faked through the database, so if it moves the character, the whole chain works.

Before this existed you would see a broadcast in game, once per core, saying the in-game
helper was inactive. That message must not appear any more.

---

## 5. What a quest is allowed to ask for

A generic "run any SQL" function would hand **every quest on your server** unrestricted
access to your entire database, with the permissions of the game core's MySQL user. Any
quest could read account passwords, hand out items, or drop tables. That is fine if you
write every quest yourself. It is not fine the day you install one you found on a forum.

The panel only ever needs to read one table and write a status back, so that is all this
binding will pass through. `wa_query_is_allowed()` refuses anything else:

1. **One statement only.** A `;` with anything after it is refused, so
   "read the queue; drop the accounts" cannot be smuggled in.
2. **No comment introducers** (`--`, `#`, `/*`). They are how a second intent gets hidden
   from a check like this one.
3. **`SELECT` or `UPDATE` only.** `DELETE`, `INSERT`, `DROP`, `GRANT` and the rest never
   get through, not even against the queue table.
4. **Every keyword that can name a table — `FROM`, `JOIN`, `UPDATE`, `INTO`, `TABLE` —
   must be followed by `web_admin_queue`** (optionally database-qualified). A subquery
   needs its own `FROM` to read a table, so subqueries are covered by the same rule, and
   `SELECT ... INTO OUTFILE` is refused by it too.
5. **`LOAD_FILE` is refused by name**, because it reads a file rather than a table and so
   rule 4 does not see it.
6. Queries longer than 3 900 characters are refused rather than silently truncated:
   `DirectQuery` formats into a fixed 4 096-byte buffer, and a truncated statement is a
   different statement.

Every refusal is written to `syserr` with the query that caused it.

**Be clear about what this is and is not.** Rules 1–4 are a real restriction: they are
about the shape of the statement, not a list of bad words, and they are what stops a
hostile quest reading `account.account`. Rule 5 is a denylist entry, and a denylist of one
is not a proof that nothing else can reach outside the database. If you want a guarantee
rather than a good fence, the design that gives it is a purpose-built binding — three
functions that take an id and a status and contain the SQL in C++, with no query text
crossing the Lua boundary at all. That was weighed and not done: it would mean rewriting
the quest and this document around an interface nobody else uses, for a server whose whole
quest tree is baked into the image by the same build that compiles the core.

To lift the restriction on a server where you write every quest yourself, make
`wa_query_is_allowed()` `return true;` — and nothing else. Then read the first paragraph
of this section again.

Worth doing regardless:

* Give the panel's own MySQL user only the rights it needs; it does not need `DROP`.
* Keep the panel behind a firewall or VPN and never expose port 7788 to the open internet.

---

## 6. Building without it

Completely reasonable, and nothing breaks. Delete `web_admin.quest` from the staged build
context (section 3a) and the image ships no helper.

### Still works (through the panel's database fallback)

| Action | Works? | How it behaves |
| --- | --- | --- |
| **Give item** | yes | The panel waits about 7 seconds for the in-game helper, gets no answer, then writes the item straight into the player's inventory in the database. |
| **Give yang** | yes | Same: written directly to `player.player.gold` after the short wait. |
| **Set level** | yes | Same: written directly to `player.player.level` after the short wait. |

Two things to know about the fallback:

1. **There is a delay of roughly 7 seconds** per action, because the panel first gives the
   in-game helper a fair chance to answer.
2. **The change lands in the database**, and the game core only reads that when the
   character is loaded. So the player has to relog before they see the item / yang / level.
   If they are online and the server later saves their character, the direct write can even
   be overwritten. In short: use the fallback for offline players, and ask online players
   to relog.

### Does not work at all

| Action | Works? | Why not |
| --- | --- | --- |
| **Teleport / warp** | no | A teleport needs the running game core to move the character. It cannot be faked in the database: the map a coordinate belongs to is not stored anywhere the panel could look up, so writing raw x/y would risk dropping the character into empty space. The panel refuses honestly instead of corrupting the character. |
| **Running speed** | no | A speed buff is a temporary in-memory effect on the live character. There is nothing in the database to write. |

Note that the fallback is also what happens **with** the helper installed whenever the
target character is offline: the quest deliberately leaves a fresh row alone for 30
seconds so it cannot race the panel, and the panel gives up after 7. That is by design —
an offline character cannot be given an item in game anyway.

---

## 7. What has been verified, and what has not

**Verified by running it** (Docker Desktop on Windows 11, `ubuntu:24.04` base, one
channel):

* the patched source compiles; `game` and `db` link; both are 32-bit i386 ELF and
  `_TIME_BITS=64` did not leak in;
* all five cores (`db`, `auth`, three channel cores) start, listen, and the container's
  health check reports `all cores listening`;
* `qc` builds from `qc.cc` on Linux and compiles `web_admin.quest` into exactly three
  objects;
* the cores load 238 quest states (237 stock + `web_admin`) and log
  `QUEST: web_admin helper installed -- starting its 5s poll timer`;
* `syserr` contains nothing but the normal `pid_init` lines;
* a hand-inserted queue row for an offline character went `pending` → `player_offline` in
  about 32 seconds, with no client connected to the server;
* with the helper running, a panel "give item" to an offline character still took the
  database fallback — row `cancelled`, item written — so nothing that worked before was
  lost.

**Verified by reading the r40250 source:**

* No SQL function is exposed to Lua anywhere in `Source/game-src/source/game/src/` — every
  `questlua_*.cpp` registration table was checked.
* `DBManager::DirectQuery(const char * c_pszFormat, ...)` exists (`source/game/src/db.h`,
  implemented in `db.cpp`) and is printf-style with a 4096-byte buffer.
* The `std::unique_ptr<SQLMsg>` + `pmsg->Get()->pSQLResult` + `mysql_fetch_row` pattern
  used here is copied from real code in this tree: `questlua_pc.cpp` and
  `questlua_building.cpp`.
* `SQLResult` really has `pSQLResult`, `uiNumRows`, `uiAffectedRows`, `uiInsertID`, and
  `SQLMsg` really has `uiSQLErrno` (`source/libsql/src/AsyncSQL.h`).
* `CAsyncSQL::DirectQuery` sets `uiSQLErrno` on failure and always calls `Store()`, which
  is why the error check is written the way it is.
* The bundled Lua really is 5.0.3, which is why `rows.n` is set for `table.getn`.

**Verified by unit test:** `wa_query_is_allowed()` was compiled standalone and run against
25 cases — the four statements the quest actually issues (allowed), and twenty-one it must
refuse: reading `account.account`, `SELECT * FROM player.player`, `DROP`, `DELETE` and
`INSERT` even against the queue table, a second statement after a semicolon, all three
comment forms, a subquery, a `JOIN` out to another table, `LOAD_FILE`, `INTO OUTFILE`,
`UPDATE` elsewhere, and table names that merely start or end with the right letters.

**Not verified:**

* Anything that needs a person at a game client: a character actually receiving an item,
  being teleported, or being sped up in the world.
* The FreeBSD build, since the change. It carries no `#ifdef` and uses nothing
  Linux-specific, but that is an argument, not a test.
* More than one channel, and more than one locale in the data tree. The stage handles
  both by construction; only `english` and one channel were run.
