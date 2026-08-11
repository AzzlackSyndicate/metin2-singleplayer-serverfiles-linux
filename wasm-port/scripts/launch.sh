#!/bin/bash
# Start the client under WSLg. Software rendering (llvmpipe) is expected here and
# is fine for the question being asked: does this client speak to our server.
cd /opt/m2wasm/dist/desktop || exit 1
export DISPLAY=:0
export WAYLAND_DISPLAY=wayland-0
export XDG_RUNTIME_DIR=/mnt/wslg/runtime-dir
# A modest window: llvmpipe fills pixels on the CPU, and 1024x768 is the
# difference between "slow" and "unusable" while proving exactly the same thing.
export METIN2_WIDTH=1024 METIN2_HEIGHT=768

# Record which pack chunks a real session touches. The browser build needs this
# list as its boot set -- without it every first access blocks on its own
# request, which is the difference between 18 requests before the first frame
# and several hundred. Costs nothing here: a playtest that reaches the world
# produces it as a side effect, so it rides along instead of needing its own run.
export METIN2_PACK_CENSUS=/opt/m2wasm/webfs-census.txt

# Effect tracing: one line per created effect (type, CRC, bone, handle) and one
# per instance death (name, localTime, update count). Three open questions ride
# on it -- the potion effect that may simply be too short to see, the metin
# stone's aura, and the hit flash -- and one session answers all three without
# adding more code. Costs a getenv at startup when unset.
export METIN2_BGFX_FX_NAMES=1

echo "=== Start: $(date -u '+%H:%M:%SZ') ==="
timeout 5400 ./Metin2_linux-x64 2>&1 | tail -80
echo "=== Ende, exit $? ==="
