#!/usr/bin/env bash
# =============================================================================
#  prepare-context.sh -- stage the build inputs into this directory.
#
#  Most operators never call this directly: ../fetch-sources.sh obtains the
#  upstream package, applies the port patch and then runs this script for them.
#
#  Call it yourself when the source tree, the runtime data tree and the SQL
#  dumps already exist outside this directory -- a development checkout, or a
#  tree fetch-sources.sh staged earlier.
#  It copies (never moves, never modifies) into:
#
#      game/src/build-deps-40250.sh    the dependency builder
#      game/src/extern/                boost/cryptopp/DevIL tarballs + headers
#      game/src/server/                the eight modules
#      game/src/serverfiles/share/     conf/ data/ locale/ package/
#      game/src/serverfiles/mark-default/
#      panel/app/                      admin_panel.py, items.json, favicon.png
#      panel/app/VERSION               the repository's VERSION -- this is what
#                                      makes the running panel able to say which
#                                      build it is and whether it is behind
#      panel/app/CHANGELOG.md          so the panel's patch log can show what
#                                      you are running without the internet
#      panel/schema/                   web_admin_schema.sql
#      mariadb/initdb.d/dumps/         account/common/player/log/hotbackup.sql
#      game/rates/pack.sh              the server-files profile (rate maths)
#      client-builder/pack/pack.sh     the same file again (client preparation)
#
#  Idempotent. Safe to re-run after editing the source.
#
#  Usage:
#      ./prepare-context.sh
#      ./prepare-context.sh --m2port /opt/m2port --panel ../../files
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

M2PORT="${M2PORT:-/opt/m2port}"
PANEL_SRC="${PANEL_SRC:-$HERE/../../files}"

# The repository root: VERSION and CHANGELOG.md live there, and both have to be
# inside the panel image. A panel that cannot read its own VERSION reports its
# version as "unknown" -- which is the honest answer, but a useless one, so this
# is staged rather than left to chance.
REPO_ROOT="${REPO_ROOT:-$(cd "$HERE/../.." && pwd)}"

while [ $# -gt 0 ]; do
  case "$1" in
    --m2port) M2PORT="$2"; shift 2 ;;
    --panel)  PANEL_SRC="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

say()  { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
die()  { printf 'FATAL: %s\n' "$*" >&2; exit 1; }

PORT_SRC="$M2PORT/port40250"
RUNTIME_SRC="$M2PORT/server40250"
DUMP_SRC="$M2PORT/dbdump/zip"
DEPS_SCRIPT="$M2PORT/build-deps-40250.sh"

for p in "$PORT_SRC/server" "$PORT_SRC/extern" "$RUNTIME_SRC/share" "$DUMP_SRC" "$DEPS_SCRIPT"; do
  [ -e "$p" ] || die "$p not found (use --m2port to point at the porting tree)"
done
[ -f "$PANEL_SRC/admin_panel.py" ] || die "$PANEL_SRC/admin_panel.py not found (use --panel)"

GAME_CTX="$HERE/game/src"
rm -rf "$GAME_CTX"
mkdir -p "$GAME_CTX"

# -----------------------------------------------------------------------------
say "dependency builder"
cp -a "$DEPS_SCRIPT" "$GAME_CTX/build-deps-40250.sh"
info "build-deps-40250.sh"

# -----------------------------------------------------------------------------
say "extern (dependency sources)"
# Only the tarballs and the shipped headers are wanted. extern/lib holds the
# FreeBSD prebuilt archives -- ELF "version 1 (FreeBSD)" -- which must never
# reach a Linux link line; they are excluded rather than merely unused.
mkdir -p "$GAME_CTX/extern"
find "$PORT_SRC/extern" -maxdepth 1 -type f -name '*.tar.gz' -exec cp -a {} "$GAME_CTX/extern/" \;
if [ -d "$PORT_SRC/extern/include" ]; then
  cp -a "$PORT_SRC/extern/include" "$GAME_CTX/extern/include"
fi
info "$(find "$GAME_CTX/extern" -maxdepth 1 -name '*.tar.gz' | wc -l) tarball(s), $(du -sh "$GAME_CTX/extern" | cut -f1)"

# -----------------------------------------------------------------------------
say "server source (eight modules)"
mkdir -p "$GAME_CTX/server"
for m in common db game libgame liblua libpoly libserverkey libsql libthecore; do
  [ -d "$PORT_SRC/server/$m" ] || die "module $m missing from $PORT_SRC/server"
  cp -a "$PORT_SRC/server/$m" "$GAME_CTX/server/$m"
done
# Top-level Makefile is the untouched FreeBSD driver (CC=clang-devel, its game:
# and db: recipes commented out). The image builds per module and never uses
# it, so it is left out to remove the temptation.

# Derived artefacts are dropped here as well as in the Dockerfile: it keeps the
# context small and makes "did this really build from source" answerable by
# looking at the context.
find "$GAME_CTX/server" \( -name '*.o' -o -name '*.a' -o -name 'tags' \) -delete
rm -rf "$GAME_CTX/server"/*/src/OBJDIR "$GAME_CTX/server"/*/src/.obj \
       "$GAME_CTX/server"/*/OBJDIR "$GAME_CTX/server"/*/.obj
info "$(du -sh "$GAME_CTX/server" | cut -f1)"

# The regression that must be present. Checked here as well as in the
# Dockerfile so that a bad context is caught before a 10-minute build.
grep -q 'return fdwatch_sndbuf_left(fd);' "$GAME_CTX/server/libthecore/src/fdwatch.c" \
  || die "the fdwatch send-buffer fix is missing from libthecore/src/fdwatch.c -- no client could log in"
info "fdwatch send-buffer fix present"

# -----------------------------------------------------------------------------
say "runtime data tree"
mkdir -p "$GAME_CTX/serverfiles/share"
for d in conf data locale package; do
  [ -d "$RUNTIME_SRC/share/$d" ] || die "$RUNTIME_SRC/share/$d missing"
  cp -a "$RUNTIME_SRC/share/$d" "$GAME_CTX/serverfiles/share/$d"
  info "share/$d  $(du -sh "$GAME_CTX/serverfiles/share/$d" | cut -f1)"
done

# share/bin is deliberately NOT copied. The binaries in the image come from the
# builder stage; the tree also still carries the original FreeBSD game.freebsd
# and db.freebsd (90 MB + 20 MB) which cannot run on Linux at all.
info "share/bin skipped -- binaries come from the build, not the context"

# Guild mark seed for a fresh channel core.
mkdir -p "$GAME_CTX/serverfiles/mark-default"
if [ -d "$RUNTIME_SRC/channel1/first/mark" ]; then
  cp -a "$RUNTIME_SRC/channel1/first/mark/." "$GAME_CTX/serverfiles/mark-default/" 2>/dev/null || true
fi
info "mark-default  $(du -sh "$GAME_CTX/serverfiles/mark-default" | cut -f1)"

# -----------------------------------------------------------------------------
say "SQL dumps"
mkdir -p "$HERE/mariadb/initdb.d/dumps"
for d in account common player log hotbackup; do
  [ -f "$DUMP_SRC/$d.sql" ] || die "$DUMP_SRC/$d.sql missing"
  cp -a "$DUMP_SRC/$d.sql" "$HERE/mariadb/initdb.d/dumps/$d.sql"
  info "$d.sql  $(du -h "$DUMP_SRC/$d.sql" | cut -f1)"
done

# The MariaDB image's entrypoint runs an executable *.sh from initdb.d, but
# merely *sources* a non-executable one -- which leaks this script's `set -e'
# into the entrypoint's own shell. A checkout that lost the mode bit (a zip
# round-trip, a Windows working copy) would take the second path silently, so
# the bit is asserted here rather than assumed.
chmod +x "$HERE/mariadb/initdb.d/"*.sh 2>/dev/null || true

# Likewise for the scripts that go into the images. The Dockerfiles chmod them
# as well; this keeps `bash prepare-context.sh && docker compose up' honest on
# a working copy with no exec bits at all.
chmod +x "$HERE/game/bin/"* "$HERE/panel/bin/"* "$HERE/client-builder/bin/"* \
         "$HERE/updater/bin/"* 2>/dev/null || true

# -----------------------------------------------------------------------------
say "admin panel"
rm -rf "$HERE/panel/app"
mkdir -p "$HERE/panel/app" "$HERE/panel/schema"
cp -a "$PANEL_SRC/admin_panel.py" "$HERE/panel/app/"
for f in items.json favicon.png; do
  [ -f "$PANEL_SRC/$f" ] && cp -a "$PANEL_SRC/$f" "$HERE/panel/app/" && info "$f"
done
# The version, and the changelog that explains it. Both are plain text and both
# are read by the panel at runtime: VERSION is what it reports and what it
# compares against the published one, CHANGELOG.md is what its patch log shows
# for the build you are actually running. Absent, the panel says "unknown" and
# its patch log says the file is not in this build -- it never invents either.
if [ -f "$REPO_ROOT/VERSION" ]; then
  cp -a "$REPO_ROOT/VERSION" "$HERE/panel/app/VERSION"
  info "VERSION  $(tr -d ' \r\n' < "$REPO_ROOT/VERSION")"
else
  info "WARNING: $REPO_ROOT/VERSION not found -- the panel will report its"
  info "         version as \"unknown\" and cannot tell you when it is behind."
fi
if [ -f "$REPO_ROOT/CHANGELOG.md" ]; then
  cp -a "$REPO_ROOT/CHANGELOG.md" "$HERE/panel/app/CHANGELOG.md"
  info "CHANGELOG.md  $(du -h "$REPO_ROOT/CHANGELOG.md" | cut -f1)"
else
  info "WARNING: $REPO_ROOT/CHANGELOG.md not found -- the panel's patch log will"
  info "         have nothing to show for the version you are running."
fi

if [ -f "$PANEL_SRC/web_admin_schema.sql" ]; then
  cp -a "$PANEL_SRC/web_admin_schema.sql" "$HERE/panel/schema/"
  info "web_admin_schema.sql"
else
  info "WARNING: web_admin_schema.sql not found -- the panel's queue and rates"
  info "         tables will not be created and those pages will fail."
fi

# -----------------------------------------------------------------------------
say "server-files profile (the rate maths, and the client)"
# Two images carry this file, and both carry it verbatim rather than a copy of
# the logic inside it:
#
#   game/rates/pack.sh           pack_apply_rates() -- which column of which
#                                table holds experience, yang, a drop chance
#   client-builder/pack/pack.sh  pack_prepare_client() and p_write_serverinfo()
#                                -- how to turn the shipped client into one that
#                                points at this server without breaking it
#
# so the FreeBSD server and the Docker server cannot disagree about either.
mkdir -p "$HERE/game/rates" "$HERE/client-builder/pack"
PACK_SRC="${PACK_SRC:-$PANEL_SRC/packs/tmp4-r40250.pack}"
if [ -f "$PACK_SRC" ]; then
  cp -a "$PACK_SRC" "$HERE/game/rates/pack.sh"
  cp -a "$PACK_SRC" "$HERE/client-builder/pack/pack.sh"
  info "$(basename "$PACK_SRC") -> game/rates/pack.sh  ($(du -h "$PACK_SRC" | cut -f1))"
  info "$(basename "$PACK_SRC") -> client-builder/pack/pack.sh  (md5 $(md5sum "$PACK_SRC" | cut -c1-32))"
else
  info "WARNING: $PACK_SRC not found -- the panel's rates page will report that"
  info "         these server files cannot have their rates changed, and the"
  info "         client builder will refuse to run."
fi

# -----------------------------------------------------------------------------
say "done"
printf '   total staged: %s\n' "$(du -sh "$HERE" | cut -f1)"
printf '\n   next:\n     cp .env.example .env && nano .env\n     docker compose up -d --build\n\n'
