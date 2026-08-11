#!/bin/sh
# Step 1: the build dependencies. The first list is the one their README names for
# linux-x64; the second is what CPython 3.14 needs to configure (their build script
# compiles it from a release tarball); mesa-utils is only so we can ask WSLg what
# OpenGL it is really giving us before blaming the client for a black window.
set -e
export DEBIAN_FRONTEND=noninteractive

echo "=== apt update ==="
apt-get update -qq

echo "=== die Liste aus deren README ==="
apt-get install -y -qq \
    build-essential cmake ninja-build pkg-config \
    liblzo2-dev libzip-dev libspdlog-dev libfmt-dev libcrypto++-dev \
    libmsgsl-dev libgtest-dev libdevil-dev libx11-dev

echo "=== was CPython zum Bauen braucht ==="
apt-get install -y -qq \
    zlib1g-dev libssl-dev libffi-dev libbz2-dev liblzma-dev \
    libreadline-dev libsqlite3-dev uuid-dev

echo "=== Diagnose-Werkzeuge fuer die Grafik ==="
apt-get install -y -qq mesa-utils libgl1-mesa-dri libglx-mesa0 || true

echo
echo "=== Ergebnis ==="
for t in cmake ninja pkg-config glxinfo; do
    printf '  %-12s %s\n' "$t" "$(command -v "$t" 2>/dev/null || echo FEHLT)"
done
printf '  cmake        %s\n' "$(cmake --version 2>/dev/null | head -1)"
printf '  ninja        %s\n' "$(ninja --version 2>/dev/null)"

echo
echo "=== und was WSLg an OpenGL liefert ==="
glxinfo -B 2>/dev/null | grep -iE 'renderer|OpenGL version|Device' | head -4 | sed 's/^/  /' \
  || echo "  (glxinfo ohne Ausgabe -- pruefen wir spaeter mit gesetztem DISPLAY)"
