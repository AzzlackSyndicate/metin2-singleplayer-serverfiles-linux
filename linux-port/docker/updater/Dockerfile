# syntax=docker/dockerfile:1.7
# =============================================================================
#  The updater -- one shell script, and the tools it needs to run five commands.
#
#  This is the only image in the stack that is given the Docker socket, and the
#  reason it exists at all is so that the panel is not.  Keep it that way: if
#  something new needs doing here, it belongs in m2-updater where it can be
#  read in one sitting, not in the panel where it would arrive with a web
#  server attached.
#
#  It is started by nobody unless the operator asks for it by name -- see the
#  `update' profile in docker-compose.yml, and UPDATING.md.
# =============================================================================
FROM docker:27-cli AS runtime

# git       refresh the checkout
# bash      prepare-context.sh is a bash script
# tar       copy the build context over the installed stack
# patch,
# unzip,
# findutils,
# coreutils fetch-sources.sh needs them on the rare update where the port patch
#           itself changed and the source tree has to be built again. Without
#           them that update stops and says so instead of half-doing it.
# docker-cli-compose  `docker compose' -- the CLI image does not always carry it
RUN apk add --no-cache \
        docker-cli-compose \
        git bash tar patch unzip findutils coreutils \
 && rm -rf /var/cache/apk/*

COPY bin/m2-updater /usr/local/bin/m2-updater
RUN sed -i 's/\r$//' /usr/local/bin/m2-updater \
 && chmod +x /usr/local/bin/m2-updater

# The script lives in the IMAGE, not in the checkout it updates. That is not an
# accident: step 1 of an update rewrites every file in the checkout, and `sh'
# reads a script as it goes -- a script updating itself under its own feet is a
# way to find out what half a shell script does.
#
# One consequence, stated here because it surprises people: this container goes
# on running the version of the updater it was started with until it is
# recreated on purpose. See UPDATING.md.

# It holds the host's Docker socket, so it gets nothing else it does not need:
# no ports, no volumes but the spool and the two directories it must write, and
# no reason to ever be reachable over the network from inside this stack.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD [ -f "${M2_UPDATE_SPOOL:-/opt/m2update}/watcher" ] \
     && [ "$(( $(date +%s) - $(stat -c %Y "${M2_UPDATE_SPOOL:-/opt/m2update}/watcher") ))" -lt 120 ] || exit 1

ENTRYPOINT ["/usr/local/bin/m2-updater"]
CMD ["watch"]
