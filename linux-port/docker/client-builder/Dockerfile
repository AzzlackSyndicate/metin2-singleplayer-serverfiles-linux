# syntax=docker/dockerfile:1.7
# =============================================================================
#  Metin2 client builder -- produces the client.zip the panel hands to players.
#
#  A one-shot tool, not a service.  It is run explicitly:
#
#      docker compose run --rm client-builder
#
#  and it does three things: fetch the server-file archive (a MEGA share link,
#  over a gigabyte), write this server's address into the client inside it, and
#  repack it into the panel's data volume so /download starts working.
#
#  It runs as root, on purpose.  It writes into two named volumes that other
#  containers own -- the panel's data volume and its own cache -- and a one-shot
#  admin tool that the operator invokes by hand is the wrong place to spend an
#  afternoon on uid mapping.  It listens on nothing and is not running when the
#  server is.
# =============================================================================
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE} AS runtime

ENV DEBIAN_FRONTEND=noninteractive

# megatools     the MEGA client. The pack's download link is a MEGA share link
#               and nothing else can fetch it. Ubuntu 24.04 ships 1.11, which
#               understands the modern mega.nz/file/<id>#<key> form.
# 7zip          reads .rar (including rar5), .7z and .zip with one command, so
#               the outer archive can change format without changing this image.
#               unrar is not used: the usable one is non-free and not in Ubuntu's
#               default component set.
# zip, unzip    what the server-files profile itself calls to unpack and repack
#               the client. Not optional -- pack_prepare_client checks for them
#               and gives up politely if they are missing.
# curl          for the case where the operator supplies an ordinary direct
#               download link instead of the MEGA one.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      megatools \
      7zip \
      zip \
      unzip \
      curl \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# The server-files profile -- the same file the FreeBSD installer uses, copied
# in verbatim.  It is here for pack_prepare_client() and pack_apply_ip(), which
# between them know that this client reads the serverinfo.py next to the .exe,
# that pack/root.epk beats a loose file unless it is moved aside, and that the
# Eternexus folder must never reach a player.  Reimplementing that would mean
# two versions of the same hard-won knowledge, drifting apart.  Staged into the
# build context by ../prepare-context.sh.
COPY pack/ /opt/m2client/pack/

COPY bin/ /usr/local/bin/
RUN sed -i 's/\r$//' /usr/local/bin/m2-client-build \
 && chmod +x /usr/local/bin/m2-client-build \
 && if [ -f /opt/m2client/pack/pack.sh ]; then sed -i 's/\r$//' /opt/m2client/pack/pack.sh; fi

# The cache: the downloaded archive and the built client.zip.  Declared as a
# volume so that an operator who runs this image with plain `docker run' and no
# -v still does not throw away a gigabyte on every invocation.
#
# /usr/local/m2panel is a symlink into it, because pack_prepare_client() writes
# to that path and takes no argument.  Inside THIS container it is the cache,
# not the panel's volume -- the panel's copy is published separately, and only
# once the build has finished. That is what keeps a half-built zip away from
# players.
RUN mkdir -p /var/cache/m2client /out /archive \
 && ln -s /var/cache/m2client /usr/local/m2panel \
 && chown 2001:2001 /out
# ^ /out is the panel's data volume. Docker seeds an EMPTY named volume from the
# image directory it is first mounted on -- ownership included -- and this
# container is usually the first to put anything in it. Left as root, the
# volume's own directory becomes root-owned, and the panel (which runs as uid
# 2001) can then no longer create downloads.db beside client.zip. The symptom is
# nasty because it is not the client that breaks: the build succeeds, the zip
# appears, and every /download request answers 500 until the panel is restarted.
# 2001:2001 is the panel image's uid:gid -- keep the two in step.
VOLUME ["/var/cache/m2client"]

ENV M2_CLIENT_OUT=/out/client.zip \
    M2_CLIENT_DROP=/archive \
    M2_CLIENT_PACK=/opt/m2client/pack/pack.sh \
    TZ=UTC

ENTRYPOINT ["/usr/local/bin/m2-client-build"]
CMD ["build"]
