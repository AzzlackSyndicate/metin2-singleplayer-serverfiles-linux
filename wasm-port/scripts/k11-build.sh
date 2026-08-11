#!/usr/bin/env bash
# Build the experiment tool against the PREBUILT host bimg/bx libs. Touches nothing in
# /opt/m2wasm: output goes to /opt/m2wasm-ktx/.
set -uo pipefail
B=/opt/m2wasm/extern/sdk/bgfx/upstream
L=/opt/m2wasm/build/bin/Release
D=/opt/m2wasm-ktx
mkdir -p "$D" /opt/m2wasm-wasmlogs
OUT=/opt/m2wasm-wasmlogs/k11-build.log
exec > "$OUT" 2>&1
echo "=== k11 build $(date -Is) ==="
set -x
g++ -std=c++20 -O2 -DBX_CONFIG_DEBUG=0 -o "$D/k10-ktxconv" /root/kscripts/k10-ktxconv.cpp \
    -I"$B/bimg/include" -I"$B/bx/include" \
    "$L/libbimg_encode.a" "$L/libbimg_decode.a" "$L/libbimg.a" "$L/libbx.a" \
    -lpthread -ldl -lm
rc=$?
set +x
echo "rc=$rc"
ls -la "$D" 2>&1
exit $rc
