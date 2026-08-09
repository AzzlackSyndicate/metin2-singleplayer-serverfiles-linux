#!/usr/bin/env python3
# =============================================================
# Metin2 Admin Panel v2 - built for non-technical users
# Config: /usr/local/etc/m2panel.conf  (M2PANEL_CONF to move it)
# =============================================================
import datetime, json, os, re, socket, sys, threading, time, hashlib, hmac, secrets, sqlite3, subprocess
from functools import wraps
from flask import Flask, request, session, redirect, url_for, render_template_string, flash, send_file, jsonify
from flask import has_request_context
from flask.sessions import SecureCookieSessionInterface
import pymysql

# ---- where everything lives -------------------------------------------------
# The panel was written for a FreeBSD box where install.sh puts its files in
# /usr/local/m2panel and its config in /usr/local/etc. Those are still the
# defaults, so an existing installation keeps working with no changes at all.
# Inside a container none of those paths necessarily exist, so every one of
# them can be moved with an environment variable. Set M2PANEL_DIR alone and
# all four files below follow it; set an individual variable to place just
# that one file somewhere else.
def _env_path(name, default):
    """Value of environment variable 'name', or 'default' when it is unset/empty."""
    return os.environ.get(name, "").strip() or default

PANEL_DIR  = _env_path("M2PANEL_DIR", "/usr/local/m2panel")
# Files shipped next to admin_panel.py itself; that is where install.sh puts
# them and where they sit in the source tree, so this needs no variable to
# work — but a read-only image may want them elsewhere.
_HERE      = os.path.dirname(os.path.abspath(__file__))

CLIENT_ZIP = _env_path("M2PANEL_CLIENT_ZIP", os.path.join(PANEL_DIR, "client.zip"))

# ---- client download quota --------------------------------------------------
# The client is 1.2 GB; a bot fetching it in a loop would saturate the uplink
# for everyone. Each address gets DL_MAX full downloads per sliding 24 hours,
# tracked in a small SQLite file so the count survives panel restarts. Resumed
# downloads (Range requests that start mid-file) and HEAD probes are free -
# only a fresh fetch of the whole file spends a slot.
DL_DB  = _env_path("M2PANEL_DL_DB", os.path.join(PANEL_DIR, "downloads.db"))
DL_MAX, DL_WINDOW = 3, 24 * 3600

# A second ceiling, for the whole server rather than one address. The per-address
# limit assumes an abuser has one address; a botnet, an open proxy pool or a
# shared NAT breaks that assumption, and 1.2 GB a time turns into real money on
# a metered link long before anyone notices. This caps the total, so the worst
# case is bounded no matter how many addresses show up.
DL_DAY_MAX = int(os.environ.get("M2PANEL_DL_DAY_MAX", "100") or 100)

# ---- live server status -----------------------------------------------------
# Shown on the front page: is the game up, and how many people are in it.
# "Up" means both doors are open: the login server AND the first channel are
# listening. Read out of the machine's socket table rather than by making a
# test connection, because the game binds to the public address only - a
# loopback connect always fails.
# The player count is the number of established outside connections to the
# channel ports; each connected client keeps exactly one, and the cores
# talking to each other (loopback / own address) are ignored. Cached for 30
# seconds so a busy front page cannot hammer the game.
#
# Which tool reads that socket table differs per system:
#   FreeBSD  sockstat -4l / -4c   (the original, still the default there)
#   Linux    ss -H -4 -n -l -t / ss -H -4 -n -t state established
# Both are tried in turn, so the same file works on either without being told
# which it is on. M2PANEL_STATUS_CMD forces one of them ("sockstat" / "ss")
# when a machine happens to have both and picks the wrong one.
#
# In a container the panel usually cannot see the game's sockets at all —
# different network namespace, and neither tool would report anything. Set
# M2PANEL_STATUS_HOST to the game's host name and the status is decided by
# opening a TCP connection to it instead. That answers "is it up?" honestly;
# it cannot count players (nobody outside the game's namespace can), so the
# count then stays at 0.
GAME_PORT_LOW, GAME_PORT_HIGH = 13000, 13099
_SRV = {"ts": 0.0, "up": False, "count": 0}

STATUS_CMD  = _env_path("M2PANEL_STATUS_CMD", "auto").lower()

# Resolved lazily, not at import: the environment variable wins, but the config
# file is the fallback -- and CONF is not loaded until much further down this
# file. The container entrypoint writes the game's host name into the config as
# "game_host"; without this fallback nothing ever read it, the panel dropped
# through to listing local sockets, and a containerised panel -- which is in a
# different network namespace from the game -- reported a healthy server as
# offline. On FreeBSD there is no "game_host" key, so this stays empty and the
# socket listing is used exactly as before.
def _status_host():
    return _env_path("M2PANEL_STATUS_HOST", "") or str(CONF.get("game_host", "") or "")

def _status_ports():
    try:
        return [int(p) for p in CONF.get("status_ports", [11000, 13000])]
    except (TypeError, ValueError):
        return [11000, 13000]

def _sockets_sockstat(listening):
    """FreeBSD. Returns [(local_endpoint, foreign_endpoint), ...]; foreign is
    "" for listening sockets. Columns: USER COMMAND PID FD PROTO LOCAL FOREIGN."""
    out = subprocess.run(["sockstat", "-4l" if listening else "-4c"],
                         capture_output=True, text=True, timeout=3).stdout
    rows = []
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 6 and ":" in f[5]:
            rows.append((f[5], f[6] if len(f) >= 7 and ":" in f[6] else ""))
    return rows

def _sockets_ss(listening):
    """Linux. Same return shape as _sockets_sockstat.

    -H drops the header so there is nothing to skip, -n keeps ports numeric
    (otherwise 11002 would come back as a service name). Columns of the
    remaining line: STATE RECV-Q SEND-Q LOCAL PEER."""
    cmd = ["ss", "-H", "-4", "-n", "-t"]
    cmd += ["-l"] if listening else ["state", "established"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
    rows = []
    for line in out.splitlines():
        f = line.split()
        # 'ss -l' still prints the state column, 'ss state established' does
        # not - so count from the right, where local and peer always sit.
        if len(f) < 4 or ":" not in f[-2] or ":" not in f[-1]:
            continue
        rows.append((f[-2], f[-1]))
    return rows

def _sockets(listening):
    """The socket table, read with whichever tool this machine has."""
    order = {"sockstat": (_sockets_sockstat,),
             "ss":       (_sockets_ss,)}.get(STATUS_CMD,
                         (_sockets_sockstat, _sockets_ss))
    last = None
    for reader in order:
        try:
            return reader(listening)
        except (OSError, subprocess.SubprocessError) as e:
            last = e            # not installed on this system - try the next
    raise last or OSError("no socket listing tool available")

def _status_by_connect(host):
    """Container mode: is something accepting connections on every status port
    of the game host? Cannot count players, so that stays 0."""
    for p in _status_ports():
        try:
            with socket.create_connection((host, p), timeout=2):
                pass
        except OSError:
            return False, 0
    return True, 0

INGAME_WINDOW = 600      # seconds; see _players_in_game()

def _players_in_game():
    """How many characters the game has written to recently.

    Only used when the socket table is out of reach -- in a container the game's
    connections live in another network namespace, so counting them is simply
    not possible from here and the count used to sit at 0 forever. The game core
    saves each logged-in character every few minutes, so a recent `last_play' is
    a sound stand-in: it cannot see someone who logged in seconds ago, and it
    keeps counting someone for a few minutes after they leave. Approximate and
    honest beats exact and unavailable.
    """
    try:
        with db() as c, c.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM player.player "
                        "WHERE last_play > (NOW() - INTERVAL %s SECOND)",
                        (INGAME_WINDOW,))
            row = cur.fetchone()
        return int(row["n"] if isinstance(row, dict) else row[0])
    except Exception:
        return 0        # database unreachable: report nothing, never guess

def server_status():
    now = time.time()
    if now - _SRV["ts"] < 30:
        return _SRV
    up, count = False, 0
    try:
        status_host = _status_host()
        if status_host:
            up, count = _status_by_connect(status_host)
            if up:
                count = _players_in_game()
        else:
            ports_open = set()
            for local, _foreign in _sockets(listening=True):
                port = local.rpartition(":")[2]
                if port.isdigit():
                    ports_open.add(int(port))
            up = all(p in ports_open for p in _status_ports())

            if up:
                seen = set()
                for local, foreign in _sockets(listening=False):
                    if not foreign:
                        continue
                    local_ip, _, local_port = local.rpartition(":")
                    foreign_ip = foreign.rpartition(":")[0]
                    if not local_port.isdigit():
                        continue
                    if not (GAME_PORT_LOW <= int(local_port) <= GAME_PORT_HIGH):
                        continue
                    if foreign_ip in ("127.0.0.1", local_ip):
                        continue
                    seen.add(foreign)
                count = len(seen)
    except Exception:
        # no way to read the socket table, or it failed - claim nothing rather
        # than guess; the page then simply shows the server as down
        up, count = False, 0
    _SRV.update(ts=now, up=up, count=count)
    return _SRV

# ---- public server rates ----------------------------------------------------
# The front page shows the rates every private-server visitor asks about
# first. Missing table or unreachable database simply hides the badges -
# the front page must never break because the game side is down.
_RATES_PUB = {"ts": 0.0, "vals": None}

def public_rates():
    now = time.time()
    if now - _RATES_PUB["ts"] < 60:
        return _RATES_PUB["vals"]
    vals = None
    try:
        with db() as c, c.cursor() as cur:
            cur.execute("SELECT name, value FROM player.web_admin_rates")
            found = {r["name"]: int(r["value"]) for r in cur.fetchall()}
        vals = {n: found.get(n, 100) for n in RATE_NAMES}
    except Exception:
        vals = None
    _RATES_PUB.update(ts=now, vals=vals)
    return vals

# ---- client download facts --------------------------------------------------
# Size is instant; the SHA256 of 1.2 GB takes a few seconds, so it is
# computed once in a background thread after startup and appears on the
# page as soon as it is ready.
CLIENT_FACTS = {"size": 0, "sha256": "", "mtime": 0.0, "ts": 0.0}
_CLIENT_LOCK = threading.Lock()

def _client_sha_worker(path, size, mtime):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return
    with _CLIENT_LOCK:
        # Publish only if the file is still the one we hashed. A rebuild that
        # lands mid-hash would otherwise get the previous client's checksum.
        if CLIENT_FACTS["size"] == size and CLIENT_FACTS["mtime"] == mtime:
            CLIENT_FACTS["sha256"] = h.hexdigest()

def client_facts():
    """Size and checksum of the client, re-checked as it appears or changes.

    Reading this once at import was wrong in the normal case rather than an
    edge case: the client is built AFTER the panel starts -- often half an hour
    after, on a fresh install -- and it is rebuilt whenever the server address
    changes. The size and checksum then stayed at their startup values until
    somebody restarted the panel, and nothing anywhere told them to.
    """
    now = time.time()
    with _CLIENT_LOCK:
        if now - CLIENT_FACTS["ts"] < 10:      # a stat per request is wasteful
            return CLIENT_FACTS
        CLIENT_FACTS["ts"] = now
        try:
            st = os.stat(CLIENT_ZIP)
        except OSError:                        # not built yet, or removed again
            CLIENT_FACTS.update(size=0, sha256="", mtime=0.0)
            return CLIENT_FACTS
        if st.st_size == CLIENT_FACTS["size"] and st.st_mtime == CLIENT_FACTS["mtime"]:
            return CLIENT_FACTS
        CLIENT_FACTS.update(size=st.st_size, mtime=st.st_mtime, sha256="")
        size, mtime = st.st_size, st.st_mtime
    # Hashing 1.2 GB takes seconds, so it happens off the request thread and
    # the checksum simply appears on the page once it is ready.
    threading.Thread(target=_client_sha_worker,
                     args=(CLIENT_ZIP, size, mtime), daemon=True).start()
    return CLIENT_FACTS

def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return "%.0f %s" % (n, unit)
        n /= 1024.0
    return "%.1f GB" % n
ITEMS_PATH = _env_path("M2PANEL_ITEMS", os.path.join(_HERE, "items.json"))

# ---- server-wide rates ------------------------------------------------------
# The installer puts these two next to the panel. apply_rates.sh reads the wanted
# rates out of player.web_admin_rates, hands them to the server-files profile and
# restarts the game; rates.status is the one-line answer it leaves behind.
RATES_SCRIPT = _env_path("M2PANEL_RATES_SCRIPT", os.path.join(PANEL_DIR, "apply_rates.sh"))
RATES_STATUS = _env_path("M2PANEL_RATES_STATUS", os.path.join(PANEL_DIR, "rates.status"))
RATE_NAMES   = ("exp", "drop", "yang")
RATE_MIN, RATE_MAX = 1, 10000

CONF_PATH = _env_path("M2PANEL_CONF", "/usr/local/etc/m2panel.conf")
REQUIRED_CONF = ("flask_secret", "db_user", "db_pass", "salt", "pass_hash")

# Every setting in the config file can also come from the environment, which is
# how a container is normally fed its secrets: M2PANEL_DB_PASS for "db_pass",
# M2PANEL_FLASK_SECRET for "flask_secret", and so on. An environment variable
# wins over the file, and if it supplies everything REQUIRED_CONF asks for then
# there need not be a config file at all. Nothing is read from the environment
# unless it is set, so an existing installation behaves exactly as before.
ENV_CONF = ("flask_secret", "db_host", "db_user", "db_pass", "salt", "pass_hash",
            "bind", "port", "brand", "client_url", "client_name",
            "inventory_slots", "max_item_count", "status_ports", "local_only",
            "contact_email")

def _conf_from_env():
    """The config keys the environment sets, already turned into the right type."""
    out = {}
    for key in ENV_CONF:
        raw = os.environ.get("M2PANEL_" + key.upper(), "").strip()
        if not raw:
            continue
        if key in ("port", "inventory_slots", "max_item_count"):
            try:
                out[key] = int(raw)
            except ValueError:
                continue                      # nonsense value: let the default stand
        elif key == "local_only":
            # Whether this server is reachable only from the machine it runs on.
            # It cannot be inferred from "bind": a Linux server behind nginx
            # also binds the panel to 127.0.0.1 while being perfectly public,
            # and guessing there would tell a working host that nobody can
            # reach it. So the installer states it, and the default is "no".
            out[key] = raw.lower() in ("1", "true", "yes", "on")
        elif key == "status_ports":
            # "11002,13000" -> [11002, 13000]
            ports = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
            if all(p.isdigit() for p in ports) and ports:
                out[key] = [int(p) for p in ports]
        else:
            out[key] = raw
    return out

def _conf_die(problem):
    """Config is broken -> explain it in plain words and stop (no ugly traceback)."""
    print("\n".join([
        "",
        "  ⚠️  The Metin2 Admin Panel cannot start.",
        "",
        "  %s" % problem,
        "  Config file: %s" % CONF_PATH,
        "",
        "  EN: Please run the panel installer again — it creates this file for you.",
        "  DE: Bitte führe das Installationsprogramm erneut aus — es legt diese Datei für dich an.",
        "  TR: Lütfen panel kurulumunu tekrar çalıştır — bu dosyayı senin için oluşturur.",
        "",
        "  (Every setting can also be given as an environment variable instead,",
        "   e.g. M2PANEL_DB_PASS — that is how the container image is configured.)",
        "",
    ]), file=sys.stderr)
    sys.exit(1)

_ENV_CONF_VALUES = _conf_from_env()
try:
    with open(CONF_PATH) as f:
        CONF = json.load(f)
except FileNotFoundError:
    # Only fatal when the environment is not carrying the settings itself.
    if not all(_ENV_CONF_VALUES.get(k) for k in REQUIRED_CONF):
        _conf_die("The config file does not exist yet.")
    CONF = {}
except json.JSONDecodeError as e:
    _conf_die("The config file is not valid JSON (%s)." % e)
except OSError as e:
    _conf_die("The config file could not be read (%s)." % e)

if not isinstance(CONF, dict):
    _conf_die("The config file must contain a JSON object like { \"db_user\": \"...\" }.")
CONF.update(_ENV_CONF_VALUES)          # the environment has the last word
_missing = [k for k in REQUIRED_CONF if not CONF.get(k)]
if _missing:
    _conf_die("These settings are missing or empty in the config file: %s." % ", ".join(_missing))

# How many inventory slots this server build has. Raising it is only safe if the
# server really has more inventory pages, so the default stays at one page (45).
try:
    INVENTORY_SLOTS = int(CONF.get("inventory_slots", 45))
except (TypeError, ValueError):
    INVENTORY_SLOTS = 45
if INVENTORY_SLOTS < 1:
    INVENTORY_SLOTS = 45

# The biggest stack the game can store, which is whatever player.item.count
# holds. That is NOT the same everywhere: some server files declare it
# SMALLINT UNSIGNED (65535), the [40250] reference files declare it TINYINT
# UNSIGNED (255). It matters, because MySQL outside strict mode does not
# complain about a too-large number — it quietly stores the largest one that
# fits, and the admin then wonders why "give 1000 potions" produced 255.
# The default stays at 65535 so nothing changes for the servers this was
# written on; set "max_item_count" in the config (or M2PANEL_MAX_ITEM_COUNT)
# to 255 on server files whose column is a TINYINT.
try:
    MAX_ITEM_COUNT = int(CONF.get("max_item_count", 65535))
except (TypeError, ValueError):
    MAX_ITEM_COUNT = 65535
if not (1 <= MAX_ITEM_COUNT <= 65535):
    MAX_ITEM_COUNT = 65535

# ---- what the game download is called -------------------------------------
# The installer writes "client_name" into the config from the chosen server-files
# pack, e.g. "40250 - Official 2014 Client (15 Languages)". Config files written
# by an older installer do not have the key: then everything stays as it was.
# path separators, the characters Windows refuses in a file name, and everything
# that would break the Content-Disposition header (quotes, semicolons, controls).
# Accented letters survive — "Türkçe Client.zip" is a perfectly good file name.
_UNSAFE_IN_FILENAME = re.compile(r"[\x00-\x1f\x7f/\\<>:\"'|?*%;,]+")

def _client_download_name(raw):
    """Turn a friendly client name into a safe file name ending in .zip."""
    name = _UNSAFE_IN_FILENAME.sub(" ", str(raw or ""))
    name = re.sub(r"\s+", " ", name).strip()           # collapse the gaps that leaves
    name = name.strip(". ")                            # no ".." and no hidden files
    if name.lower().endswith(".zip"):                  # do not end up with "x.zip.zip"
        name = name[:-4].strip(". ")
    return (name + ".zip") if name else "Metin2Client.zip"

# The name this server goes by, shown in the header and the browser tab. It is a
# config key so a different install can call itself something else without the
# panel needing to be edited.
BRAND = str(CONF.get("brand", "") or "").strip() or "Singleplayer Official Metin2"

CLIENT_NAME  = str(CONF.get("client_name", "Metin2 Client") or "").strip()
CLIENT_FILE  = _client_download_name(CLIENT_NAME)
# shown on the download button; empty means "no name configured" -> t('download')
CLIENT_LABEL = str(CONF.get("client_name", "") or "").strip()

# Optional: hand the download off to somewhere else (MEGA, Google Drive, your own
# https:// site) instead of serving the 1 GB zip from this panel.
#
# Why you may want this: Windows Defender flags downloads that arrive over plain
# http:// from a bare IP address on an unusual port — it reports them as
# "Trojan:Win32/MalUri" ("malicious URI"). That verdict is about the ADDRESS, not
# the file; the very same zip fetched from an https:// link with a real domain
# name is accepted. Serving a gigabyte through Flask's development server is also
# slow, so pointing players at proper hosting fixes both problems at once.
def _clean_client_url(u):
    u = str(u or "").strip()
    # only ever emit a link we would be happy to put in an href
    if u[:8].lower() == "https://" or u[:7].lower() == "http://":
        return u if len(u) < 2000 and "\n" not in u and "\r" not in u else ""
    return ""

CLIENT_URL = _clean_client_url(CONF.get("client_url", ""))

# item database (built from item_proto): [{v:vnum, n:name, k:search keywords, c:category}, ...]
try:
    with open(ITEMS_PATH, encoding="utf-8") as f:
        ITEMS = json.load(f)
except Exception:
    ITEMS = []
# vnum -> display name, for the read-only inventory view
ITEM_NAMES = {it["v"]: it["n"] for it in ITEMS}

# ---- UI translations (interface language: de / en / tr) ----
LANGS = {"en": "English", "de": "Deutsch", "tr": "Türkçe"}
T = {
 "welcome":      {"en":"Welcome!","de":"Willkommen!","tr":"Hoş geldin!"},
 "admin_hint":   {"en":"If you're the admin, enter your passphrase.","de":"Wenn du der Admin bist, gib deine Passphrase ein.","tr":"Yöneticiysen gizli kelimeni yaz."},
 "passphrase":   {"en":"Passphrase","de":"Passphrase","tr":"Gizli kelime"},
 "login":        {"en":"Log in","de":"Einloggen","tr":"Giriş yap"},
 "logout":       {"en":"Log out","de":"Abmelden","tr":"Çıkış"},
 "player_q":     {"en":"Are you a player?","de":"Bist du ein Spieler?","tr":"Oyuncu musun?"},
 "dl_hint":      {"en":"Download it, extract it anywhere you prefer and just run Metin2Distribute.exe — no installation, everything comes pre-configured. The game is fully portable: copy the folder onto a flash drive and play from any machine you like.",
                  "de":"Herunterladen, an einen beliebigen Ort entpacken und einfach Metin2Distribute.exe starten — keine Installation, alles ist vorkonfiguriert. Das Spiel ist komplett portabel: Kopiere den Ordner auf einen USB-Stick und spiele von jedem Rechner, den du magst.",
                  "tr":"İndir, istediğin yere çıkart ve sadece Metin2Distribute.exe'yi çalıştır — kurulum yok, her şey hazır gelir. Oyun tamamen taşınabilir: klasörü bir USB belleğe kopyala, istediğin bilgisayardan oyna."},
 "download":     {"en":"📥 Download the Game","de":"📥 Spiel herunterladen","tr":"📥 Oyunu İndir"},
 "game_account": {"en":"Game account","de":"Spiel-Konto","tr":"Oyun hesabı"},
 "create_acc":   {"en":"📝 Create account","de":"📝 Konto erstellen","tr":"📝 Kayıt ol"},
 "my_acc":       {"en":"👤 My account","de":"👤 Mein Konto","tr":"👤 Hesabım"},
 "players":      {"en":"Players","de":"Spieler","tr":"Oyuncular"},
 "acc_col":      {"en":"Account","de":"Konto","tr":"Hesap"},
 "srv_online":   {"en":"Server online","de":"Server online","tr":"Sunucu çevrimiçi"},
 # --- download steps & facts ---
 "dl_how":       {"en":"How it works","de":"So geht's","tr":"Nasıl çalışır"},
 "dl_st1":       {"en":"Download the zip","de":"Zip herunterladen","tr":"Zip'i indir"},
 "dl_st2":       {"en":"Extract it anywhere you like — a flash drive works too","de":"Irgendwohin entpacken — auch ein USB-Stick geht","tr":"İstediğin yere çıkart — USB bellek de olur"},
 "dl_st3":       {"en":"Run Metin2Distribute.exe and play","de":"Metin2Distribute.exe starten und spielen","tr":"Metin2Distribute.exe'yi çalıştır ve oyna"},
 "dl_sha":       {"en":"With this fingerprint you can verify the download arrived intact — compare it with what your checksum tool says.",
                  "de":"Mit diesem Fingerabdruck kannst du prüfen, ob der Download heil angekommen ist — vergleiche ihn mit dem, was dein Prüfsummen-Tool sagt.",
                  "tr":"Bu parmak iziyle indirmenin sağlam geldiğini doğrulayabilirsin — sağlama aracının söylediğiyle karşılaştır."},
 # --- registration polish ---
 "reg_title":    {"en":"Create your game account","de":"Spiel-Konto erstellen","tr":"Oyun hesabı oluştur"},
 "reg_hint":     {"en":"This is the account you'll use to log into the game itself.","de":"Mit diesem Konto meldest du dich im Spiel selbst an.","tr":"Oyuna bu hesapla giriş yapacaksın."},
 "reg_ph_user":  {"en":"Username (4-16 letters/numbers)","de":"Benutzername (4-16 Buchstaben/Zahlen)","tr":"Kullanıcı adı (4-16 harf/rakam)"},
 "reg_ph_pw":    {"en":"Password (at least 6 characters)","de":"Passwort (mindestens 6 Zeichen)","tr":"Şifre (en az 6 karakter)"},
 "reg_ph_pw2":   {"en":"Password again","de":"Passwort wiederholen","tr":"Şifre (tekrar)"},
 "reg_ph_social":{"en":"Delete code — pick any 7 digits","de":"Löschcode — beliebige 7 Ziffern","tr":"Silme kodu — 7 rakam seç"},
 "reg_social_hint":{"en":"💡 The delete code is asked by the game when you delete a character. Pick 7 digits you'll remember (e.g. 1234567).",
                  "de":"💡 Den Löschcode fragt das Spiel ab, wenn du einen Charakter löschst. Wähle 7 Ziffern, die du dir merkst (z. B. 1234567).",
                  "tr":"💡 Silme kodunu oyun, karakter silerken sorar. Hatırlayacağın 7 rakam seç (örn. 1234567)."},
 "reg_free":     {"en":"✓ Name is free","de":"✓ Name ist frei","tr":"✓ İsim boş"},
 "reg_taken":    {"en":"✗ Already taken","de":"✗ Schon vergeben","tr":"✗ Zaten alınmış"},
 "reg_pw_match": {"en":"✓ Passwords match","de":"✓ Passwörter stimmen überein","tr":"✓ Şifreler uyuşuyor"},
 "reg_pw_diff":  {"en":"✗ Passwords differ","de":"✗ Passwörter unterscheiden sich","tr":"✗ Şifreler farklı"},
 "reg_done_title":{"en":"Your account is ready!","de":"Dein Konto ist fertig!","tr":"Hesabın hazır!"},
 "reg_done_next":{"en":"From here to the game:","de":"Von hier bis ins Spiel:","tr":"Buradan oyuna:"},
 "reg_done_login":{"en":"Log in with the username and password you just chose","de":"Melde dich mit dem eben gewählten Namen und Passwort an","tr":"Az önce seçtiğin ad ve şifreyle giriş yap"},
 # --- admin: player search, activity, inventory ---
 "search_players":{"en":"Filter by character or account…","de":"Nach Charakter oder Konto filtern…","tr":"Karakter veya hesaba göre süz…"},
 "tip_active":   {"en":"Was in the game within the last few minutes.","de":"War in den letzten Minuten im Spiel.","tr":"Son birkaç dakika içinde oyundaydı."},
 "inv_title":    {"en":"Inventory","de":"Inventar","tr":"Envanter"},
 "tip_inv":      {"en":"What this character carries, straight from the database — read-only. Handy for checking before you gift something twice.",
                  "de":"Was dieser Charakter bei sich trägt, direkt aus der Datenbank — nur lesend. Praktisch, um nicht doppelt zu schenken.",
                  "tr":"Bu karakterin üzerindekiler, doğrudan veritabanından — salt okunur. Bir şeyi iki kez hediye etmemek için kullanışlı."},
 "inv_empty":    {"en":"Nothing in the inventory yet.","de":"Noch nichts im Inventar.","tr":"Envanterde henüz bir şey yok."},
 "srv_playing":  {"en":"in game right now","de":"gerade im Spiel","tr":"şu an oyunda"},
 "srv_offline":  {"en":"The game server is down at the moment — it usually comes right back. Downloads and account pages keep working.",
                  "de":"Der Spielserver ist gerade aus — meist ist er gleich wieder da. Download und Konto-Seiten funktionieren weiter.",
                  "tr":"Oyun sunucusu şu an kapalı — genellikle hemen geri gelir. İndirme ve hesap sayfaları çalışmaya devam eder."},
 "tip_srv":      {"en":"Checked live against the game itself, at most every 30 seconds: green means the login server and the first channel both answer. The number is characters the game has saved in the last few minutes, so somebody who just logged in may take a moment to appear, and somebody who just left lingers a little.",
                  "de":"Live am Spiel selbst geprüft, höchstens alle 30 Sekunden: Grün heißt, Login-Server und erster Kanal antworten beide. Die Zahl sind Charaktere, die das Spiel in den letzten Minuten gespeichert hat — wer sich gerade erst eingeloggt hat, erscheint also etwas verzögert, und wer eben gegangen ist, bleibt kurz stehen.",
                  "tr":"Doğrudan oyunun kendisinden, en fazla 30 saniyede bir kontrol edilir: yeşil, giriş sunucusunun ve ilk kanalın yanıt verdiği anlamına gelir. Sayı, oyunun son birkaç dakikada kaydettiği karakterlerdir; yeni giren biri biraz gecikmeyle görünür, yeni çıkan biri ise kısa süre sayılmaya devam eder."},
 "tip_acc_col":  {"en":"The game account this character belongs to — the name the player types at the game login. One account can hold several characters.",
                  "de":"Das Spiel-Konto, zu dem dieser Charakter gehört — der Name, den der Spieler beim Spiel-Login eintippt. Ein Konto kann mehrere Charaktere haben.",
                  "tr":"Bu karakterin bağlı olduğu oyun hesabı — oyuncunun oyun girişinde yazdığı ad. Bir hesapta birden çok karakter olabilir."},
 "tap_hint":     {"en":"Tap a player's name to manage them.","de":"Tippe auf einen Spielernamen, um ihn zu verwalten.","tr":"Yönetmek için oyuncunun adına dokun."},
 "character":    {"en":"Character","de":"Charakter","tr":"Karakter"},
 "level":        {"en":"Level","de":"Level","tr":"Seviye"},
 "last_seen":    {"en":"Last seen","de":"Zuletzt online","tr":"Son görülme"},
 "no_chars":     {"en":"No characters yet. They appear here after the first login.","de":"Noch keine Charaktere. Sie erscheinen nach dem ersten Login.","tr":"Henüz karakter yok. İlk girişten sonra görünecek."},
 "back_players": {"en":"← Back to players","de":"← Zurück zu den Spielern","tr":"← Oyunculara dön"},
 "give_item":    {"en":"🎁 Give an item","de":"🎁 Gegenstand geben","tr":"🎁 Eşya ver"},
 "search_item":  {"en":"Type to search item (e.g. sword, potion)…","de":"Zum Suchen tippen (z.B. Schwert, Trank)…","tr":"Aramak için yaz (örn. kılıç, iksir)…"},
 "category":     {"en":"Category","de":"Kategorie","tr":"Kategori"},
 "qty":          {"en":"How many?","de":"Wie viele?","tr":"Kaç adet?"},
 "send_item":    {"en":"🎁 Send item","de":"🎁 Senden","tr":"🎁 Gönder"},
 "give_gold":    {"en":"💰 Give yang","de":"💰 Yang geben","tr":"💰 Yang ver"},
 "amount":       {"en":"Amount (negative takes yang away)","de":"Menge (negativ = abziehen)","tr":"Miktar (eksi = al)"},
 "send_gold":    {"en":"💰 Send yang","de":"💰 Yang senden","tr":"💰 Yang gönder"},
 "set_level":    {"en":"⭐ Set level","de":"⭐ Level setzen","tr":"⭐ Seviye ayarla"},
 "new_level":    {"en":"New level (1-120)","de":"Neues Level (1-120)","tr":"Yeni seviye (1-120)"},
 "change_level": {"en":"⭐ Change level","de":"⭐ Level ändern","tr":"⭐ Seviyeyi değiştir"},
 "teleport":     {"en":"🗺️ Teleport","de":"🗺️ Teleportieren","tr":"🗺️ Işınla"},
 "ingame_only":  {"en":"(works while the player is in game)","de":"(nur wenn der Spieler online ist)","tr":"(oyuncu oyundayken çalışır)"},
 "speed":        {"en":"🏃 Running speed","de":"🏃 Laufgeschwindigkeit","tr":"🏃 Koşma hızı"},
 "apply":        {"en":"🏃 Apply","de":"🏃 Anwenden","tr":"🏃 Uygula"},
 "cat_all":      {"en":"All","de":"Alle","tr":"Hepsi"},
 "cat_weapon":   {"en":"Weapons","de":"Waffen","tr":"Silahlar"},
 "cat_armor":    {"en":"Armor","de":"Rüstung","tr":"Zırhlar"},
 "cat_usable":   {"en":"Potions/Usable","de":"Tränke/Nutzbar","tr":"İksir/Kullanılabilir"},
 "cat_ds":       {"en":"Dragon Soul","de":"Drachenseele","tr":"Ejder Ruhu"},
 "cat_metin":    {"en":"Metin Stones","de":"Metinsteine","tr":"Metin Taşları"},
 "cat_special":  {"en":"Special","de":"Spezial","tr":"Özel"},
 "cat_other":    {"en":"Other","de":"Sonstige","tr":"Diğer"},
 # --- status & error messages ---
 "db_down":      {"en":"The game database can't be reached right now. The server may be down — please try again in a bit. 🙏",
                  "de":"Die Spiel-Datenbank ist gerade nicht erreichbar. Der Server ist vielleicht aus — bitte versuche es gleich noch einmal. 🙏",
                  "tr":"Oyun veritabanına şu anda ulaşılamıyor. Sunucu kapalı olabilir — birazdan tekrar dene. 🙏"},
 "csrf_bad":     {"en":"For your safety that request was blocked — it didn't come from this page. Please open the panel again and retry. 🔒",
                  "de":"Zu deiner Sicherheit wurde diese Anfrage blockiert — sie kam nicht von dieser Seite. Bitte öffne das Panel neu und versuche es erneut. 🔒",
                  "tr":"Güvenliğin için bu istek engellendi — bu sayfadan gelmedi. Lütfen paneli yeniden aç ve tekrar dene. 🔒"},
 # {max} is MAX_ITEM_COUNT, which depends on the server files (see there):
 # 65,535 on most of them, 255 on the [40250] reference files.
 "qty_range":    {"en":"Please enter a quantity between 1 and {max} — the game can't store more than that in one stack. 🙂",
                  "de":"Bitte gib eine Menge zwischen 1 und {max} ein — mehr passt im Spiel nicht in einen Stapel. 🙂",
                  "tr":"Lütfen 1 ile {max} arasında bir adet gir — oyun tek yığında bundan fazlasını saklayamaz. 🙂"},
 # {conf} is the config file this panel actually read — it is not always
 # /usr/local/etc/m2panel.conf any more (see M2PANEL_CONF).
 "inv_full":     {"en":"The inventory is full — ask the player to free up some space. 🎒 (If their server has more inventory pages, raise \"inventory_slots\" in {conf}.)",
                  "de":"Das Inventar ist voll — bitte den Spieler, Platz zu schaffen. 🎒 (Hat der Server mehr Inventarseiten, erhöhe \"inventory_slots\" in {conf}.)",
                  "tr":"Envanter dolu — oyuncudan yer açmasını iste. 🎒 (Sunucunda daha fazla envanter sayfası varsa {conf} içindeki \"inventory_slots\" değerini artır.)"},
 "ingame_offline":{"en":"🙂 {name} isn't in game right now, and this action only works while they're online. Nothing was changed — ask them to log in and try again.",
                  "de":"🙂 {name} ist gerade nicht im Spiel, und diese Aktion funktioniert nur online. Es wurde nichts geändert — bitte einloggen lassen und erneut versuchen.",
                  "tr":"🙂 {name} şu anda oyunda değil ve bu işlem yalnızca oyundayken çalışır. Hiçbir şey değiştirilmedi — giriş yapmasını isteyip tekrar dene."},
 "ingame_timeout":{"en":"⏳ The in-game helper didn't answer, so this action couldn't be delivered to {name}. Nothing was changed — check that the game server is running and try again in a moment.",
                  "de":"⏳ Der Ingame-Helfer hat nicht geantwortet, deshalb konnte die Aktion {name} nicht zugestellt werden. Es wurde nichts geändert — prüfe, ob der Spielserver läuft, und versuche es gleich noch einmal.",
                  "tr":"⏳ Oyun içi yardımcı yanıt vermedi, bu yüzden işlem {name} oyuncusuna iletilemedi. Hiçbir şey değiştirilmedi — oyun sunucusunun çalıştığını kontrol et ve az sonra tekrar dene."},
 "act_done":     {"en":"✅ Done! The action was applied instantly for {name}.",
                  "de":"✅ Fertig! Die Aktion wurde sofort für {name} angewendet.",
                  "tr":"✅ Tamam! İşlem {name} için anında uygulandı."},
 "act_offline":  {"en":"✅ {name} wasn't in game right now — it was applied to their account and will be ready when they log in! 🎉",
                  "de":"✅ {name} war gerade nicht im Spiel — es wurde direkt auf dem Konto angewendet und ist beim nächsten Login da! 🎉",
                  "tr":"✅ {name} şu anda oyunda değildi — işlem hesabına uygulandı, giriş yaptığında hazır olacak! 🎉"},
 "act_late_done":{"en":"✅ Just in time! {name} picked it up in game while we were waiting, so it was applied there — not twice. 🎉",
                  "de":"✅ Gerade noch rechtzeitig! {name} hat es im Spiel erhalten, während wir gewartet haben — es wurde also nur einmal angewendet. 🎉",
                  "tr":"✅ Tam zamanında! {name} beklerken bunu oyun içinde aldı, yani işlem iki kez değil bir kez uygulandı. 🎉"},
 "act_late_other":{"en":"ℹ️ The in-game helper took this over while we were waiting (status: {status}), so nothing was applied twice. Please check {name} and try again if needed.",
                  "de":"ℹ️ Der Ingame-Helfer hat das während des Wartens übernommen (Status: {status}) — es wurde also nichts doppelt angewendet. Bitte prüfe {name} und versuche es bei Bedarf erneut.",
                  "tr":"ℹ️ Oyun içi yardımcı bunu beklerken devraldı (durum: {status}), bu yüzden hiçbir şey iki kez uygulanmadı. Lütfen {name} oyuncusunu kontrol et ve gerekirse tekrar dene."},
 "act_error":    {"en":"Something went wrong ({status}), but no worries — you can just try again.",
                  "de":"Etwas ist schiefgelaufen ({status}), aber keine Sorge — versuche es einfach noch einmal.",
                  "tr":"Bir şeyler ters gitti ({status}) ama merak etme — tekrar deneyebilirsin."},
 "act_novalue":  {"en":"Looks like you forgot to enter a value — could you try again? 🙂",
                  "de":"Da fehlt wohl noch ein Wert — magst du es noch einmal versuchen? 🙂",
                  "tr":"Görünüşe göre bir değer girmeyi unuttun — tekrar dener misin? 🙂"},
 "act_unexpected":{"en":"An unexpected problem occurred. Try again; if it keeps happening, tell the person who set up the server. 🙏",
                  "de":"Es gab ein unerwartetes Problem. Versuche es erneut; wenn es bleibt, sag der Person Bescheid, die den Server eingerichtet hat. 🙏",
                  "tr":"Beklenmeyen bir sorun oluştu. Tekrar dene; devam ederse sunucuyu kuran kişiye haber ver. 🙏"},
 "not_found":    {"en":"Player not found.","de":"Spieler nicht gefunden.","tr":"Oyuncu bulunamadı."},
 # --- server rates ---
 "rates_nav":    {"en":"⚙️ Server rates","de":"⚙️ Server-Raten","tr":"⚙️ Sunucu oranları"},
 "rates_open":   {"en":"⚙️ Open server rates","de":"⚙️ Server-Raten öffnen","tr":"⚙️ Sunucu oranlarını aç"},
 "rates_dash_hint":{"en":"Make the whole server give more experience, more items and more yang — handy if you would rather do quests than grind.",
                  "de":"Lass den ganzen Server mehr Erfahrung, mehr Gegenstände und mehr Yang geben — praktisch, wenn du lieber Quests machst als zu grinden.",
                  "tr":"Tüm sunucunun daha çok tecrübe, daha çok eşya ve daha çok yang vermesini sağla — grind yerine görev yapmayı seviyorsan çok işine yarar."},
 "rates_intro":  {"en":"These three numbers decide how quickly the whole server moves. 100% is exactly how the game was made — higher means faster. Saving restarts the game server, so anyone playing gets dropped for a moment.",
                  "de":"Diese drei Zahlen bestimmen, wie schnell der ganze Server läuft. 100% ist genau so, wie das Spiel gemacht wurde — höher heißt schneller. Beim Speichern wird der Spielserver neu gestartet, wer gerade spielt fliegt also kurz raus.",
                  "tr":"Bu üç sayı tüm sunucunun ne kadar hızlı ilerlediğini belirler. 100%, oyunun yapıldığı hâlidir — yüksek olması daha hızlı demektir. Kaydetmek oyun sunucusunu yeniden başlatır, o sırada oynayan varsa kısa bir süre düşer."},
 "rates_exp":    {"en":"Experience","de":"Erfahrung","tr":"Tecrübe"},
 "rates_exp_help":{"en":"How much experience monsters give. 300% means levelling up goes three times as fast.",
                  "de":"Wie viel Erfahrung Monster geben. 300% heißt, du levelst dreimal so schnell.",
                  "tr":"Canavarların verdiği tecrübe. 300% seviye atlamanın üç kat hızlanması demek."},
 "rates_drop":   {"en":"Item drop","de":"Gegenstände","tr":"Eşya düşme"},
 "rates_drop_help":{"en":"How often monsters drop items. 200% means twice as many drops. A chance can never go above certain, so items that always dropped simply keep dropping.",
                  "de":"Wie oft Monster Gegenstände fallen lassen. 200% heißt doppelt so viele. Mehr als sicher geht nicht — was immer gedroppt ist, droppt einfach weiter.",
                  "tr":"Canavarların ne sıklıkla eşya düşürdüğü. 200% iki katı düşme demek. Bir ihtimal kesinliğin üstüne çıkamaz, yani zaten hep düşen eşyalar düşmeye devam eder."},
 "rates_yang":   {"en":"Yang","de":"Yang","tr":"Yang"},
 "rates_yang_help":{"en":"How much money monsters drop when you kill them.",
                  "de":"Wie viel Geld Monster fallen lassen, wenn du sie besiegst.",
                  "tr":"Canavarları öldürdüğünde düşen para miktarı."},
 "rates_percent":{"en":"Percent — 100 is normal","de":"Prozent — 100 ist normal","tr":"Yüzde — 100 normaldir"},
 "rates_current":{"en":"Active right now","de":"Gerade aktiv","tr":"Şu anda geçerli"},
 "rates_presets":{"en":"Or take one of these","de":"Oder nimm eine davon","tr":"Ya da bunlardan birini seç"},
 "rates_presets_hint":{"en":"One tap fills the three boxes in — you still have to save.",
                  "de":"Ein Tipp füllt die drei Felder aus — speichern musst du trotzdem noch.",
                  "tr":"Bir dokunuş üç kutuyu doldurur — yine de kaydetmen gerekir."},
 "rates_p_normal":{"en":"🎯 Normal — exactly like the original game",
                  "de":"🎯 Normal — genau wie im Originalspiel",
                  "tr":"🎯 Normal — orijinal oyundaki gibi"},
 "rates_p_relaxed":{"en":"🌿 Relaxed questing — experience 300%, items 200%, yang 200%",
                  "de":"🌿 Entspannt questen — Erfahrung 300%, Gegenstände 200%, Yang 200%",
                  "tr":"🌿 Rahat görev — tecrübe 300%, eşya 200%, yang 200%"},
 "rates_p_fast": {"en":"🚀 Fast — experience 1000%, items 500%, yang 500%",
                  "de":"🚀 Schnell — Erfahrung 1000%, Gegenstände 500%, Yang 500%",
                  "tr":"🚀 Hızlı — tecrübe 1000%, eşya 500%, yang 500%"},
 "rates_save":   {"en":"💾 Save and restart the server","de":"💾 Speichern und Server neu starten","tr":"💾 Kaydet ve sunucuyu yeniden başlat"},
 "rates_range":  {"en":"Each of the three has to be a whole number between 1 and 10000. Nothing was changed. 🙂",
                  "de":"Alle drei müssen ganze Zahlen zwischen 1 und 10000 sein. Es wurde nichts geändert. 🙂",
                  "tr":"Üçü de 1 ile 10000 arasında tam sayı olmalı. Hiçbir şey değiştirilmedi. 🙂"},
 "rates_saved":  {"en":"✅ Saved! The game server is restarting now and should be back in under a minute. Give this page a reload in a moment to see how it went.",
                  "de":"✅ Gespeichert! Der Spielserver startet gerade neu und sollte in weniger als einer Minute wieder da sein. Lade diese Seite gleich neu, um das Ergebnis zu sehen.",
                  "tr":"✅ Kaydedildi! Oyun sunucusu şimdi yeniden başlıyor, bir dakikadan kısa sürede geri gelmeli. Sonucu görmek için birazdan bu sayfayı yenile."},
 "rates_no_script":{"en":"This server was set up before the rates feature existed, so the little helper that applies them is missing. Run the installer again on the server — it adds the helper and changes nothing else. 🙂",
                  "de":"Dieser Server wurde eingerichtet, bevor es die Raten gab, deshalb fehlt das kleine Hilfsprogramm dafür. Führe das Installationsprogramm auf dem Server noch einmal aus — es ergänzt nur dieses Hilfsprogramm. 🙂",
                  "tr":"Bu sunucu oran özelliği eklenmeden önce kurulmuş, bu yüzden oranları uygulayan küçük yardımcı yok. Sunucuda kurulumu tekrar çalıştır — sadece bu yardımcıyı ekler, başka bir şeye dokunmaz. 🙂"},
 "rates_no_table":{"en":"The rates could not be saved, because the table they live in is not there yet. Running the installer again on the server creates it. 🙏",
                  "de":"Die Raten konnten nicht gespeichert werden, weil die zugehörige Tabelle noch fehlt. Ein erneuter Lauf des Installationsprogramms legt sie an. 🙏",
                  "tr":"Oranlar kaydedilemedi, çünkü bulundukları tablo henüz yok. Sunucuda kurulumu tekrar çalıştırmak bu tabloyu oluşturur. 🙏"},
 "rates_st":     {"en":"How the last change went","de":"Wie die letzte Änderung lief","tr":"Son değişiklik nasıl gitti"},
 "rates_st_running":{"en":"⏳ The rates are being applied and the server is restarting. This normally takes well under a minute.",
                  "de":"⏳ Die Raten werden angewendet und der Server startet neu. Das dauert normalerweise deutlich unter einer Minute.",
                  "tr":"⏳ Oranlar uygulanıyor ve sunucu yeniden başlıyor. Bu genelde bir dakikadan çok kısa sürer."},
 "rates_st_ok":  {"en":"✅ These rates are live on the server.",
                  "de":"✅ Diese Raten sind auf dem Server aktiv.",
                  "tr":"✅ Bu oranlar sunucuda geçerli."},
 "rates_st_unsupported":{"en":"⚠️ These server files cannot have their rates changed, so nothing was applied — whatever you type here will have no effect in the game. This is not something you did wrong; the set of server files simply does not support it.",
                  "de":"⚠️ Bei diesen Serverdateien lassen sich die Raten nicht ändern, es wurde also nichts angewendet — was du hier einträgst, wirkt sich im Spiel nicht aus. Du hast nichts falsch gemacht, diese Serverdateien können es einfach nicht.",
                  "tr":"⚠️ Bu sunucu dosyalarının oranları değiştirilemiyor, bu yüzden hiçbir şey uygulanmadı — buraya ne yazarsan yaz oyunda bir etkisi olmaz. Senin hatan değil; bu sunucu dosyaları bunu desteklemiyor."},
 "rates_st_failed":{"en":"⚠️ Something went wrong while applying the rates, so they may not all be live. The details are in /var/log/m2rates.log on the server.",
                  "de":"⚠️ Beim Anwenden der Raten ist etwas schiefgelaufen, vielleicht sind nicht alle aktiv. Die Einzelheiten stehen auf dem Server in /var/log/m2rates.log.",
                  "tr":"⚠️ Oranlar uygulanırken bir şeyler ters gitti, hepsi geçerli olmayabilir. Ayrıntılar sunucudaki /var/log/m2rates.log dosyasında."},
 "rates_st_no_restart":{"en":"ℹ️ The new rates are saved, but the game server could not be restarted on its own. Restart it yourself and they take effect right away.",
                  "de":"ℹ️ Die neuen Raten sind gespeichert, aber der Spielserver konnte nicht selbst neu gestartet werden. Starte ihn von Hand neu, dann gelten sie sofort.",
                  "tr":"ℹ️ Yeni oranlar kaydedildi ama oyun sunucusu kendi kendine yeniden başlatılamadı. Sen yeniden başlatınca hemen geçerli olurlar."},

 # --- what this project is (shown to everyone, no login needed) ---
 "about_title":  {"en":"What is this?","de":"Was ist das hier?","tr":"Bu nedir?"},
 "about_goal":   {"en":"This is Metin2 as it was in 2014 — the original game, unchanged — running on a server somebody set up for themselves. No item shop, nothing to buy, and none of the grind that only exists to sell you something. You play at your own pace.",
                  "de":"Das hier ist Metin2, wie es 2014 war — das Originalspiel, unverändert — auf einem Server, den sich jemand selbst eingerichtet hat. Kein Item-Shop, nichts zu kaufen, und nichts von dem Grind, den es nur gibt, um dir etwas zu verkaufen. Du spielst in deinem eigenen Tempo.",
                  "tr":"Burada 2014'teki hâliyle Metin2 var — orijinal oyun, değiştirilmemiş — birinin kendisi için kurduğu bir sunucuda çalışıyor. Item shop yok, satın alınacak bir şey yok ve sadece sana bir şey satmak için var olan o grind yok. Kendi hızında oynarsın."},
 "about_hobby":  {"en":"This is a hobby project. Nobody earns anything from it, there is nothing to buy, and there never will be.",
                  "de":"Das hier ist ein Hobbyprojekt. Niemand verdient daran etwas, es gibt nichts zu kaufen, und das wird auch so bleiben.",
                  "tr":"Burası bir hobi projesi. Kimse bundan para kazanmıyor, satın alınacak bir şey yok ve olmayacak da."},
 "about_simple": {"en":"Everything is deliberately kept as simple as possible — this panel included. Plain pages, big buttons, and an explanation on almost everything: hold your mouse over a button or a field and it tells you what it does.",
                  "de":"Alles ist bewusst so einfach wie möglich gehalten — auch dieses Panel. Schlichte Seiten, große Schaltflächen und zu fast allem eine Erklärung: Halte die Maus über eine Schaltfläche oder ein Feld, dann steht dort, was sie tut.",
                  "tr":"Her şey bilerek olabildiğince basit tutuldu — bu panel de dahil. Sade sayfalar, büyük düğmeler ve neredeyse her şey için bir açıklama: farenle bir düğmenin veya alanın üzerinde bekle, ne işe yaradığını yazar."},
 "about_uptime": {"en":"Characters live in this server's own database and nowhere else — nobody has a second copy, and nothing is sent anywhere. Which also means how long this world lasts is entirely up to whoever runs it.",
                  "de":"Charaktere liegen in der Datenbank dieses Servers und sonst nirgends — niemand hat eine zweite Kopie, und es wird nichts irgendwohin übertragen. Das heißt aber auch: Wie lange es diese Welt gibt, entscheidet allein, wer den Server betreibt.",
                  "tr":"Karakterler yalnızca bu sunucunun kendi veritabanında durur — kimsede ikinci bir kopya yok ve hiçbir yere bir şey gönderilmiyor. Bu aynı zamanda şu demek: bu dünyanın ne kadar süreceğine yalnızca sunucuyu işleten kişi karar verir."},
 "about_oss":    {"en":"Everything needed to run this is open source. Anyone can set up exactly the same server on their own machine with a single command — no Metin2 knowledge required.",
                  "de":"Alles, was dafür nötig ist, ist Open Source. Jeder kann sich mit einem einzigen Befehl genau denselben Server selbst aufsetzen — ganz ohne Metin2-Vorwissen.",
                  "tr":"Bunu çalıştırmak için gereken her şey açık kaynak. İsteyen herkes tek bir komutla aynı sunucuyu kendi makinesinde kurabilir — Metin2 bilgisi gerekmez."},
 "about_contact":{"en":"If you feel the server needs adjusting — the rates, movement speed, or anything else — write to",
                  "de":"Wenn du findest, dass am Server etwas angepasst werden sollte — die Raten, die Laufgeschwindigkeit oder irgendetwas anderes — schreib an",
                  "tr":"Sunucuda bir şeyin ayarlanması gerektiğini düşünüyorsan — oranlar, hareket hızı ya da başka herhangi bir şey — şu adrese yaz:"},
 # --- local install: the game is on this machine, there is nothing to fetch ---
 "admin_hint_local":{"en":"This server only listens to this computer, so there is no passphrase to type.",
                  "de":"Dieser Server lauscht nur auf diesem Computer, es gibt also keine Passphrase einzugeben.",
                  "tr":"Bu sunucu yalnızca bu bilgisayarı dinliyor, bu yüzden girilecek bir parola yok."},
 "admin_open":   {"en":"🛠️ Manage the server","de":"🛠️ Server verwalten","tr":"🛠️ Sunucuyu yönet"},
 "back_front":   {"en":"← Front page","de":"← Startseite","tr":"← Ana sayfa"},
 "dl_local_t":   {"en":"The game is on this computer","de":"Das Spiel liegt auf diesem Computer","tr":"Oyun bu bilgisayarda"},
 "dl_local":     {"en":"Nothing to download: this server runs on the machine you are sitting at, so the game was unpacked here directly. Look for <b>Metin2 Singleplayer</b> on your Desktop and start it from there.",
                  "de":"Nichts herunterzuladen: Dieser Server läuft auf dem Rechner, an dem du sitzt, das Spiel wurde also gleich hier ausgepackt. Auf dem Desktop findest du <b>Metin2 Singleplayer</b> — von dort startest du es.",
                  "tr":"İndirilecek bir şey yok: bu sunucu şu an başında oturduğun makinede çalışıyor, oyun da doğrudan buraya açıldı. Masaüstünde <b>Metin2 Singleplayer</b> kısayolunu bul ve oradan başlat."},
 "dl_local_w":   {"en":"Still unpacking. It is well over a gigabyte, so give it a few minutes — the shortcut appears on the Desktop when it is done.",
                  "de":"Wird noch ausgepackt. Es ist deutlich über ein Gigabyte, gib ihm also ein paar Minuten — die Verknüpfung erscheint auf dem Desktop, sobald es fertig ist.",
                  "tr":"Hâlâ açılıyor. Bir gigabayttan epey büyük, birkaç dakika ver — bitince kısayol masaüstünde belirir."},
 # --- the operator's orientation, shown once they are logged in ---
 # This is the first thing the person who installed the server sees. It exists
 # because the dashboard used to open straight onto three cards with no
 # explanation of what had just been built or what to do with it.
 "op_title":     {"en":"Your server is running","de":"Dein Server läuft","tr":"Sunucun çalışıyor"},
 "op_intro":     {"en":"Everything is up: the game, the database and this panel. The badge at the top says whether the game itself is accepting connections — if it ever reads offline while you are sure it should not, that is the first place to look.",
                  "de":"Alles läuft: das Spiel, die Datenbank und dieses Panel. Die Anzeige oben sagt dir, ob das Spiel selbst Verbindungen annimmt — falls dort einmal „offline“ steht, obwohl du sicher bist, dass es nicht so sein sollte, schau zuerst dort nach.",
                  "tr":"Her şey ayakta: oyun, veritabanı ve bu panel. Üstteki rozet oyunun bağlantı kabul edip etmediğini gösterir — bir gün „çevrimdışı“ yazıyorsa ve bundan emin değilsen, önce oraya bak."},
 "op_share":     {"en":"To let someone play, give them the address of this page. They register an account here and download the game from the same page — it already points at your server, so there is nothing for them to configure.",
                  "de":"Damit jemand spielen kann, gib ihm die Adresse dieser Seite. Er registriert sich hier und lädt das Spiel von derselben Seite — es zeigt bereits auf deinen Server, es gibt für ihn nichts einzustellen.",
                  "tr":"Birinin oynaması için ona bu sayfanın adresini ver. Hesabını burada açar ve oyunu aynı sayfadan indirir — oyun zaten senin sunucunu gösteriyor, ayarlaması gereken bir şey yok."},
 # Shown instead of op_share when the installer reported a loopback-only setup.
 "op_local_t":   {"en":"This server is for you alone","de":"Dieser Server ist nur für dich","tr":"Bu sunucu yalnızca sana ait"},
 "op_local":     {"en":"This was installed as a local server, so everything listens on this computer only. Nobody else can join — not over the internet, and not from another device on the same network. No port was opened and no firewall rule was created. Register an account here, download the game from this page, and play.",
                  "de":"Das hier wurde als lokaler Server installiert, es lauscht also alles ausschließlich auf diesem Computer. Niemand sonst kann mitspielen — weder über das Internet noch von einem anderen Gerät im selben Netzwerk. Es wurde kein Port geöffnet und keine Firewallregel angelegt. Registriere hier ein Konto, lade das Spiel von dieser Seite und spiel los.",
                  "tr":"Bu, yerel bir sunucu olarak kuruldu; yani her şey yalnızca bu bilgisayarda dinliyor. Başka kimse katılamaz — ne internet üzerinden ne de aynı ağdaki başka bir cihazdan. Hiçbir port açılmadı ve hiçbir güvenlik duvarı kuralı oluşturulmadı. Buradan bir hesap aç, oyunu bu sayfadan indir ve oyna."},
 "op_local_hint":{"en":"If you later want friends to play, do not open ports on your home router: that hands your home address to every player, your upload speed becomes the bottleneck, and the server is gone whenever this computer is. Rent a small Linux server instead and run the installer there — the project's documentation has the one command for it.",
                  "de":"Wenn später Freunde mitspielen sollen, öffne keine Ports an deinem Heimrouter: Das gibt deine Heimadresse an jeden Spieler weiter, dein Upload wird zum Flaschenhals, und der Server ist weg, sobald dieser Rechner aus ist. Miete stattdessen einen kleinen Linux-Server und führe den Installer dort aus — der eine Befehl dafür steht in der Dokumentation des Projekts.",
                  "tr":"İleride arkadaşlarının da oynamasını istersen, ev yönlendiricinde port açma: bu, ev adresini her oyuncuya verir, yükleme hızın darboğaz olur ve bu bilgisayar kapandığında sunucu da gider. Bunun yerine küçük bir Linux sunucu kirala ve kurulumu orada çalıştır — bunun tek komutu projenin belgelerinde."},
 "op_secure":    {"en":"The server files ship with two accounts, admin and test, sharing one password that is printed in the package's own readme. They were deleted when this server was set up, along with the game-master entry that went with them — because leaving that entry behind would have let the next person to register those names inherit the rights. So there is no game master yet: to make yourself one, add your own account and character to the gmlist table and restart the game.",
                  "de":"Die Serverdateien bringen zwei Konten mit, admin und test, mit einem gemeinsamen Passwort, das in der Readme des Pakets abgedruckt ist. Beide wurden beim Einrichten dieses Servers gelöscht, zusammen mit dem zugehörigen Spielleiter-Eintrag — denn hätte man den stehen lassen, hätte der Nächste, der diese Namen registriert, die Rechte geerbt. Es gibt daher noch keinen Spielleiter: Trag dich mit deinem eigenen Konto und Charakter in die gmlist-Tabelle ein und starte das Spiel neu.",
                  "tr":"Sunucu dosyaları admin ve test adında iki hesapla gelir; ikisi de paketin kendi readme dosyasında yazan tek bir şifreyi paylaşır. Bu sunucu kurulurken ikisi de, kendilerine ait oyun yöneticisi kaydıyla birlikte silindi — çünkü o kayıt kalsaydı, bu adları sonradan kaydeden kişi yetkileri devralırdı. Dolayısıyla henüz bir oyun yöneticisi yok: kendini yönetici yapmak için kendi hesabını ve karakterini gmlist tablosuna ekle ve oyunu yeniden başlat."},
 "op_rates":     {"en":"Rates decide how fast the whole server plays: experience, item drops and yang. 100% is the game exactly as it shipped. Saving restarts the game for well under a minute, so players are briefly disconnected.",
                  "de":"Die Raten bestimmen, wie schnell sich der ganze Server spielt: Erfahrung, Item-Drops und Yang. 100 % ist das Spiel genau so, wie es ausgeliefert wurde. Beim Speichern startet das Spiel für deutlich unter einer Minute neu, Spieler fliegen also kurz raus.",
                  "tr":"Oranlar tüm sunucunun ne kadar hızlı oynandığını belirler: tecrübe, eşya düşüşü ve yang. %100, oyunun çıktığı hâlidir. Kaydettiğinde oyun bir dakikadan çok kısa süre yeniden başlar, oyuncular kısa süre düşer."},
 "op_players":   {"en":"Under Players you find every character with the account it belongs to. Open one to give items or yang, or to set a level. Those go straight into the database, so the player has to log out and back in before they see them.",
                  "de":"Unter „Spieler“ findest du jeden Charakter mit dem Konto, zu dem er gehört. Öffne einen, um Gegenstände oder Yang zu geben oder ein Level zu setzen. Das geht direkt in die Datenbank — der Spieler muss sich also aus- und wieder einloggen, bevor er es sieht.",
                  "tr":"„Oyuncular“ altında her karakteri, ait olduğu hesapla birlikte görürsün. Eşya ya da yang vermek veya seviye ayarlamak için birini aç. Bunlar doğrudan veritabanına yazılır, yani oyuncunun görmesi için çıkıp yeniden girmesi gerekir."},
 "op_limits":    {"en":"Teleport and Running speed are different: they act on a character who is online right now, through a helper script inside the game. A standard Docker install does not include that helper, so on one of those the two buttons will report that they got no answer — which is the panel being honest rather than pretending it worked.",
                  "de":"Teleportieren und Laufgeschwindigkeit sind anders: Sie wirken auf einen gerade eingeloggten Charakter, über ein Hilfsskript im Spiel. Eine normale Docker-Installation bringt dieses Skript nicht mit — dort melden die beiden Schaltflächen deshalb, dass sie keine Antwort bekommen haben. Das ist das Panel, das ehrlich bleibt, statt Erfolg vorzutäuschen.",
                  "tr":"Işınla ve Koşma hızı farklıdır: o anda çevrimiçi olan bir karaktere, oyunun içindeki bir yardımcı betik üzerinden etki ederler. Standart bir Docker kurulumu bu betiği içermez, dolayısıyla orada iki düğme de yanıt alamadığını bildirir — bu, panelin başarılı gibi davranmak yerine dürüst kalmasıdır."},
 "op_forgot":    {"en":"When a player forgets their password, you make them a reset link below. It works once and expires after a day — you never see or set their password yourself.",
                  "de":"Wenn ein Spieler sein Passwort vergisst, erzeugst du ihm unten einen Reset-Link. Er funktioniert einmal und verfällt nach einem Tag — du siehst oder setzt sein Passwort nie selbst.",
                  "tr":"Bir oyuncu şifresini unutursa, aşağıda ona bir sıfırlama bağlantısı oluşturursun. Bir kez çalışır ve bir gün sonra geçersiz olur — şifresini asla sen görmez ya da belirlemezsin."},
 "op_more":      {"en":"Everything else — backups, moving the server, rebuilding the client for a new address — is in the project's documentation.",
                  "de":"Alles Weitere — Sicherungen, Serverumzug, den Client für eine neue Adresse neu bauen — steht in der Dokumentation des Projekts.",
                  "tr":"Geri kalan her şey — yedekler, sunucu taşıma, yeni bir adres için istemciyi yeniden derleme — projenin belgelerinde."},
 "op_hide":      {"en":"Got it — hide this","de":"Verstanden — ausblenden","tr":"Anladım — gizle"},
 "op_show":      {"en":"Show the introduction again","de":"Einführung wieder anzeigen","tr":"Tanıtımı yeniden göster"},
 # --- admin-made password reset links ---
 "reset_title":  {"en":"Password reset link","de":"Passwort-Reset-Link","tr":"Şifre sıfırlama bağlantısı"},
 "reset_hint":   {"en":"A player who forgot their password writes to you (the address is on the front page). Type their username here, send them the link this creates, and they choose a new password themselves. Each link works once and expires after 24 hours; making a new one cancels the old.",
                  "de":"Ein Spieler, der sein Passwort vergessen hat, schreibt dir (die Adresse steht auf der Startseite). Trage hier seinen Benutzernamen ein, schicke ihm den erzeugten Link, und er wählt selbst ein neues Passwort. Jeder Link funktioniert einmal und verfällt nach 24 Stunden; ein neuer Link macht den alten ungültig.",
                  "tr":"Şifresini unutan oyuncu sana yazar (adres ana sayfada). Buraya kullanıcı adını yaz, oluşan bağlantıyı ona gönder, yeni şifresini kendisi seçer. Her bağlantı bir kez çalışır ve 24 saat sonra geçersiz olur; yenisi eskisini iptal eder."},
 "reset_user_ph":{"en":"Player's username","de":"Benutzername des Spielers","tr":"Oyuncunun kullanıcı adı"},
 "reset_make":   {"en":"🔗 Create reset link","de":"🔗 Reset-Link erstellen","tr":"🔗 Bağlantı oluştur"},
 "reset_noacc":  {"en":"There is no account with that username.","de":"Es gibt kein Konto mit diesem Benutzernamen.","tr":"Bu kullanıcı adıyla bir hesap yok."},
 "reset_made":   {"en":"Send this link to the player — it works once and expires in 24 hours:",
                  "de":"Schick diesen Link an den Spieler — er funktioniert einmal und verfällt in 24 Stunden:",
                  "tr":"Bu bağlantıyı oyuncuya gönder — bir kez çalışır ve 24 saat sonra geçersiz olur:"},
 "reset_set_title":{"en":"Set a new password","de":"Neues Passwort setzen","tr":"Yeni şifre belirle"},
 "reset_for":    {"en":"New password for account","de":"Neues Passwort für das Konto","tr":"Yeni şifre belirlenecek hesap:"},
 "reset_ph1":    {"en":"New password (at least 6 characters)","de":"Neues Passwort (mindestens 6 Zeichen)","tr":"Yeni şifre (en az 6 karakter)"},
 "reset_ph2":    {"en":"New password again","de":"Neues Passwort wiederholen","tr":"Yeni şifre (tekrar)"},
 "reset_set_btn":{"en":"🔒 Save new password","de":"🔒 Neues Passwort speichern","tr":"🔒 Yeni şifreyi kaydet"},
 "reset_bad_link":{"en":"This link is not valid any more — it was already used, has expired, or was replaced by a newer one. Ask the admin for a fresh link.",
                  "de":"Dieser Link ist nicht mehr gültig — er wurde schon benutzt, ist abgelaufen oder wurde durch einen neueren ersetzt. Bitte den Admin um einen frischen Link.",
                  "tr":"Bu bağlantı artık geçerli değil — zaten kullanıldı, süresi doldu ya da yenisiyle değiştirildi. Yöneticiden yeni bir bağlantı iste."},
 "reset_short":  {"en":"The new password must be at least 6 characters. 🙂","de":"Das neue Passwort muss mindestens 6 Zeichen haben. 🙂","tr":"Yeni şifre en az 6 karakter olmalı. 🙂"},
 "reset_mismatch":{"en":"The two passwords don't match — try again. 🙂","de":"Die beiden Passwörter stimmen nicht überein — versuch es noch einmal. 🙂","tr":"İki şifre birbirini tutmuyor — tekrar dene. 🙂"},
 "reset_done":   {"en":"Your password has been changed — you can log into the game with it right away. 🎉",
                  "de":"Dein Passwort wurde geändert — du kannst dich damit sofort im Spiel anmelden. 🎉",
                  "tr":"Şifren değiştirildi — hemen oyuna girebilirsin. 🎉"},
 "tip_reset":    {"en":"Creates a one-time link that lets this player set a new password themselves. Nothing changes on the account until the link is used.",
                  "de":"Erzeugt einen Einmal-Link, mit dem der Spieler selbst ein neues Passwort setzt. Am Konto ändert sich nichts, bis der Link benutzt wird.",
                  "tr":"Oyuncunun kendisinin yeni şifre belirlemesini sağlayan tek kullanımlık bir bağlantı oluşturur. Bağlantı kullanılana dek hesapta hiçbir şey değişmez."},
 "dl_limit_title":{"en":"Download limit reached","de":"Download-Limit erreicht","tr":"İndirme sınırına ulaşıldı"},
 # Shown when the whole server has hit its daily ceiling rather than the visitor
 # -- otherwise the reader concludes they did something wrong, and they didn't.
 "dl_limit_all": {"en":"The game has been downloaded the maximum number of times across the whole server today. This is not about you — somebody has to be the one who arrives after the last slot. It frees up again in about {h} h, and an interrupted download can always be resumed, which costs nothing.",
                  "de":"Das Spiel wurde heute serverweit schon so oft heruntergeladen, wie erlaubt ist. Das liegt nicht an dir — irgendwer muss der sein, der nach dem letzten freien Platz ankommt. In etwa {h} Std. wird wieder einer frei. Ein abgebrochener Download lässt sich jederzeit fortsetzen, das kostet nichts.",
                  "tr":"Oyun bugün sunucu genelinde izin verilen en yüksek sayıda indirildi. Bu senden kaynaklanmıyor — birinin son boş yerden sonra gelmesi gerekiyordu. Yaklaşık {h} saat içinde yeniden yer açılır. Yarım kalan bir indirme her zaman kaldığı yerden sürdürülebilir, bu sınırdan sayılmaz."},
 "dl_limit":     {"en":"The game was already downloaded 3 times from your address in the last 24 hours — that is the limit, so the server's bandwidth stays free for playing. Please try again in about {h} h. An interrupted download can always be resumed, that costs nothing.",
                  "de":"Das Spiel wurde von deiner Adresse in den letzten 24 Stunden schon 3-mal heruntergeladen — mehr geht nicht, damit die Bandbreite des Servers zum Spielen frei bleibt. Versuche es in etwa {h} Std. wieder. Ein abgebrochener Download lässt sich jederzeit fortsetzen, das kostet nichts.",
                  "tr":"Oyun son 24 saatte senin adresinden zaten 3 kez indirildi — sunucunun bant genişliği oyuna kalsın diye sınır bu. Yaklaşık {h} saat sonra tekrar dene. Yarım kalan bir indirme her zaman kaldığı yerden sürdürülebilir, bu sınırdan sayılmaz."},

 # --- mouseover explanations (title="..." on the elements themselves) ---
 "tip_lang":     {"en":"Switch the language of this panel. Nothing in the game changes.",
                  "de":"Ändert die Sprache dieses Panels. Im Spiel ändert sich nichts.",
                  "tr":"Bu panelin dilini değiştirir. Oyunda hiçbir şey değişmez."},
 "tip_logout":   {"en":"End your admin session on this panel. Your game account is not affected.",
                  "de":"Beendet deine Admin-Sitzung in diesem Panel. Dein Spiel-Konto ist davon nicht betroffen.",
                  "tr":"Bu paneldeki yönetici oturumunu kapatır. Oyun hesabın etkilenmez."},
 "tip_passphrase":{"en":"The admin passphrase chosen when the server was installed. Players do not need this — only the person running the server.",
                  "de":"Die Admin-Passphrase, die bei der Installation des Servers gewählt wurde. Spieler brauchen sie nicht — nur wer den Server betreibt.",
                  "tr":"Sunucu kurulurken seçilen yönetici parolası. Oyuncuların buna ihtiyacı yok — sadece sunucuyu işleten kişinin."},
 "tip_login":    {"en":"Opens the admin area. Only works with the admin passphrase, not with your game password.",
                  "de":"Öffnet den Admin-Bereich. Funktioniert nur mit der Admin-Passphrase, nicht mit deinem Spiel-Passwort.",
                  "tr":"Yönetici alanını açar. Sadece yönetici parolasıyla çalışır, oyun şifrenle değil."},
 "tip_download": {"en":"Downloads the complete game, around 1.2 GB. The server address is already filled in, so you do not have to configure anything — unpack it and start the game. At most 3 downloads per day; resuming an interrupted one is always free.",
                  "de":"Lädt das komplette Spiel herunter, etwa 1,2 GB. Die Serveradresse ist schon eingetragen, du musst nichts einstellen — entpacken und starten. Höchstens 3 Downloads pro Tag; einen abgebrochenen fortzusetzen ist immer frei.",
                  "tr":"Oyunun tamamını indirir, yaklaşık 1,2 GB. Sunucu adresi zaten girili, hiçbir ayar yapman gerekmiyor — çıkart ve başlat. Günde en fazla 3 indirme; yarım kalanı sürdürmek her zaman serbest."},
 "tip_create_acc":{"en":"Creates the account you log into the game with. It is separate from this panel and takes about half a minute.",
                  "de":"Erstellt das Konto, mit dem du dich im Spiel anmeldest. Es ist unabhängig von diesem Panel und dauert etwa eine halbe Minute.",
                  "tr":"Oyuna giriş yapacağın hesabı oluşturur. Bu panelden bağımsızdır ve yarım dakika sürer."},
 "tip_my_acc":   {"en":"Log into your game account here to change its password.",
                  "de":"Melde dich hier mit deinem Spiel-Konto an, um dessen Passwort zu ändern.",
                  "tr":"Oyun hesabının şifresini değiştirmek için burada giriş yap."},
 "tip_players":  {"en":"Every character that exists on this server. Characters show up after someone has logged in and created one.",
                  "de":"Alle Charaktere, die es auf diesem Server gibt. Sie erscheinen, sobald jemand sich eingeloggt und einen erstellt hat.",
                  "tr":"Bu sunucudaki tüm karakterler. Biri giriş yapıp karakter oluşturduktan sonra burada görünürler."},
 "tip_player":   {"en":"Open this character to give items or yang, set the level, teleport them or change their speed.",
                  "de":"Öffnet diesen Charakter, um Gegenstände oder Yang zu geben, das Level zu setzen, ihn zu teleportieren oder seine Geschwindigkeit zu ändern.",
                  "tr":"Bu karakteri açar: eşya veya yang verebilir, seviyesini ayarlayabilir, ışınlayabilir veya hızını değiştirebilirsin."},
 "tip_search_item":{"en":"Type part of a name, or an item number if you know it. Around 9,800 items are searchable.",
                  "de":"Tippe einen Teil des Namens oder die Item-Nummer, falls du sie kennst. Rund 9.800 Gegenstände sind durchsuchbar.",
                  "tr":"Adının bir kısmını yaz, ya da biliyorsan eşya numarasını. Yaklaşık 9.800 eşya aranabilir."},
 "tip_category": {"en":"Narrows the search to one kind of item, so you do not have to scroll past everything else.",
                  "de":"Schränkt die Suche auf eine Art von Gegenstand ein, damit du nicht an allem anderen vorbeiscrollen musst.",
                  "tr":"Aramayı tek bir eşya türüyle sınırlar, böylece diğer her şeyi kaydırmak zorunda kalmazsın."},
 "tip_qty":      {"en":"How many of this item to give. One stack holds at most 65,535.",
                  "de":"Wie viele von diesem Gegenstand gegeben werden. In einen Stapel passen höchstens 65.535.",
                  "tr":"Bu eşyadan kaç adet verilecek. Bir yığında en fazla 65.535 durur."},
 "tip_send_item":{"en":"Puts the item into the character's inventory. If they are offline it is waiting at their next login.",
                  "de":"Legt den Gegenstand in das Inventar des Charakters. Ist er offline, liegt er beim nächsten Login bereit.",
                  "tr":"Eşyayı karakterin çantasına koyar. Çevrimdışıysa bir sonraki girişinde onu bekliyor olur."},
 "tip_amount":   {"en":"How much yang to add. Enter a negative number to take yang away instead.",
                  "de":"Wie viel Yang hinzukommt. Gib eine negative Zahl ein, um stattdessen Yang abzuziehen.",
                  "tr":"Ne kadar yang ekleneceği. Yang almak için eksi bir sayı gir."},
 "tip_level":    {"en":"Sets the character straight to this level. It does not add levels, it replaces the current one.",
                  "de":"Setzt den Charakter direkt auf dieses Level. Es wird nicht dazugezählt, sondern ersetzt.",
                  "tr":"Karakteri doğrudan bu seviyeye ayarlar. Seviye eklemez, mevcut olanı değiştirir."},
 "tip_teleport": {"en":"Moves the character to another map. This only works while they are actually in game.",
                  "de":"Bewegt den Charakter auf eine andere Karte. Das geht nur, solange er wirklich im Spiel ist.",
                  "tr":"Karakteri başka bir haritaya taşır. Bu sadece gerçekten oyundayken çalışır."},
 "tip_speed":    {"en":"Changes how fast the character runs. 100 is normal; this only works while they are in game.",
                  "de":"Ändert, wie schnell der Charakter läuft. 100 ist normal; das geht nur, solange er im Spiel ist.",
                  "tr":"Karakterin ne kadar hızlı koştuğunu değiştirir. 100 normaldir; sadece oyundayken çalışır."},
 "tip_rates":    {"en":"Experience, item drops and yang for the whole server. Saving restarts the game for under a minute.",
                  "de":"Erfahrung, Item-Drops und Yang für den ganzen Server. Beim Speichern startet das Spiel für weniger als eine Minute neu.",
                  "tr":"Tüm sunucu için tecrübe, eşya düşme oranı ve yang. Kaydettiğinde oyun bir dakikadan kısa bir süre yeniden başlar."},
 "tip_delcode":  {"en":"Seven digits the game asks for when you delete a character. Pick something you will remember.",
                  "de":"Sieben Ziffern, nach denen das Spiel fragt, wenn du einen Charakter löschst. Wähle etwas, das du dir merkst.",
                  "tr":"Bir karakteri silerken oyunun soracağı yedi rakam. Hatırlayacağın bir şey seç."},
}
CATS = ["all","weapon","armor","usable","ds","metin","special","other"]

def lang():
    """Chosen language, or the browser's if none was chosen yet.

    First visit: the browser says what it prefers (Accept-Language) and a
    German visitor sees German without clicking anything. Clicking a language
    in the header stores it in the session and wins from then on.
    """
    chosen = session.get("lang")
    if chosen in LANGS:
        return chosen
    if has_request_context():
        best = request.accept_languages.best_match(list(LANGS))
        if best:
            return best
    return "en"

def t(key):
    return T.get(key, {}).get(lang(), T.get(key, {}).get("en", key))

app = Flask(__name__)
app.secret_key = CONF["flask_secret"]


class _LocalProxyFix:
    """Apply X-Forwarded-* headers, but only when nginx sent them.

    The panel is reachable two ways at once: through the HTTPS reverse proxy on
    the domain name, and directly on its own port over plain HTTP. So these
    headers can also arrive straight from a visitor, and a forged
    X-Forwarded-Host would put an attacker's domain into every link the panel
    generates. Only the proxy on the loopback address is believed.
    """

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if environ.get("REMOTE_ADDR") in ("127.0.0.1", "::1"):
            proto = environ.get("HTTP_X_FORWARDED_PROTO")
            host  = environ.get("HTTP_X_FORWARDED_HOST")
            fwd   = environ.get("HTTP_X_FORWARDED_FOR")
            if proto in ("http", "https"):
                environ["wsgi.url_scheme"] = proto
                # remembered so the download can be handed to nginx, which only
                # works for requests that really came through it
                environ["panel.via_proxy"] = True
            if host:
                environ["HTTP_HOST"] = host.split(",")[0].strip()
            if fwd:
                environ["REMOTE_ADDR"] = fwd.split(",")[0].strip()
        return self.app(environ, start_response)


app.wsgi_app = _LocalProxyFix(app.wsgi_app)


class _SchemeAwareSession(SecureCookieSessionInterface):
    """Mark the session cookie 'secure' on HTTPS requests only.

    A fixed SESSION_COOKIE_SECURE cannot work here: switched on it would break
    login over the plain-HTTP address, switched off it would let the cookie
    travel unencrypted on the HTTPS one. Deciding per request gives each address
    the strongest setting it can actually support.
    """

    def get_cookie_secure(self, app):
        return bool(has_request_context() and request.is_secure)


app.session_interface = _SchemeAwareSession()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

def csrf_token():
    """One random token per browser session, handed to every form."""
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf"] = tok
    return tok

@app.before_request
def csrf_protect():
    """Every POST must carry the token from the page it claims to come from."""
    if request.method != "POST":
        return
    sent = request.form.get("_csrf", "")
    real = session.get("_csrf", "")
    if not (real and sent and hmac.compare_digest(sent, real)):
        flash(t("csrf_bad"), "error")
        return redirect(url_for("login"))

@app.context_processor
def inject_i18n():
    _cf = client_facts()
    return {"t": t, "langs": LANGS, "curlang": lang(), "csrf_token": csrf_token(),
            "brand": BRAND, "srv": server_status(), "rates": public_rates(),
            "dlsize": human_size(_cf["size"]) if _cf["size"] else "",
            "dlsha": _cf["sha256"],
            "local_only": bool(CONF.get("local_only", False)),
            # Empty unless the operator set one. It used to be a hard-coded
            # address, which meant every server built from this project pointed
            # its players at one particular person's inbox.
            "contact": str(CONF.get("contact_email", "") or "").strip()}

@app.route("/lang/<code>")
def setlang(code):
    if code in LANGS:
        session["lang"] = code
    return redirect(request.referrer or url_for("login"))

MAX_FAIL, LOCK_SEC = 5, 900
FAILS = {}

JOB_EMOJI = {0:"⚔️",4:"⚔️",5:"🗡️",1:"🗡️",2:"🔮",6:"🔮",7:"🌀",3:"🌀",8:"🐺"}
JOB_NAME  = {0:"Warrior",4:"Warrior",5:"Ninja",1:"Ninja",2:"Sura",6:"Sura",7:"Shaman",3:"Shaman",8:"Lycan"}

GOLD_PRESETS = [("💰 1 Million", 1_000_000), ("💰 10 Million", 10_000_000),
                ("💰 100 Million", 100_000_000), ("👑 1 Billion", 1_000_000_000)]
WARP_LOC = [  # (emoji, {lang:name}, coords)
  ("🏯", {"en":"Shinsoo City","de":"Shinsoo-Stadt","tr":"Shinsoo Şehri"}, "474300 954800"),
  ("🏮", {"en":"Chunjo City","de":"Chunjo-Stadt","tr":"Chunjo Şehri"}, "65900 155600"),
  ("⛩️", {"en":"Jinno City","de":"Jinno-Stadt","tr":"Jinno Şehri"}, "963500 279700"),
  ("🏜️", {"en":"Desert","de":"Wüste","tr":"Çöl"}, "2178000 632900"),
  ("🔥", {"en":"Fireland","de":"Feuerland","tr":"Ateş Ülkesi"}, "1932800 2402700"),
]
SPEED_LOC = [
  ("🚶", {"en":"Normal (reset)","de":"Normal (zurücksetzen)","tr":"Normal (sıfırla)"}, 0),
  ("🏃", {"en":"Fast (+30%)","de":"Schnell (+30%)","tr":"Hızlı (+30%)"}, 30),
  ("💨", {"en":"Very Fast (+60%)","de":"Sehr schnell (+60%)","tr":"Çok Hızlı (+60%)"}, 60),
  ("⚡", {"en":"Light Speed (+100%)","de":"Lichtgeschwindigkeit (+100%)","tr":"Işık Hızı (+100%)"}, 100),
]
# Ready-made rate settings, aimed at a quiet server where questing is the point.
# The middle one is the setting most people asking for this actually want: enough
# of a push that a quest chain carries you along, without turning the game off.
RATE_PRESETS = [  # (label key, experience, item drop, yang)
  ("rates_p_normal",   100, 100, 100),
  ("rates_p_relaxed",  300, 200, 200),
  ("rates_p_fast",    1000, 500, 500),
]

def clean_rate(raw):
    """A whole percentage between 1 and 10000, or None when it is not one."""
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return v if RATE_MIN <= v <= RATE_MAX else None

def read_rates():
    """The three percentages as they stand in the database."""
    vals = {n: 100 for n in RATE_NAMES}
    with db() as c, c.cursor() as cur:
        cur.execute("SELECT name,value FROM player.web_admin_rates")
        for row in cur.fetchall():
            if row["name"] in vals:
                try:
                    vals[row["name"]] = int(row["value"])
                except (TypeError, ValueError):
                    pass
    return vals

def rates_status():
    """The 'key=value' note apply_rates.sh leaves behind. Empty when there is none."""
    out = {}
    try:
        with open(RATES_STATUS, encoding="utf-8", errors="replace") as f:
            for line in f:
                k, sep, v = line.partition("=")
                if sep:
                    out[k.strip()] = v.strip()
    except OSError:
        pass
    return out

def write_rates_status(state):
    """Say 'it is running' right away, so reloading straight after saving is honest."""
    try:
        with open(RATES_STATUS, "w", encoding="utf-8") as f:
            f.write("state=%s\ntime=%d\n" % (state, int(time.time())))
        os.chmod(RATES_STATUS, 0o600)
    except OSError:
        pass

def gold_presets_i18n():
    return GOLD_PRESETS
def warp_presets_i18n():
    lg = lang()
    return [("%s %s" % (e, n.get(lg, n["en"])), xy) for e, n, xy in WARP_LOC]
def speed_presets_i18n():
    lg = lang()
    return [("%s %s" % (e, n.get(lg, n["en"])), spd) for e, n, spd in SPEED_LOC]

def db():
    return pymysql.connect(host=CONF.get("db_host", "127.0.0.1"), user=CONF["db_user"],
                           password=CONF["db_pass"], charset="latin1",
                           autocommit=True, cursorclass=pymysql.cursors.DictCursor)

def check_pass(p):
    h = hashlib.pbkdf2_hmac("sha256", p.encode(), CONF["salt"].encode(), 200_000)
    return hmac.compare_digest(h.hex(), CONF["pass_hash"])

def m2_hash(pw):
    """MySQL PASSWORD() style hash used by Metin2 auth: * + SHA1(SHA1(pw))"""
    return "*" + hashlib.sha1(hashlib.sha1(pw.encode()).digest()).hexdigest().upper()

def rate_limited(bucket, limit, window):
    """Very simple per-IP rate limit. Returns True if the IP should be blocked."""
    ip = request.remote_addr
    now = time.time()
    key = (bucket, ip)
    hits = [ts for ts in RATE.get(key, []) if now - ts < window]
    if len(hits) >= limit:
        RATE[key] = hits
        return True
    hits.append(now)
    RATE[key] = hits
    return False

RATE = {}

@app.route("/api/items")
def api_items():
    """Live item search for the give-item box. Returns up to 40 matches."""
    if not session.get("auth"):
        return jsonify([])
    q = request.args.get("q", "").strip().lower()
    cat = request.args.get("cat", "all")
    out = []
    for it in ITEMS:
        if cat != "all" and it["c"] != cat:
            continue
        if q and q not in it["n"].lower() and q not in it.get("k", "") and q != str(it["v"]):
            continue
        out.append(it)
        if len(out) >= 40:
            break
    return jsonify(out)

@app.route("/api/status")
def api_status():
    """Public: the front-page badge refreshes itself from this. Server-side
    cache (30 s) makes polling harmless."""
    s = server_status()
    return jsonify({"up": s["up"], "count": s["count"],
                    "online": t("srv_online"), "playing": t("srv_playing"),
                    "offline": t("srv_offline")})

@app.route("/api/checkname")
def api_checkname():
    """Public: live 'is this username free?' for the registration form.
    Registration itself reveals taken names anyway, so this leaks nothing new."""
    if rate_limited("checkname", 30, 60):
        return jsonify({"ok": False})
    lg = request.args.get("u", "").strip()
    if not (4 <= len(lg) <= 16 and lg.isalnum()):
        return jsonify({"ok": False})
    try:
        with db() as c, c.cursor() as cur:
            cur.execute("SELECT 1 FROM account.account WHERE login=%s", (lg,))
            return jsonify({"ok": True, "free": cur.fetchone() is None})
    except Exception:
        return jsonify({"ok": False})

FAVICON = _env_path("M2PANEL_FAVICON", os.path.join(_HERE, "favicon.png"))

@app.route("/favicon.ico")
def favicon():
    """The icon out of Metin2Release.exe, extracted once at packaging time."""
    if not os.path.exists(FAVICON):
        return ("", 404)
    resp = send_file(FAVICON, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp

def local_open():
    """True when the passphrase is pointless and therefore skipped.

    A local install listens on 127.0.0.1 and nothing else: the only people who
    can reach this page are already sitting at the machine. Asking them to
    invent, store and re-type a passphrase to administer their own single-player
    server is friction with nothing on the other side of it.

    What it does give up, said plainly: any program running on that computer can
    then drive the panel. On a home PC that is the same trust you already extend
    to everything else you run there. On anything reachable by other people it
    would be indefensible -- which is why this follows the installer's own
    local_only flag rather than guessing from the bind address, where a public
    server behind nginx also looks like 127.0.0.1.
    """
    return bool(CONF.get("local_only", False))

def login_required(fn):
    @wraps(fn)
    def w(*a, **k):
        if not session.get("auth") and not local_open():
            return redirect(url_for("login"))
        return fn(*a, **k)
    return w

BASE = """
<!doctype html><html lang="{{curlang}}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{brand}}</title>
<link rel="icon" type="image/png" href="/favicon.ico">
<style>
:root{--gold:#e9b64b;--gold2:#f7d98c;--bg:#0e0c09;--card:#181410;--card2:#1f1a14;--line:#332b1d;
--txt:#f0eadd;--muted:#a89d84;--green:#57c15f;--red:#e05b5b;--glow:rgba(233,182,75,.16)}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;margin:0;padding:0 0 48px;color:var(--txt);
background:radial-gradient(1100px 520px at 50% -160px,#2b2210 0%,var(--bg) 62%) fixed var(--bg)}
.top{position:sticky;top:0;z-index:20;display:flex;justify-content:space-between;align-items:center;gap:10px;
padding:14px 20px;background:rgba(14,12,9,.86);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
border-bottom:1px solid var(--line)}
.top::after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:1px;
background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:.55}
.top h1{margin:0;font-size:19px;letter-spacing:.3px;display:flex;align-items:center;gap:9px;
background:linear-gradient(92deg,var(--gold),var(--gold2) 60%,var(--gold));
-webkit-background-clip:text;background-clip:text;color:transparent}
.top h1 img{width:24px;height:24px;border-radius:5px;flex:none;
box-shadow:0 0 8px rgba(233,182,75,.35);image-rendering:auto}
.wrap{max-width:780px;margin:0 auto;padding:18px 16px}
a{color:var(--gold);text-decoration:none;transition:color .15s}
a:hover{color:var(--gold2)}
.card{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:16px;
padding:18px;margin-bottom:16px;box-shadow:0 10px 28px rgba(0,0,0,.4);
animation:rise .5s cubic-bezier(.22,.7,.35,1) both;transition:border-color .25s}
.wrap>.card:nth-of-type(2){animation-delay:.06s}
.wrap>.card:nth-of-type(3){animation-delay:.12s}
.wrap>.card:nth-of-type(4){animation-delay:.18s}
.wrap>.card:nth-of-type(5){animation-delay:.24s}
.card:hover{border-color:#4a3d24}
.card h3{margin:0 0 10px;font-size:17px}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
table{border-collapse:collapse;width:100%}
th{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.5px}
th,td{padding:10px 8px;border-bottom:1px solid var(--line);text-align:left;font-size:15px}
tr{transition:background .15s}
tr:hover td{background:var(--glow)}
input,select{background:#131007;color:var(--txt);border:1px solid var(--line);padding:12px;border-radius:12px;
font-size:16px;width:100%;margin:4px 0;transition:border-color .18s,box-shadow .18s}
input:focus,select:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 3px var(--glow)}
input::placeholder{color:#6f6650}
button,.btn{background:linear-gradient(180deg,var(--gold2),var(--gold));color:#241c0d;font-weight:700;border:none;
padding:12px 18px;border-radius:12px;font-size:16px;cursor:pointer;margin:4px 0;display:inline-block;
transition:transform .16s,box-shadow .16s,filter .16s;box-shadow:0 4px 14px rgba(0,0,0,.35)}
button:hover,.btn:hover{transform:translateY(-1px);box-shadow:0 8px 22px var(--glow),0 4px 14px rgba(0,0,0,.35);filter:saturate(1.08)}
button:active,.btn:active{transform:translateY(0) scale(.985)}
.big{width:100%;padding:16px;font-size:17px}
.flash{background:#1c3d20;border:1px solid #3d7a42;padding:12px 14px;border-radius:12px;margin-bottom:12px;
font-size:15px;animation:rise .35s ease both;word-break:break-word}
.err{background:#47201f;border-color:#8a3f3a}
.muted{color:var(--muted);font-size:13px}
.badge{font-size:13px;padding:4px 12px;border-radius:999px;background:#241d12;border:1px solid var(--line);display:inline-block}
.row{display:flex;gap:8px;flex-wrap:wrap}.row>*{flex:1;min-width:130px}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;vertical-align:1px;margin-right:7px;background:var(--red)}
.dot.on{background:var(--green);animation:pulse 2.2s ease-out infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(87,193,95,.45)}70%{box-shadow:0 0 0 9px rgba(87,193,95,0)}100%{box-shadow:0 0 0 0 rgba(87,193,95,0)}}
.steps{margin:10px 0 4px;padding:0;list-style:none;counter-reset:s;text-align:left}
.steps li{counter-increment:s;margin:9px 0;padding-left:36px;position:relative;font-size:14px;color:#d8d0bd}
.steps li::before{content:counter(s);position:absolute;left:0;top:-2px;width:24px;height:24px;border-radius:50%;
background:var(--glow);border:1px solid var(--gold);color:var(--gold);font-weight:700;font-size:13px;
display:flex;align-items:center;justify-content:center}
.hintline{font-size:13px;min-height:18px;margin:2px 0 6px}
.ok-t{color:var(--green)}.bad-t{color:var(--red)}
/* Anything carrying an explanation says so quietly: the cursor changes, and
   text you can hover gets a faint dotted underline. No popups, no scripting -
   the browser's own tooltip does the work. */
[title]{cursor:help}
button[title],.btn[title],a.btn[title],select[title]{cursor:pointer}
input[title]{cursor:text}
.help{border-bottom:1px dotted #6b6350}
.about p{margin:0 0 10px;line-height:1.6;font-size:14px;color:#cdc5b0}
.about p:last-child{margin-bottom:0}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
</style></head><body>
<div class="top"><h1 title="{{t('about_goal')}}"><img src="/favicon.ico" alt="">{{brand}}</h1>
<div>
<span style="font-size:13px" title="{{t('tip_lang')}}">
{% for code, name in langs.items() %}<a href="{{url_for('setlang', code=code)}}" title="{{name}}" style="margin:0 3px;{{'font-weight:700;text-decoration:underline' if code==curlang else 'opacity:.7'}}">{{code|upper}}</a>{% endfor %}
</span>
{% if session.get('auth') %}&nbsp;<a href="{{url_for('logout')}}" title="{{t('tip_logout')}}">{{t('logout')}} 🚪</a>{% endif %}
{# A local install never logs in, so it never gets a logout link either -- and
   without one there was no way back to the front page from the admin side. #}
{% if local_only and request.endpoint != 'login' %}&nbsp;<a href="{{url_for('login')}}">{{t('back_front')}}</a>{% endif %}</div></div>
<div class="wrap">
{% with m = get_flashed_messages(with_categories=true) %}{% for c,msg in m %}
<div class="flash {{'err' if c=='error' else ''}}">{{msg}}</div>{% endfor %}{% endwith %}
__BODY__
</div></body></html>"""

TPL_LOGIN = BASE.replace("__BODY__", """
<div style="max-width:560px;margin:18px auto 0;text-align:center">
<span class="badge help" id="srvbadge" title="{{t('tip_srv')}}" style="font-size:14px;padding:7px 15px">
{% if srv.up %}<span class="dot on"></span>{{t('srv_online')}} — <b>{{srv.count}}</b> {{t('srv_playing')}}
{% else %}<span class="dot"></span>{{t('srv_offline')}}{% endif %}</span>
{% if rates %}
<div style="margin-top:9px">
<span class="badge help" title="{{t('tip_rates')}}">⭐ {{t('rates_exp')}} {{rates['exp']}}%</span>
<span class="badge help" title="{{t('tip_rates')}}">🎁 {{t('rates_drop')}} {{rates['drop']}}%</span>
<span class="badge help" title="{{t('tip_rates')}}">💰 {{t('rates_yang')}} {{rates['yang']}}%</span>
</div>
{% endif %}
</div>
<script>
setInterval(function(){
 fetch('/api/status').then(function(r){return r.json();}).then(function(s){
  var b=document.getElementById('srvbadge'); if(!b)return;
  b.innerHTML = s.up ? '<span class="dot on"></span>'+s.online+' — <b>'+s.count+'</b> '+s.playing
                     : '<span class="dot"></span>'+s.offline;
 }).catch(function(){});
},60000);
</script>
<div class="card about" style="max-width:560px;margin:24px auto">
<h3>ℹ️ {{t('about_title')}}</h3>
<p>{{t('about_goal')}}</p>
<p>{{t('about_hobby')}}</p>
<p>{{t('about_simple')}}</p>
<p>{{t('about_uptime')}}</p>
<p>{{t('about_oss')}}</p>
{% if contact %}<p>{{t('about_contact')}} <a href="mailto:{{contact}}">{{contact}}</a>.</p>{% endif %}
</div>
{% if local_only %}
{# A local server plays on the machine it runs on, so there is nothing to
   fetch over the network. Point at the Desktop shortcut instead of at a
   download button that would only copy a file to where it already is. #}
<div class="card" style="max-width:420px;margin:0 auto 16px;text-align:center">
<div style="font-size:40px">🎮</div>
<h3>{{t('dl_local_t')}}</h3>
<p class="muted">{% if client_ready %}{{t('dl_local')|safe}}{% else %}{{t('dl_local_w')}}{% endif %}</p>
</div>
{% elif client_ready or client_url %}
<div class="card" style="max-width:420px;margin:0 auto 16px;text-align:center">
<div style="font-size:40px">🎮</div>
<h3>{{t('player_q')}}</h3>
<p class="muted">{{t('dl_hint')}}</p>
<a class="btn big" href="{{ client_url if client_url else url_for('download') }}"
   title="{{t('tip_download')}}"
   {% if client_url %}rel="noopener noreferrer"{% endif %}>{% if client_name %}📥 {{client_name}}{% else %}{{t('download')}}{% endif %}{% if dlsize %} <span style="font-weight:400;font-size:13px">({{dlsize}})</span>{% endif %}</a>
<ol class="steps">
<li>{{t('dl_st1')}}{% if dlsize %} ({{dlsize}}){% endif %}</li>
<li>{{t('dl_st2')}}</li>
<li>{{t('dl_st3')}}</li>
</ol>
{% if dlsha and not client_url %}<p class="muted help" title="{{t('dl_sha')}}" style="font-size:11px;word-break:break-all">SHA-256: {{dlsha}}</p>{% endif %}
</div>
{% endif %}
<div class="card" style="max-width:380px;margin:0 auto 16px;text-align:center">
<div style="font-size:40px">🧑‍🤝‍🧑</div>
<h3>{{t('game_account')}}</h3>
<div class="row">
<a class="btn" href="{{url_for('register')}}" title="{{t('tip_create_acc')}}">{{t('create_acc')}}</a>
<a class="btn" href="{{url_for('account')}}" title="{{t('tip_my_acc')}}">{{t('my_acc')}}</a>
</div></div>
<div class="card" style="max-width:380px;margin:0 auto;text-align:center">
<div style="font-size:40px">{% if local_only %}🛠️{% else %}🔑{% endif %}</div>
<h3>{{t('welcome')}}</h3>
{% if local_only %}
<p class="muted">{{t('admin_hint_local')}}</p>
<a class="btn big" href="/admin">{{t('admin_open')}}</a>
{% else %}
<p class="muted">{{t('admin_hint')}}</p>
<form method="post"><input type="hidden" name="_csrf" value="{{csrf_token}}">
<input type="password" name="pw" placeholder="{{t('passphrase')}}" title="{{t('tip_passphrase')}}">
<button class="big" title="{{t('tip_login')}}">{{t('login')}}</button></form>
{% endif %}
</div>""")

TPL_DL_LIMIT = BASE.replace("__BODY__", """
<div class="card" style="max-width:420px;margin:40px auto;text-align:center">
<div style="font-size:48px">⏳</div>
<h3>{{t('dl_limit_title')}}</h3>
<p class="muted" style="font-size:15px">{{ t('dl_limit_all' if scope == 'all' else 'dl_limit').replace('{h}', wait_h|string) }}</p>
<p><a href="{{url_for('login')}}">← Back</a></p></div>""")

TPL_RESET = BASE.replace("__BODY__", """
<div class="card" style="max-width:420px;margin:40px auto;text-align:center">
<div style="font-size:48px">🔑</div>
<h3>{{t('reset_set_title')}}</h3>
{% if valid %}
<p class="muted">{{t('reset_for')}} <b>{{login}}</b></p>
<form method="post"><input type="hidden" name="_csrf" value="{{csrf_token}}">
<input type="password" name="new" placeholder="{{t('reset_ph1')}}" autofocus>
<input type="password" name="new2" placeholder="{{t('reset_ph2')}}">
<button class="big">{{t('reset_set_btn')}}</button></form>
{% else %}
<p class="muted">{{t('reset_bad_link')}}</p>
<p><a href="{{url_for('login')}}">← Back</a></p>
{% endif %}
</div>""")

TPL_REGISTER = BASE.replace("__BODY__", """
<p><a href="{{url_for('login')}}">← Back</a></p>
<div class="card" style="max-width:440px;margin:20px auto">
<h3>📝 {{t('reg_title')}}</h3>
<p class="muted">{{t('reg_hint')}}</p>
<form method="post">
<input type="hidden" name="_csrf" value="{{csrf_token}}">
<input name="login" id="regName" placeholder="{{t('reg_ph_user')}}" value="{{form.login}}" title="{{t('tip_create_acc')}}" autocomplete="username" autofocus>
<div class="hintline" id="nameHint"></div>
<input type="password" name="pw" id="regPw" placeholder="{{t('reg_ph_pw')}}" autocomplete="new-password">
<input type="password" name="pw2" id="regPw2" placeholder="{{t('reg_ph_pw2')}}" autocomplete="new-password">
<div class="hintline" id="pwHint"></div>
<input name="social" placeholder="{{t('reg_ph_social')}}" value="{{form.social}}" title="{{t('tip_delcode')}}" inputmode="numeric">
<p class="muted">{{t('reg_social_hint')}}</p>
<button class="big">{{t('create_acc')}}</button>
</form></div>
<script>
(function(){
 var n=document.getElementById('regName'),nh=document.getElementById('nameHint'),
     p1=document.getElementById('regPw'),p2=document.getElementById('regPw2'),
     ph=document.getElementById('pwHint'),tmr=null;
 var TXT={free:{{t('reg_free')|tojson}},taken:{{t('reg_taken')|tojson}},
          match:{{t('reg_pw_match')|tojson}},diff:{{t('reg_pw_diff')|tojson}}};
 n.addEventListener('input',function(){
   clearTimeout(tmr); nh.textContent=''; nh.className='hintline';
   var v=n.value.trim();
   if(!/^[A-Za-z0-9]{4,16}$/.test(v)) return;
   tmr=setTimeout(function(){
     fetch('/api/checkname?u='+encodeURIComponent(v)).then(function(r){return r.json();}).then(function(d){
       if(!d.ok || n.value.trim()!==v) return;
       nh.textContent = d.free?TXT.free:TXT.taken;
       nh.className = 'hintline '+(d.free?'ok-t':'bad-t');
     }).catch(function(){});
   },350);
 });
 function pwc(){
   if(!p2.value){ph.textContent='';ph.className='hintline';return;}
   var same = p1.value===p2.value;
   ph.textContent = same?TXT.match:TXT.diff;
   ph.className = 'hintline '+(same?'ok-t':'bad-t');
 }
 p1.addEventListener('input',pwc); p2.addEventListener('input',pwc);
})();
</script>""")

TPL_REG_DONE = BASE.replace("__BODY__", """
<div class="card" style="max-width:440px;margin:40px auto;text-align:center">
<div style="font-size:48px">🎉</div>
<h3>{{t('reg_done_title')}}</h3>
<p class="muted">{{t('reg_done_next')}}</p>
<ol class="steps">
<li>{{t('dl_st1')}}{% if dlsize %} ({{dlsize}}){% endif %}</li>
<li>{{t('dl_st2')}}</li>
<li>{{t('dl_st3')}}</li>
<li>{{t('reg_done_login')}}</li>
</ol>
{% if local_only %}
<p class="muted">{{t('dl_local')|safe}}</p>
{% elif client_ready or client_url %}
<a class="btn big" href="{{ client_url if client_url else url_for('download') }}"
   title="{{t('tip_download')}}" {% if client_url %}rel="noopener noreferrer"{% endif %}>{{t('download')}}</a>
{% endif %}
<p style="margin-top:10px"><a href="{{url_for('login')}}">← {{brand}}</a></p></div>""")

TPL_ACCOUNT_LOGIN = BASE.replace("__BODY__", """
<p><a href="{{url_for('login')}}">← Back</a></p>
<div class="card" style="max-width:420px;margin:20px auto;text-align:center">
<div style="font-size:40px">👤</div>
<h3>My account</h3>
<p class="muted">Log in with your game username and password.</p>
<form method="post">
<input type="hidden" name="_csrf" value="{{csrf_token}}">
<input name="login" placeholder="Username" autofocus>
<input type="password" name="pw" placeholder="Password">
<button class="big">Log in</button>
</form></div>""")

TPL_ACCOUNT = BASE.replace("__BODY__", """
<p><a href="{{url_for('login')}}">← Home</a> &nbsp;|&nbsp; <a href="{{url_for('account_logout')}}">Log out of account</a></p>
<div class="card">
<h3>👤 {{login}}</h3>
<p class="muted">Your characters:</p>
<table>
<tr><th>Character</th><th>Level</th><th>Yang</th></tr>
{% for ch in chars %}
<tr><td>{{emoji(ch.job)}} <b>{{ch.name}}</b></td><td>{{ch.level}}</td><td>{{"{:,}".format(ch.gold)}}</td></tr>
{% endfor %}
</table>
{% if not chars %}<p>No characters yet — log into the game and create one! 🙂</p>{% endif %}
</div>
<div class="card"><h3>🔒 Change password</h3>
<form method="post" action="{{url_for('account_password')}}">
<input type="hidden" name="_csrf" value="{{csrf_token}}">
<input type="password" name="old" placeholder="Current password">
<input type="password" name="new" placeholder="New password (at least 6 characters)">
<input type="password" name="new2" placeholder="New password again">
<button class="big">🔒 Change password</button>
</form></div>""")

TPL_DASH = BASE.replace("__BODY__", """
<a href="#" id="introshow" style="display:none;font-size:13px" class="muted">{{t('op_show')}}</a>
<div class="card about" id="intro">
<h3>✅ {{t('op_title')}}</h3>
<p>{{t('op_intro')}}</p>
{% if local_only %}
<p><b>🔒 {{t('op_local_t')}}.</b> {{t('op_local')}}</p>
<p>{{t('op_local_hint')}}</p>
{% else %}
<p>{{t('op_share')}}</p>
<p><b>{{t('op_secure')}}</b></p>
{% endif %}
<p>{{t('op_rates')}}</p>
<p>{{t('op_players')}}</p>
<p>{{t('op_limits')}}</p>
<p>{{t('op_forgot')}}</p>
<p class="muted">{{t('op_more')}}</p>
<button class="btn" id="introhide" type="button">{{t('op_hide')}}</button>
</div>
<script>
(function(){
 var box=document.getElementById('intro'), hide=document.getElementById('introhide'),
     show=document.getElementById('introshow'), KEY='m2_intro_hidden';
 if(!box||!hide||!show) return;
 function apply(h){ box.style.display = h ? 'none' : ''; show.style.display = h ? '' : 'none'; }
 var stored=false;
 try { stored = localStorage.getItem(KEY)==='1'; } catch(e){}   // private mode: just show it
 apply(stored);
 hide.addEventListener('click',function(){ try{localStorage.setItem(KEY,'1');}catch(e){} apply(true); });
 show.addEventListener('click',function(e){ e.preventDefault(); try{localStorage.removeItem(KEY);}catch(e){} apply(false); });
})();
</script>
<div class="card">
<h3 class="help" title="{{t('tip_rates')}}">{{t('rates_nav')}}</h3>
<p class="muted">{{t('rates_dash_hint')}}</p>
<a class="btn" href="{{url_for('rates')}}" title="{{t('tip_rates')}}">{{t('rates_open')}}</a>
</div>
<div class="card">
<h3 class="help" title="{{t('tip_reset')}}">🔗 {{t('reset_title')}}</h3>
<p class="muted">{{t('reset_hint')}}</p>
<form method="post" action="{{url_for('admin_resetlink')}}">
<input type="hidden" name="_csrf" value="{{csrf_token}}">
<input name="login" placeholder="{{t('reset_user_ph')}}" title="{{t('tip_reset')}}">
<button title="{{t('tip_reset')}}">{{t('reset_make')}}</button></form>
</div>
<div class="card">
<h3 class="help" title="{{t('tip_players')}}">👥 {{t('players')}}</h3>
<p class="muted">{{t('tap_hint')}}</p>
{% if players %}<input id="pfilter" placeholder="{{t('search_players')}}" autocomplete="off" style="margin-bottom:8px">{% endif %}
<table id="ptable">
<tr><th>{{t('character')}}</th><th class="help" title="{{t('tip_acc_col')}}">{{t('acc_col')}}</th><th>{{t('level')}}</th><th>Yang</th><th>{{t('last_seen')}}</th></tr>
{% for p in players %}
<tr data-k="{{ (p.name ~ ' ' ~ (p.account or ''))|lower }}">
<td><a href="{{url_for('player', pid=p.id)}}" title="{{t('tip_player')}}">{% if p.active %}<span class="dot on" title="{{t('tip_active')}}"></span>{% endif %}{{emoji(p.job)}} <b>{{p.name}}</b></a>
<div class="muted">{{jobname(p.job)}}</div></td>
<td title="{{t('tip_acc_col')}}">👤 {{p.account or '—'}}</td>
<td>{{p.level}}</td><td>{{"{:,}".format(p.gold)}}</td>
<td class="muted">{{p.last_play}}</td></tr>
{% endfor %}</table>
{% if not players %}<p>{{t('no_chars')}} 🙂</p>{% endif %}
</div>
<script>
(function(){
 var f=document.getElementById('pfilter'); if(!f) return;
 var rows=document.querySelectorAll('#ptable tr[data-k]');
 f.addEventListener('input',function(){
   var q=f.value.toLowerCase().trim();
   rows.forEach(function(r){ r.style.display = r.getAttribute('data-k').indexOf(q)>=0 ? '' : 'none'; });
 });
})();
</script>""")

TPL_PLAYER = BASE.replace("__BODY__", """
<p><a href="{{url_for('dash')}}">{{t('back_players')}}</a></p>
<div class="card">
<h3>{{emoji(p.job)}} {{p.name}}</h3>
<span class="badge">{{t('level')}} {{p.level}}</span>
<span class="badge">💰 {{"{:,}".format(p.gold)}} yang</span>
<span class="badge">🗺️ Map {{p.map_index}}</span>
</div>

<div class="card"><h3 class="help" title="{{t('tip_send_item')}}">{{t('give_item')}}</h3>
<form method="post" action="{{url_for('action')}}" id="itemForm">
<input type="hidden" name="_csrf" value="{{csrf_token}}">
<input type="hidden" name="pid" value="{{p.id}}"><input type="hidden" name="cmd" value="ITEM">
<input type="hidden" name="custom_vnum" id="itemVnum">
<select id="itemCat" title="{{t('tip_category')}}">
{% for c in cats %}<option value="{{c}}">{{t('cat_'+c)}}</option>{% endfor %}
</select>
<input id="itemSearch" placeholder="{{t('search_item')}}" title="{{t('tip_search_item')}}" autocomplete="off">
<div id="itemResults" style="max-height:220px;overflow:auto;margin:4px 0"></div>
<div id="itemChosen" class="muted" style="margin:4px 0">—</div>
<input name="arg2" type="number" min="1" max="65535" placeholder="{{t('qty')}}" title="{{t('tip_qty')}}" value="1">
<button class="big" title="{{t('tip_send_item')}}">{{t('send_item')}}</button></form></div>
<script>
(function(){
 var s=document.getElementById('itemSearch'),c=document.getElementById('itemCat'),
     r=document.getElementById('itemResults'),v=document.getElementById('itemVnum'),
     ch=document.getElementById('itemChosen'),tmr=null;
 function load(){
   var q=encodeURIComponent(s.value),cat=c.value;
   fetch('/api/items?q='+q+'&cat='+cat).then(x=>x.json()).then(list=>{
     r.innerHTML='';
     list.forEach(function(it){
       var b=document.createElement('div');
       b.style.cssText='padding:8px 10px;border-bottom:1px solid #3a3222;cursor:pointer';
       b.textContent=it.n+'  ·  #'+it.v;
       b.onclick=function(){v.value=it.v;ch.innerHTML='✅ '+it.n+' (#'+it.v+')';r.innerHTML='';s.value=it.n;};
       r.appendChild(b);
     });
   });
 }
 s.addEventListener('input',function(){clearTimeout(tmr);tmr=setTimeout(load,200);});
 c.addEventListener('change',load);
 document.getElementById('itemForm').addEventListener('submit',function(e){
   if(!v.value){e.preventDefault();ch.innerHTML='⚠️ '+s.getAttribute('placeholder');}
 });
})();
</script>

<div class="card"><h3 class="help" title="{{t('tip_amount')}}">{{t('give_gold')}}</h3>
<form method="post" action="{{url_for('action')}}">
<input type="hidden" name="_csrf" value="{{csrf_token}}">
<input type="hidden" name="pid" value="{{p.id}}"><input type="hidden" name="cmd" value="GOLD">
<select name="preset" title="{{t('tip_amount')}}">
{% for label, amt in gold_presets %}<option value="{{amt}}">{{label}}</option>{% endfor %}
<option value="custom">✏️ …</option>
</select>
<input name="custom_amt" placeholder="{{t('amount')}}" title="{{t('tip_amount')}}">
<button class="big" title="{{t('tip_amount')}}">{{t('send_gold')}}</button></form></div>

<div class="card"><h3 class="help" title="{{t('tip_level')}}">{{t('set_level')}}</h3>
<form method="post" action="{{url_for('action')}}">
<input type="hidden" name="_csrf" value="{{csrf_token}}">
<input type="hidden" name="pid" value="{{p.id}}"><input type="hidden" name="cmd" value="LEVEL">
<input name="arg1" type="number" min="1" max="120" placeholder="{{t('new_level')}}" title="{{t('tip_level')}}" required>
<button class="big" title="{{t('tip_level')}}">{{t('change_level')}}</button></form></div>

<div class="card"><h3 class="help" title="{{t('tip_teleport')}}">{{t('teleport')}} <span class="muted">{{t('ingame_only')}}</span></h3>
<form method="post" action="{{url_for('action')}}">
<input type="hidden" name="_csrf" value="{{csrf_token}}">
<input type="hidden" name="pid" value="{{p.id}}"><input type="hidden" name="cmd" value="WARP">
<select name="preset" title="{{t('tip_teleport')}}">
{% for label, xy in warp_presets %}<option value="{{xy}}">{{label}}</option>{% endfor %}
</select>
<button class="big" title="{{t('tip_teleport')}}">{{t('teleport')}}</button></form></div>

<div class="card"><h3 class="help" title="{{t('tip_speed')}}">{{t('speed')}} <span class="muted">{{t('ingame_only')}}</span></h3>
<form method="post" action="{{url_for('action')}}">
<input type="hidden" name="_csrf" value="{{csrf_token}}">
<input type="hidden" name="pid" value="{{p.id}}"><input type="hidden" name="cmd" value="SPEED">
<select name="arg1" title="{{t('tip_speed')}}">
{% for label, spd in speed_presets %}<option value="{{spd}}">{{label}}</option>{% endfor %}
</select>
<button class="big" title="{{t('tip_speed')}}">{{t('apply')}}</button></form></div>

<div class="card"><h3 class="help" title="{{t('tip_inv')}}">🎒 {{t('inv_title')}}{% if inv %} <span class="muted">({{inv|length}})</span>{% endif %}</h3>
{% if inv is none %}<p class="muted">{{t('db_down')}}</p>
{% elif not inv %}<p class="muted">{{t('inv_empty')}}</p>
{% else %}
<div style="max-height:320px;overflow:auto">
<table>
{% for it in inv %}<tr><td>{{it.name}}</td><td class="muted">×{{it.count}}</td><td class="muted">{{it.window}}</td></tr>{% endfor %}
</table></div>
{% endif %}</div>""")

TPL_RATES = BASE.replace("__BODY__", """
<p><a href="{{url_for('dash')}}">{{t('back_players')}}</a></p>
<div class="card">
<h3>{{t('rates_nav')}}</h3>
<p class="muted">{{t('rates_intro')}}</p>
<p><span class="badge">⭐ {{t('rates_exp')}} {{cur['exp']}}%</span>
   <span class="badge">🎁 {{t('rates_drop')}} {{cur['drop']}}%</span>
   <span class="badge">💰 {{t('rates_yang')}} {{cur['yang']}}%</span></p>
<p class="muted">{{t('rates_current')}}</p>
{% if state_msg %}<p class="muted"><b>{{t('rates_st')}}:</b> {{state_msg}}</p>{% endif %}
</div>

<div class="card"><h3>{{t('rates_presets')}}</h3>
<p class="muted">{{t('rates_presets_hint')}}</p>
{% for key, e, d, y in presets %}
<button type="button" class="big" onclick="m2rates({{e}},{{d}},{{y}})">{{t(key)}}</button>
{% endfor %}
</div>

<div class="card">
<form method="post">
<input type="hidden" name="_csrf" value="{{csrf_token}}">
<h3>⭐ {{t('rates_exp')}}</h3>
<p class="muted">{{t('rates_exp_help')}}</p>
<input id="r_exp" name="exp" type="number" min="1" max="10000" step="1"
       value="{{cur['exp']}}" placeholder="{{t('rates_percent')}}" required>
<h3 style="margin-top:18px">🎁 {{t('rates_drop')}}</h3>
<p class="muted">{{t('rates_drop_help')}}</p>
<input id="r_drop" name="drop" type="number" min="1" max="10000" step="1"
       value="{{cur['drop']}}" placeholder="{{t('rates_percent')}}" required>
<h3 style="margin-top:18px">💰 {{t('rates_yang')}}</h3>
<p class="muted">{{t('rates_yang_help')}}</p>
<input id="r_yang" name="yang" type="number" min="1" max="10000" step="1"
       value="{{cur['yang']}}" placeholder="{{t('rates_percent')}}" required>
<button class="big" style="margin-top:18px">{{t('rates_save')}}</button>
</form></div>
<script>
function m2rates(e,d,y){
  document.getElementById('r_exp').value=e;
  document.getElementById('r_drop').value=d;
  document.getElementById('r_yang').value=y;
}
</script>""")

# every state apply_rates.sh can leave behind has a sentence of its own
RATE_STATES = ("running", "ok", "unsupported", "failed", "no_restart")

@app.route("/rates", methods=["GET", "POST"])
@login_required
def rates():
    """Server-wide experience / item drop / yang rates. Applying them restarts the game."""
    have_script = os.path.exists(RATES_SCRIPT)
    if request.method == "POST":
        # (the global before_request hook has already checked the CSRF token)
        #
        # Three of the things that can go wrong here — no helper, no table, no
        # database — are conditions rather than events: they are still true a
        # moment later, and the GET this redirects to looks for every one of
        # them and says so itself. Saying it here as well is what put the same
        # red box on the page twice. So these three only redirect, and the
        # page below does the talking. Anything that is NOT still true on the
        # next page load (a number out of range, a helper that would not
        # start) is flashed here, because nothing else would ever mention it.
        if not have_script:
            return redirect(url_for("rates"))
        vals = {n: clean_rate(request.form.get(n, "")) for n in RATE_NAMES}
        if any(v is None for v in vals.values()):
            flash(t("rates_range"), "error")
            return redirect(url_for("rates"))
        try:
            with db() as c, c.cursor() as cur:
                for n in RATE_NAMES:
                    cur.execute("INSERT INTO player.web_admin_rates (name,value) VALUES (%s,%s) "
                                "ON DUPLICATE KEY UPDATE value=VALUES(value)", (n, vals[n]))
        except pymysql.err.ProgrammingError:
            return redirect(url_for("rates"))     # "the table is missing" — said below
        except Exception:
            return redirect(url_for("rates"))     # "the database is down"  — said below
        write_rates_status("running")
        try:
            # Never wait for this one: it stops and restarts the whole game server,
            # which takes far longer than a browser is prepared to sit on a request.
            subprocess.Popen(["/bin/sh", RATES_SCRIPT],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True,
                             start_new_session=True)
        except Exception:
            flash(t("rates_st_failed"), "error")
            return redirect(url_for("rates"))
        flash(t("rates_saved"))
        return redirect(url_for("rates"))

    cur_rates = {n: 100 for n in RATE_NAMES}
    try:
        cur_rates = read_rates()
    except pymysql.err.ProgrammingError:
        flash(t("rates_no_table"), "error")
    except Exception:
        flash(t("db_down"), "error")
    if not have_script:
        flash(t("rates_no_script"), "error")
    st = rates_status().get("state", "")
    return render_template_string(TPL_RATES, cur=cur_rates, presets=RATE_PRESETS,
                                  state_msg=t("rates_st_" + st) if st in RATE_STATES else "")

@app.route("/login", methods=["GET", "POST"])
def login():
    # On a local install there is nothing to log in to -- see local_open(). The
    # page itself still matters though: it carries registration, the game and
    # the server status. An earlier version redirected it to the admin view,
    # which made all of that unreachable and left no way back, because the
    # logout link is hidden when nobody is logged in. So: render it, minus the
    # passphrase box, with a button through to the admin side.
    if local_open() and request.method == "POST":
        return redirect(url_for("login"))
    ip = request.remote_addr
    if request.method == "POST":
        cnt, lock = FAILS.get(ip, [0, 0])
        if time.time() < lock:
            flash("Too many wrong attempts. Please wait 15 minutes for security. ⏳", "error")
            return render_template_string(TPL_LOGIN, client_ready=os.path.exists(CLIENT_ZIP), client_name=CLIENT_LABEL, client_url=CLIENT_URL)
        if check_pass(request.form.get("pw", "")):
            FAILS.pop(ip, None)
            session["auth"] = True
            return redirect(url_for("dash"))
        cnt += 1
        FAILS[ip] = [cnt, time.time() + LOCK_SEC if cnt >= MAX_FAIL else 0]
        time.sleep(1.5)
        flash("Wrong passphrase, try again. 🙂", "error")
    return render_template_string(TPL_LOGIN, client_ready=os.path.exists(CLIENT_ZIP), client_name=CLIENT_LABEL, client_url=CLIENT_URL)

def _dl_quota_take(ip):
    """Spend one download slot, against two ceilings, both over a rolling 24h.

      * DL_MAX per address  -- one person cannot loop the download
      * DL_DAY_MAX in total -- a pool of addresses cannot either

    Returns (allowed, seconds_until_a_slot_frees, scope) where scope is "ip" or
    "all" so the page can say which limit was hit; being told "wait 9 hours"
    without being told it is not about you is quietly infuriating.

    Addresses are stored only as a salted hash: the quota has to recognise an
    address again, not know what it was.
    """
    key = hashlib.sha256((CONF["salt"] + "|" + str(ip)).encode()).hexdigest()
    now = time.time()
    con = sqlite3.connect(DL_DB, timeout=15, isolation_level=None)
    try:
        con.execute("PRAGMA journal_mode=WAL")      # readers never block a writer
        con.execute("CREATE TABLE IF NOT EXISTS dl (ip TEXT NOT NULL, ts REAL NOT NULL)")
        con.execute("CREATE INDEX IF NOT EXISTS dl_ip ON dl (ip, ts)")
        con.execute("CREATE INDEX IF NOT EXISTS dl_ts ON dl (ts)")
        # BEGIN IMMEDIATE takes the write lock up front, so the count and the
        # insert cannot straddle another request doing the same thing. Without
        # it, two simultaneous downloads both read "2 used" and both proceed --
        # which is exactly the case a rate limit exists for.
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute("DELETE FROM dl WHERE ts < ?", (now - DL_WINDOW,))
            total = con.execute("SELECT COUNT(*) FROM dl").fetchone()[0]
            if total >= DL_DAY_MAX:
                oldest = con.execute("SELECT MIN(ts) FROM dl").fetchone()[0] or now
                con.execute("COMMIT")               # keep the cleanup
                return False, max(1, int(oldest + DL_WINDOW - now)), "all"
            rows = con.execute("SELECT ts FROM dl WHERE ip = ? ORDER BY ts",
                               (key,)).fetchall()
            if len(rows) >= DL_MAX:
                con.execute("COMMIT")
                return False, max(1, int(rows[0][0] + DL_WINDOW - now)), "ip"
            con.execute("INSERT INTO dl VALUES (?, ?)", (key, now))
            con.execute("COMMIT")
            return True, 0, ""
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()

# ---- password reset links ---------------------------------------------------
# There is no self-service "forgot password": the player writes to the admin,
# the admin makes a link here, the player sets a new password through it. Only
# a hash of the token is stored, links are single-use, live 24 hours, and a
# new link for the same account replaces any older one.
def _pwreset_table(con):
    con.execute("CREATE TABLE IF NOT EXISTS pw_reset (th TEXT PRIMARY KEY, "
                "login TEXT NOT NULL, exp REAL NOT NULL)")
    con.execute("DELETE FROM pw_reset WHERE exp < ?", (time.time(),))

def _pwreset_new(login):
    """Make a fresh token for this account, cancelling any earlier one."""
    tok = secrets.token_urlsafe(32)
    th = hashlib.sha256(tok.encode()).hexdigest()
    con = sqlite3.connect(DL_DB, timeout=5)
    try:
        _pwreset_table(con)
        con.execute("DELETE FROM pw_reset WHERE login = ?", (login,))
        con.execute("INSERT INTO pw_reset VALUES (?, ?, ?)", (th, login, time.time() + 24 * 3600))
        con.commit()
        return tok
    finally:
        con.close()

def _pwreset_login_for(tok, burn=False):
    """The account a token belongs to, or None. burn=True spends the token."""
    th = hashlib.sha256(str(tok).encode()).hexdigest()
    con = sqlite3.connect(DL_DB, timeout=5)
    try:
        _pwreset_table(con)
        row = con.execute("SELECT login FROM pw_reset WHERE th = ?", (th,)).fetchone()
        if row and burn:
            con.execute("DELETE FROM pw_reset WHERE th = ?", (th,))
        con.commit()
        return row[0] if row else None
    finally:
        con.close()

@app.route("/admin/resetlink", methods=["POST"])
@login_required
def admin_resetlink():
    lg = request.form.get("login", "").strip()
    if not (4 <= len(lg) <= 16 and lg.isalnum()):
        flash(t("reset_noacc"), "error")
        return redirect(url_for("dash"))
    try:
        with db() as c, c.cursor() as cur:
            cur.execute("SELECT 1 FROM account.account WHERE login=%s", (lg,))
            if not cur.fetchone():
                flash(t("reset_noacc"), "error")
                return redirect(url_for("dash"))
    except Exception:
        flash(t("db_down"), "error")
        return redirect(url_for("dash"))
    link = url_for("reset", token=_pwreset_new(lg), _external=True)
    flash("%s %s" % (t("reset_made"), link))
    return redirect(url_for("dash"))

@app.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    lg = _pwreset_login_for(token)
    if not lg:
        return render_template_string(TPL_RESET, valid=False, login="")
    if request.method == "POST":
        if rate_limited("pwreset", 5, 900):
            flash("Too many attempts. Please wait a while. ⏳", "error")
            return render_template_string(TPL_RESET, valid=True, login=lg)
        new, new2 = request.form.get("new", ""), request.form.get("new2", "")
        if len(new) < 6:
            flash(t("reset_short"), "error")
        elif new != new2:
            flash(t("reset_mismatch"), "error")
        else:
            try:
                with db() as c, c.cursor() as cur:
                    cur.execute("UPDATE account.account SET password=%s WHERE login=%s",
                                (m2_hash(new), lg))
                _pwreset_login_for(token, burn=True)
                flash(t("reset_done"))
                return redirect(url_for("login"))
            except Exception:
                flash(t("db_down"), "error")
    return render_template_string(TPL_RESET, valid=True, login=lg)

@app.route("/download")
def download():
    """Public client download (no passphrase needed)."""
    # When an external download URL is configured, send people there instead of
    # streaming a gigabyte through this dev server. Anyone with an old bookmark
    # pointing at /download still ends up in the right place.
    if CLIENT_URL:
        return redirect(CLIENT_URL)
    if not os.path.exists(CLIENT_ZIP):
        flash("The game download is not ready yet.", "error")
        return redirect(url_for("login"))
    # A slot is spent only by a fresh fetch of the whole file. HEAD probes cost
    # nothing, and neither does resuming: a genuine resume asks for a Range that
    # starts mid-file. A Range starting at byte 0 is the whole file wearing a
    # different hat, so it pays like one. The logged-in admin is never limited.
    rng = request.headers.get("Range", "")
    fresh = request.method == "GET" and (not rng or bool(re.match(r"\s*bytes\s*=\s*0\s*-", rng)))
    if fresh and not (session.get("auth") or local_open()):
        try:
            allowed, wait, scope = _dl_quota_take(request.remote_addr)
        except Exception:
            # The counter is a guard, not the point of the page. If its little
            # database is unwritable we log it and serve -- refusing every
            # download because a quota file is broken is the worse failure.
            app.logger.exception("download quota unavailable, serving anyway")
            allowed, wait, scope = True, 0, ""
        if not allowed:
            resp = app.response_class(
                render_template_string(TPL_DL_LIMIT,
                                       wait_h=max(1, -(-wait // 3600)),
                                       scope=scope),
                status=429)
            resp.headers["Retry-After"] = str(wait)
            return resp
    # Behind nginx, hand the file over and let it do the sending: a gigabyte
    # through Flask's single-threaded development server blocks the panel for
    # everyone else for as long as the download runs. The header is only obeyed
    # by nginx, so it is set solely for requests that actually arrived through it.
    if request.environ.get("panel.via_proxy"):
        resp = app.response_class()
        resp.headers["X-Accel-Redirect"] = "/_client_zip"
        resp.headers["Content-Type"] = "application/zip"
        resp.headers["Content-Disposition"] = 'attachment; filename="%s"' % CLIENT_FILE
        return resp
    return send_file(CLIENT_ZIP, as_attachment=True, download_name=CLIENT_FILE, conditional=True)

# ---------------- Player registration & account ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    form = {"login": "", "social": ""}
    if request.method == "POST":
        if rate_limited("register", 3, 3600):
            flash("Too many accounts were created from this connection. Please try again later. ⏳", "error")
            return render_template_string(TPL_REGISTER, form=form)
        lg = request.form.get("login", "").strip()
        pw = request.form.get("pw", "")
        pw2 = request.form.get("pw2", "")
        social = request.form.get("social", "").strip()
        form = {"login": lg, "social": social}
        if not (4 <= len(lg) <= 16 and lg.isalnum()):
            flash("The username must be 4-16 letters/numbers, no spaces. 🙂", "error")
        elif len(pw) < 6:
            flash("The password must be at least 6 characters. 🙂", "error")
        elif pw != pw2:
            flash("The two passwords don't match — try again. 🙂", "error")
        elif not (social.isdigit() and len(social) == 7):
            flash("The delete code must be exactly 7 digits (e.g. 1234567). 🙂", "error")
        else:
            try:
                with db() as c, c.cursor() as cur:
                    cur.execute("SELECT 1 FROM account.account WHERE login=%s", (lg,))
                    if cur.fetchone():
                        flash("That username is already taken — pick another one. 🙂", "error")
                        return render_template_string(TPL_REGISTER, form=form)
                    cur.execute(
                        "INSERT INTO account.account (login,password,social_id,status) "
                        "VALUES (%s,%s,%s,'OK')",
                        (lg, m2_hash(pw), social))
                return render_template_string(TPL_REG_DONE,
                                              client_ready=os.path.exists(CLIENT_ZIP),
                                              client_url=CLIENT_URL)
            except Exception:
                flash("The account could not be created right now. Please try again in a bit. 🙏", "error")
    return render_template_string(TPL_REGISTER, form=form)

@app.route("/account", methods=["GET", "POST"])
def account():
    if request.method == "POST":
        if rate_limited("acclogin", 8, 900):
            flash("Too many attempts. Please wait a while. ⏳", "error")
            return render_template_string(TPL_ACCOUNT_LOGIN)
        lg = request.form.get("login", "").strip()
        pw = request.form.get("pw", "")
        try:
            with db() as c, c.cursor() as cur:
                cur.execute("SELECT password FROM account.account WHERE login=%s", (lg,))
                row = cur.fetchone()
        except Exception:
            flash(t("db_down"), "error")
            return render_template_string(TPL_ACCOUNT_LOGIN)
        if row and hmac.compare_digest(row["password"], m2_hash(pw)):
            session["player"] = lg
        else:
            time.sleep(1.0)
            flash("Wrong username or password. 🙂", "error")
            return render_template_string(TPL_ACCOUNT_LOGIN)
    lg = session.get("player")
    if not lg:
        return render_template_string(TPL_ACCOUNT_LOGIN)
    try:
        with db() as c, c.cursor() as cur:
            cur.execute("SELECT id FROM account.account WHERE login=%s", (lg,))
            acc = cur.fetchone()
            chars = []
            if acc:
                cur.execute("SELECT name,job,level,gold FROM player.player WHERE account_id=%s", (acc["id"],))
                chars = cur.fetchall()
    except Exception:
        flash(t("db_down"), "error")
        chars = []
    return render_template_string(TPL_ACCOUNT, login=lg, chars=chars,
                                  emoji=lambda j: JOB_EMOJI.get(j, "🧑"))

@app.route("/account/password", methods=["POST"])
def account_password():
    lg = session.get("player")
    if not lg:
        return redirect(url_for("account"))
    old, new, new2 = (request.form.get(k, "") for k in ("old", "new", "new2"))
    if len(new) < 6:
        flash("The new password must be at least 6 characters. 🙂", "error")
    elif new != new2:
        flash("The two new passwords don't match. 🙂", "error")
    else:
        try:
            with db() as c, c.cursor() as cur:
                cur.execute("SELECT password FROM account.account WHERE login=%s", (lg,))
                row = cur.fetchone()
                if row and hmac.compare_digest(row["password"], m2_hash(old)):
                    cur.execute("UPDATE account.account SET password=%s WHERE login=%s", (m2_hash(new), lg))
                    flash("🔒 Your password was changed! Use the new one next time you log into the game.")
                else:
                    flash("The current password is wrong. 🙂", "error")
        except Exception:
            flash(t("db_down"), "error")
    return redirect(url_for("account"))

@app.route("/account/logout")
def account_logout():
    session.pop("player", None)
    return redirect(url_for("login"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@app.route("/admin")
@login_required
def dash():
    # A local install has no login step, so "/" would drop the owner straight
    # into the admin view with no way to reach the front page -- where the game,
    # registration and the server status live. There, "/" is the front page and
    # the admin view has its own address. Everywhere else this changes nothing:
    # "/" stays the dashboard you reach by entering the passphrase.
    if local_open() and request.path == "/":
        return redirect(url_for("login"))
    try:
        with db() as c, c.cursor() as cur:
            cur.execute("SELECT p.id, p.name, p.job, p.level, p.gold, p.last_play, "
                        "a.login AS account "
                        "FROM player.player p "
                        "LEFT JOIN account.account a ON a.id = p.account_id "
                        "ORDER BY p.last_play DESC LIMIT 200")
            players = cur.fetchall()
        # 'recently in the game' marker: last_play within the last 10 minutes.
        # The game stamps it at login/logout, so this is honest about what it
        # knows - the tooltip says 'was in the game', not 'is online'.
        now = datetime.datetime.now()
        for p in players:
            lp = p.get("last_play")
            p["active"] = bool(lp) and abs((now - lp).total_seconds()) < 600
    except Exception:
        flash(t("db_down"), "error")
        players = []
    return render_template_string(TPL_DASH, players=players,
                                  emoji=lambda j: JOB_EMOJI.get(j, "🧑"),
                                  jobname=lambda j: JOB_NAME.get(j, ""))

@app.route("/player/<int:pid>")
@login_required
def player(pid):
    try:
        with db() as c, c.cursor() as cur:
            cur.execute("SELECT * FROM player.player WHERE id=%s", (pid,))
            p = cur.fetchone()
    except Exception:
        flash(t("db_down"), "error")
        return render_template_string(TPL_DASH, players=[],
                                      emoji=lambda j: JOB_EMOJI.get(j, "🧑"),
                                      jobname=lambda j: JOB_NAME.get(j, ""))
    if not p:
        flash(t("not_found"), "error")
        return redirect(url_for("dash"))
    # read-only inventory: names resolved from items.json, unknown vnums shown
    # as #vnum rather than hidden - the admin should see everything
    try:
        with db() as c, c.cursor() as cur:
            cur.execute("SELECT vnum, count, window FROM player.item "
                        "WHERE owner_id=%s ORDER BY window, pos", (pid,))
            inv = [{"name": ITEM_NAMES.get(r["vnum"], "#%d" % r["vnum"]),
                    "count": r["count"], "window": str(r["window"]).lower()}
                   for r in cur.fetchall()]
    except Exception:
        inv = None
    return render_template_string(TPL_PLAYER, p=p, inv=inv,
                                  emoji=lambda j: JOB_EMOJI.get(j, "🧑"),
                                  cats=CATS,
                                  gold_presets=gold_presets_i18n(), warp_presets=warp_presets_i18n(),
                                  speed_presets=speed_presets_i18n())

def item_qty(raw):
    """Return a stack size the game can actually store (1..65535), or None if invalid."""
    try:
        q = int(str(raw).strip() or "1")
    except (TypeError, ValueError):
        return None
    return q if 1 <= q <= MAX_ITEM_COUNT else None

def queue_and_wait(name, cmd, arg1, arg2, wait=7.0):
    """Insert the command into the queue and wait for the in-game quest to process it.
       Returns (status, queue_row_id) with status: done | player_offline | timeout | gone | ...

       IMPORTANT: on 'timeout' the row is still 'pending', so the quest may still pick it
       up later. The caller MUST claim the row (see action()) before applying anything
       itself, otherwise the player would receive the same reward twice."""
    with db() as c, c.cursor() as cur:
        cur.execute("INSERT INTO player.web_admin_queue (player_name,cmd,arg1,arg2) VALUES (%s,%s,%s,%s)",
                    (name, cmd, str(arg1), str(arg2)))
        qid = cur.lastrowid
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.6)
        with db() as c, c.cursor() as cur:
            cur.execute("SELECT status FROM player.web_admin_queue WHERE id=%s", (qid,))
            row = cur.fetchone()
        if row is None:
            return "gone", qid          # something removed the row — never apply on top of that
        if row["status"] != "pending":
            return row["status"], qid
    return "timeout", qid

def offline_apply(cur, pid, cmd, arg1, arg2, reason="timeout", name=""):
    """Write the change straight into the database because the player isn't in game.
       Only ITEM / GOLD / LEVEL can be done this way. The caller must have claimed the
       queue row first, otherwise the quest could apply the same thing a second time."""
    if cmd == "GOLD":
        cur.execute("UPDATE player.player SET gold=GREATEST(0,gold+%s) WHERE id=%s", (int(arg1), pid))
    elif cmd == "LEVEL":
        cur.execute("UPDATE player.player SET level=%s WHERE id=%s", (int(arg1), pid))
    elif cmd == "ITEM":
        qty = item_qty(arg2)
        if qty is None:
            raise RuntimeError(t("qty_range").format(max="{:,}".format(MAX_ITEM_COUNT)))
        cur.execute("SELECT pos FROM player.item WHERE owner_id=%s AND window='INVENTORY'", (pid,))
        used = {r["pos"] for r in cur.fetchall()}
        free = next((i for i in range(INVENTORY_SLOTS) if i not in used), None)
        if free is None:
            raise RuntimeError(t("inv_full").format(conf=CONF_PATH))
        cur.execute("INSERT INTO player.item (owner_id,window,pos,count,vnum) VALUES (%s,'INVENTORY',%s,%s,%s)",
                    (pid, free, qty, int(arg1)))
    else:
        # WARP / SPEED need the in-game quest. Faking a warp by writing x/y/map_index is
        # NOT safe (map_index can't be derived from coordinates — the character could end
        # up in the void), so we refuse honestly and say why.
        key = "ingame_offline" if reason == "player_offline" else "ingame_timeout"
        raise RuntimeError(t(key).format(name=name))

@app.route("/action", methods=["POST"])
@login_required
def action():
    try:
        pid = int(request.form.get("pid", ""))
    except (TypeError, ValueError):
        flash(t("not_found"), "error")
        return redirect(url_for("dash"))
    cmd = request.form.get("cmd", "")
    preset = request.form.get("preset", "")
    arg1 = request.form.get("arg1", "").strip()
    arg2 = request.form.get("arg2", "1").strip()

    # resolve presets
    if cmd == "ITEM":
        arg1 = request.form.get("custom_vnum", "").strip()  # vnum chosen via live search
        if item_qty(arg2) is None:                          # player.item.count is smallint unsigned
            flash(t("qty_range").format(max="{:,}".format(MAX_ITEM_COUNT)), "error")
            return redirect(url_for("player", pid=pid))
        arg2 = str(item_qty(arg2))
    elif cmd == "GOLD":
        arg1 = request.form.get("custom_amt", "").strip() if preset == "custom" else preset
    elif cmd == "WARP":
        if " " not in preset:
            flash(t("act_novalue"), "error")
            return redirect(url_for("player", pid=pid))
        arg1, arg2 = preset.split(" ", 1)
    elif cmd == "SPEED":
        arg2 = "3600"

    if not arg1:
        flash(t("act_novalue"), "error")
        return redirect(url_for("player", pid=pid))

    try:
        with db() as c, c.cursor() as cur:
            cur.execute("SELECT name FROM player.player WHERE id=%s", (pid,))
            row = cur.fetchone()
    except Exception:
        flash(t("db_down"), "error")
        return redirect(url_for("dash"))
    if not row:
        flash(t("not_found"), "error")
        return redirect(url_for("dash"))
    name = row["name"]

    try:
        st, qid = queue_and_wait(name, cmd, arg1, arg2)
        if st == "done":
            flash(t("act_done").format(name=name))
        elif st in ("player_offline", "timeout", "gone"):
            with db() as c, c.cursor() as cur:
                # Take the queue row away from the in-game quest FIRST. If we skipped this
                # and the quest picked the still-pending row up afterwards, the player
                # would get the item/yang/level a second time.
                cur.execute("UPDATE player.web_admin_queue SET status='cancelled' "
                            "WHERE id=%s AND status='pending'", (qid,))
                claimed = cur.rowcount == 1
                if not claimed and st == "player_offline":
                    # The quest itself reported the player as offline, so the row is
                    # already out of the 'pending' pool and nobody will deliver it.
                    claimed = True
                if claimed:
                    offline_apply(cur, pid, cmd, arg1, arg2, reason=st, name=name)
                    flash(t("act_offline").format(name=name))
                else:
                    # The quest grabbed it while we were waiting — do NOT apply again.
                    cur.execute("SELECT status FROM player.web_admin_queue WHERE id=%s", (qid,))
                    r2 = cur.fetchone()
                    st2 = r2["status"] if r2 else "gone"
                    if st2 == "done":
                        flash(t("act_late_done").format(name=name))
                    else:
                        flash(t("act_late_other").format(name=name, status=st2))
        else:
            flash(t("act_error").format(status=st), "error")
    except RuntimeError as e:
        flash(str(e), "error")
    except Exception:
        flash(t("act_unexpected"), "error")
    return redirect(url_for("player", pid=pid))

if __name__ == "__main__":
    app.run(host=CONF.get("bind", "0.0.0.0"), port=CONF.get("port", 7788))
