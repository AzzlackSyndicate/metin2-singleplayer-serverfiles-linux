#!/bin/bash
# =============================================================================
#  Container entrypoint for the Metin2 r40250 game server.
#
#  Runs as root, does the three things that need root or need doing exactly
#  once per container start, then hands over to the supervisor as an
#  unprivileged user:
#
#     1. wait for MariaDB (the db core asserts on a missing schema and, being
#        built without -DNDEBUG, aborts rather than warns)
#     2. materialise the core tree on the state volume and render every
#        CONFIG / conf.txt from the environment
#     3. drop privileges and exec the supervisor
#
#  Every knob is an environment variable; nothing is baked into the image.
# =============================================================================
set -euo pipefail

log() { printf '%s [entrypoint] %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')" "$*"; }
die() { printf '%s [entrypoint] FATAL: %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')" "$*" >&2; exit 1; }

SHARE_DIR="${M2_SHARE_DIR:-/opt/metin2/share}"
VAR_DIR="${M2_VAR_DIR:-/opt/metin2/var}"

# -----------------------------------------------------------------------------
# Configuration contract.  See .env.example for the operator-facing version.
# -----------------------------------------------------------------------------
M2_DB_HOST="${M2_DB_HOST:-db}"
M2_DB_PORT="${M2_DB_PORT:-3306}"
M2_DB_USER="${M2_DB_USER:-metin2}"
M2_DB_PASSWORD="${M2_DB_PASSWORD:-}"

M2_CHANNELS="${M2_CHANNELS:-1}"
M2_AUTH_PORT="${M2_AUTH_PORT:-11000}"
M2_AUTH_P2P_PORT="${M2_AUTH_P2P_PORT:-12000}"
M2_DB_CORE_PORT="${M2_DB_CORE_PORT:-15000}"

M2_PUBLIC_ADDRESS="${M2_PUBLIC_ADDRESS:-}"
M2_BIND_IP="${M2_BIND_IP:-0.0.0.0}"

M2_LOG_KEEP_DAYS="${M2_LOG_KEEP_DAYS:-3}"
M2_TABLE_POSTFIX="${M2_TABLE_POSTFIX:-}"
M2_MAX_LEVEL="${M2_MAX_LEVEL:-105}"
M2_PLAYER_DELETE_LEVEL_LIMIT="${M2_PLAYER_DELETE_LEVEL_LIMIT:-70}"
M2_TEST_SERVER="${M2_TEST_SERVER:-0}"
M2_MALL_URL="${M2_MALL_URL:-}"
M2_ADMINPAGE_PASSWORD="${M2_ADMINPAGE_PASSWORD:-}"
M2_ADMINPAGE_IP="${M2_ADMINPAGE_IP:-127.0.0.1}"

# Where the panel leaves a rate change and picks the answer up. A named volume
# mounted into this container and into the panel's; see docker-compose.yml.
M2_RATES_SPOOL="${M2_RATES_SPOOL:-/opt/m2spool}"

M2_RUN_AS_ROOT="${M2_RUN_AS_ROOT:-0}"
M2_CORE_DUMPS="${M2_CORE_DUMPS:-0}"
M2_DB_WAIT_TIMEOUT="${M2_DB_WAIT_TIMEOUT:-180}"

export M2_SHARE_DIR="$SHARE_DIR" M2_VAR_DIR="$VAR_DIR"
export M2_DB_HOST M2_DB_PORT M2_DB_USER M2_DB_PASSWORD
export M2_CHANNELS M2_AUTH_PORT M2_AUTH_P2P_PORT M2_DB_CORE_PORT
export M2_PUBLIC_ADDRESS M2_BIND_IP M2_LOG_KEEP_DAYS
export M2_TABLE_POSTFIX M2_MAX_LEVEL M2_PLAYER_DELETE_LEVEL_LIMIT
export M2_TEST_SERVER M2_MALL_URL M2_ADMINPAGE_PASSWORD M2_ADMINPAGE_IP
export M2_RATES_SPOOL

# -----------------------------------------------------------------------------
# 0. Validate.  Fail loudly here rather than let a core abort ten seconds later
#    with a message only someone who has read config.cpp can interpret.
# -----------------------------------------------------------------------------
[ -n "$M2_DB_PASSWORD" ] || die "M2_DB_PASSWORD is empty. Set it in .env (see .env.example)."

# db/src/Config.cpp reads conf.txt words into a 256-byte buffer with GetWord(),
# which has no bounds check whatsoever -- an over-long value is a stack smash,
# not an error message.  The whole SQL_* line must fit, so the password is
# capped well below that.
[ "${#M2_DB_PASSWORD}" -le 120 ] \
  || die "M2_DB_PASSWORD is ${#M2_DB_PASSWORD} characters. Keep it under 120: the db core's config parser writes into a fixed 256-byte buffer without bounds checking."

# The password is written into CONFIG files as a whitespace-separated field.
case "$M2_DB_PASSWORD" in
  *[[:space:]]*|*'"'*) die 'M2_DB_PASSWORD must not contain whitespace or double quotes: the CONFIG and conf.txt parsers split on exactly those.' ;;
esac

case "$M2_CHANNELS" in
  1|2|3|4) : ;;
  *) die "M2_CHANNELS must be 1..4 (got '$M2_CHANNELS'). Channel N uses ports 13000+10*(N-1) .. +2." ;;
esac

if [ -z "$M2_PUBLIC_ADDRESS" ]; then
  log "WARNING: M2_PUBLIC_ADDRESS is not set."
  log "         The cores will advertise their container-internal address, which"
  log "         no client outside this host can reach. Players will reach the"
  log "         login server and then fail to enter the world. Set it in .env."
fi

# -----------------------------------------------------------------------------
# 1. Wait for MariaDB.
#
#    Not a nicety: every core opens its SQL handles during boot, and db/ is
#    compiled without -DNDEBUG, so a missing table is an assert() and an abort,
#    not a warning.  Starting into a half-initialised database produces a crash
#    that looks like a code fault.  bash's /dev/tcp is used so the runtime image
#    needs no netcat and no mysql client.
# -----------------------------------------------------------------------------
log "waiting for MariaDB at ${M2_DB_HOST}:${M2_DB_PORT} (timeout ${M2_DB_WAIT_TIMEOUT}s)"
deadline=$(( SECONDS + M2_DB_WAIT_TIMEOUT ))
until (exec 3<>"/dev/tcp/${M2_DB_HOST}/${M2_DB_PORT}") 2>/dev/null; do
  [ "$SECONDS" -lt "$deadline" ] || die "MariaDB at ${M2_DB_HOST}:${M2_DB_PORT} did not become reachable in ${M2_DB_WAIT_TIMEOUT}s"
  sleep 2
done
log "MariaDB is reachable"

# -----------------------------------------------------------------------------
# 2. Materialise the core tree and render the configuration.
# -----------------------------------------------------------------------------
m2-render-config

# -----------------------------------------------------------------------------
# 2b. Server-wide rates: the two things that need root.
#
#     The spool directory shared with the panel container has to be usable by
#     both service accounts, and the handful of data tables the rates rewrite
#     have to be writable by the unprivileged account the supervisor runs as.
#     Everything else about the rates happens later, in m2-supervise.
# -----------------------------------------------------------------------------
m2-rates prepare || log "could not prepare the rate spool (the rates page will say so)"

# -----------------------------------------------------------------------------
# 3. Resource limits.
#
#    Core dumps are off by default: a game core is 250-320 MB resident, so a
#    crash loop fills a VPS disk in minutes.  Turn them on deliberately when
#    chasing a crash -- cores land in <core>/cores/ on the state volume.
# -----------------------------------------------------------------------------
if [ "$M2_CORE_DUMPS" = "1" ]; then
  ulimit -c unlimited || log "could not raise core dump limit"
  log "core dumps ENABLED (<core>/cores/)"
else
  ulimit -c 0 || true
fi

# -----------------------------------------------------------------------------
# 4. Ownership, then drop privileges.
# -----------------------------------------------------------------------------
if [ "$(id -u)" = "0" ]; then
  chown -R metin2:metin2 "$VAR_DIR" 2>/dev/null || log "could not chown $VAR_DIR (continuing)"

  if [ "$M2_RUN_AS_ROOT" = "1" ]; then
    log "running as root (M2_RUN_AS_ROOT=1)"
    exec "$@"
  fi

  log "dropping privileges to metin2 and starting: $*"
  exec setpriv --reuid=metin2 --regid=metin2 --init-groups --inh-caps=-all -- "$@"
fi

log "starting: $*"
exec "$@"
