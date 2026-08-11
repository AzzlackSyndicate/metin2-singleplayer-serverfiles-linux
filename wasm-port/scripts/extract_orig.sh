#!/bin/sh
# Unpack the stock client source somewhere that survives: /tmp in this WSL is
# wiped between invocations, which has now eaten two analyses.
set -e
Z="/mnt/c/Users/hatip/Downloads/[40250] Reference Serverfile-20260803T171606Z-1-002/[40250] Reference Serverfile/Client/ClientVS22.zip"
D=/opt/m2origclient
if [ -d "$D/ClientVS22/source" ]; then
    echo "  schon ausgepackt: $(du -sh "$D" | cut -f1)"
else
    rm -rf "$D"; mkdir -p "$D"
    unzip -q "$Z" -d "$D"
    echo "  ausgepackt: $(du -sh "$D" | cut -f1)"
fi

O="$D/ClientVS22/source"
S=/opt/m2port/port40250/server/game/src

echo
echo "=== Groessen ==="
wc -l "$O/EterBase/cipher.cpp" "$O/EterBase/cipher.h" "$S/cipher.cpp" "$S/cipher.h" 2>/dev/null | sed 's/^/  /'

echo
echo "=== cipher.h: Client gegen Server ==="
if diff -q "$O/EterBase/cipher.h" "$S/cipher.h" >/dev/null 2>&1; then
    echo "  identisch"
else
    diff "$O/EterBase/cipher.h" "$S/cipher.h" 2>&1 | head -20 | sed 's/^/    /'
fi

echo
echo "=== cipher.cpp: Client gegen Server ==="
if diff -q "$O/EterBase/cipher.cpp" "$S/cipher.cpp" >/dev/null 2>&1; then
    echo "  identisch -- dieselbe Datei auf beiden Seiten"
else
    printf '  %s abweichende Zeilen\n' "$(diff "$O/EterBase/cipher.cpp" "$S/cipher.cpp" | grep -c '^[<>]')"
    diff "$O/EterBase/cipher.cpp" "$S/cipher.cpp" 2>&1 | head -30 | sed 's/^/    /'
fi
