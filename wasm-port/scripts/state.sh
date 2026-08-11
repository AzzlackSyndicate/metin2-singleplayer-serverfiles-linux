#!/bin/sh
# The true state of the checkout. Run as a FILE: passing a script inline through
# `wsl -- bash -c` loses $variables and $(...) on this host, which produced two
# rounds of nonsense readings before I noticed.
cd /opt/m2wasm || { echo "kein /opt/m2wasm"; exit 1; }
echo "cwd     : $(pwd)"
echo "HEAD    : $(git rev-parse --short HEAD 2>&1)  ($(git rev-parse --abbrev-ref HEAD 2>&1))"
echo
echo "=== was ausgecheckt ist ==="
git sparse-checkout list 2>&1 | sed 's/^/  /'
echo
echo "=== tatsaechlich auf der Platte ==="
for d in src extern tools cmake docs bin; do
    if [ -d "$d" ]; then
        printf '  %-8s %6s Dateien  %8s\n' "$d" "$(find "$d" -type f | wc -l)" "$(du -sh "$d" | cut -f1)"
    else
        printf '  %-8s (nicht ausgecheckt)\n' "$d"
    fi
done
echo
echo "=== extern/sdk ==="
ls extern/sdk 2>/dev/null | sed 's/^/  /'
