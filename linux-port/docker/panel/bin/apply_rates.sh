#!/bin/sh
# =============================================================================
#  apply_rates.sh -- the panel's rate applier, container edition.
#
#  The panel runs this the moment somebody saves the "Server rates" page
#  (M2PANEL_RATES_SCRIPT points at it) and does not wait for it, exactly as it
#  does on FreeBSD.  What it does is completely different, because it has to be:
#
#    FreeBSD    one machine. The script rewrites the game's data tables itself
#               and restarts the cores.
#    Docker     two containers. This one cannot see the game's files and cannot
#               signal its processes, so all it does is leave a request in a
#               directory both containers share, and say "running". The game
#               container's supervisor picks it up within five seconds, applies
#               the rates with the very same server-files profile, restarts the
#               cores in the proper order, and writes the answer back into the
#               status file the panel reads.
#
#  So this script is deliberately tiny: validate, write the request, report,
#  return. Everything that can take a minute happens on the other side.
#
#  It can also be run by hand to re-send the rates that are in the database:
#      docker compose exec panel sh /usr/local/bin/apply_rates.sh
# =============================================================================
set -u
LC_ALL=C
export LC_ALL

SPOOL="${M2PANEL_RATES_SPOOL:-/opt/m2spool}"
STATUS="${M2PANEL_RATES_STATUS:-$SPOOL/rates.status}"
REQUEST="$SPOOL/request"
CONF_FILE="${M2PANEL_CONF:-/usr/local/etc/m2panel.conf}"
LOG="${M2PANEL_RATES_LOG:-/tmp/m2rates.log}"

# Everything created here has to be readable by the game container's account,
# which shares the m2spool group with this one but is not the same user.
umask 007

RATE_EXP=100
RATE_DROP=100
RATE_YANG=100

log() { printf '%s %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')" "$1" >> "$LOG" 2>/dev/null; }

# set_status STATE MESSAGE -- the file the panel reads back. Same key=value
# shape the FreeBSD script writes, because the panel's reader is the same.
set_status() {
    ( umask 007
      { printf 'state=%s\n' "$1"
        printf 'time=%s\n' "$(date +%s 2>/dev/null)"
        printf 'exp=%s\ndrop=%s\nyang=%s\n' "$RATE_EXP" "$RATE_DROP" "$RATE_YANG"
        printf 'message=%s\n' "$2"
      } > "$STATUS.new" ) && mv "$STATUS.new" "$STATUS"
    return 0
}

sane_rate() {
    _v="${1:-}"
    case "${_v:-x}" in ''|*[!0-9]*) _v=100 ;; esac
    [ "$_v" -lt 1 ] && _v=100
    [ "$_v" -gt 10000 ] && _v=10000
    printf '%s' "$_v"
}

# ---------------- 1. is the other end even there? ----------------
# Without the shared volume nothing this script writes could ever be read, and
# saying "running" then would be a lie the page never recovers from.
if [ ! -d "$SPOOL" ] || [ ! -w "$SPOOL" ]; then
    log "the rate spool $SPOOL is missing or not writable"
    set_status failed "the shared rates directory is not mounted in this container"
    exit 1
fi

# ---------------- 2. what the owner asked for ----------------
# The panel has just written the three numbers into player.web_admin_rates;
# they are read back out of it rather than passed as arguments because the
# panel starts this script with no arguments at all -- on FreeBSD too.
#
# PyMySQL and the panel's own configuration file are used, so this connects
# exactly the way the panel itself does, with or without M2_DB_* in the
# environment.
VALUES=$(python3 - "$CONF_FILE" <<'PYEOF' 2>>"$LOG"
import json, os, sys

conf = {}
try:
    with open(sys.argv[1]) as f:
        conf = json.load(f)
except Exception:
    pass

host = os.environ.get("M2_DB_HOST") or conf.get("db_host") or "127.0.0.1"
user = os.environ.get("M2_DB_USER") or conf.get("db_user") or "root"
pw   = os.environ.get("M2_DB_PASSWORD") or conf.get("db_pass") or ""
port = int(os.environ.get("M2_DB_PORT") or 3306)

import pymysql
c = pymysql.connect(host=host, port=port, user=user, password=pw,
                    charset="latin1", autocommit=True)
with c.cursor() as cur:
    cur.execute("SELECT name, value FROM player.web_admin_rates")
    got = {str(n): int(v) for n, v in cur.fetchall()}
print("%d %d %d" % (got.get("exp", 100), got.get("drop", 100), got.get("yang", 100)))
PYEOF
)

if [ -z "$VALUES" ]; then
    log "could not read player.web_admin_rates"
    set_status failed "the rates could not be read out of the database"
    exit 1
fi

RATE_EXP=$(sane_rate  "$(echo "$VALUES" | awk '{print $1}')")
RATE_DROP=$(sane_rate "$(echo "$VALUES" | awk '{print $2}')")
RATE_YANG=$(sane_rate "$(echo "$VALUES" | awk '{print $3}')")

# ---------------- 3. hand it over ----------------
# The id is what stops the same change being applied twice: the game container
# remembers the last id it dealt with. Seconds plus the pid is enough -- two
# saves cannot share both.
REQ_ID="$(date +%s 2>/dev/null)-$$"

if ! { printf 'id=%s\n' "$REQ_ID"
       printf 'exp=%s\ndrop=%s\nyang=%s\n' "$RATE_EXP" "$RATE_DROP" "$RATE_YANG"
       printf 'time=%s\n' "$(date +%s 2>/dev/null)"
     } > "$REQUEST.new" 2>>"$LOG"; then
    rm -f "$REQUEST.new"
    log "could not write $REQUEST"
    set_status failed "the request could not be written to the shared directory"
    exit 1
fi
chmod 0660 "$REQUEST.new" 2>/dev/null
mv "$REQUEST.new" "$REQUEST" || {
    rm -f "$REQUEST.new"
    log "could not move the request into place"
    set_status failed "the request could not be written to the shared directory"
    exit 1
}

log "requested experience ${RATE_EXP}%, item drops ${RATE_DROP}%, yang ${RATE_YANG}% (id $REQ_ID)"

# True at this moment and until the game container says otherwise: the change
# has been handed over and the server is about to restart. If the game
# container is down, this is also the honest answer -- it is queued, and it
# will be applied when that container comes back.
set_status running "the rates are being applied"
exit 0
