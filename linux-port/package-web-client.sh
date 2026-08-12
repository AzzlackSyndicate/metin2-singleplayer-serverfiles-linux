#!/bin/sh
# package-web-client.sh -- make the two archives fetch-web-client.sh downloads.
#
#   linux-port/package-web-client.sh <dist/browser> [--out DIR] [--engine-version X.Y.Z]
#                                    [--data-version X.Y.Z] [--engine-only|--data-only]
#
# <dist/browser> is the browser client as the wasm build leaves it: index.html and
# friends beside 420 content-addressed <32 hex>.bin chunks. Out of that come
#
#   Reference_WebClientEngine.zip   the client's own code, ~18 MB, published as a
#                                   release asset of this repository
#   Reference_WebClientData.zip     the game's data, ~1.75 GB, published on MEGA
#                                   because it is not ours to redistribute
#
# and, printed at the end, the exact lines artifacts.json needs. Nothing here
# uploads anything: publishing is a decision, and it is made by a person.
#
# ═══════════════════════════════════════════════════════════════════════════════
#  WHY THE ENGINE'S CONTENTS ARE A LIST AND NOT A PATTERN
# ═══════════════════════════════════════════════════════════════════════════════
#
# The first engine archive was packed by hand, and it was packed one file short:
# index.dev, the pack list, which webfs.js writes to /pack/index.dev for
# InitPacks to read. Nothing noticed. The archive unpacked cleanly, every check
# passed, /play/ served a page -- and the client stopped at "starting…" on a
# 404 that no log on the server called an error, because a 404 IS what the
# server should say about a file nobody installed.
#
# So the engine's contents are written out below, one line each. A file that is
# missing stops the run and names itself; a file that is present and NOT listed
# is reported too, because that is how the next index.dev would announce itself.
# A glob would have shipped whatever happened to be in the directory, which is
# how the bug happened in the first place.
#
# ═══════════════════════════════════════════════════════════════════════════════
#  WHY index.dev TRAVELS WITH THE ENGINE AND NOT WITH THE DATA
# ═══════════════════════════════════════════════════════════════════════════════
#
# It describes the data -- it is the list of packs in the corpus -- so on the
# face of it it belongs in the data archive. It ships with the engine anyway,
# for a practical reason and a correct one:
#
#   practical  it is 1.8 KB. Putting it in the data archive means re-uploading
#              1.75 GB to MEGA to fix a typo in it.
#   correct    artifacts.json already pins the pairing: engine_requires_data
#              names the data version an engine was built against, and
#              fetch-web-client.sh refuses a mismatch. An engine that carries
#              the pack list of the corpus it was built against is exactly what
#              that field already promises.
#
# Change the corpus and you publish a new data version AND a new engine that
# names it. That was true before this file existed.
set -eu

# ── the engine, file by file ──────────────────────────────────────────────────
# index.html   the page, the boot sequence and the connection dialog
# index.js     the module's JS, with tools/wasm/pre.js compiled into it -- which
#              is where the WebSocket shim lives, so a fix there ships here
# index.wasm   the client
# index.dev    the pack list; see above for why it is in this archive
# webfs.js     the streaming filesystem
# serve-webfs.py  the reference static server, for running it without a panel
# m2-ws2tcp-*  the bridge, for an operator who runs it outside this stack
ENGINE_FILES='index.html
index.js
index.wasm
index.dev
webfs.js
serve-webfs.py
m2-ws2tcp-linux-x64
m2-ws2tcp-linux-x86
m2-ws2tcp-macos-arm64
m2-ws2tcp-macos-x64
m2-ws2tcp-src.zip
m2-ws2tcp-windows-x64.zip
m2-ws2tcp-windows-x86.zip'

SRC=""
OUT="."
ENGINE_VERSION=""
DATA_VERSION=""
WANT_ENGINE=1
WANT_DATA=1

die()  { printf '\n  %s\n' "$@" >&2; exit 1; }
info() { printf '  %s\n' "$*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --out)            OUT="${2:?--out needs a directory}"; shift 2 ;;
        --engine-version) ENGINE_VERSION="${2:?}"; shift 2 ;;
        --data-version)   DATA_VERSION="${2:?}"; shift 2 ;;
        --engine-only)    WANT_DATA=0; shift ;;
        --data-only)      WANT_ENGINE=0; shift ;;
        -h|--help)        sed -n '2,17p' "$0"; exit 0 ;;
        -*)               die "Unknown option: $1" ;;
        *)                [ -n "$SRC" ] && die "Only one source directory."
                          SRC="$1"; shift ;;
    esac
done

[ -n "$SRC" ] || die "Which directory? Give me the browser client's dist directory." \
                     "  linux-port/package-web-client.sh /opt/m2wasm/dist/browser"
[ -d "$SRC" ] || die "$SRC is not a directory."
command -v zip      >/dev/null 2>&1 || die "zip is not installed."
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is not installed."

mkdir -p "$OUT"
SRC=$(cd "$SRC" && pwd)
OUT=$(cd "$OUT" && pwd)

# ── the engine ────────────────────────────────────────────────────────────────
if [ "$WANT_ENGINE" = "1" ]; then
    [ -n "$ENGINE_VERSION" ] || die "--engine-version is required to build the engine archive."

    _missing=""
    for _f in $ENGINE_FILES; do
        [ -f "$SRC/$_f" ] || _missing="$_missing $_f"
    done
    [ -z "$_missing" ] || die "The engine is incomplete. Missing from $SRC:" \
                              "$(printf '   %s\n' $_missing)" \
                              "Build it first, or correct ENGINE_FILES in this script."

    # Anything in the directory that is neither a chunk nor a listed engine file.
    # Reported, never shipped: a new file that belongs in the engine has to be
    # added to the list on purpose, which is the whole point of the list.
    _stray=$(ls -1 "$SRC" 2>/dev/null | while read -r _f; do
                 case "$_f" in
                     manifest.bin) continue ;;
                     *.bin) case "$_f" in
                                [0-9a-f]*) continue ;;
                            esac ;;
                 esac
                 printf '%s\n' "$ENGINE_FILES" | grep -qxF "$_f" || printf '%s\n' "$_f"
             done)
    if [ -n "$_stray" ]; then
        info ""
        info "Not packaged, because this script was not told about them:"
        printf '     %s\n' $_stray
        info "If one of those belongs in the engine, add it to ENGINE_FILES."
        info ""
    fi

    _zip="$OUT/Reference_WebClientEngine.zip"
    rm -f "$_zip"
    # -X drops the platform's extra attributes, so the archive depends on the
    # files and not on the machine that packed them. Flat: fetch-web-client.sh
    # unpacks it beside index.html and the client resolves everything relative.
    ( cd "$SRC" && zip -q -X -9 "$_zip" $ENGINE_FILES )
    info "Reference_WebClientEngine.zip  $(printf '%s\n' "$ENGINE_FILES" | wc -l) files"
fi

# ── the data ──────────────────────────────────────────────────────────────────
if [ "$WANT_DATA" = "1" ]; then
    [ -n "$DATA_VERSION" ] || die "--data-version is required to build the data archive."
    [ -f "$SRC/manifest.bin" ] || die "$SRC has no manifest.bin -- that is not a built corpus."

    _chunks=$(ls -1 "$SRC" | grep -cE '^[0-9a-f]{32}\.bin$' || true)
    [ "$_chunks" -gt 0 ] || die "$SRC holds no <32 hex>.bin chunks."
    info "Reference_WebClientData.zip    manifest.bin + $_chunks chunks (this takes a while)"

    _zip="$OUT/Reference_WebClientData.zip"
    rm -f "$_zip"
    # -0 on purpose: the chunks are already compressed, so deflate spends
    # minutes to save nothing. manifest.bin travels with them because it is
    # their index -- a corpus and a manifest from different builds is the one
    # combination that presents as missing files rather than as a bad download.
    ( cd "$SRC" && ls -1 . | grep -E '^([0-9a-f]{32}\.bin|manifest\.bin)$' \
                           | zip -q -X -0 "$_zip" -@ )
fi

# ── what artifacts.json needs ─────────────────────────────────────────────────
printf '\n'
info "Paste into artifacts.json:"
printf '\n'
for _f in Reference_WebClientEngine.zip Reference_WebClientData.zip; do
    [ -f "$OUT/$_f" ] || continue
    _sha=$(sha256sum "$OUT/$_f" | cut -d' ' -f1)
    _mb=$(( $(wc -c < "$OUT/$_f") / 1048576 ))
    case "$_f" in
        *Engine*) printf '      "engine_version": "%s",\n' "$ENGINE_VERSION"
                  printf '      "engine_sha256": "%s",\n'  "$_sha"
                  printf '      "engine_size_mb": %s,\n\n' "$_mb" ;;
        *Data*)   printf '      "data_version": "%s",\n'   "$DATA_VERSION"
                  printf '      "data_sha256": "%s",\n'    "$_sha"
                  printf '      "data_size_mb": %s,\n\n'   "$_mb" ;;
    esac
done

info "The engine archive is a release asset: attach it to the tag v$ENGINE_VERSION,"
info "because artifacts.json builds engine_url out of engine_version and never"
info "carries a hand-written link. The data archive goes to MEGA; put its link in"
info "data_url by hand."
printf '\n'
