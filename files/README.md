# `files/` — the panel, the item index and the server-files profile

This directory is not a program you run. It is the shared material that the
Docker build copies into its images, plus the documents that explain the parts
of the game these files touch.

`linux-port/docker/prepare-context.sh` reads five things out of here every time
a build context is assembled, so **nothing below may be renamed or moved**
without changing that script too.

## What the build actually takes

| File | Where it ends up | Notes |
|---|---|---|
| `admin_panel.py` | the `panel` image | the whole web admin panel, one Flask file |
| `items.json` | the `panel` image | ~9,800 items; what the give-item search looks through |
| `favicon.png` | the `panel` image | browser tab icon |
| `web_admin_schema.sql` | the `panel` image | the two tables the panel adds to the `player` database |
| `web_admin.quest` | the `game` image | the panel's in-game helper; compiled during the build (see below) |
| `packs/tmp4-r40250.pack` | the `game` **and** `client-builder` images | see below |

`prepare-context.sh` refuses to run without `admin_panel.py` and warns (rather
than failing) if `web_admin_schema.sql` is missing.

### The pack profile

`packs/tmp4-r40250.pack` is a shell file describing one specific set of server
files — the `[40250] Reference Serverfile` by TMP4. It is copied verbatim into
two images, which source it and call three of its functions:

- `pack_apply_rates()` — the game image, when the panel's *Server rates* page is
  saved. This is where the knowledge of *which column of which text file holds
  the experience multiplier* lives.
- `pack_prepare_client()` and `pack_apply_ip()` — the client-builder image, when
  the downloadable Windows client is assembled and pointed at your address.

There is exactly one copy of that knowledge and both images carry it, so they
cannot disagree about what a correct client or a correct rate change looks like.

**Do not edit `packs/tmp4-r40250.pack`.** The same file is in use verbatim on a
live server outside this repository; a change here is a change there. It also
contains a good deal of machinery — database deployment, source patching,
starting and stopping the server — that only the older FreeBSD installer ever
called. That installer is no longer part of this repository. The unused
functions are harmless (sourcing the file has no side effects, by design) and
are left in place rather than pruned, because pruning them would fork the file.

[PACKS.md](PACKS.md) documents the format, including which parts of it the
Docker path actually uses.

## The two quest files

`web_admin.quest` and `speed_boost.quest` are Lua quest scripts for the game
core.

- `web_admin.quest` — polls `web_admin_queue` and carries out the panel's
  commands on a character who is in game. **This one is installed by the
  build.** `prepare-context.sh` stages it into the game build context and stage
  2b of `linux-port/docker/game/Dockerfile` compiles it with a `qc` built from
  the same source tree as the cores. The `mysql_direct_query` binding it calls
  is part of the port patch — see
  [ADD_SQL_BINDING.md](ADD_SQL_BINDING.md), which is also where to look if the
  panel's **Teleport** or **Running speed** buttons are not working.
- `speed_boost.quest` — unrelated to the panel, and **not** installed by
  anything here: gives every character +20% running speed at login, as a
  server-wide setting. It is source, for someone willing to install it by hand.

To build a game image without the helper, delete `web_admin.quest` from the
staged context (`linux-port/docker/game/quest/`) and rebuild. The build stage
becomes a no-op and the panel falls back to writing items, yang and levels
straight into the database, as it did before this existed.

## The documents

| | |
|---|---|
| [PACKS.md](PACKS.md) | the pack-profile format, and which of it still runs |
| [ADD_SQL_BINDING.md](ADD_SQL_BINDING.md) | `mysql_direct_query` — the one feature in the port patch: what it does, what a quest may ask it for, and how to check it is alive |
| [ADDING_SHOP_ITEMS.md](ADDING_SHOP_ITEMS.md) | how NPC shop prices actually work, with a worked example |

## Running the panel by hand

The panel needs no game server to start, which makes it the cheap way to try a
change. Every setting has an `M2PANEL_`-prefixed environment variable, so you
never have to write a config file:

```sh
M2PANEL_CONF=/tmp/m2panel.conf \
M2PANEL_DB_HOST=127.0.0.1 M2PANEL_DB_USER=... M2PANEL_DB_PASS=... \
python3 admin_panel.py
```

Pages that need a database it cannot reach degrade honestly rather than
crashing.

### The variables

| Variable | Default | What it is |
|---|---|---|
| `M2PANEL_DIR` | `/usr/local/m2panel` | The panel's own folder. The four paths below follow it unless set individually. |
| `M2PANEL_CONF` | `/usr/local/etc/m2panel.conf` | The settings file. |
| `M2PANEL_CLIENT_ZIP` | `$M2PANEL_DIR/client.zip` | The game download players get. |
| `M2PANEL_DL_DB` | `$M2PANEL_DIR/downloads.db` | Download counter (SQLite). Needs a writable folder. |
| `M2PANEL_RATES_SCRIPT` | `$M2PANEL_DIR/apply_rates.sh` | The script the rates page runs. |
| `M2PANEL_RATES_STATUS` | `$M2PANEL_DIR/rates.status` | The note that script leaves behind. |
| `M2PANEL_ITEMS` | `items.json` next to `admin_panel.py` | The item index the search uses. |
| `M2PANEL_FAVICON` | `favicon.png` next to `admin_panel.py` | The browser tab icon. |

Every key of `m2panel.conf` can be given the same way — `M2PANEL_` plus the key
in capitals: `M2PANEL_DB_PASS`, `M2PANEL_FLASK_SECRET`, `M2PANEL_SALT`,
`M2PANEL_PASS_HASH`, `M2PANEL_DB_HOST`, `M2PANEL_DB_USER`, `M2PANEL_BIND`,
`M2PANEL_PORT`, `M2PANEL_BRAND`, `M2PANEL_CLIENT_URL`, `M2PANEL_CLIENT_NAME`,
`M2PANEL_INVENTORY_SLOTS`, `M2PANEL_MAX_ITEM_COUNT`, `M2PANEL_STATUS_PORTS` (a
comma-separated list, e.g. `11000,13000`).

A variable always beats the file. If the variables supply everything the panel
needs — `flask_secret`, `db_user`, `db_pass`, `salt`, `pass_hash` — there need
not be a settings file at all. The Docker stack takes the other route: its
entrypoint generates `m2panel.conf` on first start (so the passphrase hash and
session secret stay stable across restarts) and leaves it alone afterwards.

### Two settings that depend on the server files

Not about where things are, about what the game can hold — and getting them
wrong is quiet rather than loud.

* **`inventory_slots`** — how many inventory slots a character has. Default 45,
  which is one page. The panel only looks for a free slot inside that range, so
  on server files with more pages it says "inventory is full" while the
  character has plenty of room. The `[40250]` reference files have **180**
  (4 pages).
* **`max_item_count`** — the biggest stack the game can store, i.e. how wide
  `player.item.count` is. Default 65,535 (`SMALLINT UNSIGNED`). The `[40250]`
  reference files declare that column `TINYINT UNSIGNED`, so the real limit
  there is **255** — and MySQL outside strict mode does not complain about a
  bigger number, it silently keeps 255 of the 1000 potions you asked for. Set it
  to 255 on those files and the panel refuses the amount up front instead.

Check your own files with:

```sh
mysql -u metin2 -p -e 'DESCRIBE player.item' | grep -w count
```

### The "is the server up?" badge

The front page reads the machine's socket table to see whether the login server
and the first channel are listening, and how many players are connected. It
tries `sockstat` and then `ss`, so nothing has to be configured.

| Variable | Default | What it is |
|---|---|---|
| `M2PANEL_STATUS_CMD` | `auto` | Force one of them: `sockstat` or `ss`. |
| `M2PANEL_STATUS_HOST` | *(empty)* | Ask by connecting instead of reading sockets. |

`M2PANEL_STATUS_HOST` is for the case where the panel genuinely cannot see the
game's sockets, which is what happens when they are separate containers: set it
to the game's host name and the badge is decided by opening a TCP connection to
every status port. That answers "is it up?" honestly, but nobody outside the
game's own network namespace can count connections, so the player number stays
at 0 in that mode.

Note that the Docker stack does **not** currently set `M2PANEL_STATUS_HOST`. Its
entrypoint writes a `game_host` key into the generated `m2panel.conf`, and the
panel only ever reads this setting from the environment variable — so the badge
in a Docker deployment is decided by a socket listing that cannot see the game
container. Treat "offline" there as "unknown". Fixing it is a one-line change in
the container plumbing, not in the panel.

### The two tables

If you are bringing up a database yourself,
[`web_admin_schema.sql`](web_admin_schema.sql) creates both in one go:

```sh
mysql -u metin2 -p player < web_admin_schema.sql
```

Running it again changes nothing. The Docker stack applies it for you.
