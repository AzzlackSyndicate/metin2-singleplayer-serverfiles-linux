#!/usr/bin/env bash
#
# build-deps-40250.sh -- build the 32-bit Linux static dependency tree for the
#                        Metin2 "40250" server fork, replacing the FreeBSD
#                        prebuilts that ship in port40250/extern/.
#
# Target: Ubuntu 24.04 (noble) x86_64 host with i386 multiarch enabled.
# Output: $PREFIX (default /opt/m2port/extern/Linux40250), laid out exactly the
#         way the 40250 Makefiles expect:
#
#           $PREFIX/include/{IL,boost,cryptopp,mysql}/   <- -I../../../extern/include
#           $PREFIX/lib/*.a                              <- -L../../../extern/lib
#           $PREFIX/lua/include/{lua,lualib,lauxlib}.h   \ mirror of the in-tree
#           $PREFIX/lua/lib/{liblua,liblualib}.a         / server/liblua module
#
# The stock Makefiles reach Lua through -I../../liblua/include /
# -L../../liblua/lib, i.e. the module directory itself.  $PREFIX/lua/ is a
# freshly built 32-bit mirror of it so the whole dependency set is in one place
# and so the module's FreeBSD prebuilts never get linked by accident; point the
# ported Makefiles at either.
#
# Usage:
#   ./build-deps-40250.sh                       # everything (apt + all + verify)
#   ./build-deps-40250.sh zlib openssl cryptopp # only these components
#   ./build-deps-40250.sh --force lua           # rebuild lua even if stamped
#   ./build-deps-40250.sh clean                 # drop stamps + $PREFIX contents
#   ./build-deps-40250.sh verify                # re-run the verification suite
#   PREFIX=/tmp/stage ./build-deps-40250.sh     # build into an alternate prefix
#
# Components: apt zlib openssl libmd lua cryptopp mysql devil boost verify
#
# ===========================================================================
# NOTES / gotchas -- READ BEFORE CHANGING VERSIONS
# ===========================================================================
#
# * LUA IS 5.0.3, AND IT IS A FORK. Do not substitute an upstream tarball.
#   The game calls lua_open / lua_dostring / lua_ref / lua_unref (all gone in
#   5.1) and links -llualib (5.0 only). More importantly, 40250 ships its own
#   patched copy at server/liblua/ whose lexer (src/llex.{c,h}) adds the quest
#   engine's reserved words -- quest / state / with / when -- renames the "do"
#   keyword to "begin" (keeping "do" as a *preserved* token), and widens
#   identifier characters to bytes >= 0xa0 so EUC-KR identifiers lex.  A stock
#   lua-5.0.3 build produces a byte-identical liblua.a EXCEPT for llex.o, and
#   would silently fail to parse every quest script.  => we build from the
#   in-tree module.  Evidence: see the "lua provenance" note at the bottom.
#
# * NO SYSTEM LZO IS NEEDED. 40250 vendors miniLZO 1.08 in
#   server/game/src/minilzo.c together with its own headers in
#   server/game/src/lzo/.  lzoconf.h there declares LZO_VERSION 0x1080 and
#   minilzo.c hard-#errors on MINILZO_VERSION != 0x1080.  Nothing in the tree
#   passes -llzo/-llzo2 and nothing includes a *system* <lzo/...>.  Do NOT add
#   liblzo2 -- a 2.x header on the include path ahead of game/src/lzo would
#   break the build.  (The sister 2014 fork needed lzo 2.09; 40250 does not.)
#
# * MARIADB CONNECTOR/C 3.3.10, NOT MySQL 8.  libsql/AsyncSQL.cpp does
#   "my_bool reconnect = true; mysql_options(&h, MYSQL_OPT_RECONNECT, ...)".
#   MySQL 8 deleted my_bool and deprecated MYSQL_OPT_RECONNECT; MariaDB C/C
#   3.3.10 still ships "typedef char my_bool;" in mysql.h.  libmysqlclient.a is
#   a *byte copy* of libmariadb.a so the historical -lmysqlclient / explicit
#   /usr/local/lib/mysql/libmysqlclient.a references keep resolving.  Never put
#   both on the same link line -- every symbol would be defined twice.
#
# * NEVER define _TIME_BITS=64 anywhere.  -D_FILE_OFFSET_BITS=64 on its own is
#   fine, but _TIME_BITS=64 switches glibc to 64-bit time_t on i386, which does
#   not match the ABI of these prebuilt archives (or of libstdc++:i386) and
#   produces silent struct-layout corruption.  guard_time_bits() below aborts
#   the build if it ever leaks in through the environment.
#
# * BOOST IS 1.72.0 AND HEADER-ONLY.  40250 ships boost_1_72_0.tar.gz; the host
#   has Boost 1.83 in /usr/include which must NOT be used (1.83 dropped several
#   headers 40250 includes).  No i386 boost *binaries* exist or are needed --
#   the server links no -lboost_*.  We extract headers only.
#
# * THE SHIPPED TARBALLS ARE PLAIN GZIP despite what their names suggest being
#   ambiguous; some forks ship bzip2 payloads under a .gz name.  Always use
#   `tar xf` (auto-detect), never `tar xzf`, so either works.
#
# * DEVIL IS REQUIRED but only for TGA.  game/src/MarkImage.cpp does
#   ilTexImage/ilSave(IL_TGA)/ilLoad and main.cpp calls ilInit().  We build with
#   PNG/JPEG/TIFF/LCMS/MNG/JP2/EXR/WDP explicitly OFF so libIL.a is fully
#   self-contained: it needs nothing but libstdc++ and libc.  Leaving those to
#   cmake's auto-detection is non-deterministic (it happily finds the *64-bit*
#   libpng and then fails to link -m32), which is why they are forced here.
#
# * -lmd IS ON THE 40250 GAME LINK LINE (game/src/Makefile: LIBS = -pthread -lm
#   -lmd).  libmd.a is provided so that resolves.  Be aware that libthecore's
#   xmd5.h and /usr/include/md5.h both define MD5_CTX; if you keep -lmd you must
#   make sure only one of the two is included per TU.
#
# * ILU/ILUT are built and installed for completeness; the server links only
#   libIL.a.  libILUT.a is ~17 KB because every backend (OpenGL, X11, SDL,
#   DirectX, Allegro) compiles to an empty TU without those SDKs.  That is
#   expected, not a broken build.
# ===========================================================================
set -euo pipefail

PREFIX="${PREFIX:-/opt/m2port/extern/Linux40250}"
BUILD="${BUILD:-/opt/m2port/build40250}"
PORT="${PORT:-/opt/m2port/port40250}"          # the 40250 source tree
SRC="$BUILD/src"
LOG="$BUILD/log"
STAMP="$BUILD/stamp"
STAGE="$BUILD/stage"
JOBS="${JOBS:-$(nproc)}"
FORCE="${FORCE:-0}"

# --- where the 40250 tree keeps things we consume --------------------------
EXTERN_TARBALLS="$PORT/extern"                 # boost/cryptopp/devil sources
EXTERN_INC="$PORT/extern/include"              # shipped headers (authoritative)
LIBLUA_SRC="$PORT/server/liblua"               # the forked Lua 5.0.3 module
GAME_SRC="$PORT/server/game/src"               # for the minilzo header check

# --- pinned versions -------------------------------------------------------
LUA_VER="5.0.3-40250fork"   # in-tree server/liblua; lua.h says "Lua 5.0.3"
BOOST_TARBALL="boost_1_72_0.tar.gz"            # BOOST_LIB_VERSION "1_72"
CRYPTOPP_TARBALL="cryptopp_8_4_0.tar.gz"       # CRYPTOPP_VERSION 840
DEVIL_TARBALL="devil_1_8_0_patched.tar.gz"     # IL_VERSION 180
MARIADB_VER="3.3.10"                           # MARIADB_PACKAGE_VERSION 3.3.10
# MariaDB C/C bakes its cmake install prefix into ma_client_plugin.c as
# PLUGINDIR and into mariadb_version.h as MARIADB_PLUGINDIR.  Left alone that
# leaks $BUILD into libmariadb.a and makes the archive non-reproducible across
# build directories (observed: a 32-byte delta in ma_client_plugin.c.o and
# nothing else).  Every auth plugin is linked STATIC here, so the directory is
# never consulted at runtime -- pin it to a stable literal.
MARIADB_PLUGINDIR="${MARIADB_PLUGINDIR:-/usr/local/lib/mariadb/plugin}"

M32="-m32"
SYSLIB32="/usr/lib/i386-linux-gnu"
export PKG_CONFIG_LIBDIR="$SYSLIB32/pkgconfig"
export PKG_CONFIG_PATH="$SYSLIB32/pkgconfig"
export LC_ALL=C

ALL_COMPONENTS=(apt zlib openssl libmd lua cryptopp mysql devil boost verify)

# ---------------------------------------------------------------------------
# logging helpers
# ---------------------------------------------------------------------------
say()  { printf '=== %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '    OK   %s\n' "$*"; }
warn() { printf '    WARN %s\n' "$*" >&2; }
fail() { printf '!!! FAIL %s\n' "$*" >&2; }
die()  { fail "$*"; exit 1; }

RC=0

# ---------------------------------------------------------------------------
# hard constraint: _TIME_BITS=64 must never reach a compiler here.
# ---------------------------------------------------------------------------
guard_time_bits() {
  local v
  for v in CFLAGS CXXFLAGS CPPFLAGS DEB_CFLAGS_SET DEB_CPPFLAGS_SET; do
    case "${!v:-}" in
      *_TIME_BITS*) die "$v contains _TIME_BITS -- refusing to build (see notes)";;
    esac
  done
}

# ---------------------------------------------------------------------------
# stamping / idempotency
# ---------------------------------------------------------------------------
stamped()   { [ "$FORCE" = 1 ] && return 1; [ -f "$STAMP/$1.done" ]; }
mark_done() { mkdir -p "$STAMP"; date -u +%FT%TZ > "$STAMP/$1.done"; }

# extract a shipped tarball into $SRC/<name>/ (auto-detects gz/bz2/xz)
untar_shipped() { # tarball-basename dest-subdir
  local tb="$EXTERN_TARBALLS/$1" dst="$SRC/$2"
  [ -f "$tb" ] || { fail "missing shipped tarball $tb"; return 1; }
  rm -rf "$dst"; mkdir -p "$dst"
  tar xf "$tb" -C "$dst" || { fail "extract $tb"; return 1; }   # NB: xf, not xzf
}

fetch() { # url filename
  local url="$1" out="$SRC/$2"
  [ -s "$out" ] && return 0
  mkdir -p "$SRC"
  wget -q -O "$out.part" "$url" && mv "$out.part" "$out"
}

# copy the shipped 40250 headers over what we just installed.  They are
# byte-identical to the tarball headers for every common file and additionally
# carry cryptoppLibLink.h / il_wrap.h / ilu_region.h / ilut_config.h /
# devil_internal_exports.h / devil_cpp_wrapper.hpp, which the tarballs' install
# rules do not emit but the 40250 sources include.
overlay_shipped_headers() { # subdir (IL|cryptopp|boost)
  local sub="$1"
  [ -d "$EXTERN_INC/$sub" ] || return 0
  install -d "$PREFIX/include/$sub"
  cp -a "$EXTERN_INC/$sub/." "$PREFIX/include/$sub/"
}

# ---------------------------------------------------------------------------
# 0. apt prerequisites
#
# Derived empirically from what the builds below actually consume:
#   gcc-multilib/g++-multilib/libc6-dev-i386/linux-libc-dev:i386 -> -m32 at all
#   cmake+make+git+wget+ca-certificates                          -> drivers
#   zlib1g-dev:i386  -> libz.a AND MariaDB's -DWITH_EXTERNAL_ZLIB=ON
#   libssl-dev:i386  -> libssl.a/libcrypto.a AND MariaDB -DWITH_SSL=OPENSSL
#   libmd-dev:i386   -> libmd.a (game/src/Makefile passes -lmd)
#   file, binutils   -> the 32-bit verification below (file/ar/nm/size)
# Deliberately NOT required: libpng/libjpeg/libtiff/liblcms2 (DevIL builds with
# those codecs off) and liblzo2 (40250 vendors miniLZO 1.08).
# ---------------------------------------------------------------------------
APT_PKGS="build-essential gcc-multilib g++-multilib libc6-dev-i386
linux-libc-dev:i386 cmake make git wget ca-certificates pkg-config file
binutils zlib1g-dev:i386 libssl-dev:i386 libmd-dev:i386"

c_apt() {
  if stamped apt; then info "apt prerequisites already satisfied (stamped)"; return 0; fi
  say "apt prerequisites"
  dpkg --add-architecture i386
  apt-get update -qq
  # shellcheck disable=SC2086
  apt-get install -y --no-install-recommends $APT_PKGS || { fail "apt-get install"; return 1; }
  mark_done apt
}

# ---------------------------------------------------------------------------
# 1..3  system i386 static archives.  These are Ubuntu's own -dev:i386 builds;
#       they are copied verbatim so the whole dependency set lives under one
#       -L directory and the tree is self-describing.
# ---------------------------------------------------------------------------
copy_syslib() { # libname.a
  [ -f "$SYSLIB32/$1" ] || { fail "$SYSLIB32/$1 missing -- run the 'apt' component first"; return 1; }
  install -d "$PREFIX/lib"
  install -m644 "$SYSLIB32/$1" "$PREFIX/lib/$1"
  info "$1  <- $(dpkg -S "$SYSLIB32/$1" 2>/dev/null | cut -d: -f1 || echo '?')"
}

c_zlib() {
  if stamped zlib && [ -f "$PREFIX/lib/libz.a" ]; then info "zlib up to date"; return 0; fi
  say "zlib (32-bit, from zlib1g-dev:i386)"
  copy_syslib libz.a || return 1
  mark_done zlib
}

c_openssl() {
  if stamped openssl && [ -f "$PREFIX/lib/libssl.a" ] && [ -f "$PREFIX/lib/libcrypto.a" ]; then
    info "openssl up to date"; return 0
  fi
  say "openssl (32-bit, from libssl-dev:i386)"
  copy_syslib libcrypto.a || return 1
  copy_syslib libssl.a    || return 1
  mark_done openssl
}

c_libmd() {
  if stamped libmd && [ -f "$PREFIX/lib/libmd.a" ]; then info "libmd up to date"; return 0; fi
  say "libmd (32-bit, from libmd-dev:i386)  -- game/src/Makefile passes -lmd"
  copy_syslib libmd.a || return 1
  mark_done libmd
}

# ---------------------------------------------------------------------------
# 4. Lua 5.0.3, 40250 fork  ->  lua/lib/liblua.a + liblualib.a
#
# Built from $PORT/server/liblua, NOT from lua.org.  See the header notes.
# The module ships stale FreeBSD .o/.a artefacts; they are removed first so
# `ar r` cannot leave a FreeBSD member behind in a mixed archive.
# ---------------------------------------------------------------------------
c_lua() {
  if stamped lua && [ -f "$PREFIX/lua/lib/liblua.a" ] && [ -f "$PREFIX/lua/lib/liblualib.a" ]; then
    info "lua up to date"; return 0
  fi
  say "lua $LUA_VER (32-bit, from in-tree $LIBLUA_SRC)"
  [ -d "$LIBLUA_SRC/src" ] || { fail "in-tree liblua module not found at $LIBLUA_SRC"; return 1; }
  local d="$SRC/liblua40250"
  rm -rf "$d"; mkdir -p "$SRC"
  cp -a "$LIBLUA_SRC" "$d"
  cd "$d" || return 1
  find . -name '*.o' -delete
  rm -f lib/*.a
  install -d lib
  # -m32 must live in CC: Lua 5.0's Makefiles link with plain $(CC) and never
  # pass MYLDFLAGS, so -m32 in MYCFLAGS alone is not enough.
  # -fno-strict-aliasing: gcc>=6 miscompiles Lua 5.0's tagged-union TObject.
  # -std=gnu89: the sources are K&R-ish and trip C99+ implicit-decl errors.
  # The shipped config says "CC= clang-devel" (a FreeBSD-ism) -- overwrite it.
  sed -i \
    -e 's|^CC=.*|CC= gcc -m32|' \
    -e 's|^MYCFLAGS=.*|MYCFLAGS= -O2 -fno-strict-aliasing -fPIC -std=gnu89|' \
    -e 's|^MYLDFLAGS=.*|MYLDFLAGS= -m32|' \
    -e 's|^WARN=.*|WARN= -w|' \
    config
  mkdir -p "$LOG"
  make -j"$JOBS" > "$LOG/lua.log" 2>&1 || { fail "lua build (see $LOG/lua.log)"; return 1; }
  install -d "$PREFIX/lua/lib" "$PREFIX/lua/include"
  install -m644 lib/liblua.a lib/liblualib.a "$PREFIX/lua/lib/"
  install -m644 include/lua.h include/lualib.h include/lauxlib.h "$PREFIX/lua/include/"
  mark_done lua
}

# ---------------------------------------------------------------------------
# 5. Crypto++ 8.4.0  ->  lib/libcryptopp.a  + include/cryptopp/*.h
#    Source is the shipped cryptopp_8_4_0.tar.gz (CRYPTOPP_VERSION 840).
#    IS_X86/IS_X64 must be forced: the makefile probes `uname -m`, sees x86_64
#    and enables -m64-only SIMD translation units.
# ---------------------------------------------------------------------------
c_cryptopp() {
  if stamped cryptopp && [ -f "$PREFIX/lib/libcryptopp.a" ]; then info "cryptopp up to date"; return 0; fi
  say "cryptopp 8.4.0 (32-bit, from $CRYPTOPP_TARBALL)"
  untar_shipped "$CRYPTOPP_TARBALL" cryptopp_8_4_0 || return 1
  local d="$SRC/cryptopp_8_4_0/cryptopp_8_4_0"
  [ -d "$d" ] || d=$(find "$SRC/cryptopp_8_4_0" -maxdepth 2 -name cryptlib.cpp -printf '%h\n' | head -1)
  cd "$d" || return 1
  mkdir -p "$LOG"
  make -j"$JOBS" static \
      CXX="g++ $M32" \
      CXXFLAGS="-DNDEBUG -O2 -fPIC $M32 -std=c++17 -w -march=i686" \
      IS_X86=1 IS_X64=0 \
      > "$LOG/cryptopp.log" 2>&1 || { fail "cryptopp build (see $LOG/cryptopp.log)"; return 1; }
  install -d "$PREFIX/lib" "$PREFIX/include/cryptopp"
  install -m644 libcryptopp.a "$PREFIX/lib/"
  install -m644 ./*.h "$PREFIX/include/cryptopp/"
  overlay_shipped_headers cryptopp        # adds cryptoppLibLink.h
  mark_done cryptopp
}

# ---------------------------------------------------------------------------
# 6. MariaDB Connector/C 3.3.10 -> lib/libmariadb.a (+ libmysqlclient.a copy)
#    Headers land in include/mysql/ so `#include <mysql/mysql.h>` and the
#    Makefiles' -I../../../extern/include both work.
#    All auth plugins are linked STATIC: a dynamic plugin dir does not exist on
#    a deployed server and mysql_real_connect() would fail on auth negotiation.
# ---------------------------------------------------------------------------
c_mysql() {
  if stamped mysql && [ -f "$PREFIX/lib/libmariadb.a" ] && [ -f "$PREFIX/lib/libmysqlclient.a" ]; then
    info "mysql up to date"; return 0
  fi
  say "mariadb-connector-c $MARIADB_VER (32-bit)"
  fetch "https://codeload.github.com/mariadb-corporation/mariadb-connector-c/tar.gz/refs/tags/v$MARIADB_VER" \
        "mariadb-connector-c-$MARIADB_VER.tar.gz" || { fail "mariadb download"; return 1; }
  rm -rf "$SRC/mariadb-connector-c-$MARIADB_VER"
  tar xf "$SRC/mariadb-connector-c-$MARIADB_VER.tar.gz" -C "$SRC" || return 1
  cd "$SRC/mariadb-connector-c-$MARIADB_VER" || return 1
  rm -rf build && mkdir build && cd build || return 1
  mkdir -p "$LOG"
  {
    cmake .. \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_C_FLAGS="$M32 -O2 -fPIC" \
      -DCMAKE_CXX_FLAGS="$M32 -O2 -fPIC" \
      -DCMAKE_EXE_LINKER_FLAGS="$M32" \
      -DCMAKE_SYSTEM_PROCESSOR=i686 \
      -DCMAKE_INSTALL_PREFIX="$STAGE/mariadb" \
      -DWITH_UNIT_TESTS=OFF -DWITH_SSL=OPENSSL -DWITH_EXTERNAL_ZLIB=ON \
      -DWITH_CURL=OFF \
      -DPLUGINDIR="$MARIADB_PLUGINDIR" \
      -DCLIENT_PLUGIN_DIALOG=STATIC \
      -DCLIENT_PLUGIN_MYSQL_CLEAR_PASSWORD=STATIC \
      -DCLIENT_PLUGIN_CACHING_SHA2_PASSWORD=STATIC \
      -DCLIENT_PLUGIN_SHA256_PASSWORD=STATIC \
      -DCLIENT_PLUGIN_AUTH_GSSAPI_CLIENT=OFF \
      -DCLIENT_PLUGIN_REMOTE_IO=OFF &&
    # mariadb_version.h.in expands "@CMAKE_INSTALL_PREFIX@/@INSTALL_PLUGINDIR@"
    # and ma_client_plugin.c compiles that string in as PLUGINDIR.  -DPLUGINDIR
    # does NOT reach it (the CMakeLists variable of that name feeds a different
    # code path), so patch the *generated* header before the first compile.
    # Without this, $BUILD leaks into libmariadb.a and the archive is not
    # reproducible across build directories.
    sed -i "s|^#define MARIADB_PLUGINDIR .*|#define MARIADB_PLUGINDIR \"$MARIADB_PLUGINDIR\"|" \
        include/mariadb_version.h &&
    grep -q "MARIADB_PLUGINDIR \"$MARIADB_PLUGINDIR\"" include/mariadb_version.h &&
    make -j"$JOBS" mariadbclient && make install
  } > "$LOG/mariadb.log" 2>&1 || { fail "mariadb build (see $LOG/mariadb.log)"; return 1; }
  install -d "$PREFIX/lib" "$PREFIX/include/mysql"
  local a
  a=$(find "$STAGE/mariadb" -name 'libmariadbclient.a' | head -1)
  [ -n "$a" ] || { fail "libmariadbclient.a not produced"; return 1; }
  install -m644 "$a" "$PREFIX/lib/libmariadb.a"
  # Historical name.  A byte-for-byte copy, NOT a symlink: the FreeBSD tree had
  # two real files and some Makefiles reference the absolute path
  # /usr/local/lib/mysql/libmysqlclient.a.  Never link both at once.
  cp "$PREFIX/lib/libmariadb.a" "$PREFIX/lib/libmysqlclient.a"
  cp -a "$STAGE/mariadb/include/mariadb/." "$PREFIX/include/mysql/"
  grep -q "MARIADB_PLUGINDIR \"$MARIADB_PLUGINDIR\"" "$PREFIX/include/mysql/mariadb_version.h" \
    || { fail "MARIADB_PLUGINDIR was not pinned -- archive is not reproducible"; return 1; }
  mark_done mysql
}

# ---------------------------------------------------------------------------
# 7. DevIL 1.8.0 (40250's patched drop) -> lib/libIL.a libILU.a libILUT.a
#    Every optional codec is forced OFF; see the header notes.
# ---------------------------------------------------------------------------
c_devil() {
  if stamped devil && [ -f "$PREFIX/lib/libIL.a" ]; then info "devil up to date"; return 0; fi
  say "DevIL 1.8.0 patched (32-bit, from $DEVIL_TARBALL)"
  untar_shipped "$DEVIL_TARBALL" devil_1_8_0_patched || return 1
  local d
  d=$(find "$SRC/devil_1_8_0_patched" -maxdepth 4 -type d -name DevIL -exec test -f '{}/CMakeLists.txt' \; -print | head -1)
  [ -n "$d" ] || { fail "DevIL cmake root not found"; return 1; }
  cd "$d" || return 1
  rm -rf build && mkdir build && cd build || return 1
  mkdir -p "$LOG"
  {
    cmake .. \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_C_FLAGS="$M32 -O2 -fPIC -w" \
      -DCMAKE_CXX_FLAGS="$M32 -O2 -fPIC -w" \
      -DCMAKE_EXE_LINKER_FLAGS="$M32" \
      -DCMAKE_INSTALL_PREFIX="$STAGE/devil" \
      -DBUILD_SHARED_LIBS=OFF \
      -DIL_NO_PNG=ON -DIL_NO_JPG=ON -DIL_NO_TIF=ON -DIL_NO_LCMS=ON \
      -DIL_NO_MNG=ON -DIL_NO_JP2=ON -DIL_NO_EXR=ON -DIL_NO_WDP=ON \
      -DIL_USE_DXTC_NVIDIA=OFF -DIL_USE_DXTC_SQUISH=OFF &&
    make -j"$JOBS" && make install
  } > "$LOG/devil.log" 2>&1 || { fail "DevIL build (see $LOG/devil.log)"; return 1; }
  install -d "$PREFIX/lib" "$PREFIX/include/IL"
  find "$STAGE/devil" -name 'libIL*.a' -exec install -m644 '{}' "$PREFIX/lib/" \;
  cp -a "$STAGE/devil/include/IL/." "$PREFIX/include/IL/"
  overlay_shipped_headers IL              # adds il_wrap.h, ilut_config.h, ...
  mark_done devil
}

# ---------------------------------------------------------------------------
# 8. Boost 1.72.0 headers (header-only; no i386 binaries exist or are needed)
# ---------------------------------------------------------------------------
c_boost() {
  if stamped boost && [ -f "$PREFIX/include/boost/version.hpp" ]; then info "boost up to date"; return 0; fi
  say "boost 1.72.0 headers (from $BOOST_TARBALL)"
  untar_shipped "$BOOST_TARBALL" boost_1_72_0 || return 1
  local b
  b=$(find "$SRC/boost_1_72_0" -maxdepth 3 -type d -name boost -exec test -f '{}/version.hpp' \; -print | head -1)
  [ -n "$b" ] || { fail "boost header dir not found"; return 1; }
  install -d "$PREFIX/include"
  rm -rf "$PREFIX/include/boost"
  cp -a "$b" "$PREFIX/include/boost"
  overlay_shipped_headers boost
  grep -q 'BOOST_LIB_VERSION "1_72"' "$PREFIX/include/boost/version.hpp" \
    || { fail "installed boost is not 1.72"; return 1; }
  mark_done boost
}

# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------
c_clean() {
  say "clean: removing stamps and $PREFIX contents"
  case "$PREFIX" in /|/usr|/opt|"") die "refusing to clean PREFIX=$PREFIX";; esac
  rm -rf "$STAMP" "$PREFIX/lib" "$PREFIX/include" "$PREFIX/lua"
  rm -rf "$STAGE" "$BUILD/verify"
  info "sources kept in $SRC (delete manually for a truly cold rebuild)"
}

# ===========================================================================
# verification
# ===========================================================================
V_DIR="$BUILD/verify"

# every archive member must be ELF 32-bit LSB relocatable, Intel 80386
check_arch() { # archive
  local a="$1" m t
  m=$(ar t "$a" 2>/dev/null | grep -m1 '\.o$') || true
  [ -n "$m" ] || { printf '  %-46s BAD (no object members)\n' "${a#"$PREFIX"/}"; return 1; }
  t=$(ar p "$a" "$m" 2>/dev/null | file - | sed 's|^[^:]*: ||')
  case "$t" in
    *"ELF 32-bit LSB relocatable, Intel 80386"*)
      printf '  %-46s OK  32-bit x86  (%s)\n' "${a#"$PREFIX"/}" "$m"; return 0;;
    *)
      printf '  %-46s BAD (%s)\n' "${a#"$PREFIX"/}" "$t"; return 1;;
  esac
}

vrun() { # name  source  extra-flags...
  local name="$1" src="$2"; shift 2
  mkdir -p "$V_DIR"
  printf '%s\n' "$src" > "$V_DIR/$name.cpp"
  if g++ -m32 -std=c++17 -w -o "$V_DIR/$name" "$V_DIR/$name.cpp" "$@" 2> "$V_DIR/$name.err"; then
    if "$V_DIR/$name" > "$V_DIR/$name.out" 2>&1; then
      printf '  %-12s LINK+RUN OK   %s\n' "$name" "$(head -1 "$V_DIR/$name.out")"; return 0
    fi
    printf '  %-12s LINK OK, RUN FAILED  (%s)\n' "$name" "$(head -1 "$V_DIR/$name.out")"; return 1
  fi
  printf '  %-12s LINK FAILED (see %s)\n' "$name" "$V_DIR/$name.err"; return 1
}

c_verify() {
  local bad=0 a
  mkdir -p "$V_DIR"

  say "1) architecture: every .a under $PREFIX must be 32-bit x86"
  while read -r a; do check_arch "$a" || bad=1; done < <(find "$PREFIX" -name '*.a' | sort)

  say "2) pinned versions"
  printf '  %-12s %s\n' lua      "$(grep -m1 'define LUA_VERSION' "$PREFIX/lua/include/lua.h" | tr -s ' \t' ' ')"
  printf '  %-12s %s\n' boost    "$(grep -m1 'define BOOST_LIB_VERSION' "$PREFIX/include/boost/version.hpp" | tr -s ' \t' ' ')"
  printf '  %-12s %s\n' cryptopp "$(grep -m1 'define CRYPTOPP_VERSION' "$PREFIX/include/cryptopp/config_ver.h" | tr -s ' \t' ' ')"
  printf '  %-12s %s\n' devil    "$(grep -m1 'define IL_VERSION ' "$PREFIX/include/IL/il.h" | tr -s ' \t' ' ')"
  printf '  %-12s %s\n' mariadb  "$(grep -m1 'MARIADB_PACKAGE_VERSION ' "$PREFIX/include/mysql/mariadb_version.h" | tr -s ' \t' ' ')"
  grep -q '"Lua 5.0.3"' "$PREFIX/lua/include/lua.h" || { fail "lua is not 5.0.3"; bad=1; }
  grep -q 'typedef char my_bool' "$PREFIX/include/mysql/mysql.h" \
    || { fail "mysql.h has no my_bool -- this is MySQL 8, not MariaDB C/C"; bad=1; }

  say "3) link + run"

  # Lua: 5.0-only API (lua_open/lua_dostring/lua_ref) AND the 40250 fork's
  # lexer extension.  "begin ... end" only parses with the forked llex.c;
  # a stock lua-5.0.3 rejects it.  This is the load-bearing quest-engine test.
  vrun lua '#include <cstdio>
extern "C" {
#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"
}
int main(){
  lua_State*L=lua_open(); luaopen_base(L); luaopen_string(L); luaopen_table(L);
  if(lua_dostring(L,"x = string.format(\"%d\",7)")!=0){puts("dostring failed");return 1;}
  lua_getglobal(L,"x");
  const char* x = lua_tostring(L,-1);
  int ref = lua_ref(L,1); lua_unref(L,ref);              /* 5.0-only API */
  int forked = (lua_dostring(L,"y=0 begin y=1 end")==0); /* 40250 lexer fork */
  int plaindo = (lua_dostring(L,"z=0 do z=1 end")==0);   /* preserved token */
  printf("%s x=%s  fork-begin=%s  do=%s\n", LUA_VERSION, x,
         forked?"yes":"NO", plaindo?"yes":"NO");
  lua_close(L);
  return (forked && plaindo) ? 0 : 1; }' \
    -I"$PREFIX/lua/include" -L"$PREFIX/lua/lib" -llualib -llua -lm || bad=1

  vrun cryptopp '#include <cstdio>
#include <cryptopp/sha.h>
#include <cryptopp/config.h>
int main(){ CryptoPP::SHA256 h; unsigned char d[32];
  h.CalculateDigest(d,(const unsigned char*)"abc",3);
  printf("cryptopp %d sha256[0]=%02x\n", CRYPTOPP_VERSION, d[0]);
  return d[0]==0xba?0:1; }' \
    -I"$PREFIX/include" -L"$PREFIX/lib" -lcryptopp || bad=1

  # my_bool + MYSQL_OPT_RECONNECT are exactly what libsql/AsyncSQL.cpp uses.
  # Link order matters: -lmariadb must precede -lssl -lcrypto -lz -ldl -lpthread.
  vrun mysql '#include <cstdio>
#include <mysql/mysql.h>
int main(){ MYSQL*m=mysql_init(NULL); if(!m) return 1;
  my_bool rc=1; mysql_options(m, MYSQL_OPT_RECONNECT, &rc);
  printf("mariadb client %s\n", mysql_get_client_info()); mysql_close(m); return 0; }' \
    -I"$PREFIX/include" -L"$PREFIX/lib" -lmariadb -lssl -lcrypto -lz -ldl -lpthread || bad=1

  # same source, linked through the libmysqlclient.a alias
  vrun mysqlalias '#include <cstdio>
#include <mysql/mysql.h>
int main(){ MYSQL*m=mysql_init(NULL); if(!m) return 1;
  printf("via -lmysqlclient: %s\n", mysql_get_client_info()); mysql_close(m); return 0; }' \
    -I"$PREFIX/include" -L"$PREFIX/lib" -lmysqlclient -lssl -lcrypto -lz -ldl -lpthread || bad=1

  # exactly the DevIL calls game/src/MarkImage.cpp makes
  vrun devil '#include <cstdio>
#include <IL/il.h>
int main(){ ilInit(); ILuint img; ilGenImages(1,&img); ilBindImage(img);
  ilEnable(IL_FILE_OVERWRITE); ilEnable(IL_ORIGIN_SET); ilOriginFunc(IL_ORIGIN_UPPER_LEFT);
  static unsigned char px[16*16*4]; for(int i=0;i<16*16*4;i++) px[i]=(unsigned char)i;
  if(!ilTexImage(16,16,1,4,IL_BGRA,IL_UNSIGNED_BYTE,px)) return 1;
  if(!ilSave(IL_TGA,(const ILstring)"/tmp/_il40250.tga")) return 1;
  ilDeleteImages(1,&img); ilGenImages(1,&img); ilBindImage(img);
  if(!ilLoad(IL_TYPE_UNKNOWN,(const ILstring)"/tmp/_il40250.tga")) return 1;
  printf("devil %d tga roundtrip %dx%d\n", IL_VERSION,
         ilGetInteger(IL_IMAGE_WIDTH), ilGetInteger(IL_IMAGE_HEIGHT));
  return ilGetInteger(IL_IMAGE_WIDTH)==16?0:1; }' \
    -I"$PREFIX/include" -L"$PREFIX/lib" -lIL -lm || bad=1

  vrun zssl '#include <cstdio>
#include <zlib.h>
#include <openssl/sha.h>
#include <openssl/opensslv.h>
int main(){ unsigned char d[20]; SHA1((const unsigned char*)"abc",3,d);
  printf("zlib %s / %s sha1[0]=%02x\n", zlibVersion(), OPENSSL_VERSION_TEXT, d[0]);
  return d[0]==0xa9?0:1; }' \
    -L"$PREFIX/lib" -lz -lssl -lcrypto -ldl -lpthread || bad=1

  vrun libmd '#include <cstdio>
extern "C" {
#include <md5.h>
}
int main(){ MD5_CTX c; char buf[33]; MD5Init(&c);
  MD5Update(&c,(const unsigned char*)"abc",3); MD5End(&c,buf);
  printf("libmd md5(abc)=%s\n", buf); return 0; }' \
    -L"$PREFIX/lib" -lmd || bad=1

  vrun boost '#include <cstdio>
#include <boost/version.hpp>
#include <boost/unordered_map.hpp>
#include <boost/algorithm/string.hpp>
int main(){ boost::unordered_map<int,int> m; m[1]=2;
  std::string s="A,B"; std::vector<std::string> v; boost::split(v,s,boost::is_any_of(","));
  printf("boost %s (%d entries, split=%zu)\n", BOOST_LIB_VERSION,(int)m.size(),v.size());
  return 0; }' -I"$PREFIX/include" || bad=1

  # 40250 compiles its own miniLZO 1.08 against its own game/src/lzo headers.
  # No system lzo is involved; this proves the vendored copy still builds and
  # that we have NOT polluted the include path with an lzo 2.x header.
  say "4) vendored miniLZO 1.08 (no system lzo required)"
  if [ -f "$GAME_SRC/minilzo.c" ]; then
    if gcc -m32 -w -c -o "$V_DIR/minilzo.o" "$GAME_SRC/minilzo.c" \
         -I"$GAME_SRC" -I"$GAME_SRC/lzo" 2> "$V_DIR/minilzo.err"; then
      ok "minilzo.c compiles (LZO_VERSION $(grep -m1 'define LZO_VERSION ' "$GAME_SRC/lzo/lzoconf.h" | awk '{print $3}'))"
    else
      fail "minilzo.c failed to compile (see $V_DIR/minilzo.err)"; bad=1
    fi
    if grep -rqE '(^|[^_A-Za-z])-llzo' "$PORT/server" 2>/dev/null; then
      warn "something in the tree passes -llzo -- re-check the lzo verdict"
    else
      ok "no -llzo/-llzo2 anywhere in $PORT/server -- system lzo not needed"
    fi
  else
    warn "$GAME_SRC/minilzo.c not found; skipped"
  fi

  say "5) duplicate-symbol sanity"
  if cmp -s "$PREFIX/lib/libmariadb.a" "$PREFIX/lib/libmysqlclient.a"; then
    ok "libmysqlclient.a is a byte copy of libmariadb.a -- link ONE of them, never both"
  else
    warn "libmysqlclient.a differs from libmariadb.a -- check which one the Makefiles pick"
  fi

  [ "$bad" = 0 ] && say "VERIFY: all checks passed" || fail "VERIFY: some checks failed"
  return "$bad"
}

# ===========================================================================
main() {
  guard_time_bits
  local args=() a
  for a in "$@"; do
    case "$a" in
      --force|-f) FORCE=1;;
      --prefix=*) PREFIX="${a#--prefix=}";;
      --jobs=*)   JOBS="${a#--jobs=}";;
      -h|--help)  sed -n '2,40p' "$0"; return 0;;
      *)          args+=("$a");;
    esac
  done
  # re-derive anything that depends on PREFIX/BUILD after flag parsing
  SRC="$BUILD/src"; LOG="$BUILD/log"; STAMP="$BUILD/stamp"
  STAGE="$BUILD/stage"; V_DIR="$BUILD/verify"
  mkdir -p "$SRC" "$LOG" "$STAMP" "$STAGE" "$PREFIX"

  local todo=("${args[@]+"${args[@]}"}")
  [ "${#todo[@]}" -eq 0 ] && todo=("${ALL_COMPONENTS[@]}")

  say "PREFIX=$PREFIX  BUILD=$BUILD  JOBS=$JOBS  FORCE=$FORCE"
  say "components: ${todo[*]}"
  local c t0
  for c in "${todo[@]}"; do
    declare -F "c_$c" >/dev/null || die "unknown component '$c' (have: ${ALL_COMPONENTS[*]} clean)"
    t0=$SECONDS
    if "c_$c"; then
      [ "$c" = verify ] || info "[$c] finished in $((SECONDS-t0))s"
    else
      fail "[$c] failed after $((SECONDS-t0))s"; RC=1
    fi
    cd /
  done
  [ "$RC" = 0 ] && say "ALL DONE" || fail "COMPLETED WITH ERRORS"
  return "$RC"
}
main "$@"

# ===========================================================================
# APPENDIX -- provenance of every archive this script produces
#
#   lib/libz.a          zlib 1.3        Ubuntu zlib1g-dev:i386 1:1.3.dfsg-3.1ubuntu2.1
#   lib/libssl.a        OpenSSL 3.0.13  Ubuntu libssl-dev:i386 3.0.13-0ubuntu3.12
#   lib/libcrypto.a     OpenSSL 3.0.13  Ubuntu libssl-dev:i386 3.0.13-0ubuntu3.12
#   lib/libmd.a         libmd 1.1.0     Ubuntu libmd-dev:i386 1.1.0-2build1.1
#   lib/libcryptopp.a   Crypto++ 8.4.0  port40250/extern/cryptopp_8_4_0.tar.gz
#   lib/libmariadb.a    MariaDB C/C 3.3.10 (github tag v3.3.10)
#   lib/libmysqlclient.a  byte copy of libmariadb.a
#   lib/libIL.a         DevIL 1.8.0     port40250/extern/devil_1_8_0_patched.tar.gz
#   lib/libILU.a        DevIL 1.8.0     (same)
#   lib/libILUT.a       DevIL 1.8.0     (same; ~17 KB, all backends compile empty)
#   lua/lib/liblua.a    Lua 5.0.3, 40250 fork -- port40250/server/liblua/src
#   lua/lib/liblualib.a Lua 5.0.3, 40250 fork -- port40250/server/liblua/src/lib
#
# KNOWN DIVERGENCE FROM THE ORIGINAL HAND-BUILT TREE:
#   The tree that was built by hand before this script existed had
#   MARIADB_PLUGINDIR pointing at that session's scratch directory, and the
#   same string leaked into ma_client_plugin.c.o.  This script pins it (see
#   MARIADB_PLUGINDIR above), so libmariadb.a / libmysqlclient.a differ from
#   that tree by 32 bytes in exactly one member, and one line of
#   include/mysql/mariadb_version.h differs.  Global symbol sets, archive
#   member lists and all ten other archives are byte-identical.  The value is
#   dead weight: every auth plugin is linked STATIC, nothing dlopen()s it.
#
# LUA PROVENANCE EVIDENCE (why the in-tree module, not lua.org):
#   Building lua-5.0.3 from lua.org with these exact flags yields objects that
#   are byte-identical to ours for lapi.o lgc.o lvm.o ltable.o lstring.o lzio.o
#   (the fork's edits there are pure whitespace) but llex.o differs:
#   8438 bytes of .text upstream vs 8709 here.  llex.c/llex.h are the only
#   functional patches -- they add TK_QUEST/TK_STATE/TK_WITH/TK_WHEN/TK_BEGIN,
#   remap "do" -> "begin" with "do" kept as a preserved token, and accept
#   identifier bytes >= 0xa0.  include/{lua,lualib,lauxlib}.h are unmodified
#   upstream 5.0.3 and are byte-identical to $PORT/server/liblua/include.
#
# LINK-ORDER / RUNTIME NOTES FOR WHOEVER DOES THE FINAL LINK:
#   * Static archives are order-sensitive.  Use, in this order:
#       -lIL -lcryptopp -lmariadb -lssl -lcrypto -lz -lmd -llualib -llua
#       -lm -ldl -lpthread
#     (-llualib before -llua: lualib calls into the core, not the reverse.)
#   * libIL.a and libcryptopp.a are C++ -- link the final binary with g++.
#   * libmariadb.a needs -lssl -lcrypto -lz -ldl -lpthread *after* it.
#   * NEVER put -lmariadb and -lmysqlclient on the same line (identical archives
#     -> duplicate definitions of every mysql_* symbol).
#   * Do not link the whole binary with -static: MariaDB C/C calls
#     getaddrinfo/gethostbyname, and glibc's NSS backends are dlopen()ed, so a
#     fully static build resolves no hostnames.  Static third-party archives +
#     dynamic glibc is the supported configuration.
#   * -lmd resolves against Ubuntu's libmd, whose <md5.h> collides with
#     libthecore's xmd5.h (both define MD5_CTX / MD5Init).  Include only one.
# ===========================================================================
