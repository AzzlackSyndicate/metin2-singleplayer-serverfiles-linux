#!/bin/sh
# Turn the improved packet encryption off in the staged source and rebuild the
# game image. This is a TEST on the box: if it works, the change goes into the
# repository as a proper, documented switch and the installer puts it back.
#
# Reversible: the original line is kept beside the file.
set -e
F=/opt/metin2/stack/game/src/server/common/service.h
[ -f "$F" ] || { echo "nicht gefunden: $F"; exit 1; }

echo "=== vorher ==="
grep -an "IMPROVED_PACKET_ENCRYPTION" "$F" | cat -v | sed 's/^/  /'

[ -f "$F.m2orig" ] || cp -p "$F" "$F.m2orig"

# Comment the define out. Byte-wise sed: the trailing comment is EUC-KR Korean,
# which is exactly what made grep call this file binary earlier.
sed -i 's|^#define _IMPROVED_PACKET_ENCRYPTION_|//#define _IMPROVED_PACKET_ENCRYPTION_ /* off: the browser client has no key agreement */|' "$F"

echo
echo "=== nachher ==="
grep -an "IMPROVED_PACKET_ENCRYPTION" "$F" | cat -v | sed 's/^/  /'

echo
echo "=== ist es wirklich auskommentiert? ==="
if grep -aqE '^#define _IMPROVED_PACKET_ENCRYPTION_' "$F"; then
    echo "  NEIN -- die Zeile ist noch aktiv, Abbruch"; exit 1
else
    echo "  ja"
fi
