#!/bin/sh
# Step 3: CPython 3.14.7, which the client embeds. Their script compiles it from
# the release tarball fetch.sh downloaded. This is the long one before the client
# itself -- several minutes of compiling.
set -e
cd /opt/m2wasm || exit 1
echo "=== extern/sdk/python/build-linux3.sh ==="
bash extern/sdk/python/build-linux3.sh 2>&1 | tail -35
echo
echo "=== Ergebnis ==="
find extern/sdk/python -maxdepth 3 -name 'libpython*' -o -maxdepth 3 -name 'python3*' -type f 2>/dev/null | head -10 | sed 's/^/  /'
