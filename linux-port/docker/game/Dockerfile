# syntax=docker/dockerfile:1.7
# =============================================================================
#  Metin2 r40250 game server -- 32-bit Linux, built from source.
#
#  Three stages:
#     deps     the 32-bit dependency tree, built by the project's own
#              build-deps-40250.sh.  Depends only on that script and on the
#              shipped tarballs under extern/, so editing the server source
#              never re-runs it.
#     builder  the eight server modules, compiled and linked.  Produces
#              exactly two files: game and db.
#     runtime  ubuntu:24.04 + the three i386 shared libraries the binaries
#              actually need + those two binaries + the static data tree.
#
#  The build context is prepared by ../prepare-context.sh, which stages:
#     src/build-deps-40250.sh   the dependency builder
#     src/extern/               boost/cryptopp/DevIL tarballs + shipped headers
#     src/server/               the eight modules (port40250/server)
#     src/serverfiles/share/    conf/ data/ locale/ package/  (no binaries)
# =============================================================================

ARG BASE_IMAGE=ubuntu:24.04

# =============================================================================
# Stage 1 -- deps
# =============================================================================
FROM ${BASE_IMAGE} AS deps

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Where the 32-bit dependency set is installed.  The module Makefiles find it
# through M2_EXTERN_LINUX, which the builder stage passes on every make line.
ENV M2_EXTERN=/opt/m2extern

# build-deps-40250.sh's own `apt' component runs `dpkg --add-architecture i386'
# and installs the package list documented in its header, so there is no
# separate apt layer to keep in sync with it.
COPY src/build-deps-40250.sh /build/build-deps-40250.sh

# boost 1.72 / cryptopp 8.4.0 / DevIL 1.8.0 are shipped as tarballs inside the
# source tree at port40250/extern; only the MariaDB Connector/C tarball is
# fetched from the network.  extern/include also carries the authoritative
# headers that get overlaid on top of the built ones.
COPY src/extern /build/port40250/extern

# The `lua' component builds a 32-bit mirror of the in-tree forked Lua 5.0.3.
# It is the only part of the server source this stage needs, and it is stable.
COPY src/server/liblua /build/port40250/server/liblua

# CRLF guard: this repository is edited on Windows and a stray \r in a shell
# script is a silent, baffling failure inside a container.
RUN sed -i 's/\r$//' /build/build-deps-40250.sh && chmod +x /build/build-deps-40250.sh

# `verify' is deliberately NOT run here -- it inspects game/src/lzo headers,
# which only exist in the builder stage.  It is run there instead.
RUN PREFIX="${M2_EXTERN}" BUILD=/build/work PORT=/build/port40250 \
    /build/build-deps-40250.sh apt zlib openssl libmd lua cryptopp mysql devil boost \
 && rm -rf /build/work/src /build/work/stage /var/lib/apt/lists/*

# =============================================================================
# Stage 2 -- builder
# =============================================================================
FROM deps AS builder

ARG MAKE_JOBS=
ARG STRIP_BINARIES=1

COPY src/server /src/server
WORKDIR /src/server

# A from-source build must not silently reuse an object that came in with the
# context.  The tree ships committed .o/.a files (libpoly/*.o, libsql.a, ...)
# whose mtimes relative to the sources are arbitrary after COPY, so make cannot
# be trusted to rebuild them.  Everything derived is removed first.
#
# Nothing needed is lost: every .a under server/ is a build output.  The
# FreeBSD prebuilts that must never be linked live in extern/, not here.
RUN find /src/server \( -name '*.o' -o -name '*.a' \) -delete \
 && rm -rf libthecore/src/OBJDIR libgame/src/OBJDIR game/src/OBJDIR db/src/.obj

# The one-line regression that must never silently revert.  Without it,
# fdwatch_get_buffer_size() returns 0 whenever the current epoll round carried
# no write event for the fd, DESC::ProcessOutput() bails at its buffer_left<=0
# guard, and the pre-encryption flush in input.cpp becomes a no-op.  The
# symptom is that every client completes the key agreement and then hangs
# forever while the server logs absolutely nothing -- an image that ships this
# regression looks healthy from the outside and is completely unusable.
RUN grep -q 'return fdwatch_sndbuf_left(fd);' libthecore/src/fdwatch.c \
 || { echo "FATAL: the fdwatch send-buffer fix is missing from libthecore/src/fdwatch.c."; \
      echo "       No client would be able to log in. Refusing to build."; exit 1; }

# The prefix is spelled three different ways across the tree -- M2_EXTERN_LINUX
# in game/src and db/src (relative in one, absolute in the other) and EXTERN_DIR
# in libsql/libserverkey.  All of them are passed on every make line rather than
# relying on any default, because every default points somewhere that only
# existed on the porting host.
ENV M2_MAKEVARS="M2_EXTERN_LINUX=/opt/m2extern EXTERN_DIR=/opt/m2extern"

# Build order is dependency order.
#
# `make dep' first where the target exists: libthecore, libpoly and libgame end
# with a hard `include Depend' (not sinclude), and the checked-in Depend was
# generated by makedepend on FreeBSD -- it names /usr/include/sys/_null.h and
# friends by absolute path, so make dies before compiling anything.  libsql
# additionally needs it to produce Depend.linux, which its Makefile selects on
# Linux.  makedepend comes from xutils-dev.
#
# liblua is built without -j: it is a recursive make over include/, src/ and
# src/lib/ with no inter-directory ordering declared, and has no dep target.
RUN apt-get update \
 && apt-get install -y --no-install-recommends xutils-dev \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    J="${MAKE_JOBS:-$(nproc)}"; \
    for m in libthecore/src libpoly libgame/src libsql libserverkey; do \
      make -C "$m" dep ${M2_MAKEVARS} >/dev/null 2>&1 || echo "note: no usable dep target in $m"; \
    done; \
    make -C libthecore/src -j"$J" ${M2_MAKEVARS}; \
    make -C libpoly        -j"$J" ${M2_MAKEVARS}; \
    make -C libgame/src    -j"$J" ${M2_MAKEVARS}; \
    make -C liblua                ${M2_MAKEVARS}; \
    make -C libsql         -j"$J" ${M2_MAKEVARS}; \
    make -C libserverkey   -j"$J" ${M2_MAKEVARS}; \
    for a in libthecore/lib/libthecore.a libpoly/libpoly.a libgame/lib/libgame.a \
             liblua/lib/liblua.a liblua/lib/liblualib.a libsql/libsql.a \
             libserverkey/libserverkey.a; do \
      [ -f "$a" ] || { echo "FATAL: $a was not produced"; exit 1; }; \
      file -b "$a" | grep -qi 'archive' || { echo "FATAL: $a is not an archive"; exit 1; }; \
    done; \
    echo "all seven support archives present"

# Now that game/src exists, run the dependency tree's own verification suite.
# It checks the minilzo 1.08 version pin in game/src/lzo and that every archive
# in the prefix is 32-bit -- both things that fail silently at link time.
# build-deps addresses the source as $PORT/server/..., so the module tree is
# linked into the layout it already expects.
RUN set -eux; \
    ln -sfn /src/server/game /build/port40250/server/game; \
    PREFIX="${M2_EXTERN}" BUILD=/build/work PORT=/build/port40250 \
      /build/build-deps-40250.sh verify

# game: the Linux branch links into game/ and leaves the binary there;
#       `install' is the only thing that writes outside the module.
# db:   its default target still ends in `mv $(TARGET) $(DBDIR)', so DBDIR is
#       pointed at the output directory instead of /usr/metin2/server/share/bin.
RUN set -eux; \
    J="${MAKE_JOBS:-$(nproc)}"; \
    mkdir -p /out; \
    make -C game/src -j"$J" ${M2_MAKEVARS}; \
    make -C game/src install GAMEDIR=/out ${M2_MAKEVARS}; \
    make -C db/src   -j"$J" ${M2_MAKEVARS} DBDIR=/out

# Both binaries must be 32-bit, and sizeof(time_t) must still be 4 -- a 64-bit
# time_t under -m32 silently changes the size of packed wire and DB structs.
# _TIME_BITS is checked rather than assumed because it can be injected from a
# CFLAGS override as easily as from a Makefile edit.
RUN set -eux; \
    for b in /out/game /out/db; do \
      file -b "$b" | grep -q 'ELF 32-bit LSB .* Intel 80386' \
        || { echo "FATAL: $b is not a 32-bit i386 ELF"; file "$b"; exit 1; }; \
    done; \
    if strings -a /out/game | grep -q '_TIME_BITS=64'; then \
      echo "FATAL: _TIME_BITS=64 leaked into the build"; exit 1; \
    fi; \
    echo "unstripped: $(du -sh /out | cut -f1)"; \
    if [ "${STRIP_BINARIES}" = "1" ]; then strip --strip-unneeded /out/game /out/db; fi; \
    echo "delivered:  $(du -sh /out | cut -f1)"; \
    ls -la /out

# =============================================================================
# Stage 3 -- runtime
# =============================================================================
FROM ${BASE_IMAGE} AS runtime

ENV DEBIAN_FRONTEND=noninteractive

# The binaries link everything else statically.  `ldd game' resolves exactly
# these three, plus the loader:
#     libstdc++.so.6  libgcc_s.so.1  libc.so.6
# tzdata is here so the server's log timestamps match the operator's clock;
# everything else the entrypoint and supervisor need (bash, coreutils,
# util-linux/setpriv) is already in the base image.
RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
        libc6:i386 libstdc++6:i386 libgcc-s1:i386 \
        tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Non-root: the game parses untrusted network input and needs no privilege
# beyond binding ports above 1024.  The entrypoint starts as root only long
# enough to fix ownership of the state volume, then drops to this account.
RUN groupadd -r -g 2000 metin2 && useradd -r -u 2000 -g metin2 -d /opt/metin2 -s /usr/sbin/nologin metin2

# The rate spool is the one directory two containers write to.  They run as
# different users (metin2 here, panel there) and always will, so a shared group
# with a FIXED gid in both images is what makes the volume usable from both --
# a name alone would not, since the kernel only sees the number.
RUN groupadd -r -g 2050 m2spool && usermod -a -G m2spool metin2

# ---- static half: never written at runtime, replaced by rebuilding the image
#      share/bin      the two binaries this build produced
#      share/conf     CMD, item_proto/mob_proto/item_names/mob_names
#      share/data     mob spawns, dungeons, pc tables
#      share/locale   quest objects, maps, translations
#      share/package  (empty in this fork, kept because every core symlinks it)
COPY --from=builder /out/game /opt/metin2/share/bin/game
COPY --from=builder /out/db   /opt/metin2/share/bin/db
COPY src/serverfiles/share/conf    /opt/metin2/share/conf
COPY src/serverfiles/share/data    /opt/metin2/share/data
COPY src/serverfiles/share/locale  /opt/metin2/share/locale
COPY src/serverfiles/share/package /opt/metin2/share/package

# Seed for a core's mark/ directory (guild symbols).  Copied into the state
# volume on first run only, so guild marks uploaded by players survive.
COPY src/serverfiles/mark-default /opt/metin2/share/mark-default

# The server-files profile -- the same file the FreeBSD installer uses.  It is
# in here for exactly one function, pack_apply_rates(), which knows which
# columns of which tables carry experience, yang and drop chances.  Copying it
# rather than reimplementing it is the point: there is then only one piece of
# code in the project that scales a rate, and the FreeBSD server and this one
# cannot drift apart.  Staged into the build context by ../prepare-context.sh.
COPY rates/ /opt/metin2/rates/

COPY bin/ /usr/local/bin/
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh /usr/local/bin/m2-* /usr/local/bin/m2ctl \
 && chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/m2-* /usr/local/bin/m2ctl \
 && chmod 0755 /opt/metin2/share/bin/game /opt/metin2/share/bin/db \
 && if [ -f /opt/metin2/rates/pack.sh ]; then sed -i 's/\r$//' /opt/metin2/rates/pack.sh; fi

# ---- mutable half: everything the cores write lives here, and only here.
#      Declared as a VOLUME so an operator who forgets the compose file still
#      does not lose guild marks and logs on `docker rm'.
RUN mkdir -p /opt/metin2/var && chown metin2:metin2 /opt/metin2/var
VOLUME ["/opt/metin2/var"]

ENV M2_SHARE_DIR=/opt/metin2/share \
    M2_VAR_DIR=/opt/metin2/var \
    TZ=UTC

EXPOSE 11000/tcp 13000/tcp 13001/tcp 13002/tcp

HEALTHCHECK --interval=15s --timeout=5s --start-period=180s --retries=4 \
    CMD ["/usr/local/bin/m2-healthcheck"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["m2-supervise"]
