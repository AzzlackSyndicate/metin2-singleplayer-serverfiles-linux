#!/bin/sh
# Step 2: the third-party sources. Granny, SpeedTree, Miles, minimp3 and the four
# bgfx repositories, all public GitHub clones at pinned commits. Several hundred MB,
# so this is the long one.
set -e
cd /opt/m2wasm || exit 1
echo "=== extern/sdk/fetch.sh ==="
bash extern/sdk/fetch.sh 2>&1 | tail -40
echo
echo "=== was gelandet ist ==="
for d in granny speedtree miles minimp3 bgfx; do
    if [ -d "extern/sdk/$d/upstream" ]; then
        printf '  %-10s %s\n' "$d" "$(du -sh "extern/sdk/$d/upstream" 2>/dev/null | cut -f1)"
    else
        printf '  %-10s FEHLT\n' "$d"
    fi
done
