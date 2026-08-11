#!/bin/sh
# Build linux-x64. The whole log goes to a file inside WSL -- truncating it at
# the call site once hid the only error line and cost a rebuild.
cd /opt/m2wasm || exit 1
LOG=/opt/m2wasm/build-linux.log
echo "=== tools/build-clients.sh linux-x64 -> $LOG ==="
bash tools/build-clients.sh linux-x64 > "$LOG" 2>&1
rc=$?
echo "  exit: $rc, $(wc -l < "$LOG") Zeilen"

echo
echo "=== Fehler ==="
grep -nE "error:|FAILED:|fatal" "$LOG" | head -25 | sed 's/^/  /' || echo "  keine"

echo
echo "=== letzte Zeilen ==="
tail -12 "$LOG" | sed 's/^/  /'

echo
echo "=== was im dist liegt ==="
ls -la dist/desktop/Metin2_linux-x64 2>/dev/null | sed 's/^/  /' || echo "  keine Binaerdatei"
