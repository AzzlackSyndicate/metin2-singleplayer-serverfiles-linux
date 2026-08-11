#!/bin/sh
# sdl12_compat is what the Miles port builds against, and it is missing from the
# apt list in their README. Install it, then re-run the build -- if more are
# missing the next configure names the next one.
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq libsdl1.2-compat-dev 2>/dev/null \
  || apt-get install -y -qq libsdl1.2-dev 2>/dev/null \
  || echo "  weder libsdl1.2-compat-dev noch libsdl1.2-dev installierbar"

echo "=== liefert pkg-config jetzt sdl12_compat? ==="
pkg-config --exists sdl12_compat && echo "  sdl12_compat: $(pkg-config --modversion sdl12_compat)" || echo "  sdl12_compat: FEHLT"
pkg-config --exists sdl && echo "  sdl:           $(pkg-config --modversion sdl)" || true

echo
echo "=== was noch fehlen koennte (die uebrigen pkg_check_modules im Baum) ==="
grep -rhoE 'pkg_check_modules\([^)]*' /opt/m2wasm/extern/sdk/*/CMakeLists.txt /opt/m2wasm/CMakeLists.txt 2>/dev/null \
  | grep -oE '[a-z0-9_]+$' | sort -u | while read -r m; do
      pkg-config --exists "$m" 2>/dev/null && printf '  %-16s ok\n' "$m" || printf '  %-16s FEHLT\n' "$m"
  done
