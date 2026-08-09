# Adding `mysql_direct_query` to your game core

**What this is:** a small, copy-and-paste patch for the r40250 C++ game source. It teaches
the game server one new thing — how to let a quest read from and write to your MySQL
database. The web panel's quest (`web_admin.quest`) needs exactly this.

**Do I have to do it?** No. The panel works without it. Read section 6
("If you'd rather not recompile") to see exactly what you gain and what you lose.

---

## 0. Where this stands today — read this before anything else

**Nothing in this repository applies this patch, and nothing in it installs
`web_admin.quest` either.**

- The Linux port (`linux-port/patches/0001-r40250-linux-port.patch`) does not contain this
  change. Search it for `mysql_direct_query` and you will find nothing.
- The Docker stack has no step that copies a quest into the game image or compiles one.
  The profile function that used to do it, `pack_install_quest()`, is never called.

The consequence is concrete and is not hidden anywhere else: on a Docker deployment the
panel's **Teleport** and **Running speed** buttons do not work. Pressing one writes a row
into `web_admin_queue`, the panel waits about seven seconds for an in-game answer that
cannot come, and then shows an error saying the in-game helper did not answer. Nothing is
changed, nothing is corrupted, and nothing else on the panel is affected — items, yang and
levels go straight into the database and work.

So this document is a description of work that has **not** been done here, kept because it
is correct, because someone may want to do it, and because it is the honest explanation of
why two buttons in the panel do nothing. Doing it means applying the edits in section 2 to
the staged source before the image is built — see section 3.

---

## 1. Why this is needed (the short, non-technical version)

The web panel puts a little note into a database table whenever you click a button
("give Player X 5 000 yang"). A tiny script inside the game (`web_admin.quest`) is supposed
to read those notes every few seconds and carry them out immediately, while the player is
online.

The problem: **the r40250 game server cannot read the database from a quest.**
There is simply no function for it. We checked every `questlua_*.cpp` file in
`Source/game-src/source/game/src/` — there is no SQL function offered to quests under any
name. (`mysql_query` is *listed* in `share/quest/quest_functions`, but that file is only a
list of names for the quest compiler; nothing implements it.)

The database machinery itself is already there in C++ (`DBManager::instance().DirectQuery`,
used for example by `pc.change_name`). It has just never been handed to the quest language.
This patch is the missing hand-over. It is about 45 lines.

Once the patched core is running, `web_admin.quest` finds the new function on its own —
you do **not** need to edit the quest.

---

## 2. The exact change — three edits in one file

Everything happens in:

```
Source/game-src/source/game/src/questlua_global.cpp
```

### Edit 1 of 3 — add one `#include`

That file does not yet know about the database manager. Find the include block at the very
top (lines 1–25). The last line of it is:

```cpp
#include "quest_sys_err.h"
```

Add one line directly underneath:

```cpp
#include "db.h"
```

That is all that is needed — `db.h` already pulls in `AsyncSQL.h`, which in turn brings in
`<mysql/mysql.h>`, so `SQLMsg`, `SQLResult`, `MYSQL_ROW`, `MYSQL_FIELD`, `mysql_fetch_row`,
`mysql_num_fields` and `mysql_fetch_fields` all become available.

### Edit 2 of 3 — add the function

Scroll to the end of the file. You will find the last existing function,
`_warp_all_in_area_to_area`, whose closing `}` is on **line 1289**. Line 1290 is empty, and
`void RegisterGlobalFunctionTable(lua_State* L)` starts on **line 1291**.

Paste the block below into that gap — i.e. after line 1289 and before line 1291. It must
stay inside `namespace quest { ... }`, which it will if you paste it there.

```cpp
	// -----------------------------------------------------------------------
	// mysql_direct_query(query_string)
	//
	// Runs one SQL statement on the game core's direct (synchronous) MySQL
	// connection and hands the answer back to Lua.
	//
	//   SELECT ...   -> a table of rows. Each row is itself a table whose keys
	//                   are the column names, e.g. rows[1].player_name.
	//                   Values are always strings (that is what MySQL hands us);
	//                   use tonumber() in the quest where you need a number.
	//                   rows.n holds the row count, so table.getn() works under
	//                   the Lua 5.0.3 this source ships with.
	//   INSERT/UPDATE/DELETE -> a number: how many rows were changed.
	//   error        -> nil.
	//
	// SECURITY: read section 5 of ADD_SQL_BINDING.md before shipping this on a
	// server where you do not control every quest file.
	// -----------------------------------------------------------------------
	int _mysql_direct_query(lua_State* L)
	{
		if (!lua_isstring(L, 1))
		{
			sys_err("mysql_direct_query: argument 1 must be a query string");
			lua_pushnil(L);
			return 1;
		}

		const char * c_pszQuery = lua_tostring(L, 1);

		// DBManager::DirectQuery is printf-style (it does vsnprintf internally,
		// into a 4096 byte buffer). The query text must therefore be passed as
		// an ARGUMENT, never as the format string -- otherwise a '%' anywhere in
		// the query would be interpreted as a format specifier and corrupt it.
		std::unique_ptr<SQLMsg> pmsg(DBManager::instance().DirectQuery("%s", c_pszQuery));

		if (!pmsg.get() || pmsg->uiSQLErrno != 0 || !pmsg->Get())
		{
			// DirectQuery already wrote the MySQL error text to syserr.
			lua_pushnil(L);
			return 1;
		}

		SQLResult * pRes = pmsg->Get();

		// No result set -> this was not a SELECT. Report the affected rows.
		if (!pRes->pSQLResult)
		{
			lua_pushnumber(L, pRes->uiAffectedRows);
			return 1;
		}

		MYSQL_FIELD * pFields	= mysql_fetch_fields(pRes->pSQLResult);
		unsigned int uiFields	= mysql_num_fields(pRes->pSQLResult);

		lua_newtable(L);				// outer table: the list of rows

		MYSQL_ROW row;
		int iRow = 0;

		while ((row = mysql_fetch_row(pRes->pSQLResult)))
		{
			++iRow;

			lua_pushnumber(L, iRow);	// outer key: 1, 2, 3, ...
			lua_newtable(L);			// inner table: this one row

			for (unsigned int i = 0; i < uiFields; ++i)
			{
				lua_pushstring(L, pFields[i].name);
				lua_pushstring(L, row[i] ? row[i] : "");
				lua_settable(L, -3);	// row[column_name] = value
			}

			lua_settable(L, -3);		// rows[iRow] = row
		}

		// Lua 5.0 uses the 'n' field for table.getn(). Set it explicitly so the
		// row count is always exact, including for an empty result.
		lua_pushstring(L, "n");
		lua_pushnumber(L, iRow);
		lua_settable(L, -3);

		return 1;
	}
```

### Edit 3 of 3 — register the name

A few lines further down is the table that tells Lua which names exist. Its **last real
entry** is on **line 1361**:

```cpp
			{	"warp_all_in_area_to_area",		_warp_all_in_area_to_area		},
```

Line 1362 is empty and line 1363 is the terminator `{ NULL, NULL }`. Insert your entry on
the blank line 1362, so the end of the table reads:

```cpp
			{	"warp_all_in_area_to_area",		_warp_all_in_area_to_area		},
			{	"mysql_direct_query",			_mysql_direct_query				},

			{	NULL,	NULL	}
		};
```

Nothing else needs touching. You do **not** need to edit the `Makefile`, because
`questlua_global.cpp` is already in its file list (line 140), and you do **not** need to
create a new `.cpp` file.

*(Line numbers refer to the file exactly as it ships in this pack. If you have already
edited it, search for the text instead of trusting the numbers.)*

---

## 3. Rebuilding the game core

### 3a. On Linux, in the Docker build (the route this repository uses)

`linux-port/fetch-sources.sh` stages the patched source into the Docker build context. The
file in section 2 lands here:

```
linux-port/docker/game/src/server/game/src/questlua_global.cpp
```

Apply the three edits there, then rebuild — the image builds the core from that tree:

```sh
cd linux-port/docker
docker compose build game
docker compose up -d game
```

`prepare-context.sh` **wipes and recreates `game/src/` every time it runs**, so an edit
made only in the context is lost the next time `fetch-sources.sh` or `prepare-context.sh`
is called. To keep it, fold the change into the port patch
(`linux-port/patches/make-patch.sh` regenerates it) rather than editing the staged copy.

The C++ change alone is not enough on this route. `web_admin.quest` also has to reach the
game, and nothing does that for you:

1. Copy `files/web_admin.quest` into the quest tree, which the image keeps under
   `/opt/metin2/share/locale/<lang>/quest/`.
2. Add the line `mysql_direct_query` to that tree's `quest_functions` file, or the quest
   compiler refuses the file with "Calls undeclared function!".
3. Compile the quests (`python2.7 make.py` in that directory on r40250) and restart the
   game.

Do it in the build context and rebuild, not inside a running container: `share/` is baked
into the image, so a change made in the container is gone the next time it is recreated.

**None of section 3a has been carried out or tested.** It is what the layout implies, not a
procedure anyone here has run.

### 3b. On FreeBSD 14 (how the original was built)

The source ships real `Makefile`s — one per library plus one for the core:

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
  a problem with this patch.

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

Afterwards, `web_admin.quest` starts working by itself. If you also changed the quest, use
`index.sh` option 4 ("Quests reloaden"), which runs `python make.py` in
`/usr/home/game/share/quest`.

---

## 4. How to tell it worked

* Log in with any character (that is what arms the quest's 5-second timer).
* Push a "give item" from the panel while that character is online. It should arrive within
  a few seconds, and the panel should say the action was done in game — not that it was
  applied to the account for next login.
* Then try **Teleport**. That is the real test: it is one of the two things that cannot be
  faked through the database, so if it moves the character, the whole chain works.
* If it is still not working, check the first channel's `syserr` for lines containing
  `mysql_direct_query` or `AsyncSQL::DirectQuery` — on FreeBSD that is
  `/usr/home/game/Channel1/syserr`; under Docker, `docker compose logs game` and the log
  directory in the `game-var` volume.
* Before the patch you would see a broadcast in game, exactly once per core, telling you
  the in-game helper is inactive. After the patch that message must not appear any more.
  (You will not see that broadcast on a Docker deployment either, because the quest that
  emits it is not installed.)

---

## 5. ⚠️ SECURITY WARNING — please read

**This patch gives *every* quest on your server unrestricted SQL access to your entire
database**, with the permissions of the game core's MySQL user. Any quest could read
account passwords, hand out items, or delete tables. There is no sandbox.

That is fine if you write every quest yourself. It is **not** fine if you install quests
from forums, marketplaces or other people. A single malicious quest file becomes a full
database compromise.

If you run quests you did not write, use this restricted version instead. It is the same
function with a doorman in front: it only lets through statements that mention the panel's
own queue table.

Insert this right before the `std::unique_ptr<SQLMsg> pmsg(...)` line:

```cpp
		// --- restriction: only the web panel's own queue table is reachable ---
		// Removes the "any quest can read your whole database" problem. Drop
		// these six lines if you deliberately want unrestricted access.
		if (!strstr(c_pszQuery, "web_admin_queue"))
		{
			sys_err("mysql_direct_query: refused (only web_admin_queue is allowed): %s", c_pszQuery);
			lua_pushnil(L);
			return 1;
		}
```

Additional hardening worth doing regardless:

* Give the panel's own MySQL user only the rights it needs; it does not need `DROP`.
* Keep the panel behind a firewall / VPN and never expose port 7788 to the open internet.
* Remember the query buffer inside `DBManager::DirectQuery` is 4096 bytes — longer queries
  are silently cut off. The panel's queries are far shorter, but keep it in mind if you
  write your own.

---

## 6. If you'd rather not recompile

Completely reasonable — recompiling a game core is not a beginner job. **This is also
where every Docker deployment stands by default**, so this section describes the normal
case, not a compromise you have to opt into.

### Still works (through the panel's database fallback)

| Action | Works? | How it behaves |
| --- | --- | --- |
| **Give item** | ✅ yes | The panel waits about 7 seconds for the in-game helper, gets no answer, then writes the item straight into the player's inventory in the database. |
| **Give yang** | ✅ yes | Same: written directly to `player.player.gold` after the short wait. |
| **Set level** | ✅ yes | Same: written directly to `player.player.level` after the short wait. |

Two things to know about the fallback:

1. **There is a delay of roughly 7 seconds** per action, because the panel first gives the
   in-game helper a fair chance to answer.
2. **The player must not be online when it is applied**, or rather: the change lands in the
   database, and the game core only reads that database when the character is loaded. So
   the player has to relog before they see the item / yang / level. If they are online and
   the server later saves their character, the direct write can even be overwritten. In
   short: use the fallback for offline players, and ask online players to relog.

### Does not work at all

| Action | Works? | Why not |
| --- | --- | --- |
| **Teleport / warp** | ❌ no | A teleport needs the running game core to move the character. It cannot be faked in the database: the map a coordinate belongs to is not stored anywhere the panel could look up, so writing raw x/y would risk dropping the character into empty space. The panel refuses honestly instead of corrupting the character. |
| **Running speed** | ❌ no | A speed buff is a temporary in-memory effect on the live character. There is nothing in the database to write. |

So the short version: **item, yang and level are covered without any patch; teleport and
speed are the two features you are buying with the recompile** — plus instant application
instead of a 7-second delay and a relog.

---

## 7. What has been verified, and what has not

Honesty matters more here than confidence.

**Verified by reading the r40250 source:**

* No SQL function is exposed to Lua anywhere in `Source/game-src/source/game/src/` — every
  `questlua_*.cpp` registration table was checked.
* `DBManager::DirectQuery(const char * c_pszFormat, ...)` exists (`source/game/src/db.h`
  line 67, implemented in `db.cpp` line 52) and is printf-style with a 4096-byte buffer.
* The `std::unique_ptr<SQLMsg>` + `pmsg->Get()->pSQLResult` + `mysql_fetch_row` pattern used
  above is copied from real code in this tree: `questlua_pc.cpp` lines 1991–2018 and
  `questlua_building.cpp` line 90.
* `SQLResult` really has the members `pSQLResult`, `uiNumRows`, `uiAffectedRows`,
  `uiInsertID`, and `SQLMsg` really has `uiSQLErrno` (`source/libsql/src/AsyncSQL.h`).
* `CAsyncSQL::DirectQuery` sets `uiSQLErrno` on failure and always calls `Store()`
  (`source/libsql/src/AsyncSQL.cpp` lines 223–252), which is why the error check above is
  written the way it is.
* The registration table, its line numbers, and the `luaL_reg` / `lua_register` style are
  from `questlua_global.cpp` lines 1291–1373.
* The bundled Lua really is 5.0.3 (`extern/FreeBSD/lua/lua/lua.h` line 17), which is why
  `rows.n` is set for `table.getn`.
* `questlua_global.cpp` is already listed in the core `Makefile` (line 140), and that
  Makefile's `TARGET` is `../game`.
* The runtime layout (`Channel1/2/3/99` + `Loginserver` each holding their own `game`,
  `Datenbank` holding `db`, quests in `share/quest`, compiled with `python make.py` →
  `qc_x64`) is from the shipped `index.sh` and the contents of `FreeBSD/FreeBSD/game.tar.gz`.

**Verified about this repository, by grep:**

* The Linux port patch does not contain `mysql_direct_query` — 0 hits in
  `linux-port/patches/0001-r40250-linux-port.patch`.
* No file under `linux-port/docker/` installs or compiles a quest. `pack_install_quest()`
  exists in the server-files profile and is called by nothing here.
* `files/admin_panel.py` refuses `WARP` and `SPEED` in `offline_apply()` and surfaces the
  `ingame_timeout` message, so the failure is an honest error rather than a silent
  no-op — but the operator only learns it after the ~7-second wait, and the button gives
  no warning beforehand beyond the label "(works while the player is in game)", which on
  a Docker deployment is misleading.

**Not verified, by construction:** this patch has **not been compiled or run**, on either
platform. The code follows this codebase's own working examples closely, but treat the
first build as the real test — and that is exactly why section 3b tells you to keep a
`game.backup` before you roll it out.
