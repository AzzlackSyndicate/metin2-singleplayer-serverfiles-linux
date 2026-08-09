# Updating

Nothing in this document touches your database. Accounts, characters, items,
guilds and safeboxes live in a Docker volume, and no step here goes near it.
The only command that would destroy them is `docker compose down -v`, and it
appears nowhere in this project.

There are two ways to update: by hand, which is the one to use, and from the
panel, which is off by default and which you should read the last section of
before switching on.

---

## Which version am I running?

Log into the admin panel. The version is at the bottom of every admin page, and
it is a link to the patch log — the changelog for the build you are running,
and, when there is a newer one, the changelog for that.

If it says **unknown**, this build has no `VERSION` file in it: a development
checkout, or an image built from a context that was staged by hand. The panel
will not guess a version and will not tell you that you are up to date when it
cannot know.

On the machine itself:

```sh
cat /var/cache/m2src/repo/VERSION      # what the checkout has
docker compose exec panel cat /opt/panel/VERSION   # what is actually running
```

Those two can differ: the first is what you would get, the second is what you
have.

---

## Updating by hand

On the server, as root (or with `sudo`). This is the whole thing — paste it in
one go:

```sh
REPO=/var/cache/m2src/repo
STACK=/opt/metin2/stack

git -C "$REPO" fetch --depth 1 origin main
git -C "$REPO" reset --hard FETCH_HEAD
sh "$REPO/linux-port/fetch-sources.sh" fetch
(cd "$REPO/linux-port/docker" && tar cf - .) | (cd "$STACK" && tar xf -)
cd "$STACK" && docker compose up -d --build
```

Those two paths are what `install.sh` uses. If you installed somewhere else,
use your own — `docker compose ls` shows where the stack lives.

What each line does:

1. **`git fetch` / `reset --hard`** — brings the checkout to the published
   version. The checkout is a copy of the repository; nothing local is ever
   committed into it, so there is nothing to merge and nothing to lose.
2. **`fetch-sources.sh fetch`** — re-stages the Docker build context from the
   refreshed checkout. It skips everything it has already done, so on an
   installed machine this takes seconds and downloads nothing: the upstream
   r40250 package is already in the cache.
3. **`tar`** — copies the new context over the installed stack. `tar` writes
   the files it carries and deletes nothing else, which is exactly why it is
   used here: your `.env` and your `docker-compose.override.yml` are not in the
   context, so they are not overwritten. Your passwords, ports and addresses
   survive.
4. **`docker compose up -d --build`** — rebuilds the images that changed and
   recreates the containers that changed. Anything unchanged is left running.

Players are disconnected while the game restarts. Most updates take two or
three minutes. An update that changes the Linux port itself recompiles the
game, which takes considerably longer — ten to forty minutes depending on the
machine. The panel's patch log tells you which kind you are getting before you
start.

### If the port patch changed

`fetch-sources.sh` reuses the patched source tree it staged last time. When an
update changes `linux-port/patches/*.patch`, that tree is stale and has to be
built again from the new patch:

```sh
sh "$REPO/linux-port/fetch-sources.sh" fetch --force restage
```

The panel's updater does this check itself. By hand, if you are unsure, running
it with `--force restage` is never wrong — only slower.

### If something goes wrong

Nothing is deleted at any point, so there is nothing to undo. The old images
are still on the machine until Docker prunes them, and the database was never
touched.

```sh
cd /opt/metin2/stack
docker compose ps                 # what is up
docker compose logs game --tail 80
docker compose logs panel --tail 40
```

To go back to a version that worked, check the old commit out and run the same
five lines again:

```sh
git -C /var/cache/m2src/repo fetch --depth 50 origin main
git -C /var/cache/m2src/repo checkout <commit>
```

---

## Updating from the panel

**This is off, and on a server other people can reach it should stay off.**
Read the trade-off at the end of this section before you switch it on.

### Turning it on

Two switches, both required, on purpose.

1. In `/opt/metin2/stack/.env`:

   ```
   M2_UPDATE_APPLY=1
   ```

2. Start the updater — it is in a compose profile, so `docker compose up -d`
   never starts it:

   ```sh
   cd /opt/metin2/stack
   docker compose --profile update up -d updater
   docker compose up -d panel          # picks up M2_UPDATE_APPLY
   ```

Check it is happy before you rely on it:

```sh
docker compose exec updater m2-updater selftest
```

That changes nothing. It reports whether it can see the Docker socket, the
checkout, and the installed stack, and it names whatever is missing.

An **Install it from here** button now appears on the panel's patch-log page —
but only while all three of these are true: the setting is on, the updater is
running, and there is actually a newer version. When the setting is on but the
updater is not running, the page says so and gives you the command, rather than
showing a button that would do nothing.

### Turning it off again

```sh
cd /opt/metin2/stack
docker compose stop updater && docker compose rm -f updater
# then set M2_UPDATE_APPLY=0 in .env and: docker compose up -d panel
```

### What it actually does

The panel cannot update anything. It writes a small file into a directory it
shares with the updater:

```
id=1754790000-31
version=1.2.0
time=1754790000
```

That is the entire message, and the updater reads exactly two things out of it:
the `id`, which is only used to tell a new request from one it has already
carried out, and the `version`, which it prints in the log and never acts on.
There is no field for a command, a path, a URL, a branch or an image name, and
nothing from that file is ever passed to a shell. A panel that has been
completely taken over can make the updater update the server, repeatedly — and
nothing else.

The updater then runs the same five steps as the manual procedure above, in the
same order, and writes its progress back into the shared directory so the panel
can show it. The script is
[`linux-port/docker/updater/bin/m2-updater`](linux-port/docker/updater/bin/m2-updater);
it is short enough to read in one sitting, and that is the point of it.

It never runs `docker compose down`, in any form. The word does not appear in
the file.

### The security trade-off, plainly

Updating means rebuilding images and recreating containers, and that needs the
host's Docker daemon. **Access to the Docker socket is root on the host** — not
"almost root", not "root in a container". Anything that can talk to it can
start a container with the host's entire filesystem mounted inside it and read
or change anything on the machine.

So the question is not whether that power is used — an update needs it — but
which process holds it.

- **The panel must not.** It is the one process here that faces the internet.
  It accepts registrations from strangers, serves a download, sends password
  reset links and renders HTML. Every one of those is an attack surface, and a
  bug in any of them would become a full host takeover the moment the panel
  holds the socket.
- **The updater may.** It is one shell script with one job. It has no network
  service, no port, no login, nothing that a stranger can reach and nothing to
  submit to it. The only way in is the request file, which cannot carry an
  instruction. If it is compromised, it is because the host already was.

That is the whole design, and it is why this feature is two containers rather
than four lines in the panel.

What you are accepting when you switch it on:

- **A container on your machine holds the Docker socket, permanently.** It is
  small and it is auditable, but it is there, and a hole in the Docker daemon
  or in that script is a hole in your host.
- **Whoever can log into the panel can restart your server.** That is the
  admin passphrase, and the rate limit on it. On a `--local` install, where the
  panel has no passphrase at all because it is reachable only from the machine
  itself, it means any program running on that computer can do it.
- **You are trusting the published repository.** The updater fetches what is on
  `main` and builds it. That is also true of updating by hand — the difference
  is that by hand you choose the moment and can read the patch log first.

If none of that is comfortable, leave it off. Updating by hand is one paste,
and it is the same five commands.

---

## What an update will never do

- Remove a volume, or run `docker compose down -v`.
- Touch your `.env` or your `docker-compose.override.yml`.
- Change your admin passphrase, your database password, or your Flask session
  secret. They are generated once, on first run, and are never regenerated.
- Delete a client build. `client.zip` is on its own volume and is left alone.

If an update ever needs you to do something by hand — move a setting, run a
command — it is a **MAJOR** version, and [CHANGELOG.md](CHANGELOG.md) says so
at the top of that release, in full, before anything else.

---

## Updating the updater

One wrinkle worth knowing about. The updater is in a compose profile that is
not active during the update it is running — which is deliberate, because
otherwise `docker compose up -d --build` would stop the very container running
the command, halfway through.

The consequence is that the updater does not update itself. It goes on running
the image it was started with until you recreate it:

```sh
cd /opt/metin2/stack
docker compose --profile update up -d --build updater
```

Worth doing after an update that mentions it in the changelog. Otherwise it
does not matter: the script has one job and it does not change often.
