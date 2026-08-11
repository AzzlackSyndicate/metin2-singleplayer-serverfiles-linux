#!/usr/bin/env bash
# Regenerate ONLY the browser filesystem: manifest + chunks + flat dist mirror.
#
# `dist-fs` depends on web/fs/manifest.bin and serve-webfs.py and on NOTHING that
# compiles, so this rebuilds the data without touching a single .cpp — which matters
# while other agents have client code in flight. No linux-x64 build is possible from
# here either: this only ever names the wasm build directory.
set -uo pipefail
mkdir -p /opt/m2wasm-wasmlogs
LOG=/opt/m2wasm-wasmlogs/w10-rebuild-fs.log
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1
echo "=== w10-rebuild-fs $(date -Is) ==="

R=/opt/m2wasm
CENSUS=/opt/m2wasm/webfs-census.txt

echo "-- desktop client:"; pgrep -f Metin2_linux-x64 >/dev/null && echo "   RUNNING (untouched)" || echo "   not running"
[ -f "$CENSUS" ] || { echo "FATAL: no census at $CENSUS"; exit 1; }
echo "-- census: $(wc -l < "$CENSUS") lines"
[ -d "$R/build-wasm-gfx" ] || { echo "FATAL: no build-wasm-gfx"; exit 1; }

# shellcheck disable=SC1091
. "$HOME/emsdk/emsdk_env.sh" >/dev/null 2>&1
command -v emcmake >/dev/null || { echo "FATAL: emcmake not on PATH"; exit 1; }

cd "$R" || exit 1

echo
echo "-- reconfigure with the census (everything else comes from the cache)"
emcmake cmake -S . -B build-wasm-gfx -DMETIN2_WEBFS_CENSUS="$CENSUS" > /tmp/w10-cfg.log 2>&1
rc=$?
tail -5 /tmp/w10-cfg.log
if [ $rc -ne 0 ]; then
    echo "FATAL: configure failed rc=$rc — full log:"; cat /tmp/w10-cfg.log; exit $rc
fi

echo
echo "-- force the webfs to rebuild (the tool changed, but be explicit)"
rm -f build-wasm-gfx/web/fs/manifest.bin

echo
echo "-- build target dist-fs ONLY (no C++ compiled)"
cmake --build build-wasm-gfx -t dist-fs -j12
rc=$?
echo "=== w10 rc=$rc $(date -Is) ==="
exit $rc
