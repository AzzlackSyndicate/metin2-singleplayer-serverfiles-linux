#!/bin/sh
# =============================================================================
#  fetch-web-client.sh -- put the browser client on the panel's volume.
#
#      sh fetch-web-client.sh install --target /var/lib/.../browser
#      sh fetch-web-client.sh status  --target /var/lib/.../browser
#      sh fetch-web-client.sh use v1.10.0 --target /var/lib/.../browser
#
#  The browser client is NOT built here and never will be. Building it needs
#  Emscripten, Go, a CPython of its own and 194,000 lines of C++; it is loaded
#  as two finished archives instead, and this script is the thing that loads
#  them.
#
#  TWO ARCHIVES, BECAUSE THEY CHANGE AT DIFFERENT RATES. Of ten fixes in one
#  day, nine were in the client's code and one was in its data. So:
#
#      Reference_WebClientEngine.zip     17.6 MB
#              index.html, index.js, index.wasm, webfs.js, serve-webfs.py and
#              m2-ws2tcp-* for six platforms. No game data in it, so it is a
#              release asset of this repository -- which is why its URL is
#              derived from the version and never maintained by hand.
#
#      Reference_WebClientData.zip     1749.8 MB
#              419 content-addressed <32 hex>.bin blocks plus manifest.bin,
#              which is their index and therefore belongs with them. Game data,
#              so it is on MEGA like the server files.
#
#  A fix therefore costs 17.6 MB rather than 1.75 GB, and that is the whole
#  point of the split.
#
#  WHAT IT LEAVES BEHIND, and why it is shaped like this:
#
#      <target>/engine-1.11.0/    the engine, exactly as it came out of the zip
#      <target>/data-1.0.0/       the corpus, exactly as it came out of the zip
#      <target>/v1.11.0/          the two of them together -- HARD LINKS, so it
#                                 costs kilobytes -- and this is what is served
#      <target>/current -> v1.11.0
#      <target>/installed.json    what is here, for the panel to read
#
#  The union directory exists because the client resolves every asset against
#  its own page URL and has no base-path knob: manifest.bin and 419 blocks have
#  to be siblings of index.html or the client sees an empty filesystem. Hard
#  links give that for free, and they also make each v<version> self-contained
#  -- deleting an old data-* directory cannot hollow out an old release, so a
#  rollback is one symlink and nothing else.
#
#  NOTHING IS EVER WRITTEN OVER. Every unpack happens beside what is running,
#  is checked, and only then is `current' moved -- a rename(2), which the kernel
#  either does or does not do. There is no instant at which a player can be
#  handed half an engine.
#
#  Exit codes, so a caller can say something useful instead of "it failed":
#      0  installed, or already up to date
#      2  wrong arguments
#      3  a tool is missing (unzip, curl, megatools, sha256sum)
#      4  a download did not succeed
#      5  a downloaded archive has the wrong sha256
#      6  an archive does not contain what it must
#      7  not enough disk space
#      8  the MEGA link for Reference_WebClientData.zip has not been filled in yet
#      9  artifacts.json contradicts itself, or would poison a browser cache
# =============================================================================
set -u
LC_ALL=C
export LC_ALL

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)

POINTER="${M2_WEBCLIENT_POINTER:-$REPO/artifacts.json}"
TARGET="${M2_WEBCLIENT_TARGET:-}"
CACHE="${M2_WEBCLIENT_CACHE:-/var/cache/m2webclient}"
KEEP="${M2_WEBCLIENT_KEEP:-3}"
ENGINE_URL_OVERRIDE="${M2_WEBCLIENT_ENGINE_URL:-}"
DATA_URL_OVERRIDE="${M2_WEBCLIENT_DATA_URL:-}"
MEGA_USER="${M2_SRC_MEGA_USER:-}"
MEGA_PASS="${M2_SRC_MEGA_PASS:-}"
STALL_MIN="${M2_WEBCLIENT_STALL_MIN:-5}"
FORCE=0
QUIET=0

CMD=""
USE_NAME=""
while [ $# -gt 0 ]; do
    case "$1" in
        install|status|check|clean) CMD="$1"; shift ;;
        use)      CMD=use; USE_NAME="${2:-}"; shift 2 ;;
        --pointer) POINTER="${2:-}"; shift 2 ;;
        --target)  TARGET="${2:-}";  shift 2 ;;
        --cache)   CACHE="${2:-}";   shift 2 ;;
        --keep)    KEEP="${2:-}";    shift 2 ;;
        --engine-url) ENGINE_URL_OVERRIDE="${2:-}"; shift 2 ;;
        --data-url)   DATA_URL_OVERRIDE="${2:-}";   shift 2 ;;
        --mega-user)  MEGA_USER="${2:-}"; shift 2 ;;
        --mega-pass)  MEGA_PASS="${2:-}"; shift 2 ;;
        --stall-minutes) STALL_MIN="${2:-}"; shift 2 ;;
        --force)   FORCE=1; shift ;;
        --quiet)   QUIET=1; shift ;;
        -h|--help) sed -n '2,60p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) printf 'fetch-web-client: unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[ -n "$CMD" ] || CMD=install

# -----------------------------------------------------------------------------
#  Saying things
# -----------------------------------------------------------------------------
note() { [ "$QUIET" = "1" ] || printf '%s\n' "$*"; }
step() { [ "$QUIET" = "1" ] || printf '\n== %s\n' "$*"; }
ok()   { [ "$QUIET" = "1" ] || printf '   + %s\n' "$*"; }
warn() { printf '   ! %s\n' "$*" >&2; }
die()  { _c="$1"; shift; printf '\nfetch-web-client: %s\n' "$1" >&2; shift
         for _l in "$@"; do printf '%s\n' "$_l" >&2; done; exit "$_c"; }

have() { command -v "$1" >/dev/null 2>&1; }
need_tools() {
    for _t in "$@"; do
        have "$_t" || die 3 "$_t is not installed on this machine." \
            "  Debian/Ubuntu:  apt-get install -y unzip curl megatools coreutils"
    done
}

human() { # bytes -> something a person reads
    awk -v b="${1:-0}" 'BEGIN{
        if (b>=1073741824) printf "%.1f GB", b/1073741824;
        else if (b>=1048576) printf "%.1f MB", b/1048576;
        else if (b>=1024) printf "%.1f KB", b/1024;
        else printf "%d B", b }'
}
fsize() { wc -c < "$1" 2>/dev/null | tr -d ' ' || printf '0'; }

# Free megabytes on the filesystem holding DIR (or its nearest existing parent).
free_mb() {
    _d="$1"
    while [ -n "$_d" ] && [ ! -d "$_d" ]; do _d=$(dirname "$_d"); [ "$_d" = "/" ] && break; done
    df -Pm "$_d" 2>/dev/null | awk 'NR==2{print $4+0}'
}
check_space() { # check_space MB DIR WHAT
    _need="$1"; _dir="$2"; _what="$3"
    _got=$(free_mb "$_dir")
    [ -n "$_got" ] || return 0
    [ "$_got" -ge "$_need" ] && return 0
    die 7 "There is not enough disk space for $_what." \
        "  needed:    ${_need} MB in $_dir" \
        "  available: ${_got} MB"
}

sha_of() { sha256sum "$1" 2>/dev/null | awk '{print $1}'; }

# -----------------------------------------------------------------------------
#  Reading the pointer file.
#
#  artifacts.json is kept FLAT -- one "key": "value" per line -- precisely so
#  that it can be read here without a JSON parser. There is no python3 in the
#  helper container the Windows installer uses, and adding one to pull four
#  strings out of a file we write ourselves would be the wrong trade.
# -----------------------------------------------------------------------------
json_get() { # json_get KEY FILE  -- string or number, empty when absent
    _k="$1"; _f="$2"
    sed -n 's/.*"'"$_k"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$_f" 2>/dev/null | head -1 |
    {
        read -r _v || _v=""
        if [ -z "$_v" ]; then
            sed -n 's/.*"'"$_k"'"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$_f" 2>/dev/null | head -1
        else
            printf '%s\n' "$_v"
        fi
    }
}

# A value the user has not filled in yet. Every real link is https://, so the
# test is "is it a URL" rather than a list of placeholder spellings that would
# rot the moment somebody typed a different one.
url_is_set() { case "${1:-}" in https://*|http://*) return 0 ;; *) return 1 ;; esac; }
sha_is_set()  { case "${1:-}" in
                    ????????????????????????????????????????????????????????????????)
                        case "$1" in *[!0-9a-fA-F]*) return 1 ;; *) return 0 ;; esac ;;
                    *) return 1 ;; esac; }

[ -f "$POINTER" ] || die 2 "$POINTER does not exist." \
    "  It is the pointer file at the top of the repository (artifacts.json)." \
    "  Pass --pointer if it lives somewhere else."

ENGINE_VERSION=$(json_get engine_version "$POINTER")
ENGINE_URL=$(json_get engine_url "$POINTER")
ENGINE_URL_TPL=$(json_get engine_url_template "$POINTER")
ENGINE_SHA=$(json_get engine_sha256 "$POINTER")
ENGINE_MB=$(json_get engine_size_mb "$POINTER")
ENGINE_NEEDS_DATA=$(json_get engine_requires_data "$POINTER")
DATA_VERSION=$(json_get data_version "$POINTER")
DATA_URL=$(json_get data_url "$POINTER")
DATA_SHA=$(json_get data_sha256 "$POINTER")
DATA_MB=$(json_get data_size_mb "$POINTER")
DATA_FALLBACK=$(json_get data_url_fallback "$POINTER")
DATA_FALLBACK2=$(json_get data_url_fallback2 "$POINTER")

[ -n "$ENGINE_VERSION" ] && [ -n "$DATA_VERSION" ] || die 9 \
    "$POINTER is missing engine_version or data_version."

# The URL is derived from the version unless somebody overrode it, so publishing
# a new engine is: attach the archive to the release tagged v<version>, put the
# version and the sum in artifacts.json, done. No link is maintained by hand,
# and a link therefore cannot go stale while the version moves on.
[ -n "$ENGINE_URL_OVERRIDE" ] && ENGINE_URL="$ENGINE_URL_OVERRIDE"
if [ -z "$ENGINE_URL" ] && [ -n "$ENGINE_URL_TPL" ]; then
    ENGINE_URL=$(printf '%s' "$ENGINE_URL_TPL" | sed "s|{version}|$ENGINE_VERSION|g")
fi
[ -n "$DATA_URL_OVERRIDE" ] && DATA_URL="$DATA_URL_OVERRIDE"

# The corpus is 1.75 GB from an anonymous MEGA share, which is exactly the
# thing that answers "509 over quota" when somebody else has been downloading
# it all afternoon. Everything after the first entry is the same archive
# somewhere else, checked against the same sha256. Placeholders are dropped
# here rather than at the download, so an empty fallback simply is not a list
# entry; --data-url replaces the lot, because somebody who names a link means
# that link.
DATA_URLS=""
for _u in "$DATA_URL" "$DATA_FALLBACK" "$DATA_FALLBACK2"; do
    url_is_set "$_u" && DATA_URLS="${DATA_URLS:+$DATA_URLS }$_u"
done
[ -n "$DATA_URL_OVERRIDE" ] && DATA_URLS="$DATA_URL_OVERRIDE"

# The compatibility rule, checked here as well as in the panel. The engine names
# the corpus it was built against; if the pointer file offers a different one it
# contradicts itself, and installing that pair would produce a client that
# starts and then goes strange halfway in -- the worst kind of failure, because
# it looks like a bug in the game.
[ "$ENGINE_NEEDS_DATA" = "$DATA_VERSION" ] || die 9 \
    "artifacts.json contradicts itself." \
    "  engine $ENGINE_VERSION was built against data $ENGINE_NEEDS_DATA," \
    "  but the file offers data $DATA_VERSION." \
    "" \
    "  Nothing was downloaded. Fix engine_requires_data or data_version."

[ -n "$TARGET" ] || die 2 "--target is required: the directory the panel serves" \
    "  /play from, which is <panel-data volume>/browser."

ENGINE_DIR="$TARGET/engine-$ENGINE_VERSION"
DATA_DIR="$TARGET/data-$DATA_VERSION"
PLAY_DIR="$TARGET/v$ENGINE_VERSION"
STATE="$TARGET/installed.json"
STAMP=".m2-web-client"      # inside every v<version>, so `use' knows what it is

# -----------------------------------------------------------------------------
#  What is here already.
#
#  Read off the disk, never remembered from a previous answer. Somebody who
#  deletes a directory by hand has changed what is installed, and a cached
#  "yes they wanted it" would then be a lie that survives every future run.
# -----------------------------------------------------------------------------
have_engine=0; have_data=0; have_play=0
cur_engine=""; cur_data=""; cur_engine_sha=""
[ -f "$ENGINE_DIR/index.wasm" ] && have_engine=1
[ -f "$DATA_DIR/manifest.bin" ] && have_data=1
[ -f "$PLAY_DIR/index.wasm" ] && [ -f "$PLAY_DIR/manifest.bin" ] && have_play=1
if [ -f "$STATE" ]; then
    cur_engine=$(json_get engine_version "$STATE")
    cur_data=$(json_get data_version "$STATE")
    cur_engine_sha=$(json_get engine_sha256 "$STATE")
fi

print_status() {
    printf 'target        %s\n' "$TARGET"
    printf 'installed     engine %s / data %s\n' "${cur_engine:-none}" "${cur_data:-none}"
    printf 'published     engine %s / data %s\n' "$ENGINE_VERSION" "$DATA_VERSION"
    printf 'serving       %s\n' "$(readlink "$TARGET/current" 2>/dev/null || echo 'nothing')"
    if [ -d "$TARGET" ]; then
        printf 'kept          %s\n' "$(ls -d "$TARGET"/v* 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' ')"
    fi
}

if [ "$CMD" = "status" ]; then print_status; exit 0; fi

if [ "$CMD" = "check" ]; then
    if [ "$have_play" = "1" ] && [ "$cur_engine" = "$ENGINE_VERSION" ] && [ "$cur_data" = "$DATA_VERSION" ]; then
        exit 0
    fi
    exit 1
fi

# -----------------------------------------------------------------------------
#  `use' -- roll back, or forward, to a version that is still on disk.
#
#  This is the whole reason old versions are kept: a bad engine is one symlink
#  away from not being the one that is served, with no download and no unpack.
# -----------------------------------------------------------------------------
swap_current() { # swap_current NAME  -- atomically point `current' at <target>/NAME
    _n="$1"
    _tmp="$TARGET/.current.$$"
    rm -f "$_tmp"
    ln -s "$_n" "$_tmp" || return 1
    # mv -T over an existing symlink is rename(2): it either happens or it does
    # not, and no reader ever sees the link missing. Without -T, mv would follow
    # `current' into the directory it points at and put the new link INSIDE it.
    if mv -T "$_tmp" "$TARGET/current" 2>/dev/null; then return 0; fi
    # Some minimal userlands have no mv -T. ln -sfn is not atomic (it unlinks,
    # then links) but the window is microseconds and it is the only fallback
    # there is; a reader that lands in it gets one 404, not half an engine.
    rm -f "$_tmp"
    ln -sfn "$_n" "$TARGET/current"
}

write_state() { # write_state ENGINEVER DATAVER ENGINESHA DATASHA PLAYDIRNAME
    cat > "$STATE.tmp.$$" <<JSON
{
  "schema": 1,
  "_note": "Written by linux-port/fetch-web-client.sh. The panel reads this to decide whether to offer the button and where to point it. Do not edit by hand -- re-run the installer.",
  "engine_version": "$1",
  "data_version": "$2",
  "engine_sha256": "$3",
  "data_sha256": "$4",
  "engine_requires_data": "$2",
  "play_dir": "$5",
  "installed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo unknown)"
}
JSON
    mv -f "$STATE.tmp.$$" "$STATE"
    chmod a+r "$STATE" 2>/dev/null || true
}

if [ "$CMD" = "use" ]; then
    [ -n "$USE_NAME" ] || die 2 "use needs a version directory, e.g.  use v1.10.0"
    case "$USE_NAME" in */*|.*) die 2 "'$USE_NAME' is not a version directory name." ;; esac
    [ -f "$TARGET/$USE_NAME/index.wasm" ] || die 2 \
        "$TARGET/$USE_NAME is not an installed browser client." \
        "  What is here:  $(ls -d "$TARGET"/v* 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' ')"
    _e=$(json_get engine_version "$TARGET/$USE_NAME/$STAMP")
    _d=$(json_get data_version   "$TARGET/$USE_NAME/$STAMP")
    _es=$(json_get engine_sha256 "$TARGET/$USE_NAME/$STAMP")
    _ds=$(json_get data_sha256   "$TARGET/$USE_NAME/$STAMP")
    swap_current "$USE_NAME" || die 9 "The symlink could not be moved."
    write_state "${_e:-$USE_NAME}" "${_d:-unknown}" "$_es" "$_ds" "$USE_NAME"
    ok "now serving $USE_NAME (engine ${_e:-?}, data ${_d:-?})"
    exit 0
fi

if [ "$CMD" = "clean" ]; then
    _keepname=$(readlink "$TARGET/current" 2>/dev/null || echo '')
    for _d in "$TARGET"/v* "$TARGET"/engine-* "$TARGET"/data-*; do
        [ -d "$_d" ] || continue
        [ "$(basename "$_d")" = "$_keepname" ] && continue
        note "  removing $(basename "$_d")"
        rm -rf "$_d"
    done
    exit 0
fi

# =============================================================================
#  install
# =============================================================================
need_tools unzip sha256sum find awk sed df

need_engine=0
need_data=0
[ "$have_engine" = "1" ] || need_engine=1
[ "$have_data"   = "1" ] || need_data=1
[ "$cur_engine" = "$ENGINE_VERSION" ] || need_engine=1
[ "$cur_data"   = "$DATA_VERSION" ]   || need_data=1
[ "$FORCE" = "1" ] && { need_engine=1; need_data=1; }

# The cache-poisoning rail. v<engine_version> is served with the engine's own
# files marked immutable for a year, which is only safe while a version number
# means one and only one build. Re-publishing the engine archive under a version that is
# already installed somewhere would leave players on the old index.wasm with no
# way to notice -- so it is refused here rather than discovered later.
if [ "$cur_engine" = "$ENGINE_VERSION" ] && [ -n "$cur_engine_sha" ] &&
   [ -n "$ENGINE_SHA" ] && [ "$cur_engine_sha" != "$ENGINE_SHA" ] && [ "$FORCE" != "1" ]; then
    die 9 "engine $ENGINE_VERSION is already installed, but from a different build." \
        "  installed sha256  $cur_engine_sha" \
        "  artifacts.json    $ENGINE_SHA" \
        "" \
        "  A version number has to mean one build: /play/v$ENGINE_VERSION/ is served" \
        "  with the engine cached for a year, so a second build under the same" \
        "  number would leave players on the old one forever." \
        "" \
        "  Raise engine_version in artifacts.json (and the release tag with it)."
fi

if [ "$need_engine" = "0" ] && [ "$need_data" = "0" ] && [ "$have_play" = "1" ]; then
    ok "the browser client is already at engine $ENGINE_VERSION / data $DATA_VERSION"
    exit 0
fi

step "The browser client"
note "  engine $ENGINE_VERSION $([ "$need_engine" = 1 ] && echo '-- to download' || echo '-- already here')"
note "  data   $DATA_VERSION $([ "$need_data" = 1 ] && echo '-- to download' || echo '-- already here')"
if [ "$need_data" = "0" ] && [ "$need_engine" = "1" ]; then
    note ""
    note "  Only the engine changes, so this is $(human $((${ENGINE_MB:-18} * 1048576))) rather than 1.7 GB."
    note "  That is what the two archives are for."
fi

if [ "$need_data" = "1" ] && [ -z "$DATA_URLS" ]; then
    die 8 "The MEGA link for Reference_WebClientData.zip has not been filled in yet." \
        "  artifacts.json still says:  data_url = $DATA_URL" \
        "  and it names no fallback either." \
        "" \
        "  Put the share link in that file, or pass it once:" \
        "      --data-url 'https://mega.nz/file/...'" \
        "  or set M2_WEBCLIENT_DATA_URL in the environment." \
        "" \
        "  Nothing was downloaded and nothing was changed."
fi
if [ "$need_engine" = "1" ] && ! url_is_set "$ENGINE_URL"; then
    die 8 "There is no download link for Reference_WebClientEngine.zip." \
        "  artifacts.json gives neither engine_url nor engine_url_template." \
        "  Expected the release asset:" \
        "      .../releases/download/v$ENGINE_VERSION/Reference_WebClientEngine.zip"
fi

mkdir -p "$TARGET" "$CACHE" || die 7 "Could not create $TARGET or $CACHE."

# Space: the archive in the cache and the unpacked copy in the target, which may
# be two different filesystems, so both are asked. The data half is checked only
# when it is actually being fetched -- that is the saving this split exists for.
_need_cache=0; _need_target=0
[ "$need_engine" = "1" ] && { _need_cache=$((_need_cache + ${ENGINE_MB:-18})); _need_target=$((_need_target + ${ENGINE_MB:-18} * 2)); }
[ "$need_data" = "1" ]   && { _need_cache=$((_need_cache + ${DATA_MB:-1750})); _need_target=$((_need_target + ${DATA_MB:-1750})); }
check_space $((_need_cache + 200))  "$CACHE"  "the downloaded archives"
check_space $((_need_target + 500)) "$TARGET" "unpacking the browser client"

# -----------------------------------------------------------------------------
#  Downloading.
#
#  GitHub is curl; MEGA is megatools, for the same reason fetch-sources.sh uses
#  it -- an anonymous MEGA download is charged to the quota of whoever owns the
#  file, and when that runs out every chunk comes back "509 over quota" while
#  the tool retries forever having written nothing. So the transfer is watched
#  and a stalled one is reported as the quota wall it is, not as a broken link.
# -----------------------------------------------------------------------------
url_is_mega() { case "$1" in *mega.nz*|*mega.co.nz*) return 0 ;; *) return 1 ;; esac; }
mega_legacy_url() {
    printf '%s' "$1" | sed -e 's,/file/,/#!,' -e 's,/folder/,/#F!,' \
                           -e 's,#!\([^#]*\)#,#!\1!,' -e 's,#F!\([^#]*\)#,#F!\1!,'
}

watched() { # watched DIR LOG CMD...   -- kill CMD if DIR stops growing
    _wd="$1"; _wl="$2"; shift 2
    "$@" >>"$_wl" 2>&1 &
    _pid=$!
    _last=0; _still=0
    while kill -0 "$_pid" 2>/dev/null; do
        sleep 5
        kill -0 "$_pid" 2>/dev/null || break
        _now=$(find "$_wd" -maxdepth 1 -type f 2>/dev/null | while IFS= read -r _f; do fsize "$_f"; done | awk '{s+=$1} END{print s+0}')
        if [ "${_now:-0}" -gt "$_last" ]; then _last="$_now"; _still=0
        else _still=$((_still + 5)); fi
        if [ "$_still" -ge $((${STALL_MIN:-5} * 60)) ]; then
            kill -TERM "$_pid" 2>/dev/null; sleep 2; kill -KILL "$_pid" 2>/dev/null
            wait "$_pid" 2>/dev/null
            return 124
        fi
    done
    wait "$_pid"
}

fetch_to() { # fetch_to URL DESTFILE LABEL  -- leaves DESTFILE or fails
    _u="$1"; _dest="$2"; _label="$3"
    _wd="$CACHE/.dl-$_label"
    rm -rf "$_wd"; mkdir -p "$_wd" || return 1
    _log="$CACHE/download.log"

    if url_is_mega "$_u"; then
        need_tools megatools
        _cfg=""
        if [ -n "$MEGA_USER" ] && [ -n "$MEGA_PASS" ]; then
            # Into a file, never onto the command line: an argument list is
            # readable in `ps' for as long as the download runs, and that is an
            # hour.
            _cfg="$CACHE/.megarc"
            ( umask 077; printf '[Login]\nUsername = %s\nPassword = %s\n' "$MEGA_USER" "$MEGA_PASS" > "$_cfg" ) || _cfg=""
            [ -n "$_cfg" ] && note "  signing in to MEGA as $MEGA_USER (your own transfer quota)"
        fi
        if [ -n "$_cfg" ]; then
            watched "$_wd" "$_log" megatools dl --config "$_cfg" --no-progress --path "$_wd" "$_u"
        else
            watched "$_wd" "$_log" megatools dl --no-progress --path "$_wd" "$_u"
        fi
        _rc=$?
        [ -n "$_cfg" ] && rm -f "$_cfg"
        _got=$(find "$_wd" -maxdepth 1 -type f -size +1M ! -name '.megatmp.*' ! -name '*.part' 2>/dev/null | head -1)
        # Return 9 rather than 1, and say only what happened: the caller knows
        # whether there is another copy to try, and the advice to wait a few
        # hours is wrong -- and discouraging -- if the next line of the list
        # fetches the same archive in a minute.
        if [ -z "$_got" ] && grep -q '509\|over quota' "$_log" 2>/dev/null; then
            warn "MEGA answered '509 over quota' -- that is the share's daily"
            warn "allowance being spent, not a broken link."
            return 9
        fi
        if [ -z "$_got" ] && [ "$_rc" != "124" ]; then
            _legacy=$(mega_legacy_url "$_u")
            if [ "$_legacy" != "$_u" ]; then
                warn "retrying with the older MEGA link format"
                watched "$_wd" "$_log" megatools dl --no-progress --path "$_wd" "$_legacy"
                _got=$(find "$_wd" -maxdepth 1 -type f -size +1M ! -name '.megatmp.*' 2>/dev/null | head -1)
            fi
        fi
        [ -n "$_got" ] || { tail -5 "$_log" 2>/dev/null | while IFS= read -r _l; do warn "$_l"; done; return 1; }
        # The remote name is whatever the owner called it -- brackets, spaces
        # and all. It is quoted everywhere and then renamed to a name of ours,
        # so nothing downstream ever has to think about it again.
        mv -f "$_got" "$_dest" || return 1
    else
        need_tools curl
        watched "$_wd" "$_log" curl -fL --retry 5 --retry-delay 5 -C - -o "$_wd/dl" "$_u" || return 1
        [ -s "$_wd/dl" ] || return 1
        mv -f "$_wd/dl" "$_dest" || return 1
    fi
    rm -rf "$_wd"
    [ -s "$_dest" ]
}

# Sets OBTAINED rather than printing the path: everything in here says what it
# is doing as it goes, and a function that both narrates and returns a value on
# stdout ends up handing the caller its own narration.
OBTAINED=""
obtain() { # obtain URLS WANTSHA CACHENAME LABEL -> OBTAINED
    _urls="$1"; _want="$2"; _name="$3"; _label="$4"
    _f="$CACHE/$_name"
    OBTAINED=""

    if [ -f "$_f" ] && [ -n "$_want" ]; then
        if [ "$(sha_of "$_f")" = "$_want" ]; then
            note "  $_name is already in $CACHE and its sha256 matches"
            OBTAINED="$_f"; return 0
        fi
        warn "$_name in the cache has the wrong sha256 -- downloading it again"
        rm -f "$_f"
    fi

    # One archive, several places it might be. The sha256 is checked inside the
    # loop rather than after it, so a mirror that has gone stale is treated the
    # same as one that would not answer at all -- the next copy gets its turn,
    # and the only file that can end up in the cache is the right one.
    _quota=0; _tried=0; _rc=4
    for _u in $_urls; do
        url_is_set "$_u" || continue
        _tried=$((_tried + 1))
        [ "$_tried" -gt 1 ] && note "  trying another copy of the same archive"

        note "  downloading $_name"
        note "    from $_u"
        fetch_to "$_u" "$_f" "$_label"
        _rc=$?
        if [ "$_rc" != 0 ]; then
            [ "$_rc" = 9 ] && _quota=1
            rm -f "$_f"
            continue
        fi
        note "  got $(human "$(fsize "$_f")")"

        if [ -n "$_want" ] && sha_is_set "$_want"; then
            _have=$(sha_of "$_f")
            if [ "$_have" != "$_want" ]; then
                rm -f "$_f"
                warn "sha256 mismatch on $_name"
                warn "  expected $_want"
                warn "  got      ${_have:-nothing}"
                _rc=5
                continue
            fi
            ok "sha256 verified"
        else
            warn "no sha256 for $_name in artifacts.json -- it was not verified"
        fi
        OBTAINED="$_f"
        return 0
    done

    if [ "$_tried" = 0 ]; then
        warn "there is no usable link for $_name in $POINTER"
        return 4
    fi
    # Only now is the wait real: every copy has been tried.
    if [ "$_quota" = 1 ]; then
        warn "Every copy of $_name is out of reach at the moment."
        warn "MEGA's allowance clears by itself, usually within a few hours."
        warn "Run this again then, or sign in with a MEGA account of your own"
        warn "(--mega-user / --mega-pass), which charges the transfer to you."
    fi
    [ "$_rc" = 9 ] && _rc=4
    return "$_rc"
}

# -----------------------------------------------------------------------------
#  Unpacking, beside what is running.
#
#  -j flattens: both archives are flat already, and saying so here means a
#  future one with a stray top-level folder still lands where the client looks
#  rather than one directory too deep, which presents as an empty filesystem.
# -----------------------------------------------------------------------------
unpack_into() { # unpack_into ZIP FINALDIR MUSTHAVE...
    _zip="$1"; _final="$2"; shift 2
    _stage="$TARGET/.stage.$$"
    rm -rf "$_stage"; mkdir -p "$_stage" || return 1
    note "  unpacking $(basename "$_zip")"
    unzip -q -o -j "$_zip" -d "$_stage" || { rm -rf "$_stage"; return 6; }
    for _m in "$@"; do
        if [ ! -f "$_stage/$_m" ]; then
            warn "$(basename "$_zip") does not contain $_m -- it is not the archive this expects"
            rm -rf "$_stage"; return 6
        fi
    done
    # World-readable: the panel runs as uid 2001 and nginx as www-data, and
    # neither is whoever unpacked this.
    chmod -R a+rX "$_stage" 2>/dev/null || true
    rm -rf "$_final"
    mv "$_stage" "$_final" || { rm -rf "$_stage"; return 1; }
    return 0
}

if [ "$need_engine" = "1" ]; then
    obtain "$ENGINE_URL" "$ENGINE_SHA" "engine-$ENGINE_VERSION.zip" engine || exit $?
    unpack_into "$OBTAINED" "$ENGINE_DIR" index.html index.js index.wasm
    _rc=$?
    [ "$_rc" = "0" ] || exit "$_rc"
    ok "engine $ENGINE_VERSION unpacked"
fi

if [ "$need_data" = "1" ]; then
    obtain "$DATA_URLS" "$DATA_SHA" "data-$DATA_VERSION.zip" data || exit $?
    unpack_into "$OBTAINED" "$DATA_DIR" manifest.bin
    _rc=$?
    [ "$_rc" = "0" ] || exit "$_rc"
    _blocks=$(find "$DATA_DIR" -maxdepth 1 -name '*.bin' ! -name 'manifest.bin' 2>/dev/null | wc -l | tr -d ' ')
    [ "${_blocks:-0}" -ge 100 ] || die 6 \
        "the data archive unpacked to only ${_blocks:-0} blocks beside manifest.bin." \
        "  The corpus is 419 of them; this archive is not it."
    ok "data $DATA_VERSION unpacked ($_blocks blocks)"
fi

# -----------------------------------------------------------------------------
#  Putting the two halves together.
#
#  Hard links, not copies and not symlinks. Not copies because that is 1.75 GB
#  per version; not symlinks because a hard link keeps the blocks alive on its
#  own, which is what makes every v<version> self-contained and a rollback a
#  symlink rather than a download.
# -----------------------------------------------------------------------------
step "Putting it where the panel serves it"
_new="$TARGET/.play.$$"
rm -rf "$_new"; mkdir -p "$_new" || die 1 "Could not create $_new."

link_tree() { # link_tree SRCDIR DSTDIR
    _s="$1"; _d="$2"
    find "$_s" -maxdepth 1 -type f 2>/dev/null | while IFS= read -r _f; do
        _b=$(basename "$_f")
        ln "$_f" "$_d/$_b" 2>/dev/null || ln -s "../$(basename "$_s")/$_b" "$_d/$_b" 2>/dev/null || cp -p "$_f" "$_d/$_b"
    done
}
# Data first, engine second: if a name ever existed in both, the engine's own
# copy is the one the engine was built with and must win.
link_tree "$DATA_DIR" "$_new"
link_tree "$ENGINE_DIR" "$_new"

cat > "$_new/$STAMP" <<JSON
{
  "engine_version": "$ENGINE_VERSION",
  "data_version": "$DATA_VERSION",
  "engine_sha256": "$ENGINE_SHA",
  "data_sha256": "$DATA_SHA"
}
JSON

# The last check before anything is served: the four files whose absence turns
# into a blank page with a console message nobody reads.
for _m in index.html index.js index.wasm manifest.bin; do
    [ -f "$_new/$_m" ] || { rm -rf "$_new"; die 6 "$_m did not end up in the served directory."; }
done
chmod -R a+rX "$_new" 2>/dev/null || true

rm -rf "$PLAY_DIR"
mv "$_new" "$PLAY_DIR" || { rm -rf "$_new"; die 1 "Could not move the new version into place."; }
ok "$(basename "$PLAY_DIR") is complete and checked"

# And only now does anybody see it.
swap_current "v$ENGINE_VERSION" || die 9 "The new version is in place but 'current' could not be moved."
write_state "$ENGINE_VERSION" "$DATA_VERSION" "$ENGINE_SHA" "$DATA_SHA" "v$ENGINE_VERSION"
ok "now serving v$ENGINE_VERSION (engine $ENGINE_VERSION, data $DATA_VERSION)"

# -----------------------------------------------------------------------------
#  Keeping a way back.
#
#  Old v<version> directories are hard links, so two or three of them cost a few
#  megabytes between them and turn a bad release into one command:
#      sh fetch-web-client.sh use v1.10.0 --target <dir>
#  The pure engine-*/data-* directories are only needed to build the union, so
#  only the current pair is kept.
# -----------------------------------------------------------------------------
case "${KEEP:-3}" in ''|*[!0-9]*) KEEP=3 ;; esac
[ "$KEEP" -lt 1 ] && KEEP=1
_old=$(ls -1dt "$TARGET"/v* 2>/dev/null | grep -v "^$PLAY_DIR\$" | tail -n +"$KEEP")
for _d in $_old; do
    [ -d "$_d" ] || continue
    note "  removing $(basename "$_d") (keeping $KEEP)"
    rm -rf "$_d"
done
for _d in "$TARGET"/engine-* "$TARGET"/data-*; do
    [ -d "$_d" ] || continue
    [ "$_d" = "$ENGINE_DIR" ] && continue
    [ "$_d" = "$DATA_DIR" ] && continue
    note "  removing $(basename "$_d") (superseded)"
    rm -rf "$_d"
done

# A hand-placed client from before any of this existed would still be sitting
# flat in the target and would still be found first by the panel's fallback.
# Say so rather than silently serving the old one.
if [ -f "$TARGET/index.html" ]; then
    warn "there is also a loose index.html directly in $TARGET -- an older"
    warn "hand-placed client. The panel prefers what this script installed;"
    warn "delete the loose files when you are sure you do not want them."
fi

print_status
exit 0
