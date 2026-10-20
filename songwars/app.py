#!/usr/bin/env python3
"""
Songwars.

Spillere joiner en lobby med en kode, faar en genre og et beat, laver et
nummer ovenpaa i deres eget program, afleverer det, og alle stemmer 1-5
flammer. Ingen konti: man er et kaldenavn i en signeret cookie.

    python app.py              udviklingsserver paa :8092
    python app.py ryd          slet udloebne lobbyer og gamle afleveringer nu

I drift koeres den med gunicorn bag nginx. Se README.md og DEPLOY.md.

Faserne i en runde gemmes som status + tidsstempler, og den aktuelle fase
regnes ud ved hver forespoergsel (advance). Der er ingen baggrundsjob og
ingen timer i klienten der bestemmer noget - fristen haandhaeves her.

    status    fase
    lobby     lobby       venter paa spillere
    running   reveal      genre, beat og hold er trukket   (indtil make_at)
              make        nedtaelling til deadline         (indtil deadline + grace)
    voting    vote        alle stemmer
    done      result      ranglisten
    closed    closed      udloebet foer runden blev faerdig
"""

import hmac
import os
import re
import secrets
import shutil
import sqlite3
import sys
import time
import unicodedata
from functools import wraps
from hashlib import sha1
from pathlib import Path

from flask import (
    Flask, abort, flash, g, jsonify, redirect, render_template, request,
    send_file, session, url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

import audio

# --------------------------------------------------------------------
# Konfiguration. Alt kommer fra miljoeet (.env via docker compose).
# --------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent


def load_env_file(path):
    """Til `python app.py` lokalt. I containeren saetter docker compose
    variablerne fra .env, og filen kommer aldrig med ind i imaget
    (.dockerignore). Variabler der allerede er sat, vinder."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(ROOT / ".env")


def env_int(name, default, low, high):
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        sys.exit("{0} skal vaere et heltal, ikke {1!r}".format(name, raw))
    if not low <= value <= high:
        sys.exit("{0} skal ligge mellem {1} og {2}".format(name, low, high))
    return value


DATA = Path(os.environ.get("SONGWARS_DATA", ROOT / "data"))
DB_PATH = DATA / "songwars.db"
BEATS = DATA / "beats"
SUBS = DATA / "submissions"
TMP = DATA / "tmp"

SECRET_KEY = os.environ.get("SONGWARS_SECRET_KEY", "")
ADMIN_PASSWORD = os.environ.get("SONGWARS_ADMIN_PASSWORD", "")
COOKIE_SECURE = os.environ.get("SONGWARS_COOKIE_SECURE", "1") != "0"
IP_HEADER = os.environ.get("SONGWARS_IP_HEADER", "CF-Connecting-IP").strip()

MAX_UPLOAD = env_int("SONGWARS_MAX_UPLOAD_MB", 50, 1, 95) * 1024 * 1024
MAX_BEAT = env_int("SONGWARS_MAX_BEAT_MB", 95, 1, 95) * 1024 * 1024
LOBBY_SECONDS = env_int("SONGWARS_LOBBY_HOURS", 24, 1, 168) * 3600
KEEP_SECONDS = env_int("SONGWARS_KEEP_DAYS", 7, 1, 90) * 86400
REVEAL_SECONDS = env_int("SONGWARS_REVEAL_SECONDS", 10, 0, 120)
# Et upload paa 50 MB tager tid. Det der naar frem lige efter nul, faar
# lov - alt efter det her afvises. Saet til 0 for at vaere helt stram.
GRACE_SECONDS = env_int("SONGWARS_GRACE_SECONDS", 10, 0, 120)

MAX_PLAYERS = 40
MINUTES_RANGE = (1, 180)
TEAM_RANGE = (1, 10)
NAME_MAX = 20
TITLE_MAX = 80
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # ingen 0/O, 1/I/L
CODE_LEN = 5
CODE_RE = re.compile(r"[%s]{%d}" % (CODE_ALPHABET, CODE_LEN))
HEX_RE = re.compile(r"[0-9a-f]{32}")

# (antal, vindue i sekunder). Taeller ogsaa forsoeg der fejler - ellers
# kan man gaette lobbykoder saa hurtigt man vil.
LIMITS = {
    "join": (12, 300),
    "create": (6, 3600),
    "upload_ip": (30, 600),
    "upload_player": (12, 600),
    "admin": (6, 900),
    "vote": (300, 600),
}

ONLINE_SECONDS = 15        # set inden for, saa staar man som "her"
POLL_TOUCH_SECONDS = 8     # skriv ikke seen_at ved hver eneste poll
CLEANUP_EVERY = 600

# Samme politik som nginx. Saetter appen den ogsaa, gaelder den selv hvis
# server-blokken mangler en linje - og den kan testes lokalt.
CSP = ("default-src 'none'; script-src 'self'; connect-src 'self'; "
       "img-src 'self' data:; media-src 'self'; style-src 'self'; font-src 'self'; "
       "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")


def check_config():
    problems = []
    if len(SECRET_KEY) < 32:
        problems.append("SONGWARS_SECRET_KEY mangler eller er under 32 tegn. Lav en med:\n"
                        "    python -c \"import secrets; print(secrets.token_hex(32))\"")
    if len(ADMIN_PASSWORD) < 12:
        problems.append("SONGWARS_ADMIN_PASSWORD mangler eller er under 12 tegn.")
    if problems:
        sys.exit("Songwars kan ikke starte:\n  - " + "\n  - ".join(problems))

    for folder in (DATA, BEATS, SUBS, TMP):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".skrivbar"
            probe.write_text("ok")
            probe.unlink()
        except OSError as exc:
            sys.exit("Songwars kan ikke skrive i {0} ({1}).\n"
                     "Containeren koerer som uid 10001. Paa vaerten:\n"
                     "    sudo chown -R 10001:10001 data".format(folder, exc))


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=SECRET_KEY,
    MAX_CONTENT_LENGTH=MAX_UPLOAD + 1024 * 1024,     # plads til formularfelterne
    SESSION_COOKIE_NAME="songwars",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=KEEP_SECONDS,
)


# --------------------------------------------------------------------
# Database
# --------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS genres (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS beats (
    id          TEXT PRIMARY KEY,
    genre_id    INTEGER NOT NULL REFERENCES genres(id),
    title       TEXT NOT NULL,
    file        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    seconds     REAL NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS lobbies (
    id          INTEGER PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    owner_id    INTEGER,
    minutes     INTEGER NOT NULL,
    team_size   INTEGER NOT NULL,
    genre_id    INTEGER,                 -- NULL = tilfaeldig
    status      TEXT NOT NULL DEFAULT 'lobby',
    beat_id     TEXT REFERENCES beats(id) ON DELETE SET NULL,
    genre_name  TEXT NOT NULL DEFAULT '',
    beat_title  TEXT NOT NULL DEFAULT '',
    started_at  REAL,
    make_at     REAL,
    deadline    REAL,
    voting_at   REAL,
    done_at     REAL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    id          INTEGER PRIMARY KEY,
    lobby_id    INTEGER NOT NULL REFERENCES lobbies(id) ON DELETE CASCADE,
    number      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    id          INTEGER PRIMARY KEY,
    lobby_id    INTEGER NOT NULL REFERENCES lobbies(id) ON DELETE CASCADE,
    name        TEXT NOT NULL COLLATE NOCASE,
    team_id     INTEGER REFERENCES teams(id) ON DELETE SET NULL,
    joined_at   REAL NOT NULL,
    seen_at     REAL NOT NULL,
    UNIQUE (lobby_id, name)
);

CREATE TABLE IF NOT EXISTS submissions (
    id          TEXT PRIMARY KEY,
    lobby_id    INTEGER NOT NULL REFERENCES lobbies(id) ON DELETE CASCADE,
    team_id     INTEGER NOT NULL UNIQUE REFERENCES teams(id) ON DELETE CASCADE,
    file        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    seconds     REAL NOT NULL,
    player_id   INTEGER,
    uploaded_at REAL NOT NULL,
    position    INTEGER
);

CREATE TABLE IF NOT EXISTS votes (
    submission_id TEXT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    player_id     INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    flames        INTEGER NOT NULL CHECK (flames BETWEEN 1 AND 5),
    at            REAL NOT NULL,
    PRIMARY KEY (submission_id, player_id)
);

CREATE TABLE IF NOT EXISTS hits (
    key   TEXT NOT NULL,
    at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS hits_key ON hits(key, at);
CREATE INDEX IF NOT EXISTS players_lobby ON players(lobby_id);
CREATE INDEX IF NOT EXISTS subs_lobby ON submissions(lobby_id);
CREATE INDEX IF NOT EXISTS lobbies_created ON lobbies(created_at);
"""


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = connect()
    # WAL: polling-laesninger venter ikke paa en der skriver.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


# --------------------------------------------------------------------
# Smaating
# --------------------------------------------------------------------

def now():
    return time.time()


def ms(t):
    return None if t is None else int(t * 1000)


def clean_text(text, limit):
    text = unicodedata.normalize("NFC", text or "")
    text = re.sub(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]", "", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def clock(seconds):
    if not seconds or seconds <= 0:
        return "—"
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes >= 60:
        return "{0}:{1:02d}:{2:02d}".format(minutes // 60, minutes % 60, secs)
    return "{0}:{1:02d}".format(minutes, secs)


def client_ip():
    if IP_HEADER:
        value = request.headers.get(IP_HEADER, "").strip()
        if value:
            return value[:64]
    return request.remote_addr or "?"


_ASSET_V = {}


def asset(filename):
    """Statisk fil med ?v=<hash>, saa et deploy ikke serverer gammel CSS."""
    if filename not in _ASSET_V:
        try:
            digest = sha1((ROOT / "static" / filename).read_bytes()).hexdigest()[:8]
        except OSError:
            digest = "0"
        _ASSET_V[filename] = digest
    return "{0}?v={1}".format(url_for("static", filename=filename), _ASSET_V[filename])


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.context_processor
def inject():
    return {"asset": asset, "csrf_token": csrf_token, "clock": clock,
            "is_admin": is_admin()}


def json_error(status, message, **extra):
    body = {"error": message}
    body.update(extra)
    resp = jsonify(body)
    resp.status_code = status
    return resp


def wants_json():
    return (request.headers.get("X-Requested-With") == "fetch"
            or request.accept_mimetypes.best == "application/json")


def fail(status, message, back=None):
    """Fejl til et fetch-kald som JSON, til en formular som flash + redirect."""
    if wants_json():
        return json_error(status, message)
    flash(message)
    return redirect(back or request.referrer or url_for("home"))


# --------------------------------------------------------------------
# Sikkerhed: CSRF, headers, rate limiting
# --------------------------------------------------------------------

@app.before_request
def guard():
    # Foer formularen laeses: ellers er graensen allerede brugt.
    if request.endpoint == "admin_beat_create" and is_admin():
        # Beats maa vaere stoerre end afleveringer. Kun for admin.
        request.max_content_length = MAX_BEAT + 1024 * 1024
    if request.method == "POST":
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
            return fail(400, "Siden var for gammel. Prøv igen.")
    if request.endpoint != "static":
        maybe_cleanup()
    return None


@app.after_request
def headers(resp):
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.endpoint in ("lobby_state",) or resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-store"
    return resp


def limited(kind, who):
    """True hvis graensen er naaet. Forsoeget taeller med uanset udfald.
    Gemt i SQLite, saa begge gunicorn-workers ser samme taeller."""
    limit, window = LIMITS[kind]
    key = "{0}:{1}".format(kind, who)
    t = now()
    conn = db()
    count = conn.execute("SELECT COUNT(*) FROM hits WHERE key = ? AND at > ?",
                         (key, t - window)).fetchone()[0]
    if count >= limit:
        return True
    conn.execute("INSERT INTO hits (key, at) VALUES (?, ?)", (key, t))
    conn.commit()
    return False


def too_many(message="For mange forsøg. Vent lidt, og prøv igen."):
    if wants_json():
        resp = json_error(429, message)
    else:
        resp = app.make_response((render_template("error.html", title="Ro på",
                                                  message=message), 429))
    resp.headers["Retry-After"] = "60"
    return resp


# --------------------------------------------------------------------
# Oprydning
# --------------------------------------------------------------------

def remove_file(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        app.logger.warning("kunne ikke slette %s", path)


def cleanup(conn):
    """Lobbyer lukker efter LOBBY_SECONDS (det sker i advance). Efter
    KEEP_SECONDS slettes alt: lobbyen, spillerne, stemmerne og filerne."""
    t = now()
    old = conn.execute("SELECT id FROM lobbies WHERE created_at < ?",
                       (t - KEEP_SECONDS,)).fetchall()
    removed_files = 0
    for row in old:
        files = conn.execute("SELECT file FROM submissions WHERE lobby_id = ?",
                             (row["id"],)).fetchall()
        conn.execute("DELETE FROM lobbies WHERE id = ?", (row["id"],))
        conn.commit()
        for f in files:
            remove_file(SUBS / f["file"])
            removed_files += 1

    conn.execute("DELETE FROM hits WHERE at < ?", (t - 86400,))
    conn.commit()

    # Filer der ikke er en raekke til - en upload der doede midt i, eller
    # en raekke der blev slettet uden filen. Kun gamle, saa en upload der
    # er i gang lige nu ikke roeres.
    known = {r["file"] for r in conn.execute("SELECT file FROM submissions")}
    for path in SUBS.iterdir():
        if path.name not in known and path.stat().st_mtime < t - 86400:
            remove_file(path)
            removed_files += 1
    for path in TMP.iterdir():
        if path.stat().st_mtime < t - 86400:
            remove_file(path)
    return len(old), removed_files


_next_cleanup = [0.0]


def maybe_cleanup():
    """Hoejst hvert tiende minut, og kun af den forespoergsel der vinder
    retten til det - ikke af alle workers paa en gang. Foerst et tjek i
    processen, saa en poll ikke skriver i databasen for ingenting."""
    t = now()
    if t < _next_cleanup[0]:
        return
    _next_cleanup[0] = t + 60
    conn = db()
    conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('cleanup_at', '0')")
    won = conn.execute(
        "UPDATE meta SET value = ? WHERE key = 'cleanup_at' AND CAST(value AS REAL) < ?",
        (str(t), t - CLEANUP_EVERY)).rowcount
    conn.commit()
    if won:
        try:
            cleanup(conn)
        except (OSError, sqlite3.Error):
            app.logger.exception("oprydning fejlede")


# --------------------------------------------------------------------
# Lobbyer og spillere
# --------------------------------------------------------------------

def new_code(conn):
    for _ in range(50):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
        if not conn.execute("SELECT 1 FROM lobbies WHERE code = ?", (code,)).fetchone():
            return code
    raise RuntimeError("kunne ikke finde en ledig kode")


def norm_code(code):
    code = (code or "").strip().upper().replace(" ", "")
    return code if CODE_RE.fullmatch(code) else ""


def lobby_or_404(code):
    code = norm_code(code)
    if not code:
        abort(404)
    row = db().execute("SELECT * FROM lobbies WHERE code = ?", (code,)).fetchone()
    if row is None:
        abort(404)
    return advance(row)


def me_in(lobby):
    """Spilleren i den her lobby, ifoelge den signerede cookie."""
    pid = session.get("p", {}).get(lobby["code"])
    if pid is None:
        return None
    row = db().execute("SELECT * FROM players WHERE id = ? AND lobby_id = ?",
                       (pid, lobby["id"])).fetchone()
    if row is None:                                   # smidt ud, eller slettet
        mine = dict(session.get("p", {}))
        mine.pop(lobby["code"], None)
        session["p"] = mine
    return row


def remember(code, player_id):
    mine = dict(session.get("p", {}))
    mine[code] = player_id
    # Kun de seneste lobbyer. Cookien skal ikke vokse uden graense.
    if len(mine) > 12:
        for old in list(mine)[:-12]:
            mine.pop(old)
    session["p"] = mine
    session.permanent = True


def phase_of(lobby, t=None):
    t = now() if t is None else t
    status = lobby["status"]
    if status == "running":
        return "reveal" if t < lobby["make_at"] else "make"
    return {"lobby": "lobby", "voting": "vote", "done": "result", "closed": "closed"}[status]


def genres_with_beats(conn):
    return conn.execute(
        "SELECT g.id, g.name, COUNT(b.id) AS beats FROM genres g"
        " JOIN beats b ON b.genre_id = g.id GROUP BY g.id ORDER BY g.name").fetchall()


def advance(lobby):
    """Flytter lobbyen videre hvis tiden eller stemmerne siger det. Kaldes
    ved hver forespoergsel. Hver overgang er en UPDATE ... WHERE status =
    den gamle, saa kun en forespoergsel vinder den - ogsaa naar to workers
    poller paa samme tid."""
    conn = db()
    t = now()
    lid, status = lobby["id"], lobby["status"]

    if status in ("lobby", "running", "voting") and t >= lobby["created_at"] + LOBBY_SECONDS:
        # En afstemning i gang faar sit resultat; alt andet lukker bare.
        new = "done" if status == "voting" else "closed"
        conn.execute("UPDATE lobbies SET status = ?, done_at = ? WHERE id = ? AND status = ?",
                     (new, t, lid, status))
        conn.commit()

    elif status == "running" and t >= lobby["deadline"] + GRACE_SECONDS:
        won = conn.execute(
            "UPDATE lobbies SET status = 'voting', voting_at = ? WHERE id = ? AND status = 'running'",
            (t, lid)).rowcount
        if won:
            subs = [r["id"] for r in conn.execute(
                "SELECT id FROM submissions WHERE lobby_id = ?", (lid,))]
            # Raekkefoelgen i afstemningen er tilfaeldig - ikke den
            # raekkefoelge numrene blev afleveret i, og ikke holdnumrene.
            secrets.SystemRandom().shuffle(subs)
            for position, sid in enumerate(subs, start=1):
                conn.execute("UPDATE submissions SET position = ? WHERE id = ?", (position, sid))
            if not subs:
                conn.execute("UPDATE lobbies SET status = 'done', done_at = ? WHERE id = ?", (t, lid))
        conn.commit()

    elif status == "voting" and voting_complete(conn, lid):
        conn.execute("UPDATE lobbies SET status = 'done', done_at = ? WHERE id = ? AND status = 'voting'",
                     (t, lid))
        conn.commit()

    return conn.execute("SELECT * FROM lobbies WHERE id = ?", (lid,)).fetchone()


def vote_progress(conn, lid):
    """(spillere der er faerdige, spillere der skal stemme, stemmer der
    mangler i alt). Man stemmer paa alle numre undtagen sit eget holds."""
    voters = conn.execute(
        "SELECT p.id, p.team_id,"
        "  (SELECT COUNT(*) FROM submissions s WHERE s.lobby_id = p.lobby_id"
        "     AND s.team_id != p.team_id) AS need,"
        "  (SELECT COUNT(*) FROM votes v JOIN submissions s ON s.id = v.submission_id"
        "     WHERE v.player_id = p.id AND s.team_id != p.team_id) AS have"
        " FROM players p WHERE p.lobby_id = ? AND p.team_id IS NOT NULL", (lid,)).fetchall()
    voting = [v for v in voters if v["need"] > 0]
    done = sum(1 for v in voting if v["have"] >= v["need"])
    return done, len(voting), {v["id"]: (v["have"], v["need"]) for v in voters}


def voting_complete(conn, lid):
    done, total, _ = vote_progress(conn, lid)
    return done >= total


def make_teams(player_ids, size):
    """Tilfaeldige hold. Antallet er spillere // stoerrelse, og de der er
    tilovers fordeles ud - saa intet hold er mindre end den valgte
    stoerrelse, og ingen to hold er mere end en fra hinanden."""
    ids = list(player_ids)
    secrets.SystemRandom().shuffle(ids)
    count = max(1, len(ids) // size)
    teams = [[] for _ in range(count)]
    for i, pid in enumerate(ids):
        teams[i % count].append(pid)
    return teams


def start_problem(conn, lobby, players):
    """Hvorfor runden ikke kan starte endnu, eller None."""
    size = lobby["team_size"]
    need = 2 * size
    if len(players) < need:
        return ("Der skal være mindst {0} spillere, så der er to hold at stemme på."
                .format(need) if size > 1 else
                "Der skal være mindst 2 spillere.")
    available = genres_with_beats(conn)
    if not available:
        return "Der er ingen beats endnu. En admin skal lægge nogle op."
    if lobby["genre_id"] is not None and lobby["genre_id"] not in {r["id"] for r in available}:
        return "Den valgte genre har ingen beats. Vælg en anden, eller tilfældig."
    left = lobby["created_at"] + LOBBY_SECONDS - now()
    if left < REVEAL_SECONDS + lobby["minutes"] * 60 + 15 * 60:
        return ("Lobbyen udløber om {0} min. — der er ikke tid til en runde på {1} min. "
                "Lav en ny lobby.".format(max(0, int(left // 60)), lobby["minutes"]))
    return None


# --------------------------------------------------------------------
# Forsiden: opret og join
# --------------------------------------------------------------------

@app.get("/")
def home():
    return render_template("home.html", genres=genres_with_beats(db()),
                           code=norm_code(request.args.get("kode")),
                           minutes_range=MINUTES_RANGE, team_range=TEAM_RANGE,
                           name_max=NAME_MAX, code_len=CODE_LEN)


def read_settings(form):
    try:
        minutes = int(form.get("minutes", "30"))
        team_size = int(form.get("team_size", "1"))
    except ValueError:
        return None, "Tid og holdstørrelse skal være tal."
    if not MINUTES_RANGE[0] <= minutes <= MINUTES_RANGE[1]:
        return None, "Tiden skal være mellem {0} og {1} minutter.".format(*MINUTES_RANGE)
    if not TEAM_RANGE[0] <= team_size <= TEAM_RANGE[1]:
        return None, "Holdstørrelsen skal være mellem {0} og {1}.".format(*TEAM_RANGE)
    genre_id = None
    raw = (form.get("genre") or "").strip()
    if raw:
        try:
            genre_id = int(raw)
        except ValueError:
            return None, "Ukendt genre."
        if not db().execute("SELECT 1 FROM genres WHERE id = ?", (genre_id,)).fetchone():
            return None, "Ukendt genre."
    return {"minutes": minutes, "team_size": team_size, "genre_id": genre_id}, None


@app.post("/ny")
def create_lobby():
    if limited("create", client_ip()):
        return too_many("Du har oprettet mange lobbyer på kort tid. Vent lidt.")
    name = clean_text(request.form.get("name"), NAME_MAX)
    if not name:
        return fail(400, "Skriv et kaldenavn.", url_for("home"))
    settings, error = read_settings(request.form)
    if error:
        return fail(400, error, url_for("home"))

    conn = db()
    t = now()
    code = new_code(conn)
    cur = conn.execute(
        "INSERT INTO lobbies (code, minutes, team_size, genre_id, created_at)"
        " VALUES (?,?,?,?,?)",
        (code, settings["minutes"], settings["team_size"], settings["genre_id"], t))
    lid = cur.lastrowid
    pid = conn.execute(
        "INSERT INTO players (lobby_id, name, joined_at, seen_at) VALUES (?,?,?,?)",
        (lid, name, t, t)).lastrowid
    conn.execute("UPDATE lobbies SET owner_id = ? WHERE id = ?", (pid, lid))
    conn.commit()
    remember(code, pid)
    return redirect(url_for("lobby_page", code=code))


@app.post("/join")
def join():
    code = norm_code(request.form.get("code"))
    name = clean_text(request.form.get("name"), NAME_MAX)
    back = url_for("home", kode=code) if code else url_for("home")

    if limited("join", client_ip()):
        return too_many()
    if not code:
        return fail(400, "Koden er {0} tegn — bogstaver og tal.".format(CODE_LEN), back)

    conn = db()
    row = conn.execute("SELECT * FROM lobbies WHERE code = ?", (code,)).fetchone()
    if row is None:
        return fail(404, "Der er ingen lobby med koden {0}.".format(code), back)
    lobby = advance(row)

    if me_in(lobby) is not None:                      # allerede med
        return redirect(url_for("lobby_page", code=code))
    if lobby["status"] != "lobby":
        return fail(409, "Runden i {0} er allerede gået i gang.".format(code), back)
    if not name:
        return fail(400, "Skriv et kaldenavn.", back)

    count = conn.execute("SELECT COUNT(*) FROM players WHERE lobby_id = ?",
                         (lobby["id"],)).fetchone()[0]
    if count >= MAX_PLAYERS:
        return fail(409, "Lobbyen er fuld ({0} spillere).".format(MAX_PLAYERS), back)

    t = now()
    try:
        pid = conn.execute(
            "INSERT INTO players (lobby_id, name, joined_at, seen_at) VALUES (?,?,?,?)",
            (lobby["id"], name, t, t)).lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        return fail(409, "Navnet {0} er taget i den lobby. Vælg et andet.".format(name), back)
    remember(code, pid)
    return redirect(url_for("lobby_page", code=code))


# --------------------------------------------------------------------
# Lobbyen
# --------------------------------------------------------------------

def member_or_403(lobby):
    me = me_in(lobby)
    if me is None:
        abort(403)
    return me


def owner_or_403(lobby):
    me = member_or_403(lobby)
    if me["id"] != lobby["owner_id"]:
        abort(403)
    return me


@app.get("/l/<code>")
def lobby_page(code):
    lobby = lobby_or_404(code)
    me = me_in(lobby)
    if me is None:
        if lobby["status"] == "lobby":
            return redirect(url_for("home", kode=lobby["code"]))
        if lobby["status"] in ("done", "closed"):
            return render_template("error.html", title="Runden er slut",
                                   message="Lobbyen {0} er slut. Resultatet kan kun ses af dem "
                                           "der var med.".format(lobby["code"])), 409
        return render_template("error.html", title="Runden er i gang",
                               message="Lobbyen {0} er allerede gået i gang, så der kan "
                                       "ikke komme flere med.".format(lobby["code"])), 409
    return render_template("lobby.html", lobby=lobby, me=me,
                           genres=genres_with_beats(db()),
                           minutes_range=MINUTES_RANGE, team_range=TEAM_RANGE,
                           max_upload=MAX_UPLOAD)


@app.get("/l/<code>/state")
def lobby_state(code):
    lobby = lobby_or_404(code)
    me = me_in(lobby)
    if me is None:
        return json_error(403, "Du er ikke med i den lobby.", gone=True)
    conn = db()
    t = now()
    if t - me["seen_at"] > POLL_TOUCH_SECONDS:
        conn.execute("UPDATE players SET seen_at = ? WHERE id = ?", (t, me["id"]))
        conn.commit()
    return jsonify(state_for(conn, lobby, me, t))


def state_for(conn, lobby, me, t):
    lid = lobby["id"]
    phase = phase_of(lobby, t)
    is_owner = me["id"] == lobby["owner_id"]

    teams = {r["id"]: r["number"] for r in conn.execute(
        "SELECT id, number FROM teams WHERE lobby_id = ?", (lid,))}
    players = conn.execute(
        "SELECT * FROM players WHERE lobby_id = ? ORDER BY joined_at", (lid,)).fetchall()
    my_team = me["team_id"]

    genre = None
    if lobby["genre_id"] is not None:
        row = conn.execute("SELECT name FROM genres WHERE id = ?", (lobby["genre_id"],)).fetchone()
        genre = row["name"] if row else None

    state = {
        "phase": phase,
        "now": ms(t),
        "code": lobby["code"],
        "expires": ms(lobby["created_at"] + LOBBY_SECONDS),
        "me": {"id": me["id"], "name": me["name"], "owner": is_owner,
               "team": teams.get(my_team)},
        "settings": {"minutes": lobby["minutes"], "team_size": lobby["team_size"],
                     "genre": genre, "genre_id": lobby["genre_id"]},
        "players": [{
            "id": p["id"], "name": p["name"],
            "owner": p["id"] == lobby["owner_id"],
            "me": p["id"] == me["id"],
            "online": t - p["seen_at"] < ONLINE_SECONDS,
            "team": teams.get(p["team_id"]),
        } for p in players],
    }

    if phase == "lobby":
        state["start_problem"] = start_problem(conn, lobby, players)
        return state

    if phase == "closed":
        return state

    # Fra reveal og frem: genren, beatet og holdene er trukket.
    members = {}
    for p in players:
        if p["team_id"] in teams:
            members.setdefault(teams[p["team_id"]], []).append(p["name"])
    state["round"] = {
        "genre": lobby["genre_name"],
        "beat": lobby["beat_title"],
        "beat_url": url_for("beat_file", code=lobby["code"]) if lobby["beat_id"] else None,
        "make_at": ms(lobby["make_at"]),
        "deadline": ms(lobby["deadline"]),
        "grace": GRACE_SECONDS,
        "minutes": lobby["minutes"],
        "teams": [{"number": n, "members": members.get(n, []),
                   "mine": n == teams.get(my_team)} for n in sorted(set(teams.values()))],
        "solo": lobby["team_size"] == 1,
    }

    subs = conn.execute(
        "SELECT s.*, p.name AS by_name FROM submissions s"
        " LEFT JOIN players p ON p.id = s.player_id WHERE s.lobby_id = ?", (lid,)).fetchall()

    if phase in ("reveal", "make"):
        mine = next((s for s in subs if s["team_id"] == my_team), None)
        state["make"] = {
            "delivered": sorted(teams[s["team_id"]] for s in subs),
            "teams": len(teams),
            "mine": None if mine is None else {
                "at": ms(mine["uploaded_at"]), "by": mine["by_name"],
                "seconds": round(mine["seconds"], 1), "size": mine["size"],
                "url": url_for("track_file", code=lobby["code"], sid=mine["id"]),
            },
            "max_bytes": MAX_UPLOAD,
            "upload_url": url_for("submit", code=lobby["code"]),
        }
        return state

    if phase == "vote":
        mine = {r["submission_id"]: r["flames"] for r in conn.execute(
            "SELECT submission_id, flames FROM votes WHERE player_id = ?", (me["id"],))}
        done, total, per = vote_progress(conn, lid)
        have, need = per.get(me["id"], (0, 0))
        state["vote"] = {
            "tracks": [{
                "id": s["id"],
                "label": "Nummer {0}".format(s["position"]),
                "url": url_for("track_file", code=lobby["code"], sid=s["id"]),
                "seconds": round(s["seconds"], 1),
                "mine": s["team_id"] == my_team,
                "flames": mine.get(s["id"]),
            } for s in sorted(subs, key=lambda s: s["position"] or 0)],
            "done_players": done,
            "voters": total,
            "my_have": have,
            "my_need": need,
            "vote_url": url_for("vote", code=lobby["code"]),
        }
        return state

    # Resultat. Navnene kommer frem.
    scores = {r["submission_id"]: (r["total"], r["n"]) for r in conn.execute(
        "SELECT v.submission_id, SUM(v.flames) AS total, COUNT(*) AS n FROM votes v"
        " JOIN submissions s ON s.id = v.submission_id WHERE s.lobby_id = ?"
        " GROUP BY v.submission_id", (lid,))}
    ranked = []
    for s in subs:
        total, n = scores.get(s["id"], (0, 0))
        ranked.append({
            "id": s["id"],
            "label": "Nummer {0}".format(s["position"]) if s["position"] else "",
            "team": teams[s["team_id"]],
            "members": members.get(teams[s["team_id"]], []),
            "by": s["by_name"],
            "average": round(total / n, 2) if n else None,
            "votes": n,
            "seconds": round(s["seconds"], 1),
            "url": url_for("track_file", code=lobby["code"], sid=s["id"]),
            "mine": s["team_id"] == my_team,
        })
    ranked.sort(key=lambda r: (r["average"] is None, -(r["average"] or 0), -r["votes"]))
    place, last = 0, object()
    for i, r in enumerate(ranked, start=1):
        if r["average"] != last:                      # lige gennemsnit, samme plads
            place, last = i, r["average"]
        r["place"] = place if r["average"] is not None else None
    delivered = {teams[s["team_id"]] for s in subs}
    state["result"] = {
        "ranking": ranked,
        "missing": [{"team": n, "members": members.get(n, [])}
                    for n in sorted(set(teams.values())) if n not in delivered],
    }
    return state


@app.post("/l/<code>/indstillinger")
def lobby_settings(code):
    lobby = lobby_or_404(code)
    owner_or_403(lobby)
    back = url_for("lobby_page", code=lobby["code"])
    if lobby["status"] != "lobby":
        return fail(409, "Runden er startet — indstillingerne er låst.", back)
    settings, error = read_settings(request.form)
    if error:
        return fail(400, error, back)
    db().execute("UPDATE lobbies SET minutes = ?, team_size = ?, genre_id = ? WHERE id = ?",
                 (settings["minutes"], settings["team_size"], settings["genre_id"], lobby["id"]))
    db().commit()
    return redirect(back)


@app.post("/l/<code>/smid-ud")
def kick(code):
    lobby = lobby_or_404(code)
    me = owner_or_403(lobby)
    back = url_for("lobby_page", code=lobby["code"])
    if lobby["status"] != "lobby":
        return fail(409, "Man kan kun fjerne spillere før runden starter.", back)
    try:
        target = int(request.form.get("player", ""))
    except ValueError:
        abort(400)
    if target == me["id"]:
        return fail(400, "Du kan ikke smide dig selv ud. Forlad lobbyen i stedet.", back)
    db().execute("DELETE FROM players WHERE id = ? AND lobby_id = ?", (target, lobby["id"]))
    db().commit()
    return redirect(back)


@app.post("/l/<code>/forlad")
def leave(code):
    lobby = lobby_or_404(code)
    me = member_or_403(lobby)
    conn = db()
    if lobby["status"] in ("running", "voting"):
        # Man kan ikke forlade en runde: holdet og stemmerne skal staa ved
        # magt, og uden cookien kunne man ikke komme ind igen.
        return fail(409, "Runden er i gang. Du kan lukke fanen og komme tilbage "
                         "med samme browser.", url_for("lobby_page", code=lobby["code"]))
    if lobby["status"] == "lobby":
        conn.execute("DELETE FROM players WHERE id = ?", (me["id"],))
        if me["id"] == lobby["owner_id"]:
            # Ejerskabet gaar videre til den der har vaeret laengst med.
            heir = conn.execute("SELECT id FROM players WHERE lobby_id = ? ORDER BY joined_at LIMIT 1",
                                (lobby["id"],)).fetchone()
            if heir:
                conn.execute("UPDATE lobbies SET owner_id = ? WHERE id = ?", (heir["id"], lobby["id"]))
            else:
                conn.execute("DELETE FROM lobbies WHERE id = ?", (lobby["id"],))
        conn.commit()
    # Efter runden bliver spilleren staaende i resultatet. Cookien glemmer
    # bare lobbyen.
    mine = dict(session.get("p", {}))
    mine.pop(lobby["code"], None)
    session["p"] = mine
    return redirect(url_for("home"))


@app.post("/l/<code>/start")
def start(code):
    lobby = lobby_or_404(code)
    owner_or_403(lobby)
    back = url_for("lobby_page", code=lobby["code"])
    conn = db()
    if lobby["status"] != "lobby":
        return fail(409, "Runden er allerede startet.", back)

    players = conn.execute("SELECT * FROM players WHERE lobby_id = ? ORDER BY joined_at",
                           (lobby["id"],)).fetchall()
    problem = start_problem(conn, lobby, players)
    if problem:
        return fail(409, problem, back)

    rng = secrets.SystemRandom()
    if lobby["genre_id"] is None:
        genre = rng.choice(genres_with_beats(conn))
    else:
        genre = conn.execute("SELECT id, name FROM genres WHERE id = ?", (lobby["genre_id"],)).fetchone()
    beat = rng.choice(conn.execute("SELECT * FROM beats WHERE genre_id = ?", (genre["id"],)).fetchall())

    t = now()
    make_at = t + REVEAL_SECONDS
    won = conn.execute(
        "UPDATE lobbies SET status = 'running', beat_id = ?, genre_name = ?, beat_title = ?,"
        " started_at = ?, make_at = ?, deadline = ? WHERE id = ? AND status = 'lobby'",
        (beat["id"], genre["name"], beat["title"], t, make_at,
         make_at + lobby["minutes"] * 60, lobby["id"])).rowcount
    if not won:                                       # dobbeltklik
        conn.rollback()
        return redirect(back)
    for number, members in enumerate(make_teams([p["id"] for p in players], lobby["team_size"]), 1):
        tid = conn.execute("INSERT INTO teams (lobby_id, number) VALUES (?, ?)",
                           (lobby["id"], number)).lastrowid
        conn.executemany("UPDATE players SET team_id = ? WHERE id = ?", [(tid, pid) for pid in members])
    conn.commit()
    return redirect(back)


@app.post("/l/<code>/afslut")
def finish_phase(code):
    """Ejeren kan lukke afleveringen eller afstemningen foer tid - fx
    naar alle er klar, eller en spiller er gaaet hjem uden at stemme."""
    lobby = lobby_or_404(code)
    owner_or_403(lobby)
    back = url_for("lobby_page", code=lobby["code"])
    conn = db()
    phase = phase_of(lobby)
    t = now()
    if phase in ("reveal", "make"):
        conn.execute("UPDATE lobbies SET deadline = ? WHERE id = ? AND status = 'running'",
                     (t - GRACE_SECONDS, lobby["id"]))
    elif phase == "vote":
        conn.execute("UPDATE lobbies SET status = 'done', done_at = ? WHERE id = ? AND status = 'voting'",
                     (t, lobby["id"]))
    else:
        return fail(409, "Der er ikke noget at afslutte lige nu.", back)
    conn.commit()
    advance(conn.execute("SELECT * FROM lobbies WHERE id = ?", (lobby["id"],)).fetchone())
    return redirect(back)


# --------------------------------------------------------------------
# Beat, aflevering, afstemning
# --------------------------------------------------------------------

def safe_download_name(*parts):
    text = " - ".join(p for p in parts if p)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9 ._-]+", "", text).strip()
    return text[:120] or "beat"


@app.get("/l/<code>/beat")
def beat_file(code):
    lobby = lobby_or_404(code)
    member_or_403(lobby)
    if lobby["status"] not in ("running", "voting", "done") or not lobby["beat_id"]:
        abort(404)
    beat = db().execute("SELECT * FROM beats WHERE id = ?", (lobby["beat_id"],)).fetchone()
    if beat is None:
        abort(404)
    download = request.args.get("hent") == "1"
    return send_file(
        BEATS / beat["file"], conditional=True,
        mimetype="audio/mpeg" if beat["kind"] == "mp3" else "audio/wav",
        as_attachment=download,
        download_name=safe_download_name("Songwars", lobby["genre_name"], beat["title"])
        + "." + beat["kind"] if download else None)


def save_upload(storage, limit):
    """Stream til en midlertidig fil, tjek indholdet, og flyt den ind under
    et tilfaeldigt navn. Brugerens filnavn roeres aldrig - det bliver
    hverken brugt paa disken eller gemt."""
    tmp = TMP / (secrets.token_hex(16) + ".part")
    try:
        storage.save(tmp)
        if tmp.stat().st_size > limit:
            raise audio.NotAudio("Filen er over {0} MB.".format(limit // (1024 * 1024)))
        kind, _mime, seconds = audio.inspect(tmp)
    except Exception:
        remove_file(tmp)
        raise
    return tmp, kind, seconds, tmp.stat().st_size


@app.post("/l/<code>/aflever")
def submit(code):
    lobby = lobby_or_404(code)
    me = member_or_403(lobby)
    back = url_for("lobby_page", code=lobby["code"])

    if limited("upload_ip", client_ip()) or limited("upload_player", me["id"]):
        return too_many("Du har afleveret mange gange på kort tid. Vent lidt.")
    if me["team_id"] is None:
        return fail(409, "Du er ikke på et hold i den her runde.", back)
    if lobby["status"] != "running":
        return fail(409, "Afleveringen er lukket.", back)
    if now() > lobby["deadline"] + GRACE_SECONDS:
        return fail(409, "Tiden er gået. Afleveringen er lukket.", back)

    storage = request.files.get("track")
    if storage is None or not storage.filename:
        return fail(400, "Vælg en mp3- eller wav-fil.", back)
    try:
        tmp, kind, seconds, size = save_upload(storage, MAX_UPLOAD)
    except audio.NotAudio as exc:
        return fail(415, str(exc), back)

    sid = secrets.token_hex(16)
    final = sid + "." + kind
    conn = db()
    # Fristen tjekkes igen inde i transaktionen. Ejeren kan have lukket
    # afleveringen mens filen blev sendt.
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    fresh = conn.execute("SELECT status, deadline FROM lobbies WHERE id = ?", (lobby["id"],)).fetchone()
    if fresh["status"] != "running" or now() > fresh["deadline"] + GRACE_SECONDS:
        conn.rollback()
        remove_file(tmp)
        return fail(409, "Tiden løb ud mens filen blev sendt. Afleveringen er lukket.", back)

    old = conn.execute("SELECT file FROM submissions WHERE team_id = ?", (me["team_id"],)).fetchone()
    os.replace(tmp, SUBS / final)
    conn.execute("DELETE FROM submissions WHERE team_id = ?", (me["team_id"],))
    conn.execute(
        "INSERT INTO submissions (id, lobby_id, team_id, file, kind, size, seconds, player_id, uploaded_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (sid, lobby["id"], me["team_id"], final, kind, size, seconds, me["id"], now()))
    conn.commit()
    if old:
        remove_file(SUBS / old["file"])               # det forrige nummer er erstattet

    if wants_json():
        return jsonify({"ok": True, "seconds": round(seconds, 1)})
    flash("Afleveret. Et nyt upload fra holdet erstatter det her.")
    return redirect(back)


@app.get("/l/<code>/spor/<sid>")
def track_file(code, sid):
    """Afspilning. Under afstemningen er der intet i svaret der siger hvem
    der lavede nummeret - ingen filnavne, kun et tilfaeldigt id."""
    lobby = lobby_or_404(code)
    me = member_or_403(lobby)
    if not HEX_RE.fullmatch(sid):
        abort(404)
    sub = db().execute("SELECT * FROM submissions WHERE id = ? AND lobby_id = ?",
                       (sid, lobby["id"])).fetchone()
    if sub is None:
        abort(404)
    status = lobby["status"]
    own = sub["team_id"] == me["team_id"]
    if not (status in ("voting", "done") or (status == "running" and own)):
        abort(403)
    # Neutralt navn: send_file ville ellers saette filnavnet paa disken i
    # Content-Disposition. Det er tilfaeldigt, men intet er bedre end noget.
    return send_file(SUBS / sub["file"], conditional=True,
                     mimetype="audio/mpeg" if sub["kind"] == "mp3" else "audio/wav",
                     download_name="nummer." + sub["kind"])


@app.post("/l/<code>/stem")
def vote(code):
    lobby = lobby_or_404(code)
    me = member_or_403(lobby)
    if limited("vote", me["id"]):
        return too_many()
    if lobby["status"] != "voting":
        return json_error(409, "Afstemningen er lukket.")
    data = request.get_json(silent=True) or {}
    sid, flames = str(data.get("track", "")), data.get("flames")
    if not isinstance(flames, int) or isinstance(flames, bool) or not 1 <= flames <= 5:
        return json_error(400, "Giv mellem 1 og 5 flammer.")
    conn = db()
    sub = conn.execute("SELECT team_id FROM submissions WHERE id = ? AND lobby_id = ?",
                       (sid, lobby["id"])).fetchone()
    if sub is None:
        return json_error(404, "Nummeret findes ikke.")
    if me["team_id"] is None:
        return json_error(403, "Du var ikke med i runden.")
    if sub["team_id"] == me["team_id"]:
        return json_error(403, "Du kan ikke stemme på dit eget hold.")
    conn.execute(
        "INSERT INTO votes (submission_id, player_id, flames, at) VALUES (?,?,?,?)"
        " ON CONFLICT (submission_id, player_id) DO UPDATE SET flames = excluded.flames, at = excluded.at",
        (sid, me["id"], flames, now()))
    conn.commit()
    advance(lobby)                                    # den sidste stemme afslutter runden
    return jsonify({"ok": True})


# --------------------------------------------------------------------
# Admin: genrer og beats
# --------------------------------------------------------------------

ADMIN_SECONDS = 12 * 3600


def is_admin():
    at = session.get("admin")
    return isinstance(at, (int, float)) and now() - at < ADMIN_SECONDS


def admin_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not is_admin():
            return redirect(url_for("admin"))
        return view(*a, **kw)
    return wrapped


@app.get("/admin")
def admin():
    if not is_admin():
        return render_template("admin_login.html")
    conn = db()
    genres = conn.execute(
        "SELECT g.*, COUNT(b.id) AS beats FROM genres g LEFT JOIN beats b ON b.genre_id = g.id"
        " GROUP BY g.id ORDER BY g.name").fetchall()
    beats = conn.execute(
        "SELECT b.*, g.name AS genre FROM beats b JOIN genres g ON g.id = b.genre_id"
        " ORDER BY g.name, b.title").fetchall()
    stats = {
        "lobbies": conn.execute("SELECT COUNT(*) FROM lobbies WHERE status IN ('lobby','running','voting')").fetchone()[0],
        "subs": conn.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM submissions").fetchone(),
    }
    return render_template("admin.html", genres=genres, beats=beats, stats=stats,
                           max_beat=MAX_BEAT, keep_days=KEEP_SECONDS // 86400)


@app.post("/admin/login")
def admin_login():
    if limited("admin", client_ip()):
        return too_many("For mange forkerte forsøg. Vent et kvarter.")
    sent = request.form.get("password", "")
    if not hmac.compare_digest(sent.encode(), ADMIN_PASSWORD.encode()):
        flash("Forkert adgangskode.")
        return redirect(url_for("admin"))
    # Ny cookie-identitet ved login, men behold de lobbyer man er med i.
    players = session.get("p", {})
    session.clear()
    session["p"] = players
    session["admin"] = now()
    session.permanent = True
    return redirect(url_for("admin"))


@app.post("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("home"))


@app.post("/admin/genre")
@admin_required
def admin_genre_create():
    name = clean_text(request.form.get("name"), 40)
    if not name:
        return fail(400, "Genren skal have et navn.", url_for("admin"))
    try:
        db().execute("INSERT INTO genres (name, created_at) VALUES (?, ?)", (name, now()))
        db().commit()
    except sqlite3.IntegrityError:
        return fail(409, "Genren {0} findes allerede.".format(name), url_for("admin"))
    return redirect(url_for("admin"))


@app.post("/admin/genre/<int:gid>/omdoeb")
@admin_required
def admin_genre_rename(gid):
    name = clean_text(request.form.get("name"), 40)
    if not name:
        return fail(400, "Genren skal have et navn.", url_for("admin"))
    try:
        db().execute("UPDATE genres SET name = ? WHERE id = ?", (name, gid))
        db().commit()
    except sqlite3.IntegrityError:
        return fail(409, "Der er allerede en genre der hedder {0}.".format(name), url_for("admin"))
    return redirect(url_for("admin"))


@app.post("/admin/genre/<int:gid>/slet")
@admin_required
def admin_genre_delete(gid):
    conn = db()
    if conn.execute("SELECT 1 FROM beats WHERE genre_id = ?", (gid,)).fetchone():
        return fail(409, "Genren har beats. Slet dem først.", url_for("admin"))
    conn.execute("DELETE FROM genres WHERE id = ?", (gid,))
    # Lobbyer der havde valgt den, falder tilbage til tilfaeldig.
    conn.execute("UPDATE lobbies SET genre_id = NULL WHERE genre_id = ? AND status = 'lobby'", (gid,))
    conn.commit()
    return redirect(url_for("admin"))


@app.post("/admin/beat")
@admin_required
def admin_beat_create():
    title = clean_text(request.form.get("title"), TITLE_MAX)
    try:
        gid = int(request.form.get("genre", ""))
    except ValueError:
        gid = None
    if not title:
        return fail(400, "Beatet skal have en titel.", url_for("admin"))
    if gid is None or not db().execute("SELECT 1 FROM genres WHERE id = ?", (gid,)).fetchone():
        return fail(400, "Vælg en genre.", url_for("admin"))
    storage = request.files.get("file")
    if storage is None or not storage.filename:
        return fail(400, "Vælg en mp3- eller wav-fil.", url_for("admin"))
    try:
        tmp, kind, seconds, size = save_upload(storage, MAX_BEAT)
    except audio.NotAudio as exc:
        return fail(415, str(exc), url_for("admin"))
    bid = secrets.token_hex(16)
    os.replace(tmp, BEATS / (bid + "." + kind))
    db().execute(
        "INSERT INTO beats (id, genre_id, title, file, kind, size, seconds, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (bid, gid, title, bid + "." + kind, kind, size, seconds, now()))
    db().commit()
    flash("{0} er lagt op ({1}).".format(title, clock(seconds)))
    return redirect(url_for("admin"))


@app.post("/admin/beat/<bid>/slet")
@admin_required
def admin_beat_delete(bid):
    conn = db()
    beat = conn.execute("SELECT * FROM beats WHERE id = ?", (bid,)).fetchone()
    if beat is None:
        abort(404)
    busy = conn.execute(
        "SELECT COUNT(*) FROM lobbies WHERE beat_id = ? AND status IN ('running', 'voting')",
        (bid,)).fetchone()[0]
    if busy:
        return fail(409, "{0} bruges i en runde lige nu. Prøv igen når den er slut."
                    .format(beat["title"]), url_for("admin"))
    conn.execute("DELETE FROM beats WHERE id = ?", (bid,))
    conn.commit()
    remove_file(BEATS / beat["file"])
    return redirect(url_for("admin"))


@app.get("/admin/beat/<bid>/lyd")
@admin_required
def admin_beat_audio(bid):
    beat = db().execute("SELECT * FROM beats WHERE id = ?", (bid,)).fetchone()
    if beat is None:
        abort(404)
    return send_file(BEATS / beat["file"], conditional=True,
                     mimetype="audio/mpeg" if beat["kind"] == "mp3" else "audio/wav")


# --------------------------------------------------------------------
# Drift og fejl
# --------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    db().execute("SELECT 1").fetchone()
    return "ok", 200, {"Content-Type": "text/plain"}


@app.errorhandler(RequestEntityTooLarge)
def too_large(_e):
    limit = MAX_BEAT if request.endpoint == "admin_beat_create" else MAX_UPLOAD
    message = "Filen er for stor. Grænsen er {0} MB.".format(limit // (1024 * 1024))
    if wants_json():
        return json_error(413, message)
    return render_template("error.html", title="For stor fil", message=message), 413


@app.errorhandler(403)
def forbidden(_e):
    if wants_json() or request.path.endswith("/state"):
        return json_error(403, "Det har du ikke adgang til.")
    return render_template("error.html", title="Ingen adgang",
                           message="Det kan kun lobbyens ejer — eller dem der er med."), 403


@app.errorhandler(404)
def not_found(_e):
    if wants_json():
        return json_error(404, "Findes ikke.")
    return render_template("error.html", title="Findes ikke",
                           message="Lobbyen eller siden findes ikke — måske er den udløbet."), 404


# --------------------------------------------------------------------
# Opstart
# --------------------------------------------------------------------

def setup():
    check_config()
    init_db()


if __name__ == "__main__":
    args = sys.argv[1:]
    setup()
    if args[:1] == ["ryd"]:
        conn = connect()
        lobbies, files = cleanup(conn)
        conn.close()
        print("Slettet: {0} lobbyer, {1} filer.".format(lobbies, files))
    else:
        app.config["SESSION_COOKIE_SECURE"] = False   # udviklingsserveren er http
        app.run(host="127.0.0.1", port=8092, debug="--debug" in args)
else:
    setup()
