#!/usr/bin/env bash
# Build the wasm-x32 (browser) client via the sanctioned wrapper.
#
# SAFETY: build-clients.sh's build_wasm() calls need_host_shaderc(), which would build
# linux-x64 if no host shaderc existed — that must NEVER happen here, the desktop client
# is running and a relink would hit ETXTBSY. So we assert shaderc is present FIRST and
# refuse to run otherwise. The wasm target itself builds into build-wasm-gfx/ and stages
# into dist/browser (or dist/wasm32); it never touches build/ or dist/desktop.
set -uo pipefail
mkdir -p /opt/m2wasm-wasmlogs
LOG=/opt/m2wasm-wasmlogs/w05-build-wasm.log
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1
echo "=== w05-build-wasm $(date -Is) ==="

R=/opt/m2wasm
SHADERC=""
for c in "$R/build/bin/Release/shaderc" "$R/build/bin/shaderc"; do
    [ -x "$c" ] && SHADERC="$c" && break
done
if [ -z "$SHADERC" ]; then
    echo "REFUSING: no host shaderc. Running build-clients.sh would trigger a linux-x64"
    echo "build, and the desktop client is live. Build only the shaderc target by hand."
    exit 3
fi
echo "-- host shaderc: $SHADERC"

# shellcheck disable=SC1091
. "$HOME/emsdk/emsdk_env.sh" >/dev/null 2>&1
command -v emcmake >/dev/null || { echo "FATAL: emcmake not on PATH"; exit 1; }
emcc --version | head -1
go version

[ -d "$R/extern/sdk/wasm-deps" ] || { echo "FATAL: no extern/sdk/wasm-deps"; exit 1; }
PYA="$R/extern/sdk/python/upstream3/cross-build/wasm32-emscripten/build/python/libpython3.14.a"
[ -f "$PYA" ] || { echo "FATAL: no wasm libpython at $PYA"; exit 1; }

cd "$R" || exit 1
tools/build-clients.sh wasm-x32 -j12
rc=$?
echo "=== w05 rc=$rc $(date -Is) ==="
echo "-- output:"
ls -la "$R/build-wasm-gfx/web" 2>&1 | head -20
ls -la "$R/dist" 2>&1
exit $rc
