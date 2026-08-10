# Worked example — the Linux port deployed to a Debian 13 VPS

This is the write-up of a **real deployment** that was carried out and verified,
kept because the problems it ran into are the ones anybody doing the same thing
will run into. Every address, hostname, password and account name below has been
**replaced with a placeholder** — `203.0.113.10` (a documentation address),
`panel.example.com`, and `<...>` for anything secret. Nothing here is a working
credential. Substitute your own values as you read.

**Status: running in Docker.** Panel reachable over HTTPS, all five game cores
up, survives a reboot and a full `docker compose down`. This is the [40250]
server files running **natively on Linux, in containers** — no FreeBSD, no VM,
no emulation.

```
metin2-db      mariadb:10.11        healthy   (internal only)
metin2-game    metin2/game:40250    healthy   0.0.0.0:11000, 0.0.0.0:13000-13002
metin2-panel   metin2/panel:latest  healthy   127.0.0.1:7788  (nginx proxies to it)
```

This box got there in two steps, and both are written up below because both are
instructive: first a **host deployment** — the binaries and five systemd units
straight on Debian, which is the conservative way to prove a port on a real
machine — and then a **migration into containers**. Where a section describes
the host stage it says so. The containers are what runs now.

Stack lives in `/opt/metin2/stack/`. `restart: unless-stopped` on all three
services and the Docker daemon is enabled, so a reboot brings everything back.

```sh
cd /opt/metin2/stack
docker compose ps
docker compose logs game --tail 50
docker compose restart game
docker compose down && docker compose up -d      # data survives this
```

**Rollback**: the pre-container host deployment is still installed, only
disabled. `docker compose down && systemctl enable --now m2-db m2-auth m2-first
m2-game1 m2-game2 m2panel` puts it back in seconds. A pre-migration database dump
is at `/opt/metin2/backup/pre-docker.sql.gz`.

## Access

| | |
|---|---|
| **Panel** | `https://panel.example.com` |
| **Panel passphrase** | chosen at install time, stored only as a hash |
| Game server (client) | `203.0.113.10` — auth `11000`, channel `13000` |
| SSH | `root@203.0.113.10` |
| MySQL user | `metin2` / `<generated>` (localhost only) |

**Where the secrets live on the box** — this is the part worth copying. The
panel passphrase and the database password are written to `/root/.metin2-panel`
and `/root/.metin2-dbpass`, both mode `600`, so they are recoverable by root and
by nobody else. Keep that habit: nothing secret belongs in a file that gets
committed, and the stack reads the database password from `.env`, which is
`.gitignore`d.

**Game accounts.** The server files ship an `admin` account whose password hash
is public knowledge — it is inside the distributed database dump, so anyone can
look it up. **Change it before the server is reachable from the internet**, and
change the shipped `test` account too or delete it. The installer does this for
you on the FreeBSD path; on this one it is manual.

## Client setup

On the container stack this is one command —
`docker compose run --rm client-builder` builds a client with this server's
address already inside it and hands it to the panel's download page. See
[docker/README.md → Giving players the game client](docker/README.md#giving-players-the-game-client).

By hand: copy [`client/serverinfo.py`](client/serverinfo.py) next to
`Metin2Release.exe`, renamed to `serverinfo.py`, and put your server's address
in it. The client reads the copy **beside the .exe** — a `root/serverinfo.py` is
never read. On a `_DISTRIBUTE` client, `pack/root.epk` is searched before loose
files, so the pack has to be moved aside as well or the loose file loses
silently.

### Why the client uses the IP and not the hostname

`panel.example.com` is **Cloudflare-proxied**, and the proxy only
forwards HTTP/HTTPS on a fixed port list (80, 443, 8080, 8443 and a few more).
Metin2 speaks raw TCP on 11000 and 13000-13002, so the proxy cannot carry it.

The panel goes through the proxy (that is exactly what it is for). The game does
not. If you want a hostname in the client too, add a **second DNS record with the
grey cloud (DNS only)** — e.g. `play.example.com` → `203.0.113.10` — and
change one line in `serverinfo.py`. Clients then survive a server move.

## What ran where — the host stage

**Historical: this is the pre-container layout**, kept because the port map, the
map split and the boot ordering are the same in the containers and this is where
they were worked out. The current stack is the three containers at the top of
this page; MariaDB in it is `mariadb:10.11`, not the host's 11.8.

| Service | Unit | Port | Note |
|---|---|---|---|
| MariaDB 11.8 | `mariadb` | 3306 (localhost) | `sql_mode=NO_ENGINE_SUBSTITUTION`, latin1 |
| db core | `m2-db` | 15000 (localhost) | everything else dials this |
| auth (login) | `m2-auth` | 11000 | same binary as `game`, different name |
| channel1/first | `m2-first` | 13000 | maps 1 4 5 6 3 23 43 112 107 67 68 72 208 302 304 |
| channel1/game1 | `m2-game1` | 13001 | maps 21 24 25 26 108 61 63 69 70 73 216 217 303 |
| channel1/game2 | `m2-game2` | 13002 | maps 41 44 45 46 109 62 64 65 66 71 104 301 351 |
| Admin panel | `m2panel` | 7788 (localhost) | Flask, behind nginx |
| nginx | `nginx` | 80, 443 | TLS termination, proxies to 7788 |

Memory: **1.5 GB of 3.9 GB** in use. Disk: 13 GB of 75 GB.

Boot ordering was handled by systemd `Requires`/`After` plus staged
`ExecStartPre=/bin/sleep` — the db core must be up before anything else, or the
other cores give up on `127.0.0.1:15000` and exit.

```sh
systemctl status m2-db m2-auth m2-first m2-game1 m2-game2 m2panel
systemctl restart m2-first          # one core
journalctl -u m2-first -n 50        # its output
```

Logs: `/opt/metin2/logs/*.log` plus each core's own `syslog`/`syserr` under
`/opt/metin2/server40250/`.

In the container stack the same ordering is `m2-supervise`, which improves on
this in one respect: it **waits for each core's port to open** rather than
sleeping a fixed number of seconds, and on shutdown stops them in reverse order
so the channel cores flush their players to the db core while it is still up.

## TLS

Let's Encrypt certificate for `panel.example.com`, valid to
**5 Nov 2026**, renewed by an `acme.sh` cron job four times a day. The HTTP-01
challenge was verified to pass **through** the Cloudflare proxy before the
certificate was requested — a token was served on port 80 and fetched back via
the public hostname.

Note the browser sees Cloudflare's own certificate (Google Trust Services),
because the record is proxied. Cloudflare then talks to our nginx using the
Let's Encrypt certificate. Both legs are valid; this is the intended shape.

Recommended: set **SSL/TLS → Full (strict)** in the Cloudflare dashboard.

## Verified

- All four game ports reachable from outside; **handshake and key agreement
  complete on every core** (auth, first, game1, game2) — tested from a different
  machine over the public internet, against the containerised stack.
- Panel: HTTPS 200, valid certificate chain, HTTP→HTTPS redirect (301), correct
  title, and its live status badge reports the game server as **up**.
- Database: 5743 item prototypes, 1334 mobs, 290 shop entries, 2 characters,
  2 accounts, both `web_admin` tables, `sql_mode=NO_ENGINE_SUBSTITUTION`.
- **Persistence**: after a full `docker compose down && up`, player count,
  account count and the test character's yang balance were byte-identical, the panel's
  session secret survived (otherwise everyone would be logged out), and all four
  game ports were back in 10 seconds.
- No errors in any core's `syserr`.
- Resource use: **940 MB of 3.9 GB**, 58 GB disk free.

### The database-unreachable bug, and its real cause

The panel showed *"Die Spiel-Datenbank ist gerade nicht erreichbar"* and an empty
player list, while the game server itself was running perfectly.

**Root cause: the imported dump carried its own user and grants.** MariaDB's
first-run init created `metin2` with the password from `.env`; the `.sql` dump
that was imported straight afterwards contained its own `CREATE USER` /
`GRANT` statements and **overwrote that password**. The panel then authenticated
with the `.env` value and was refused — `Access denied for user
'metin2'@'172.18.0.4'`.

It looked like a network problem and was a credentials problem. The giveaway was
that a direct connection *using the config file's own password* also failed,
while root worked.

Fix applied:

```sh
docker exec -i "$(docker compose ps -q mariadb)" mysql -uroot -p"$ROOT_PW" -e "
  ALTER USER 'metin2'@'%' IDENTIFIED BY '<M2_DB_PASSWORD from .env>';
  GRANT ALL PRIVILEGES ON *.* TO 'metin2'@'%' WITH GRANT OPTION;
  FLUSH PRIVILEGES;"
docker compose up -d --force-recreate panel
```

**Anyone restoring a full dump into this stack can hit this.** Either strip
`CREATE USER`/`GRANT` from the dump before importing, or re-assert the
application user afterwards as above.

A stock `docker compose up` does not hit it: `mariadb/initdb.d/10-import-dumps.sh`
creates the game user, re-asserts its password with `ALTER USER`, and only then
imports five *raw table* dumps that contain no user or grant statements at all.
The hazard is restoring somebody's `--all-databases` backup on top.

Verified afterwards: login succeeds, no error banner, **all three characters
render** on the dashboard with their account column.

### Server rates — working, and verified on this box

The rates page originally said the helper program was missing, because on FreeBSD
the panel simply **shells out** to a script that rewrites the game's data files
and restarts the cores. In Docker the panel and the game are separate containers,
so that model cannot work.

Replaced with a **request spool** on a volume both containers share. The panel's
`apply_rates.sh` writes a request and returns immediately; a watcher inside the
game container picks it up, applies the change, restarts the cores through the
supervisor that already exists (reverse order, so channels flush through the db
core while it is still up), and writes the result back to the status file the
panel reads. No Docker socket and no SSH between containers — both would trade a
text-file edit for arbitrary code execution in the one public-facing web app.

The rate arithmetic is **not** reimplemented: the game image carries
`files/packs/tmp4-r40250.pack` verbatim and calls its `pack_apply_rates()`.
Verified identical, md5 `b4ab2c4d0c56b8e025b8bf448f51f643` in repo and image, so
FreeBSD and Linux cannot drift apart.

Because the data tables live in the *image* and a container's writable layer dies
on recreate, the wanted rates are kept on the game's state volume and
**re-applied at every container start**, before the cores read anything.

**Proven end to end on this VPS** (nobody was online at the time):

| stage | result |
|---|---|
| baseline | `mob_proto.txt` md5 `85df0eb5…`, EXP 15 / 39 / 51 |
| save 300 / 200 / 250 | panel: "✅ Saved! … restarting now"; status `running` → `ok` after 40 s |
| after | EXP **45 / 117 / 153** — exactly ×3 |
| save 100 / 100 / 100 | back to `ok` after 40 s |
| after restore | md5 `85df0eb5ff38638de726f05337a895f7` — **byte-identical to the original** |

That last row is the property worth protecting: values are always recomputed from
an untouched baseline, never from the current file. So 200 % then 300 % gives 3×
the original and not 6×, and 100 % restores the shipped data exactly.

Two things to know: the game learns the rates from the spool, not from MariaDB —
restoring a database with non-100 values changes nothing in the game until
somebody presses save once. And if the game container is down when you save, the
page keeps saying "being applied" until it comes back and picks the request up.

### One container-specific fix that was needed

The panel's live status badge decides "is the game up?" by reading the socket
table — which inside its own container shows only its own sockets, so it reported
the server offline while it was demonstrably running. The panel supports a TCP
probe instead, but at the time the stack named its variables `M2_PANEL_*` while
the panel code read `M2PANEL_*`, so the value never arrived. It was patched over
on the box with a `docker-compose.override.yml`.

**Reconciled in the repo since.** The panel's entrypoint now writes a `game_host`
key into `m2panel.conf` from `M2_GAME_HOST`, which `docker-compose.yml` sets to
`game`; the panel reads that key. No override file is needed for the status
badge any more.

Two of the four settings in that old override are **still not carried by the
stack**, and both are 40250-specific:

| | override had | stack today | consequence |
|---|---|---|---|
| `inventory_slots` | 180 | `M2_INVENTORY_SLOTS` defaults to **45** | 40250 has 4 inventory pages, not 1. The panel thinks a full first page means a full inventory and refuses to give items. Set `M2_INVENTORY_SLOTS=180` in `.env`. |
| `max_item_count` | 255 | not passed at all; the panel falls back to **65535** | `player.item.count` is `TINYINT UNSIGNED`. Outside strict mode MySQL does not complain about a too-large number, it quietly stores the largest that fits — so "give 1000 potions" silently produces 255. |

The first is a one-line `.env` change. The second has no `.env` variable at all:
the panel reads `M2PANEL_MAX_ITEM_COUNT` from its own environment, and
`docker-compose.yml` does not pass it, so today it takes a
`docker-compose.override.yml`. Both would be better as defaults in the stack,
since it only ever builds r40250.

## Closed since, and what is still open

1. ~~**The client download in the panel is empty.**~~ **Closed.** On the
   container stack it is one command — see
   [docker/README.md → Giving players the game client](docker/README.md#giving-players-the-game-client):

   ```sh
   docker compose run --rm client-builder
   ```

   It fetches the server files, writes this server's address into the client
   with the FreeBSD installer's own `pack_prepare_client()`, and puts the result
   on the panel's data volume. Re-running it after an address change takes
   seconds and downloads nothing.
2. ~~**Docker packaging** is built but not yet deployed here.~~ **Closed** — the
   migration happened and the containers are what runs. The host deployment is
   still installed but disabled; see **Rollback** at the top.
3. ~~**Rate changes from the panel** need `apply_rates.sh`, which is
   FreeBSD-shaped.~~ **Closed** — see [Server rates](#server-rates) below,
   proven end to end on this box.
4. ~~**Teleport and running speed from the panel do not work.**~~ **Closed in the
   build, not yet confirmed on this box.** Both halves are now in:
   - the game image stages and compiles `files/web_admin.quest` (stage 2b of
     `docker/game/Dockerfile`), with a `qc` built from the same source as the
     cores, because the `qc` in the server files is a FreeBSD binary;
   - the port patch carries the `mysql_direct_query` binding the quest calls,
     marked `M2_FEATURE quest-sql-binding` and named in the patch header as the
     one thing in there that is not part of the port
     (`files/ADD_SQL_BINDING.md`).

   Verified on a throwaway stack on a development machine: the cores load the
   quest, start its poll timer at boot, and move a queued row out of `pending`
   within about 35 seconds. **Not yet verified with a real character in game,
   and not yet verified on this server.** Before believing it here, redeploy and
   run the queue check in `ADD_SQL_BINDING.md` section 4 — it needs nothing but
   a database prompt.

   **Items, yang and level worked before and still do**: in game when the helper
   answers, straight to the database when it does not.

## Server rates

The panel's **Server rates** page works in the container stack. Saving it
rewrites `share/conf/mob_proto.txt` and the three drop tables, restarts the five
cores in the proper order, and reports back on the page — roughly 35 seconds
from pressing save to "these rates are live".

The panel container cannot reach the game's files or its processes, so it drops
a request into a small volume mounted into both containers (`/opt/m2spool`) and
the game container's supervisor carries it out. No Docker socket and no SSH
between containers. The whole design is documented in
[`docker/README.md`](docker/README.md#server-rates-across-two-containers).

### How it was rolled out here — the pattern for any upgrade to a live server

This has been done; what follows is kept as the recipe, because *upgrading a
server that has players on it* is the general problem and this is the shape of
the answer. It needed **both images rebuilt** (the panel gained the request
script; the game gained the rate engine and the server-files profile) and the
stack brought up once with the current `docker-compose.yml`, which adds the
`rates-spool` volume and three panel environment variables.

```sh
cd /opt/metin2/stack

# 1. back up first -- this is a live server
docker compose exec -T mariadb mysqldump -uroot -p"$ROOT_PW" \
  --all-databases --single-transaction | gzip > /opt/metin2/backup/pre-rates.sql.gz

# 2. the game image needs the server-files profile in its build context.
#    Both fetch-sources.sh and prepare-context.sh put it at game/rates/pack.sh.
ls game/rates/pack.sh || ./prepare-context.sh

# 3. rebuild. The game's dependency and compile stages are cached, so this is
#    the runtime stage only -- about a minute. If the cache is gone it is a
#    full source build (15-20 minutes on this box).
docker compose build panel game

# 4. bring it up. Both containers are recreated; the database is not touched.
docker compose up -d

# 5. check
docker compose exec panel ls -la /opt/m2spool
docker compose exec game  ls -la /opt/m2spool
docker compose logs game --tail 30
```

Players are disconnected while the game container restarts (step 4) and again
for half a minute the first time somebody saves the page. Nothing else changes:
the database, the guild emblems, the panel password and the session secret are
all on volumes that are not touched.

If the build cache on this box is cold and a 20-minute compile is unwelcome,
build the game image somewhere else and ship it:

```sh
# on the build machine
docker save metin2/game:40250 metin2/panel:latest | gzip > m2-images.tar.gz
# on the VPS
gunzip -c m2-images.tar.gz | docker load && docker compose up -d
```
