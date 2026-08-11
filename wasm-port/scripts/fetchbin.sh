#!/bin/sh
# The build stages game data out of bin/, and their layout is extracted folders
# plus index.dev -- not the .epk/.eix archives our own client carries. Rather
# than hand-build an index.dev and guess at its semantics, take theirs once.
#
# This lands ONLY in /opt/m2wasm inside WSL. None of it may ever reach our own
# repository: same rule as always, no copyrighted client bytes in git.
set -e
cd /opt/m2wasm || exit 1
echo "=== bin/ dazunehmen ==="
git sparse-checkout set src docs tools cmake extern bin
echo
echo "=== Ergebnis ==="
du -sh bin 2>/dev/null | sed 's/^/  /'
ls bin | head -12 | sed 's/^/  /'
echo "  -- pack: --"
ls bin/pack 2>/dev/null | head -8 | sed 's/^/  /'
printf '  index.dev vorhanden: %s\n' "$([ -f bin/pack/index.dev ] && echo ja || echo NEIN)"
