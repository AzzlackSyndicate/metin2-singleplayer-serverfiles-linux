# syntax=docker/dockerfile:1.7
# =============================================================================
#  Metin2 admin panel -- Flask + PyMySQL.
#
#  Single stage: it is a Python application with two pure-Python dependencies,
#  so there is nothing to compile and nothing a builder stage would strip out.
#
#  The panel is the one public HTTP surface in this stack and the only piece a
#  non-technical operator interacts with daily, so it gets a real WSGI server
#  (waitress) rather than Flask's development server.
# =============================================================================
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# mariadb-client: the entrypoint applies the panel's schema (the two non-stock
# tables it needs) at every start, so the panel works against a database that
# already existed as well as a freshly initialised one.
# curl: the healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends mariadb-client curl \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir flask PyMySQL waitress

RUN groupadd -r -g 2001 panel && useradd -r -u 2001 -g panel -d /opt/panel -s /usr/sbin/nologin panel

# The rate spool: a volume shared with the game container, which runs as a
# different user.  The gid must be the same number in both images -- the kernel
# checks the number, not the name.  (See game/Dockerfile, which creates the
# same group and puts its own service account in it.)
RUN groupadd -r -g 2050 m2spool && usermod -a -G m2spool panel

WORKDIR /opt/panel

# app/ is staged by ../prepare-context.sh from the panel source directory.
COPY app/ /opt/panel/
COPY schema/ /opt/panel/schema/
COPY bin/entrypoint.sh /usr/local/bin/entrypoint.sh
# The panel starts this one itself, through M2PANEL_RATES_SCRIPT, whenever
# somebody saves the rates page.  It lives in /usr/local/bin and NOT on the
# data volume on purpose: a stack that is upgraded keeps its old volume, and a
# file placed there by an image would never appear.
COPY bin/apply_rates.sh /usr/local/bin/apply_rates.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh /usr/local/bin/apply_rates.sh \
 && chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/apply_rates.sh

# Two separate volumes, because they have completely different lifecycles:
#
#   /usr/local/etc      m2panel.conf -- generated once at first run and then
#                       never regenerated, because it holds the password hash
#                       and the Flask secret. Regenerating it would log every
#                       operator out and invalidate every session cookie.
#
#   /usr/local/m2panel  client.zip (the ~1.2 GB game client the panel serves to
#                       players), downloads.db (the per-IP download quota),
#                       rates.status. client.zip is far too big to bake into an
#                       image and is not ours to redistribute.
RUN mkdir -p /usr/local/etc /usr/local/m2panel \
 && chown -R panel:panel /usr/local/etc /usr/local/m2panel /opt/panel
VOLUME ["/usr/local/etc", "/usr/local/m2panel"]

EXPOSE 7788/tcp

HEALTHCHECK --interval=20s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS -o /dev/null "http://127.0.0.1:${M2_PANEL_PORT:-7788}/" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
