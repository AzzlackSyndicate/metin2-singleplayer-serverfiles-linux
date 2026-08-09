# Metin2 Server Suite

Tooling that turns a rented Linux server into a working **Metin2** server with a
web admin panel, without you having to understand Metin2 server files.

It is three things that grew out of each other:

1. **A Linux port of the game server.** The original r40250 binaries only run on
   FreeBSD, which most cheap VPS providers do not offer. The source was ported —
   kqueue to epoll, signal handling, the 32-bit build, and a long list of smaller
   things — so it builds and runs on an ordinary Ubuntu or Debian box.
2. **Docker packaging.** The port is wrapped in a compose stack: game cores,
   MariaDB, the panel and a client builder. `docker compose up -d --build`, or
   one installer command that does everything including the firewall.
3. **A web admin panel** — Flask, in English, German and Turkish. Give items,
   yang and levels; set experience/drop/yang rates; hand out password-reset
   links. Players use the same site to register and to download a client that
   already points at your server.

This is a hobby project aimed at small private and single-player servers. It is
written to be honest about what does not work rather than to sell you anything;
where something has a catch, the docs say so.

The project used to carry a FreeBSD installer as well. That has been removed
from this repository — what is here now is Linux and Docker only.

---

## The legal situation, up front

**Metin2 belongs to Ymir Interactive and Webzen. It does not belong to this
project, and this project cannot give you permission to use it.**

This repository contains **only tooling** — the port, the packaging, the panel —
and that tooling is MIT licensed. It contains **no game files and no game
source**. The Linux port itself is a single 109 KB patch touching 28 files; the
code it patches is fetched at install time from its own publisher's link.

Running a private server is very likely a copyright infringement wherever you
live. Small private servers are generally left alone; that is a risk you accept,
not a permission anyone here grants. Do not charge money for access and do not
present yourself as official. See [NOTICE.md](NOTICE.md) for the full breakdown
of what the licence covers.

---

## What you need

| | |
|---|---|
| **Host** | Ubuntu 22.04/24.04 or Debian 12/13, root access. Windows 10/11 with Docker Desktop works for a local-only server. |
| **Architecture** | **x86 (Intel/AMD)**. The game server is 32-bit x86 code from 2014 and cannot run on ARM. |
| **Resources** | 4 GB RAM, 40 GB disk (8 GB RAM during the first build) |
| **Address** | **an IPv4 address** — Metin2 is from 2004 and cannot work over IPv6 alone |
| **Setup time** | 10–25 minutes for the first build, seconds after |
| **Prior knowledge** | none |

You also need a **Windows machine to play on**. The game client is Windows-only.

The server files themselves (roughly 2 GB, containing the game server source and
the Windows client) are **not in this repository** — see
[Why the game files are not here](#why-the-game-files-are-not-here). The install
scripts fetch them for you, when the share they come from is willing.

---

## Getting started

The step-by-step version for someone who has never done this is
**[TUTORIAL.md](TUTORIAL.md)**. The short version:

### Linux — one command

```sh
curl -fsSL https://raw.githubusercontent.com/AzzlackSyndicate/metin2-singleplayer-serverfiles-linux/main/installer/install.sh | sudo sh
```

Checks the machine, installs Docker, assembles the server, builds it, starts it,
opens the firewall, and prints the panel address and a generated password. Safe
to run twice. With `--domain` and `--email` it also gets a Let's Encrypt
certificate and puts the panel on HTTPS.

### Windows — one command

```powershell
irm https://raw.githubusercontent.com/AzzlackSyndicate/metin2-singleplayer-serverfiles-linux/main/installer/install.ps1 | iex
```

The same thing, but everything binds to `127.0.0.1`: no port is opened, no
firewall rule is created, and nobody else can join. It is for playing alone.

### Or by hand, from a checkout

```sh
curl -fsSL https://get.docker.com | sh      # Docker's official installer
./linux-port/fetch-sources.sh               # fetch upstream, apply the port, stage the context
cd linux-port/docker
cp .env.example .env && nano .env           # public IP + two passwords
docker compose up -d --build
```

Full walkthrough: **[linux-port/docker/README.md](linux-port/docker/README.md)**.

### Then

Open `http://YOUR-SERVER-IP:7788` for the panel. If you gave the installer a
domain, it is on HTTPS at that name instead.

---

## How this repository is laid out

```
files/                  THE SHARED MATERIAL — panel, item index, server-files profile
  admin_panel.py          the web admin panel, one Flask file
  items.json favicon.png  panel assets
  packs/tmp4-r40250.pack  the server-files profile: rate maths and client preparation
  web_admin_schema.sql    the two database tables the panel adds
  web_admin.quest         the in-game side of the panel — NOT installed by anything here
  speed_boost.quest       a standalone server-wide speed buff — likewise not installed
  README.md               what is in here and how the panel is configured
  PACKS.md                the pack-profile format
  ADD_SQL_BINDING.md      the C++ change teleport and running speed would need
  ADDING_SHOP_ITEMS.md    how NPC shop prices actually work

installer/              THE ONE-COMMAND INSTALLERS
  install.sh              Linux. Publishes the game ports and opens the firewall.
  install.ps1             Windows. Binds 127.0.0.1 only; creates no firewall rule.

linux-port/             THE PORT — write-ups, the patch, then packaging
  patches/0001-r40250-linux-port.patch    the port itself: 109 KB, 28 files
  fetch-sources.sh        fetch the upstream package, apply the patch, stage the context
  README.md               why, how, and how far it got
  PORT40250.md            what the port does to the source, and why
  RUNTIME.md              preparing the runtime tree and database, by hand
  FDWATCH-BUG.md          the kqueue-to-epoll work, in detail
  PLAYTEST.md             the first real client login
  VPS-DEPLOYMENT.md       a sanitised worked example of a real deployment
  client/serverinfo.py    client-side config template
  docker/                 the compose stack (its own README, its own .env.example)

LICENSE  NOTICE.md  CONTRIBUTING.md  TUTORIAL.md
```

**`files/` is not a leftover.** The Docker build copies out of it —
`linux-port/docker/prepare-context.sh` reads `files/admin_panel.py`,
`files/items.json`, `files/favicon.png`, `files/web_admin_schema.sql` and
`files/packs/tmp4-r40250.pack` directly, and fails if the first is missing.
There is exactly one copy of the panel and exactly one copy of the rate
arithmetic. That is deliberate: the same pack file is in use on a production
server outside this repository, so nothing can drift if there is nothing to
drift.

The directory name is historical and mildly misleading, but **nothing in it may
be renamed or moved without changing `prepare-context.sh` in the same commit.**

---

## Why the game files are not here

`Client/`, `Source/` and `FreeBSD/` will appear in your working copy once you
have the full server-file package, but they are `.gitignore`d and never
committed.

The reason is licensing first: those are Webzen's game assets and a
redistributed third-party server package. They are not ours to publish. Size
confirms it — `Client/` alone is 1.6 GB, four individual files are over GitHub's
100 MB hard limit, and the total is well past what Git LFS's free tier can carry.

So there is **no release archive of this project containing the game, and there
never will be one.** The repository holds the port and the tooling. Everything
else is assembled on your machine at install time by
`linux-port/fetch-sources.sh`, which downloads the upstream r40250 package,
verifies it against a recorded SHA-256 baseline, applies the patch and fills in
the Docker build context. If you already have the archive, point
`M2_SRC_ARCHIVE` at it and nothing is downloaded.

### The download is currently failing

The upstream package is published as a MEGA share, and **that share is over its
bandwidth quota right now**. MEGA answers `509 (over quota)` and the download
does not complete. `fetch-sources.sh` recognises this and says so plainly
instead of retrying forever, but it cannot work around it.

Until the quota resets or the link is replaced, you need the archive by other
means and must supply it yourself:

```sh
M2_SRC_ARCHIVE=/path/to/serverfiles.zip ./linux-port/fetch-sources.sh
```

This is the normal condition of an anonymous file share, not a temporary
accident, and one day the link will stop working for good.

---

## Supported server files

| Profile | What it is |
|---|---|
| `packs/tmp4-r40250.pack` | **[40250] Reference Serverfile** by TMP4 — a reconstruction of the official 2014 r40250 files. This is the only package this project targets. |

The port is a patch against that exact source tree, checked against a recorded
hash before it is applied, so a different package would need its own port rather
than just its own profile. [files/PACKS.md](files/PACKS.md) describes the profile
format.

---

## Known limits

Stated here rather than buried, because they change whether this is useful to
you:

- **Teleport and running speed do not work on a Docker deployment.** The panel
  offers both. Both work by writing a row into `web_admin_queue` for an in-game
  quest to pick up — and nothing in the Docker stack installs that quest, nor
  does the ported game core have the `mysql_direct_query` binding the quest
  needs. Clicking either button waits about seven seconds and then shows an
  error saying the in-game helper did not answer. Nothing is changed and nothing
  is corrupted, but the feature is not there. Items, yang and levels are written
  straight to the database and do work. See
  [files/ADD_SQL_BINDING.md](files/ADD_SQL_BINDING.md).
- **The panel runs on Flask's development server.** Fine for the few people
  administering a private server; not a public web server. The Linux installer
  puts nginx in front of it when you give it a domain, which takes the worst
  edges off.
- **Changing server rates restarts the game.** The game reads them once at
  startup. Anyone playing is dropped for well under a minute.
- **The port is 32-bit.** The server builds `-m32`, as the original does. Moving
  to 64-bit is tempting and risky for 2014-era code.
- **x86 only.** No ARM, for the same reason. The installers check and stop early
  with an explanation.
- **IPv6-only hosts will not work.** The game protocol predates IPv6 support.
- **The upstream download is unreliable** — see above.

---

## Contributing

There is a production server on one side and a container stack on the other, so
"how do I change something without breaking either" has a real answer:
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT for the tooling — [LICENSE](LICENSE). The game is not covered and is not
ours — [NOTICE.md](NOTICE.md). Read that before you fork.
