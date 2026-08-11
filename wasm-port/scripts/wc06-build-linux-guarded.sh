#!/usr/bin/env bash
# k18: build the desktop client, but ONLY if none is running (ETXTBSY, and we must never
# kill a live one). Verifies the X11Input / IInput.h / CPythonApplication changes compile.
set -uo pipefail

if pgrep -f Metin2_linux-x64 >/dev/null 2>&1; then
    echo "REFUSING: a desktop client is running (pgrep -f Metin2_linux-x64 matched):"
    pgrep -af Metin2_linux-x64
    echo "Not building, not killing it."
    exit 3
fi

mkdir -p /opt/m2wasm-wasmlogs
LOG=/opt/m2wasm-wasmlogs/k18-build-linux.log
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1
echo "=== k18 linux-x64 $(date -Is) ==="

cd /opt/m2wasm || exit 1
tools/build-clients.sh linux-x64 -j12
rc=$?
echo "=== k18 rc=$rc $(date -Is) ==="
exit $rc
