#!/bin/sh
# Port the stock client's cipher into the Old-Metin2 tree.
#
# Copied rather than rewritten on purpose: this is the exact counterpart the
# server was tested against, and a re-derivation of a Diffie-Hellman exchange
# plus a CTR-mode block cipher would have to be bit-identical to be worth
# anything.
#
# The only edits are the ones the different tree layout forces:
#   - stdafx.h        this tree has no precompiled header
#   - "cipher.h"      now lives under the public include directory
#   - __THEMIDA__     a Windows-only code-protection SDK; the blocks are removed
#                     WHOLE. Deleting only the #ifdef lines left nine orphaned
#                     #endif behind, which is how this script failed the first
#                     time.
set -e
O=/opt/m2origclient/ClientVS22/source/EterBase
T=/opt/m2wasm/src/NetworkLib

[ -f "$O/cipher.cpp" ] || { echo "Original nicht gefunden: $O/cipher.cpp"; exit 1; }

cp "$O/cipher.h" "$T/include/NetworkLib/cipher.h"

awk '
    /^[[:space:]]*#[[:space:]]*ifdef[[:space:]]+__THEMIDA__/ { skip = 1; next }
    skip && /^[[:space:]]*#[[:space:]]*endif/               { skip = 0; next }
    skip                                                     { next }
    /#include "stdafx.h"/                                    { next }
    /#include <cryptopp\/cryptoppLibLink.h>/                 { next }
    /#include "Debug.h"/                                     { next }
    { gsub(/#include "cipher.h"/, "#include \"NetworkLib/cipher.h\""); print }
' "$O/cipher.cpp" > "$T/src/cipher.cpp"

echo "=== Groessen ==="
wc -l "$O/cipher.cpp" "$T/src/cipher.cpp" | sed 's/^/  /'

echo
echo "=== Praeprozessor-Bilanz der portierten Datei ==="
o=$(grep -cE '^[[:space:]]*#[[:space:]]*(if|ifdef|ifndef)' "$T/src/cipher.cpp")
c=$(grep -cE '^[[:space:]]*#[[:space:]]*endif' "$T/src/cipher.cpp")
printf '  oeffnend: %s   schliessend: %s   %s\n' "$o" "$c" \
    "$([ "$o" = "$c" ] && echo ausgeglichen || echo 'UNGLEICH')"

echo
echo "=== Reste ==="
grep -n "Themida\|VM_START\|VM_END\|STR_ENCRYPT\|stdafx\|Debug.h" "$T/src/cipher.cpp" | sed 's/^/  /' || echo "  keine"
