# The browser client's fetcher.
#
# It exists as a container for one reason: the panel serves /play out of the
# panel-data volume, and the only supported way to write into a named volume is
# from a container that mounts it. Reaching into Docker's own directory from the
# host works until it does not -- a different storage driver, a remote daemon, a
# rootless install -- and this is the same shape client-builder already uses to
# put the desktop client on that same volume.
#
# It carries nothing of its own: fetch-web-client.sh and artifacts.json are
# mounted read-only, so changing either is a re-run, not a rebuild.
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

# megatools is the part that is not obvious. A MEGA link carries its decryption
# key after the '#', the file is stored encrypted, and curl cannot fetch it --
# it would download the web page. fetch-sources.sh already learned this the hard
# way for the server archive; the browser corpus comes the same route.
#
# ca-certificates: the engine comes from a GitHub release over TLS.
# coreutils: sha256sum, and the flock the script uses to serialise runs.
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        coreutils \
        curl \
        megatools \
        unzip \
 && rm -rf /var/lib/apt/lists/*

# Root, deliberately: it writes into a volume the panel owns, and the files have
# to end up readable by the panel's user. The container exits as soon as the
# fetch is done -- it is a task, not a service.
WORKDIR /opt

# No ENTRYPOINT. The command lives in docker-compose.yml where the target path
# is, so that reading the compose file tells you what this actually does.
