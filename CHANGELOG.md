# Changelog

Every release of this project, newest first. The admin panel reads this file to
show you what an update would bring before you install it.

Versions are `MAJOR.MINOR.PATCH`:

- **PATCH** — a fix. Nothing you do changes.
- **MINOR** — something new, or something behaves better than it did. Safe to
  take; nothing you have set up stops working.
- **MAJOR** — something you have to act on. A setting that has to move, a
  command that no longer exists, a manual step during the update. These say so
  at the top, in full, before anything else.

Updating never touches your database. Characters, accounts and settings survive
every version here.

---

## 1.11.7 — 2026-08-12

### Fixed

- The Windows installer no longer stops at "Which clients your players get".
  It called `Invoke-DockerQuiet` and `Say`, which are functions of the UPDATER
  -- a second script that lives inside `install.ps1` as a here-string and is
  written to disk, not run. They do not exist in the installer's own scope, so
  the step died the moment it was reached. `install.ps1` is now checked with
  PowerShell's own parser, which lists the functions the file really defines
  and every command it calls: that check finds this class of mistake, and
  reading the file did not.
- It also read the fetcher's exit code from `$LASTEXITCODE` after calling a
  PowerShell function, which is whatever the last native command inside it
  happened to leave behind. It uses the return value now, and -- as install.sh
  already did -- checks that a browser client actually arrived rather than
  believing any exit code at all.
- A new install downloads **the server alone**, not the old combined package.
  `artifacts.json` has named the split archives since 1.11.0, but nothing told
  `fetch-sources.sh`, so it fell back to the address compiled into it: 1.6 GB
  of server *and* desktop client, downloaded in full even by somebody who chose
  the browser client and would never unpack a desktop client. It is 223 MB now,
  and its checksum is verified.
- The desktop client is fetched from its own archive. Left unset, the client
  builder looked for a client inside the server files, which is where it used
  to be and no longer is. The installer writes `M2_CLIENT_ARCHIVE_URL` from
  `artifacts.json` -- and leaves it alone if you set it yourself.

## 1.11.6 — 2026-08-12

### Fixed

- 1.11.5 stopped the admin panel from starting on a server with a domain. The
  list of headers it hands waitress was comma-separated; waitress splits that
  value on spaces, so the whole list arrived as one unrecognised name and it
  refused to start -- and since the panel is the container's only process,
  refusing to start is the container restarting for ever. The syntax is now
  checked against waitress itself before release rather than assumed.

## 1.11.5 — 2026-08-12

### Fixed

- The panel now really does know that nginx is in front of it. 1.11.3 told it
  so, and it still made no difference, because the evidence never reached the
  application: waitress deletes `X-Forwarded-*` headers it has not been told to
  trust, and it had not been told. The panel's own handling of those headers --
  which checks that they came from the proxy before believing a word -- was
  correct and never ran once. Waitress is now told about the proxy, and it
  consumes the headers itself: it applies them to the scheme, the host and the
  client address, and takes them out. So the panel reads the setting instead of
  looking for a header that is gone by then.

  Two things were quietly wrong the whole time this was: **Play in Browser**
  handed out the bridge's own port over plain `ws://`, which no browser allows
  from an HTTPS page, and the gigabyte client download was streamed through
  Python instead of being handed to nginx -- one download blocking the panel
  for everyone else, which is exactly what that hand-off exists to prevent.

## 1.11.4 — 2026-08-12

### Fixed

- Updating actually updates the browser client. The installer placed
  `artifacts.json` -- the file that says which engine version an install should
  have -- only when it was not already there, so every later run consulted the
  pointer that shipped with the version being replaced and correctly concluded
  there was nothing to do. Measured: a server ran the 1.11.3 installer to
  completion, reported success, and kept engine 1.11.0. Both helper files are
  refreshed every run now, and an existing copy is used only when neither the
  checkout nor GitHub can be reached.
- 1.11.3 could not write its nginx configuration. A comment added to the
  `/play/` block contained a plain double quote, which ended the shell string
  that block is built in; the rest was read as commands. `sh -n` cannot see
  that -- it is valid syntax, just not the intended one -- so it passed every
  check and failed on the server, leaving the bootstrap configuration in place
  and HTTPS down until the installer was run again.

## 1.11.3 — 2026-08-12

> [!IMPORTANT]
> This one needs browser-client engine **1.11.3**, which the installer fetches
> by itself. Two of the fixes below are in the client's own files, not on your
> server, so an install that keeps an older engine keeps the bugs.

### Fixed

- The browser client no longer stops at "starting…". `index.dev`, the pack list
  the client writes to `/pack/index.dev` for `InitPacks` to read, was missing
  from the engine archive: `webfs.js` gave up on the 404 and the game never
  started. Nothing reported it as a fault -- a 404 is the correct answer about
  a file nobody installed -- so the page simply sat there.
- The browser client reaches a server named by a **domain**. It only ever
  reached one named by an IP address: the address goes through `inet_addr()`,
  which parses dotted quads and nothing else, so a name became `INADDR_NONE`
  and the page dialled `wss://255.255.255.255/`. The page's WebSocket shim now
  takes the host from the URL it was opened with. Resolving it instead would
  have been wrong even if a browser could: a certificate is issued for a name.
- **Play in Browser** hands out a working address behind HTTPS. The panel could
  not tell that nginx was in front of it -- it runs in a container, and Docker
  rewrites the source address, so the loopback test it used never matched. It
  therefore named the bridge's own port and left the connection unencrypted,
  and browsers block a `ws://` connection from an HTTPS page outright. The
  installer now states the fact, because it is the only party that knows it.
- A 404 under `/play/` is no longer cached, by anyone, for a year. The rule that
  keeps data chunks forever -- correct, their names are content hashes -- was
  attached to error responses too. One request that arrived while the client was
  still installing taught the CDN that a chunk did not exist, permanently.
  Measured on a live server: a 4.2 MB chunk present on disk, served as a cached
  404. It presents as files missing from the game, not as a caching problem.

### Changed

- **Play in Browser** asks the page to keep 768 MB of game data in memory
  instead of the page's own default of 96 MB. The client reads its data in
  4.2 MB chunks, and a chunk it does not already hold is fetched with a
  synchronous request that stops the frame -- the browser's own cache does not
  help, because the bytes still have to be converted mid-frame. 96 MB holds 23
  of the 420 chunks; 768 MB holds around 180, which is more than a session in
  one region touches. Set `M2_BROWSER_CACHE_MB` in `.env` to change it.

### Added

- `linux-port/package-web-client.sh` builds both browser-client archives and
  prints the lines `artifacts.json` needs. The engine's contents are a written
  list, not a pattern, and a missing file stops the run: the first archive was
  packed by hand and was one file short, which is the `index.dev` bug above.

## 1.11.2 — 2026-08-12

### Fixed

- The panel shows the **Play in Browser** card again. It looked for the browser
  client at `browser/index.html` while the installer puts it at
  `browser/current/index.html` -- the same one-level-too-high mistake 1.11.1
  fixed in nginx, in the second place that had its own copy of the path. The
  panel now looks in `browser/current` first and falls back to `browser`, so a
  client placed there by hand keeps working.
- That check is made per request rather than once at startup. Installing the
  browser client into a running panel now shows the card immediately instead of
  after the next restart.

## 1.11.1 — 2026-08-12

### Fixed

- nginx serves `/play/` from `browser/current`, the symlink to the version being
  served, rather than from its parent -- which held only the version
  directories, so every request under `/play/` was a 404 while the files sat one
  level down.
- The fetcher mounts its script and `artifacts.json` from beside
  `docker-compose.yml` rather than from above it. Those two live one and two
  levels up in a checkout, and an install directory is a copy of that one
  folder -- and Docker answers a missing bind source by creating an empty
  directory, so the fetcher started with a directory where its script should
  have been and did nothing, silently.
- The fetcher's script and pointer file are placed beside `docker-compose.yml`
  on every run, downloaded if no checkout is at hand, not only when the build
  context is restaged. A server already at
  the published version skips restaging, so on those the two files never
  arrived and the browser client could not be fetched.
- The installer checks that a browser client actually arrived instead of
  believing the fetcher's exit code. One that started without its script
  reported success while nothing was installed, and the installer repeated that
  to the operator.

  Four faults between them, none of which announced itself: a server that
  chose the browser client got no browser client, and the installer said it
  had installed one.

## 1.11.0 — 2026-08-11

> [!TIP]
> ## **The Web Client is now available to install.**
>
> **Your players can play in the browser — no download, no installation, just a
> link.** The installer asks which clients you want and fetches what it needs:
> browser only, desktop only, or both. An existing server keeps working
> untouched; on your next update you are simply offered the browser client.

### Added

- **The installer offers a choice of clients.** On a first install: browser
  only, desktop only, or both, with what each costs on disk. On an update
  nothing is asked when both are already installed; when one is missing it is
  offered, because that is the only moment you find out the other way exists.
- Only what you chose is downloaded. A browser-only server never fetches the
  1.29 GB desktop client, and a desktop-only server never fetches the 1.75 GB
  browser corpus.
- **The browser client is fetched, verified and installed for you** — no more
  placing files on a volume by hand. `webclient-fetcher` is a task container in
  the `webclient` profile: it runs, writes to the panel's volume, and exits.
- `artifacts.json` at the top of the repository says where the five archives
  come from and what they must hash to. The engine's URL is derived from its
  version, so publishing a new one is: attach the file to the release, change
  two lines here.
- The browser client is two archives on purpose: the engine (17.6 MB) and the
  game data (1.75 GB). Nearly every fix touches only the engine, so an update
  costs 17.6 MB rather than 1.75 GB.
- Nothing is ever written over. Each version is unpacked beside what is running,
  checked, and only then is `current` moved — a rename, which either happens or
  does not. There is no instant at which a player is handed half an engine, and
  a rollback is one symlink.
- **A WebSocket bridge**, so a browser can reach a game server that speaks TCP.
  It is the `wsbridge` service, it is in a compose profile, and
  `docker compose up -d` does not start it or build it.
- The bridge speaks the browser client's own protocol: one port, with the
  destination in the path as `/to/<host>:<port>`, and a `/ping` the client's
  connection dialog checks before it will start.
- It only ever connects to the game container. The host named in the URL is
  read, logged and discarded; the port must be one the game runs on. The db
  core and the cores' peer-to-peer ports are refused even when named
  explicitly.
- **Play in the browser** on the panel's front page, with the client served
  from `/play/`. It appears only when `M2_BROWSER_PLAY=1`, a browser client is
  on the panel's volume, and the bridge answers.
- The button carries the address in the link the client expects
  (`?serverHost=…&serverPort=…`), so the page goes straight into the game
  instead of asking the player for an address.
- nginx serves `/play/` off the panel's volume directly, with the cache rules
  the client needs — content-addressed blobs kept for a year, `manifest.bin`
  never kept. It is 1.7 GB in 421 files.
- With a domain, nginx routes `/to/` and `/ping` to the bridge on port 443. The
  client cannot be given a path and an HTTPS page cannot open a plain
  WebSocket, so this is the only arrangement that works behind TLS.
- `M2_BROWSER_PLAY`, `M2_BRIDGE_PORT`, `M2_BRIDGE_BIND_ADDRESS`,
  `M2_BRIDGE_TRUST_PROXY`, `M2_BRIDGE_HOST_ALIASES`,
  `M2_BRIDGE_MAX_CONNECTIONS`, `M2_BRIDGE_MAX_PER_IP` and `M2_BRIDGE_ORIGINS`
  in `.env`.
- Both installers start the bridge when `M2_BROWSER_PLAY=1` and stop it when it
  is set back to 0. On Linux the firewall step opens its port only on a server
  without a domain.
- The browser client itself is not part of this project — it carries game data,
  which this repository never does. It is fetched from its own archives, whose
  addresses and checksums are in `artifacts.json`.

## 1.10.0 — 2026-08-11

### Fixed

- The item search box works on a local install. It answered every query with
  an empty list there, so typing produced no results at all.
- The item search works in browsers without arrow-function support. It was the
  only script in the panel that used them, and a browser that cannot read them
  skips the whole block — typing then did nothing whatsoever.
- The item search says so when it cannot reach the server, instead of looking
  like an empty result.
- Setting a level above the server's cap now says so. The server silently
  ignored it and reported success, so the level stayed as it was while the
  character kept the skill and stat points from the attempt.

### Changed

- The highest character level is 120 by default, which is as high as the game
  goes. Servers installed before this keep the value in their `.env`; change
  `M2_MAX_LEVEL` there and restart to raise it.
- The "set level" box offers exactly what your server accepts.
- **Game language** and **Admin passphrase** have moved to the bottom of the
  admin page, under a heading of their own. They set up the server rather
  than run it, and both affect everyone on it.

---

## 1.9.0 — 2026-08-11

### Added

- **Game language** on the admin page. The server files carry fifteen
  languages; pick one and the game speaks it — quest text, system messages,
  item and monster names. The game restarts for well under a minute.
- The download page says which language the game is in.
- After a switch, the panel shows players who already downloaded the game how
  to change their copy: one file to rename, nothing to download again.
- The client the panel hands out is built in the server's language.

### Fixed

- The patch log button had two translations, of which only the second was ever
  used.

---

## 1.8.0 — 2026-08-10

### Added

- **Admin passphrase** on the admin page, just under the introduction. Pick
  your own instead of the generated one; it takes effect straight away and you
  stay logged in.

### Changed

- The installer shows the admin passphrase every time it runs, including one
  you chose yourself in the panel, and never changes it behind your back.
- The introduction no longer says teleport and running speed are missing from
  a normal install. They are there; they need the player to be logged in.

---

## 1.7.0 — 2026-08-10

### Fixed

- The item search shows the names your game actually uses. The index had been
  built from the German name file while the server and the client use the
  English one, so nothing you saw in game matched what the panel offered.
- Searching for several words now needs all of them. "Full Moon Sword" no
  longer offers Half Moon Sword as well.
- Item numbers work in the search box, with or without the `#`. Typing `299`
  or `#299` finds that item, and `29` offers everything starting with it.

### Added

- **Show more** at the bottom of the item list, which used to stop at forty
  without saying so.
- German and Turkish item names are search keywords, so an item can be found
  by whichever of the three names you know.

---

## 1.6.0 — 2026-08-10

### Added

- Game master ranks on the player page. Pick a rank to give somebody the
  in-game admin commands, or set them back to a normal player. Granting takes
  effect immediately, even mid-game; taking a rank away applies at the player's
  next login.

### Fixed

- The game cores' admin interface had no password, which made an empty one
  correct. It now gets a generated password, like the others.
- The tutorial no longer claims that teleport and running speed do not work.

---

## 1.5.0 — 2026-08-10

### Changed

- Updating no longer asks for the address players connect to, or for your
  domain name. Both are kept from your settings. The address is only asked
  about when this machine has moved to a different one.

### Added

- `--no-domain`, to drop the domain a server was set up with and go back to
  plain HTTP on its address.

---

## 1.4.1 — 2026-08-10

### Fixed

- Running speed can be changed back. "Normal (reset)" resets it, a slower
  setting is slower than a faster one, and characters that were sped up before
  this version are put right the next time you set their speed.

---

## 1.4.0 — 2026-08-10

### Added

- The panel's in-game actions work. Items, yang and levels reach a character
  who is logged in straight away instead of at their next login, and teleport
  and running speed work at all.

Set `M2_INGAME_HELPER=0` in `.env` to leave the helper out.

---

## 1.3.4 — 2026-08-10

### Fixed

- The in-game helper no longer crashes the channel it runs on. It is still not
  installed by default — nobody has played on the fix yet.

---

## 1.3.3 — 2026-08-10

### Fixed

- The item search works in English and Turkish. It matched the whole box
  against German names, so "Full Moon Sword" found nothing while
  "Vollmondschwert" worked. It now matches word by word, translates the common
  ones, and ranks by how many words fit.

---

## 1.3.2 — 2026-08-10

### Fixed

- The in-game helper introduced in 1.3.0 disconnects the character it acts on.
  It is no longer installed. Items, yang and levels work as they did before
  1.3.0 — written to the account, visible at the next login — and teleport and
  running speed refuse instead of dropping the player.

**If you are on 1.3.0 or 1.3.1, update.** Until you do, avoid the buttons on a
character's page while somebody is playing on them.

---

## 1.3.1 — 2026-08-10

### Fixed

- Updating a server never picked up changes to the game itself. The source was
  staged once and reused, so the rebuild produced the same binaries and every
  C++ change since the install was dropped. If you updated to 1.3.0 and teleport
  still does not work, update again.

---

## 1.3.0 — 2026-08-10

### Added

- Teleport and running speed work. The helper that carries them out is now
  built and installed with the server.
- Items, yang and levels reach a character who is logged in straight away,
  instead of at their next login.

### Fixed

- On a Linux host the game could start with no quests loaded at all, and still
  report itself healthy. Staged quest files could carry permissions the server
  account could not read.

### Security

- The database function the helper uses accepts only statements against the
  panel's own queue table.

---

## 1.2.3 — 2026-08-10

### Fixed

- The update command shown in the panel now includes the options the server was
  installed with, such as `--domain` and `--email`. It previously showed the
  bare one-liner, which on the next update would have dropped the certificate.

---

## 1.2.2 — 2026-08-10

### Fixed

- Giving an item, yang or a level to a character who is logged in no longer
  claims they were not in game. It says the change was written to the account
  and appears at their next login.
- Teleport and running speed now say that nothing in the game answered and
  that nothing was changed, instead of suggesting the game server might be
  down.

---

## 1.2.1 — 2026-08-10

### Changed

- Removed the paragraph about the deleted `admin` and `test` accounts from the
  panel's introduction.

---

## 1.2.0 — 2026-08-10

### Changed

- The patch log splits the changelog at the version you are running: "What an
  update would bring" lists only the releases you do not have yet, and "What
  you are running" the rest. No release appears in both.

---

## 1.1.9 — 2026-08-10

### Changed

- The patch log shows the changelog once. When an update was available it was
  printed twice, under two headings, with the same releases in both.

---

## 1.1.8 — 2026-08-10

### Changed

- The update page now says to re-run the command that installed the server,
  and what an update leaves alone, instead of explaining a setting that is off.

---

## 1.1.7 — 2026-08-10

### Changed

- Removed the heading above the changelog on the patch log page. The file
  brings its own, so there were two.

---

## 1.1.6 — 2026-08-10

### Added

- A "Check for the latest version" button on the patch log page. It asks
  straight away instead of waiting for the daily check.

---

## 1.1.5 — 2026-08-10

### Changed

- The installer now ends with the panel address and the game, instead of
  opening with them and scrolling them off the screen.
- A local Windows install no longer prints an admin passphrase. The panel does
  not ask for one there.
- Removed the note about the shipped `admin` and `test` accounts. They are
  deleted during setup.

---

## 1.1.4 — 2026-08-10

### Fixed

- The Windows installer now updates an existing server as well, and shows the
  installed and published versions before asking. Re-running it previously
  re-applied the settings and restarted without fetching anything.

---

## 1.1.3 — 2026-08-10

### Changed

- When a server is already installed, the installer shows which version it is
  on and which one is published, then asks whether to update or to only
  re-apply the settings and restart.
- A server installed before versions existed is recognised as such and offered
  the update.

---

## 1.1.2 — 2026-08-10

### Fixed

- Re-running the installer on a server that was already installed now updates
  it. It used to rewrite the settings and restart the containers without
  fetching anything, so the server stayed on the version it was installed with.

---

## 1.1.1 — 2026-08-10

### Changed

- The patch log has its own card in the admin area, with a button. It used to
  be a grey line at the bottom of the page.
- The card highlights itself when a newer version is available.
- The front page shows the version number only. The patch log and the update
  notice are in the admin area.
- Shorter wording when the update check cannot reach the server.

---

## 1.1.0 — 2026-08-09

### Added

- A `VERSION` file and this changelog.
- An update check in the admin panel. It compares your version against the
  published one roughly once a day and tells you when a newer one exists, with
  the release notes for it. Can be switched off with `M2_UPDATE_CHECK=0`.
- A patch-log page in the panel, showing this file.
- The command that updates your server, shown on that page. The installer
  records it, so the panel shows the exact line for your install. Re-running
  the installer pulls the published version, rebuilds and restarts, and keeps
  your database, passwords and settings.
- A one-click update button on Linux. Off by default; see
  [UPDATING.md](UPDATING.md) to turn it on. On Windows the panel shows the
  command to paste instead.

---

## 1.0.0 — 2026-08-09

First public release. A Metin2 r40250 server that runs on ordinary Linux — or
on a Windows PC, for one person — installed with a single command.

### Added

- The Linux port, as a 109 KB patch over 28 files. A fresh copy of the upstream
  package plus this patch reproduces the running server byte for byte.
- Docker packaging: game cores, MariaDB, the admin panel and a client builder,
  in one compose stack.
- One-command installers for Linux and Windows. The Linux one publishes the
  game ports and opens the firewall; the Windows one binds everything to
  `127.0.0.1` and creates no firewall rule.
- The admin panel — English, German and Turkish. Server rates, giving items and
  yang, levels, password-reset links, registration and a client download.
- A client builder that patches the game to point at your server and offers it
  for download. On a Windows install it unpacks the game and puts a
  `Metin2 Singleplayer` shortcut on the Desktop.

### Fixed

- The client download returned 500 on every request. The panel could not create
  the file it counts downloads in.
- The panel showed a running server as offline, and the player count stayed at
  zero.
- "Give 1000 potions" silently gave 255.
- Items were refused to characters that had room for them: the panel searched
  one inventory page of 45 where r40250 gives four.
- The dashboard reported "the database cannot be reached" for a character who
  had never played.
- The game archive was downloaded twice, once for the server and once for the
  client.
- A path containing a space broke the client build.

### Security

- The shipped `admin` and `test` accounts are deleted during setup, together
  with their game-master entry.
- Download limits: three per address per day, plus a server-wide daily ceiling.
- Rate limits on registration, account login and the admin passphrase.
- Passwords are generated on the machine at install time.
