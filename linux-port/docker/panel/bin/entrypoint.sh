#!/bin/bash
# =============================================================================
#  Admin panel entrypoint.
#
#  Does the three things the panel cannot do for itself in a container:
#    1. generate /usr/local/etc/m2panel.conf on FIRST RUN ONLY
#    2. wait for MariaDB and apply the panel's own tables
#    3. serve the app through waitress
#
#  No secret is baked into the image.  The admin passphrase is either supplied
#  through the environment or generated here and printed once, loudly.
# =============================================================================
set -euo pipefail

log() { printf '%s [panel] %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')" "$*"; }
die() { printf '%s [panel] FATAL: %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')" "$*" >&2; exit 1; }

CONF_PATH="${M2_PANEL_CONF:-/usr/local/etc/m2panel.conf}"
DATA_DIR="${M2_PANEL_DATA:-/usr/local/m2panel}"

M2_DB_HOST="${M2_DB_HOST:-db}"
M2_DB_PORT="${M2_DB_PORT:-3306}"
M2_DB_USER="${M2_DB_USER:-metin2}"
M2_DB_PASSWORD="${M2_DB_PASSWORD:-}"

M2_PANEL_PORT="${M2_PANEL_PORT:-7788}"
M2_PANEL_BIND="${M2_PANEL_BIND:-0.0.0.0}"
M2_PANEL_PASSWORD="${M2_PANEL_PASSWORD:-}"
M2_PANEL_THREADS="${M2_PANEL_THREADS:-8}"

M2_BRAND="${M2_BRAND:-}"
M2_CLIENT_NAME="${M2_CLIENT_NAME:-}"
M2_CLIENT_URL="${M2_CLIENT_URL:-}"
# r40250 gives a character FOUR inventory pages of 45, not one. At 45 the panel
# searches only the first page for a free slot and refuses to hand an item to
# anyone whose first page happens to be full.
M2_INVENTORY_SLOTS="${M2_INVENTORY_SLOTS:-180}"
# player.item.count is TINYINT UNSIGNED, so 255 is the real ceiling. The panel
# falls back to 65535 when this is not in its config, and MySQL then truncates
# without complaining -- "give 1000 potions" silently becomes 255.
M2_MAX_ITEM_COUNT="${M2_MAX_ITEM_COUNT:-255}"
# Whether this server is reachable only from the machine it runs on. The panel
# cannot work this out for itself -- a public Linux server behind nginx also
# binds the panel to 127.0.0.1 -- so the installer says so, and the panel then
# tells the operator that nobody else can join instead of telling them to hand
# the address out. Default "no", which is right for every server install.
M2_LOCAL_ONLY="${M2_LOCAL_ONLY:-0}"
M2_PANEL_STATUS_PORTS="${M2_PANEL_STATUS_PORTS:-11000,13000}"
M2_GAME_HOST="${M2_GAME_HOST:-game}"

M2_DB_WAIT_TIMEOUT="${M2_DB_WAIT_TIMEOUT:-180}"

# The directory shared with the game container: this side writes a rate change
# into it, the game container writes back how it went. Both containers mount
# the same named volume here.
RATES_SPOOL="${M2PANEL_RATES_SPOOL:-/opt/m2spool}"

# --skip-ssl is required, not optional.  The MariaDB 11.x client that ships in
# this image enables TLS by default and refuses to connect to a server that
# does not offer it, with:
#     ERROR 2026 (HY000): TLS/SSL error: SSL is required, but the server does
#                         not support it
# The mariadb:10.11 server image ships no certificate, so that is exactly what
# happens.  The connection never leaves the compose bridge network, so plain
# text here is the same trust boundary as the Unix socket would be on a
# single-host install.  (The panel itself uses PyMySQL, which has no such
# default and is unaffected -- this applies only to these CLI calls.)
MARIADB_CLI=(mariadb --skip-ssl -h "$M2_DB_HOST" -P "$M2_DB_PORT" -u "$M2_DB_USER" -p"$M2_DB_PASSWORD")

[ -n "$M2_DB_PASSWORD" ] || die "M2_DB_PASSWORD is empty. Set it in .env (see .env.example)."

mkdir -p "$(dirname "$CONF_PATH")" "$DATA_DIR"

# -----------------------------------------------------------------------------
# The rate spool.
#
# A fresh named volume arrives as an empty root-owned directory, and both
# containers have to be able to write in it as their own unprivileged users.
# setgid + the shared m2spool group is what makes that work: whoever creates a
# file in there, it belongs to the group both accounts are in. Done here, as
# root, because nothing later in this script runs as root.
#
# The game container's entrypoint does the same thing, so whichever of the two
# starts first sets it up and the other agrees with it.
# -----------------------------------------------------------------------------
if [ -d "$RATES_SPOOL" ]; then
  chgrp m2spool "$RATES_SPOOL" 2>/dev/null && chmod 2770 "$RATES_SPOOL" 2>/dev/null \
    || log "WARNING: could not set up $RATES_SPOOL for sharing; the rates page may not work"
else
  log "no rate spool at $RATES_SPOOL -- the rates page will report that the helper"
  log "  cannot reach the game. Mount the rates-spool volume (see docker-compose.yml)."
fi

# -----------------------------------------------------------------------------
# 1. Wait for MariaDB
# -----------------------------------------------------------------------------
log "waiting for MariaDB at ${M2_DB_HOST}:${M2_DB_PORT}"
deadline=$(( SECONDS + M2_DB_WAIT_TIMEOUT ))
until "${MARIADB_CLI[@]}" -e 'SELECT 1' >/dev/null 2>&1; do
  [ "$SECONDS" -lt "$deadline" ] || die "MariaDB did not accept a connection as '${M2_DB_USER}' within ${M2_DB_WAIT_TIMEOUT}s"
  sleep 2
done
log "MariaDB is accepting connections"

# -----------------------------------------------------------------------------
# 2. Panel tables
#
# Applied on EVERY start, not only at database initialisation: the panel must
# also work when pointed at a database that already existed (a migration from
# the FreeBSD/Windows install, or a restored backup), where the MariaDB image's
# one-shot initdb hook never runs.  Every statement is CREATE TABLE IF NOT
# EXISTS / INSERT IGNORE, so repeating it is free and never touches data.
# -----------------------------------------------------------------------------
M2_PLAYER_DB="${M2_PLAYER_DB:-player}"
if [ -d /opt/panel/schema ] && compgen -G '/opt/panel/schema/*.sql' >/dev/null; then
  # web_admin_schema.sql deliberately never names a schema, so the database is
  # selected here.  The panel's queries are fully qualified as player.web_admin_*
  # regardless, so this must be the same database the game cores use for PLAYER_SQL.
  for f in /opt/panel/schema/*.sql; do
    log "applying $(basename "$f") to database '${M2_PLAYER_DB}'"
    "${MARIADB_CLI[@]}" -D "$M2_PLAYER_DB" < "$f" \
      || die "could not apply $(basename "$f")"
  done
else
  log "WARNING: no schema files found. The panel needs player.web_admin_queue and"
  log "         player.web_admin_rates; without them its action queue and rate"
  log "         badges will fail."
fi

# -----------------------------------------------------------------------------
# 3. Configuration -- generated once, then left alone forever.
#
# m2panel.conf holds the PBKDF2 hash of the admin passphrase, the per-install
# salt and the Flask session secret.  Regenerating any of those on restart
# would invalidate every session cookie and, if the passphrase came from a
# generated default, change the password out from under the operator.  So the
# file is written only when it does not exist.
# -----------------------------------------------------------------------------
if [ -f "$CONF_PATH" ]; then
  log "using existing config at $CONF_PATH (not regenerated)"
  if [ -n "$M2_PANEL_PASSWORD" ]; then
    log "note: M2_PANEL_PASSWORD is set but the config already exists, so it is"
    log "      ignored. To change the passphrase: delete the file and restart --"
    log "      docker compose run --rm -T panel rm -f $CONF_PATH"
  fi
else
  GENERATED=0
  if [ -z "$M2_PANEL_PASSWORD" ]; then
    M2_PANEL_PASSWORD="$(python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(20)))')"
    GENERATED=1
  fi

  log "first run: generating $CONF_PATH"

  M2_PANEL_PASSWORD="$M2_PANEL_PASSWORD" \
  M2_DB_HOST="$M2_DB_HOST" M2_DB_USER="$M2_DB_USER" M2_DB_PASSWORD="$M2_DB_PASSWORD" \
  M2_PANEL_BIND="$M2_PANEL_BIND" M2_PANEL_PORT="$M2_PANEL_PORT" \
  M2_BRAND="$M2_BRAND" M2_CLIENT_NAME="$M2_CLIENT_NAME" M2_CLIENT_URL="$M2_CLIENT_URL" \
  M2_INVENTORY_SLOTS="$M2_INVENTORY_SLOTS" M2_PANEL_STATUS_PORTS="$M2_PANEL_STATUS_PORTS" \
  M2_MAX_ITEM_COUNT="$M2_MAX_ITEM_COUNT" M2_LOCAL_ONLY="$M2_LOCAL_ONLY" \
  M2_GAME_HOST="$M2_GAME_HOST" \
  CONF_PATH="$CONF_PATH" \
  python3 <<'PY'
import hashlib, json, os, secrets

# The panel verifies with:
#   hashlib.pbkdf2_hmac("sha256", passphrase, salt, 200_000).hex()
# compared against conf["pass_hash"], salt taken from conf["salt"].
# Those parameters are the panel's, not ours -- they must match exactly.
salt = secrets.token_hex(16)
pw   = os.environ["M2_PANEL_PASSWORD"]
ph   = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()

ports = [int(p) for p in os.environ["M2_PANEL_STATUS_PORTS"].replace(",", " ").split() if p.strip()]

conf = {
    "flask_secret": secrets.token_hex(32),
    "salt":         salt,
    "pass_hash":    ph,

    "db_host":      os.environ["M2_DB_HOST"],
    "db_user":      os.environ["M2_DB_USER"],
    "db_pass":      os.environ["M2_DB_PASSWORD"],

    "bind":         os.environ["M2_PANEL_BIND"],
    "port":           int(os.environ["M2_PANEL_PORT"]),
    "status_ports":   ports,
    "max_item_count": int(os.environ["M2_MAX_ITEM_COUNT"]),
    "local_only":     os.environ["M2_LOCAL_ONLY"] in ("1", "true", "yes", "on"),

    # Consumed by the portability pass on server_status(): in a container the
    # game's sockets are in a different network namespace, so `sockstat' can
    # never see them and a TCP probe of these host:ports is the only way to
    # answer "is the server up".
    "game_host":    os.environ["M2_GAME_HOST"],

    "inventory_slots": int(os.environ["M2_INVENTORY_SLOTS"]),
}
for key, env in (("brand", "M2_BRAND"), ("client_name", "M2_CLIENT_NAME"), ("client_url", "M2_CLIENT_URL")):
    if os.environ.get(env):
        conf[key] = os.environ[env]

path = os.environ["CONF_PATH"]
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(conf, f, indent=2)
    f.write("\n")
PY

  chmod 0600 "$CONF_PATH"

  if [ "$GENERATED" = "1" ]; then
    echo
    echo "==============================================================="
    echo "  ADMIN PANEL PASSWORD (generated -- shown once, save it now)"
    echo
    echo "      $M2_PANEL_PASSWORD"
    echo
    echo "  Set M2_PANEL_PASSWORD in .env to choose your own instead."
    echo "  To change it later: delete $CONF_PATH and restart the panel."
    echo "==============================================================="
    echo
  else
    log "admin passphrase taken from M2_PANEL_PASSWORD"
  fi
fi

# The panel derives its client-download page from this file; it is on the data
# volume and is normally absent until the operator uploads a client.
if [ -f "$DATA_DIR/client.zip" ]; then
  log "client.zip present ($(du -h "$DATA_DIR/client.zip" | cut -f1))"
else
  log "no client.zip in $DATA_DIR -- the client download page will be empty."
  log "  Put one there with: docker compose cp ./client.zip panel:$DATA_DIR/client.zip"
fi

# -----------------------------------------------------------------------------
# 4. Serve.
#
# waitress rather than app.run(): Flask's development server is single-threaded
# by default and explicitly not for production, and this panel streams a
# multi-gigabyte client download -- one download would block every other
# request, including the operator's.
# -----------------------------------------------------------------------------
cd /opt/panel

# The config is written by root at 0600; the app runs as `panel' and has to be
# able to read it (and to write downloads.db next to client.zip).
chown panel:panel "$CONF_PATH" 2>/dev/null || true
chown -R panel:panel "$DATA_DIR" 2>/dev/null || true

log "serving on ${M2_PANEL_BIND}:${M2_PANEL_PORT} with ${M2_PANEL_THREADS} threads"

exec setpriv --reuid=panel --regid=panel --init-groups --inh-caps=-all -- \
  python3 -m waitress \
    --host="$M2_PANEL_BIND" \
    --port="$M2_PANEL_PORT" \
    --threads="$M2_PANEL_THREADS" \
    admin_panel:app
