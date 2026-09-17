#!/usr/bin/env python3
"""
EBVB - privat lytterum.

To sektioner: Beats og Music. Man laegger et spor op med et cover, og det
ligger i en liste sammen med hvem der lagde det op. Ikke mapper.

    python app.py init                 opret database og mapper
    python app.py adduser <navn>       opret bruger (spoerger om kodeord)
    python app.py adduser <navn> --admin
    python app.py passwd <navn>        skift kodeord
    python app.py users                vis brugere
    python app.py import <mappe> <beats|music> <bruger>
                                       hent en mappe med lydfiler ind
    python app.py filnavn <navn> ...   vis hvad der laeses ud af et filnavn
    python app.py                      start udviklingsserver paa :8090

I drift koeres den med gunicorn bag nginx. Se README.md.

Afhaengigheder: Flask. Intet andet. Databasen er SQLite, filerne ligger
paa disken i EBVB_DATA (default ./data).
"""

import getpass
import os
import re
import secrets
import shutil
import sqlite3
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path

from flask import (
    Flask, abort, flash, g, jsonify, redirect, render_template, request,
    send_file, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

# --------------------------------------------------------------------
# Opsaetning
# --------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("EBVB_DATA", ROOT / "data"))
MEDIA = DATA / "media"
COVERS = DATA / "covers"
AVATARS = DATA / "avatars"
PARTS = DATA / "tmp"                   # halve filer fra mappe-upload
DB_PATH = DATA / "ebvb.db"

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aif", ".aiff"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}

# Mappe-upload tager kun de to. Alt andet i mappen ignoreres.
FOLDER_EXT = {".mp3", ".wav"}

# Slug -> label. Raekkefoelgen her er raekkefoelgen i fanerne.
SECTIONS = {"beats": "Beats", "music": "Music"}

MAX_BYTES = 512 * 1024 * 1024          # skal matche client_max_body_size i nginx
MAX_COVER_BYTES = 12 * 1024 * 1024

# Mappe-upload sender hver fil i bidder af denne stoerrelse. Cloudflare
# afviser request bodies over 100 MB paa gratis-planen, saa en 300 MB
# wav i et stykke kommer aldrig igennem. I bidder gaar den, og hverken
# Cloudflare eller client_max_body_size i nginx skal roeres.
CHUNK_BYTES = 32 * 1024 * 1024
PART_MAX_AGE = 24 * 60 * 60            # opgivne halve filer ryddes efter et doegn

HEX32 = re.compile(r"[0-9a-f]{32}")

MONTHS = ("jan", "feb", "mar", "apr", "maj", "jun",
          "jul", "aug", "sep", "okt", "nov", "dec")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES
# Gennemse-tabellen sender fire felter pr. nummer. Flask afviser som
# standard formularer med over 1000 felter - det er 250 numre.
app.config["MAX_FORM_PARTS"] = 40_000
app.config["MAX_FORM_MEMORY_SIZE"] = 8 * 1024 * 1024
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Saettes af nginx-opsaetningen. Slaa fra lokalt over http med
    # EBVB_INSECURE_COOKIE=1, ellers kan man ikke logge ind paa localhost.
    SESSION_COOKIE_SECURE=os.environ.get("EBVB_INSECURE_COOKIE") != "1",
)


def secret_key():
    """Noeglen skal overleve genstart, ellers ryger alle logins."""
    env = os.environ.get("EBVB_SECRET_KEY")
    if env:
        return env
    path = DATA / "secret_key"
    if not path.exists():
        DATA.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass                       # Windows. Ligegyldigt lokalt.
    return path.read_text(encoding="utf-8").strip()


# --------------------------------------------------------------------
# Database
# --------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash    TEXT NOT NULL,
    is_admin   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    avatar_file TEXT NOT NULL DEFAULT '',
    bio         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tracks (
    id          TEXT PRIMARY KEY,
    section     TEXT NOT NULL,
    title       TEXT NOT NULL,
    bpm         TEXT NOT NULL DEFAULT '',
    mkey        TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    audio_file  TEXT NOT NULL,
    audio_name  TEXT NOT NULL,
    audio_size  INTEGER NOT NULL,
    cover_file  TEXT NOT NULL DEFAULT '',
    uploader_id INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL,
    batch_id    TEXT NOT NULL DEFAULT '',
    parsed      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS tracks_section ON tracks(section, created_at DESC);
"""


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# Kolonner der er kommet til efter foerste udgave. CREATE TABLE IF NOT
# EXISTS roerer ikke en tabel der allerede findes, saa de skal tilfoejes
# her - ellers gaar en eksisterende database i stykker ved opgradering.
#
# tracks.batch_id  hvilken mappe-upload sporet kom med ('' = enkeltfil)
# tracks.parsed    hvad filnavnet gav:
#                    ''        lagt op som enkeltfil, ikke laest
#                    'fuld'    titel, BPM og toneart
#                    'delvis'  noget, men ikke det hele
#                    'ingen'   intet - filnavnet er brugt som titel
#                    'rettet'  var delvis/ingen, og er gennemset siden
LATER_COLUMNS = (
    ("users", "avatar_file", "TEXT NOT NULL DEFAULT ''"),
    ("users", "bio", "TEXT NOT NULL DEFAULT ''"),
    ("tracks", "batch_id", "TEXT NOT NULL DEFAULT ''"),
    ("tracks", "parsed", "TEXT NOT NULL DEFAULT ''"),
)


def migrate(conn):
    for table, column, ddl in LATER_COLUMNS:
        have = {r[1] for r in conn.execute("PRAGMA table_info({0})".format(table))}
        if column not in have:
            try:
                conn.execute("ALTER TABLE {0} ADD COLUMN {1} {2}".format(table, column, ddl))
            except sqlite3.OperationalError as exc:
                # gunicorn starter to workers samtidig, og begge kan naa
                # at se kolonnen mangle. Den anden faar saa denne fejl.
                if "duplicate column" not in str(exc):
                    raise
    # Indekset kan foerst laves naar kolonnen findes.
    conn.execute("CREATE INDEX IF NOT EXISTS tracks_batch ON tracks(batch_id)")


def init_storage():
    for folder in (DATA, MEDIA, COVERS, AVATARS, PARTS):
        folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.commit()
    conn.close()


# --------------------------------------------------------------------
# Smaating
# --------------------------------------------------------------------

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dk_date(iso):
    """2026-09-01T... -> 1. sep 2026"""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    return "{0}. {1} {2}".format(dt.day, MONTHS[dt.month - 1], dt.year)


def human_size(n):
    if n >= 1024 ** 3:
        return "{0:.1f} GB".format(n / 1024 ** 3).replace(".", ",")
    if n >= 1024 ** 2:
        return "{0:.0f} MB".format(n / 1024 ** 2)
    return "{0:.0f} KB".format(max(n / 1024, 1))


def ext_of(filename):
    return Path(filename or "").suffix.lower()


def clean_name(filename):
    """Original-filnavnet gemmes kun til download. Ingen sti, ingen styretegn."""
    name = Path(filename or "").name
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[\x00-\x1f\x7f\"\\/]", "", name).strip()
    return name[:180] or "fil"


def current_user():
    if "user" not in g:
        g.user = None
        uid = session.get("uid")
        if uid is not None:
            g.user = db().execute(
                "SELECT * FROM users WHERE id = ?", (uid,)
            ).fetchone()
            if g.user is None:
                session.clear()
    return g.user


_ASSET_V = {}


def asset(filename):
    """Statisk fil med ?v=<hash> bag URL'en.

    Uden den serverer browseren - og Cloudflare - den gamle styles.css
    efter et deploy, og siden ser uaendret ud selvom koden er ny.
    Hashen regnes en gang pr. proces; imaget bygges om ved hvert deploy,
    saa den folger med af sig selv.
    """
    if filename not in _ASSET_V:
        try:
            digest = sha1((ROOT / "static" / filename).read_bytes()).hexdigest()[:8]
        except OSError:
            digest = "0"
        _ASSET_V[filename] = digest
    return "{0}?v={1}".format(
        url_for("static", filename=filename), _ASSET_V[filename])


@app.context_processor
def inject():
    return {
        "user": current_user(),
        "sections": SECTIONS,
        "dk_date": dk_date,
        "human_size": human_size,
        "asset": asset,
    }


def login_required(view):
    def wrapped(*a, **kw):
        if current_user() is None:
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    wrapped.__name__ = view.__name__
    return wrapped


def api_login_required(view):
    """Som login_required, men til de ruter JavaScript kalder. Et
    redirect til /login ville komme tilbage som en HTML-side med status
    200, og saa ligner et udloebet login en upload der lykkedes."""
    def wrapped(*a, **kw):
        if current_user() is None:
            return json_error(401, "Du er blevet logget ud. Log ind i en ny "
                                   "fane, og prøv igen herfra.")
        return view(*a, **kw)
    wrapped.__name__ = view.__name__
    return wrapped


def json_error(status, message, **extra):
    body = {"error": message}
    body.update(extra)
    return jsonify(body), status


def can_edit(track, user):
    return track["uploader_id"] == user["id"] or bool(user["is_admin"])


# --------------------------------------------------------------------
# Metadata fra filnavn
#
# Numrene hedder fx "Midnight Drive 95BPM Fm.wav". Parseren er med vilje
# tolerant, men den gaetter hellere for lidt end forkert: en toneart der
# ogsaa kan vaere et ord ("I Am Legend") godtages kun hvis den staar ved
# siden af BPM'en eller til sidst i navnet. Det der ikke kan laeses,
# bliver tomt og markeret - intet afvises.
# --------------------------------------------------------------------

_SEPS = " \t_-–—.,;:|+()[]{}"

_BPM_SUFFIX = re.compile(
    r"(?<!\d)(?P<v>\d{2,3}(?:[.,]\d{1,2})?)[\s_\-]*bpm(?![a-z])", re.I)
_BPM_PREFIX = re.compile(
    r"(?<![a-z])bpm[\s_\-:=]*(?P<v>\d{2,3}(?:[.,]\d{1,2})?)(?!\d)", re.I)
_BPM_BARE = re.compile(
    r"(?<![A-Za-z0-9#])(?<!\d[.,])(?P<v>\d{2,3})(?![A-Za-z0-9]|[.,]\d)")

_KEY = re.compile(
    r"(?<![A-Za-z0-9#♯♭])"
    r"(?P<note>[A-Ga-g])"
    r"(?P<acc>[#♯♭]|b|[\s_\-]?(?:sharp|flat))?"
    r"(?:[\s_\-]?(?P<q>minor|moll|mol|min|major|maj|dur|m))?"
    r"(?![A-Za-z#♯♭])",
    re.I)

_MINOR = {"m", "min", "minor", "moll", "mol"}
_LONG_Q = {"min", "minor", "moll", "mol", "maj", "major", "dur"}

STRONG, MEDIUM, WEAK = 3, 2, 1


def _only_seps(text):
    return all(ch in _SEPS for ch in text)


def _key_text(note, acc, q):
    acc = (acc or "").strip(" _-").lower()
    sign = "#" if acc in ("#", "♯", "sharp") else "b" if acc in ("b", "♭", "flat") else ""
    return note.upper() + sign + ("m" if q and q.lower() in _MINOR else "")


def _key_strength(m):
    note, acc, q = m.group("note"), m.group("acc"), m.group("q")
    if q == "M":
        return 0                        # "FM" er radio, ikke F-mol
    if q and q.lower() in _LONG_Q:
        return STRONG                   # "f minor", "Fmin", "Eb dur"
    if acc:
        if note.isupper() or q:
            return STRONG               # "F#", "Bb", "f#m"
        return WEAK if acc.strip(" _-") in ("#", "♯") else 0   # "db" er decibel
    if q:
        return MEDIUM if note.isupper() else WEAK                    # "Fm" / "fm"
    return WEAK if note.isupper() else 0                             # "F" / "f"


def _number(text):
    value = float(text.replace(",", "."))
    return value, ("{0:g}".format(value))


def parse_filename(filename, username=""):
    """Filnavn -> {title, bpm, mkey, parsed}. Se kommentaren ovenfor."""
    stem = Path(unicodedata.normalize("NFC", filename or "")).stem
    stem = stem.strip() or "Uden titel"

    # --- BPM ---
    explicit = []
    for rx in (_BPM_SUFFIX, _BPM_PREFIX):
        for m in rx.finditer(stem):
            value, text = _number(m.group("v"))
            if 20 <= value <= 400:
                explicit.append((m.start(), m.end(), text))

    keys = []
    for m in _KEY.finditer(stem):
        strength = _key_strength(m)
        if strength:
            keys.append((m.start(), m.end(), strength,
                         _key_text(m.group("note"), m.group("acc"), m.group("q"))))

    bpm = None
    if explicit:
        bpm = max(explicit)             # den sidste i navnet
    else:
        bare = []
        for m in _BPM_BARE.finditer(stem):
            if 50 <= int(m.group("v")) <= 220:
                bare.append((m.start(), m.end(), str(int(m.group("v")))))
        if bare:
            # Et tal ved siden af en toneart er naesten sikkert BPM'en.
            near = [b for b in bare for k in keys if k[2] >= MEDIUM and (
                _only_seps(stem[b[1]:k[0]]) if b[1] <= k[0] else _only_seps(stem[k[1]:b[0]]))]
            bpm = max(near) if near else max(bare)

    # En toneart maa ikke ligge inde i BPM-teksten ("95 BPM" har et B).
    if bpm:
        keys = [k for k in keys if k[1] <= bpm[0] or k[0] >= bpm[1]]

    def beside_bpm(k):
        if not bpm:
            return False
        if k[1] <= bpm[0]:
            return _only_seps(stem[k[1]:bpm[0]])
        return _only_seps(stem[bpm[1]:k[0]])

    def last_in_name(k):
        return _only_seps(stem[k[1]:])

    accepted = [k for k in keys
                if k[2] == STRONG
                or (k[2] == MEDIUM and (beside_bpm(k) or last_in_name(k)))
                or (k[2] == WEAK and beside_bpm(k))]
    key = max(accepted, key=lambda k: (k[2], k[0])) if accepted else None

    if not bpm and not key:
        return {"title": stem[:120], "bpm": "", "mkey": "", "parsed": "ingen"}

    # --- Titel: det der er tilbage ---
    spans = sorted(s for s in (bpm, key) if s)
    rest, at = [], 0
    for start, end, *_ in spans:
        rest.append(stem[at:start])
        rest.append(" ")
        at = end
    rest.append(stem[at:])
    title = _tidy_title("".join(rest))

    # "lucas - Midnight Drive": han lagde det selv op, navnet staar allerede
    # paa sporet.
    if username:
        title = re.sub(r"^\s*" + re.escape(username) + r"\s*[-–—:|]\s*",
                       "", title, flags=re.I)
        title = re.sub(r"\s*[-–—:|]\s*" + re.escape(username) + r"\s*$",
                       "", title, flags=re.I)

    complete = bool(title and bpm and key)
    return {
        "title": (title or stem)[:120],
        "bpm": bpm[2] if bpm else "",
        "mkey": key[3] if key else "",
        "parsed": "fuld" if complete else "delvis",
    }


def _tidy_title(text):
    text = text.replace("_", " ")
    text = re.sub(r"\(\s*\)|\[\s*\]|\{\s*\}", " ", text)
    text = re.sub(r"\s+", " ", text)
    # "Midnight - 95 - Fm - mix" -> "Midnight -  -  - mix" -> "Midnight - mix"
    text = re.sub(r"(?:\s*[-–—|,;:+]\s*){2,}", " - ", text)
    text = re.sub(r"(?:\s+\.)+(?=\s|$)", "", text)
    return text.strip(" \t-–—|,;:+.")


def normalize_bpm(text):
    """Det man skriver i tabellen. '95 bpm' -> '95'. Kan det ikke laeses,
    bliver det staaende som skrevet - det er hans felt."""
    text = (text or "").strip()
    m = re.fullmatch(r"(?:bpm[\s:=]*)?(\d{2,3}(?:[.,]\d{1,2})?)\s*(?:bpm)?", text, re.I)
    if m:
        return _number(m.group(1))[1]
    return text[:8]


def normalize_key(text):
    """'f minor' -> 'Fm', 'bb' -> 'Bb'. Kan det ikke laeses, staar det som skrevet."""
    text = (text or "").strip()
    m = _KEY.fullmatch(text)
    if m and m.group("q") != "M":
        return _key_text(m.group("note"), m.group("acc"), m.group("q"))
    return text[:12]


def folder_audio(name):
    return ext_of(name) in FOLDER_EXT and not name.startswith("._")


# --------------------------------------------------------------------
# Ruter
# --------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user() is not None:
        return redirect(url_for("index"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""
        row = db().execute(
            "SELECT * FROM users WHERE name = ?", (name,)
        ).fetchone()
        if row and check_password_hash(row["pw_hash"], password):
            session.clear()
            session["uid"] = row["id"]
            session.permanent = True
            target = request.form.get("next") or url_for("index")
            if not target.startswith("/") or target.startswith("//"):
                target = url_for("index")
            return redirect(target)
        flash("Forkert navn eller kodeord.")

    return render_template("login.html", next=request.args.get("next", ""))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return redirect(url_for("section", slug="beats"))


@app.route("/<slug>")
@login_required
def section(slug):
    if slug not in SECTIONS:
        abort(404)
    rows = db().execute(
        "SELECT t.*, u.name AS uploader"
        "  FROM tracks t JOIN users u ON u.id = t.uploader_id"
        " ORDER BY t.created_at DESC"
    ).fetchall()
    tracks = {key: [r for r in rows if r["section"] == key] for key in SECTIONS}
    return render_template("index.html", active=slug, tracks=tracks)


@app.post("/upload")
@login_required
def upload():
    user = current_user()
    slug = request.form.get("section", "")
    if slug not in SECTIONS:
        abort(400)

    audio = request.files.get("audio")
    if audio is None or not audio.filename:
        flash("Vaelg en lydfil.")
        return redirect(url_for("section", slug=slug))

    audio_ext = ext_of(audio.filename)
    if audio_ext not in AUDIO_EXT:
        flash("Formatet {0} kan ikke laegges op. Brug wav, mp3, m4a, flac eller aiff."
              .format(audio_ext or "uden endelse"))
        return redirect(url_for("section", slug=slug))

    title = (request.form.get("title") or "").strip()
    if not title:
        title = Path(audio.filename).stem.strip() or "Uden titel"

    init_storage()
    track_id = uuid.uuid4().hex
    audio_file = track_id + audio_ext
    audio.save(MEDIA / audio_file)
    size = (MEDIA / audio_file).stat().st_size

    cover_file = ""
    cover = request.files.get("cover")
    if cover is not None and cover.filename:
        cover_ext = ext_of(cover.filename)
        if cover_ext not in IMAGE_EXT:
            flash("Coveret blev sprunget over. {0} er ikke et billedformat."
                  .format(cover_ext or "Filen har ingen endelse"))
        else:
            cover_file = track_id + cover_ext
            cover.save(COVERS / cover_file)
            if (COVERS / cover_file).stat().st_size > MAX_COVER_BYTES:
                (COVERS / cover_file).unlink()
                cover_file = ""
                flash("Coveret var over 12 MB og blev sprunget over.")

    db().execute(
        "INSERT INTO tracks (id, section, title, bpm, mkey, note, audio_file,"
        " audio_name, audio_size, cover_file, uploader_id, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (track_id, slug, title[:120],
         (request.form.get("bpm") or "").strip()[:8],
         (request.form.get("mkey") or "").strip()[:12],
         (request.form.get("note") or "").strip()[:400],
         audio_file, clean_name(audio.filename), size, cover_file,
         user["id"], now()),
    )
    db().commit()
    return redirect(url_for("section", slug=slug) + "#" + track_id)


# --------------------------------------------------------------------
# Mappe-upload
#
#   GET  /mappe          siden hvor man vaelger en mappe
#   POST /mappe/tjek     filnavne ind -> hvad der laeses ud af dem, og
#                        hvilke der findes i forvejen. Ingen filer sendes.
#   POST /mappe/del      en bid af en fil. Den sidste bid laegger sporet ind.
#   GET  /mappe/<batch>  tabellen over det der kom med uploaden
#   GET  /gennemse       alt man selv har lagt op som ikke kunne laeses
#
# Filerne sendes en ad gangen og i bidder, se CHUNK_BYTES.
# --------------------------------------------------------------------

@app.get("/mappe")
@login_required
def folder_page():
    slug = request.args.get("sektion", "beats")
    if slug not in SECTIONS:
        slug = "beats"
    return render_template("mappe.html", active=slug,
                           chunk_bytes=CHUNK_BYTES, max_bytes=MAX_BYTES)


def find_duplicate(slug, name, user):
    """Et spor i samme sektion med samme filnavn. Findes der flere, er
    ens eget det der taeller - det er det eneste man kan overskrive."""
    return db().execute(
        "SELECT t.*, u.name AS uploader"
        "  FROM tracks t JOIN users u ON u.id = t.uploader_id"
        " WHERE t.section = ? AND t.audio_name = ? COLLATE NOCASE"
        " ORDER BY (t.uploader_id = ?) DESC, t.created_at DESC"
        " LIMIT 1", (slug, name, user["id"])
    ).fetchone()


def sweep_parts():
    """Halve filer fra uploads der blev afbrudt og aldrig genoptaget."""
    cutoff = datetime.now().timestamp() - PART_MAX_AGE
    for part in PARTS.glob("*.part"):
        try:
            if part.stat().st_mtime < cutoff:
                part.unlink()
        except OSError:
            pass


@app.post("/mappe/tjek")
@api_login_required
def folder_check():
    user = current_user()
    data = request.get_json(silent=True) or {}
    slug = data.get("section")
    if slug not in SECTIONS:
        return json_error(400, "Vælg Beats eller Music.")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        return json_error(400, "Der var ingen filer at tjekke.")
    if len(files) > 5000:
        return json_error(400, "Over 5000 filer i én mappe. Del den op.")

    init_storage()
    sweep_parts()

    out = []
    for item in files:
        item = item if isinstance(item, dict) else {}
        name = clean_name(str(item.get("name", "")))
        if not folder_audio(name):
            out.append(None)
            continue
        meta = parse_filename(name, user["name"])
        dup = find_duplicate(slug, name, user)
        meta["duplicate"] = None if dup is None else {
            "title": dup["title"],
            "by": dup["uploader"],
            "own": can_edit(dup, user),
        }
        out.append(meta)

    return jsonify({"batch": uuid.uuid4().hex, "files": out})


@app.post("/mappe/del")
@api_login_required
def folder_chunk():
    user = current_user()
    form = request.form

    upload_id = form.get("upload", "")
    batch = form.get("batch", "")
    slug = form.get("section", "")
    name = clean_name(form.get("name", ""))
    on_dup = form.get("duplicate", "skip")
    if not HEX32.fullmatch(upload_id) or not HEX32.fullmatch(batch):
        return json_error(400, "Ugyldig upload.")
    if slug not in SECTIONS:
        return json_error(400, "Vælg Beats eller Music.")
    if not folder_audio(name):
        return json_error(400, "Kun mp3 og wav kan lægges op fra en mappe.")
    if on_dup not in ("skip", "overwrite", "keep"):
        return json_error(400, "Ugyldigt valg for dubletter.")
    try:
        size = int(form.get("size", ""))
        offset = int(form.get("offset", ""))
    except ValueError:
        return json_error(400, "Ugyldig størrelse.")
    if size <= 0:
        return json_error(400, "Filen er tom.")
    if size > MAX_BYTES:
        return json_error(400, "Filen er over {0}.".format(human_size(MAX_BYTES)))

    chunk = request.files.get("part")
    if chunk is None:
        return json_error(400, "Der mangler data.")

    init_storage()
    part = PARTS / "{0}-{1}.part".format(user["id"], upload_id)

    if offset == 0:
        part.unlink(missing_ok=True)   # en fil der startes forfra
        # Spring over foer der sendes 300 MB for ingenting. Tjekket
        # gentages til sidst, hvis nogen har lagt den op imens.
        dup = find_duplicate(slug, name, user)
        if dup is not None:
            if on_dup == "skip":
                return jsonify({"status": "skipped"})
            if on_dup == "overwrite" and not can_edit(dup, user):
                return json_error(403, "{0} har lagt den op. Du kan kun overskrive "
                                       "dine egne.".format(dup["uploader"]))

    have = part.stat().st_size if part.exists() else 0
    if offset != have:
        # Klienten tror den er et andet sted end vi er. Den fortsaetter
        # derfra i stedet for at starte forfra.
        return json_error(409, "Ude af trit.", have=have)

    with open(part, "ab") as fh:
        shutil.copyfileobj(chunk.stream, fh, 1024 * 1024)
    have = part.stat().st_size

    if have > size:
        part.unlink(missing_ok=True)
        return json_error(400, "Der kom flere data end filen er stor.")
    if have < size:
        return jsonify({"status": "part", "have": have})

    return finish_folder_file(part, user, slug, batch, name, on_dup)


def finish_folder_file(part, user, slug, batch, name, on_dup):
    size = part.stat().st_size
    ext = ext_of(name)
    meta = parse_filename(name, user["name"])
    dup = find_duplicate(slug, name, user)

    if dup is not None and on_dup == "skip":
        part.unlink(missing_ok=True)
        return jsonify({"status": "skipped"})

    if dup is not None and on_dup == "overwrite":
        if not can_edit(dup, user):
            part.unlink(missing_ok=True)
            return json_error(403, "Du kan kun overskrive dine egne.")

        # Lyden skiftes. Det filnavnet ikke kunne give, beholdes fra det
        # gamle spor - ellers ville en rettet toneart ryge ved en ny version.
        # Cover, note, dato og id bliver staaende.
        title = meta["title"] if meta["parsed"] != "ingen" else dup["title"]
        bpm = meta["bpm"] or dup["bpm"]
        mkey = meta["mkey"] or dup["mkey"]
        parsed = "fuld" if bpm and mkey else ("delvis" if bpm or mkey else "ingen")

        audio_file = dup["id"] + ext
        os.replace(part, MEDIA / audio_file)
        if dup["audio_file"] != audio_file:
            try:
                (MEDIA / dup["audio_file"]).unlink(missing_ok=True)
            except OSError:
                app.logger.warning("kunne ikke slette %s", dup["audio_file"])

        db().execute(
            "UPDATE tracks SET title = ?, bpm = ?, mkey = ?, audio_file = ?,"
            " audio_name = ?, audio_size = ?, batch_id = ?, parsed = ?"
            " WHERE id = ?",
            (title[:120], bpm, mkey, audio_file, name, size, batch, parsed, dup["id"]),
        )
        db().commit()
        return jsonify({"status": "overwritten", "id": dup["id"]})

    track_id = uuid.uuid4().hex
    audio_file = track_id + ext
    os.replace(part, MEDIA / audio_file)
    db().execute(
        "INSERT INTO tracks (id, section, title, bpm, mkey, note, audio_file,"
        " audio_name, audio_size, cover_file, uploader_id, created_at,"
        " batch_id, parsed)"
        " VALUES (?,?,?,?,?,'',?,?,?,'',?,?,?,?)",
        (track_id, slug, meta["title"], meta["bpm"], meta["mkey"], audio_file,
         name, size, user["id"], now(), batch, meta["parsed"]),
    )
    db().commit()
    return jsonify({"status": "added", "id": track_id})


def review(where, params, mode, batch=""):
    """Tabellen hvor titel, BPM og toneart rettes, og gemmes paa en gang."""
    sql = ("SELECT t.*, u.name AS uploader"
           "  FROM tracks t JOIN users u ON u.id = t.uploader_id"
           " WHERE " + where +
           " ORDER BY t.audio_name COLLATE NOCASE")

    if request.method == "POST":
        user = current_user()
        rows = {r["id"]: r for r in db().execute(sql, params)}
        if mode == "batch" and not rows:
            abort(404)
        saved = missing = 0
        for track_id in request.form.getlist("id"):
            row = rows.get(track_id)
            if row is None or not can_edit(row, user):
                continue
            title = (request.form.get("title_" + track_id) or "").strip()[:120]
            bpm = normalize_bpm(request.form.get("bpm_" + track_id))
            mkey = normalize_key(request.form.get("mkey_" + track_id))
            parsed = "rettet" if row["parsed"] in ("delvis", "ingen") else row["parsed"]
            db().execute(
                "UPDATE tracks SET title = ?, bpm = ?, mkey = ?, parsed = ? WHERE id = ?",
                (title or row["title"], bpm, mkey, parsed, track_id),
            )
            saved += 1
            missing += not (bpm and mkey)
        db().commit()

        if not saved:
            flash("Der var ikke noget at gemme.")
        elif missing:
            flash("Gemt. {0} af {1} mangler stadig BPM eller toneart."
                  .format(missing, saved))
        elif saved == 1:
            flash("Gemt. Nummeret har titel, BPM og toneart.")
        else:
            flash("Gemt. Alle {0} har titel, BPM og toneart.".format(saved))
        return redirect(request.path)

    rows = db().execute(sql, params).fetchall()
    if mode == "batch" and not rows:
        abort(404)
    return render_template("gennemse.html", rows=rows, mode=mode, batch=batch)


@app.route("/mappe/<batch>", methods=["GET", "POST"])
@login_required
def folder_review(batch):
    if not HEX32.fullmatch(batch):
        abort(404)
    user = current_user()
    where, params = "t.batch_id = ?", [batch]
    if not user["is_admin"]:
        where += " AND t.uploader_id = ?"
        params.append(user["id"])
    return review(where, params, "batch", batch)


@app.route("/gennemse", methods=["GET", "POST"])
@login_required
def review_pending():
    return review("t.uploader_id = ? AND t.parsed IN ('delvis', 'ingen')",
                  [current_user()["id"]], "pending")


def track_or_404(track_id):
    row = db().execute(
        "SELECT t.*, u.name AS uploader"
        "  FROM tracks t JOIN users u ON u.id = t.uploader_id"
        " WHERE t.id = ?", (track_id,)
    ).fetchone()
    if row is None:
        abort(404)
    return row


@app.get("/lyd/<track_id>")
@login_required
def audio_stream(track_id):
    row = track_or_404(track_id)
    # conditional=True giver Range-svar, saa man kan spole i en 40 MB wav
    # uden at hente hele filen forst.
    return send_file(MEDIA / row["audio_file"], conditional=True)


@app.get("/cover/<track_id>")
@login_required
def cover_image(track_id):
    row = track_or_404(track_id)
    if not row["cover_file"]:
        abort(404)
    return send_file(COVERS / row["cover_file"], conditional=True,
                     max_age=60 * 60 * 24 * 30)


@app.get("/hent/<track_id>")
@login_required
def download(track_id):
    row = track_or_404(track_id)
    return send_file(MEDIA / row["audio_file"], as_attachment=True,
                     download_name=row["audio_name"])


@app.post("/slet/<track_id>")
@login_required
def delete(track_id):
    row = track_or_404(track_id)
    user = current_user()
    if row["uploader_id"] != user["id"] and not user["is_admin"]:
        abort(403)

    # Raekken forst. Sa forsvinder sporet fra listen, ogsaa hvis filen
    # ikke kan fjernes lige nu - fx fordi nogen streamer den. Paa Linux
    # sker det ikke, men en laast fil maa ikke efterlade et spor der
    # ikke kan slettes.
    db().execute("DELETE FROM tracks WHERE id = ?", (track_id,))
    db().commit()

    for folder, name in ((MEDIA, row["audio_file"]), (COVERS, row["cover_file"])):
        if name:
            try:
                (folder / name).unlink(missing_ok=True)
            except OSError:
                app.logger.warning("kunne ikke slette %s", folder / name)
    flash("{0} er slettet.".format(row["title"]))
    return redirect(url_for("section", slug=row["section"]))


# --------------------------------------------------------------------
# Profiler
# --------------------------------------------------------------------

def tracks_by(user_id):
    return db().execute(
        "SELECT t.*, u.name AS uploader"
        "  FROM tracks t JOIN users u ON u.id = t.uploader_id"
        " WHERE t.uploader_id = ?"
        " ORDER BY t.created_at DESC", (user_id,)
    ).fetchall()


@app.get("/profil/<navn>")
@login_required
def profile(navn):
    who = db().execute("SELECT * FROM users WHERE name = ?", (navn,)).fetchone()
    if who is None:
        abort(404)
    rows = tracks_by(who["id"])
    counts = {key: sum(1 for r in rows if r["section"] == key) for key in SECTIONS}
    pending = sum(1 for r in rows if r["parsed"] in ("delvis", "ingen"))
    return render_template("profil.html", who=who, tracks=rows, counts=counts,
                           total=sum(r["audio_size"] for r in rows),
                           pending=pending)


@app.get("/avatar/<navn>")
@login_required
def avatar(navn):
    row = db().execute(
        "SELECT avatar_file FROM users WHERE name = ?", (navn,)).fetchone()
    if row is None or not row["avatar_file"]:
        abort(404)
    return send_file(AVATARS / row["avatar_file"], conditional=True,
                     max_age=60 * 60 * 24 * 30)


@app.post("/profil/rediger")
@login_required
def profile_edit():
    """Man redigerer kun sin egen profil. Heller ikke admin roerer andres -
    det er en gruppe, ikke en tjeneste med moderation."""
    me = current_user()
    old_avatar = me["avatar_file"]
    new_avatar = old_avatar

    if request.form.get("fjern_billede"):
        new_avatar = ""
    else:
        picture = request.files.get("avatar")
        if picture is not None and picture.filename:
            picture_ext = ext_of(picture.filename)
            if picture_ext not in IMAGE_EXT:
                flash("Profilbilledet blev sprunget over. {0} er ikke et "
                      "billedformat.".format(picture_ext or "Filen har ingen endelse"))
            else:
                new_avatar = uuid.uuid4().hex + picture_ext
                init_storage()
                picture.save(AVATARS / new_avatar)
                if (AVATARS / new_avatar).stat().st_size > MAX_COVER_BYTES:
                    (AVATARS / new_avatar).unlink()
                    new_avatar = old_avatar
                    flash("Profilbilledet var over 12 MB og blev sprunget over.")

    db().execute(
        "UPDATE users SET bio = ?, avatar_file = ? WHERE id = ?",
        ((request.form.get("bio") or "").strip()[:400], new_avatar, me["id"]),
    )
    db().commit()

    # Det gamle billede er der ingen der peger paa laengere.
    if old_avatar and old_avatar != new_avatar:
        try:
            (AVATARS / old_avatar).unlink(missing_ok=True)
        except OSError:
            app.logger.warning("kunne ikke slette %s", AVATARS / old_avatar)

    return redirect(url_for("profile", navn=me["name"]))


@app.errorhandler(413)
def too_large(_e):
    if request.path.startswith("/mappe/"):
        return json_error(413, "Serveren afviste bidden som for stor.")
    flash("Filen er for stor. Graensen er {0}.".format(human_size(MAX_BYTES)))
    return redirect(url_for("index"))


# --------------------------------------------------------------------
# Kommandolinje
# --------------------------------------------------------------------

def ask_password(label):
    pw = getpass.getpass("Kodeord til {0}: ".format(label))
    if len(pw) < 10:
        sys.exit("Kodeordet skal vaere mindst 10 tegn.")
    if pw != getpass.getpass("Gentag: "):
        sys.exit("De to kodeord er ikke ens.")
    return pw


def cli_adduser(argv):
    names = [a for a in argv if not a.startswith("--")]
    if not names:
        sys.exit("Brug: python app.py adduser <navn> [--admin]")
    name = names[0].strip()
    is_admin = "--admin" in argv
    init_storage()
    conn = sqlite3.connect(DB_PATH)
    if conn.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone():
        sys.exit("Brugeren {0} findes allerede.".format(name))
    pw_hash = generate_password_hash(ask_password(name))
    conn.execute(
        "INSERT INTO users (name, pw_hash, is_admin, created_at) VALUES (?,?,?,?)",
        (name, pw_hash, 1 if is_admin else 0, now()),
    )
    conn.commit()
    conn.close()
    print("Oprettet: {0}{1}".format(name, " (admin)" if is_admin else ""))


def cli_passwd(argv):
    if not argv:
        sys.exit("Brug: python app.py passwd <navn>")
    name = argv[0].strip()
    conn = sqlite3.connect(DB_PATH)
    if not conn.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone():
        sys.exit("Ingen bruger der hedder {0}.".format(name))
    conn.execute("UPDATE users SET pw_hash = ? WHERE name = ?",
                 (generate_password_hash(ask_password(name)), name))
    conn.commit()
    conn.close()
    print("Kodeord skiftet for {0}.".format(name))


def cli_users():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT name, is_admin, created_at FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    if not rows:
        print("Ingen brugere endnu. Koer: python app.py adduser <navn> --admin")
        return
    for name, is_admin, created in rows:
        print("{0:<16} {1:<6} {2}".format(
            name, "admin" if is_admin else "", dk_date(created)))


def cli_import(argv):
    """Traekker en mappe med lydfiler ind i appen. Bruges til at flytte
    indholdet af Nextclouds MUSIC- og Beats-mapper over."""
    plain = [a for a in argv if not a.startswith("--")]
    if len(plain) < 3:
        sys.exit("Brug: python app.py import <mappe> <beats|music> <bruger>"
                 " [--flyt] [--proev]")

    folder, section, owner = Path(plain[0]), plain[1].lower(), plain[2]
    move = "--flyt" in argv
    dry = "--proev" in argv

    if not folder.is_dir():
        sys.exit("Mappen findes ikke: {0}".format(folder))
    if section not in SECTIONS:
        sys.exit("Sektionen skal vaere en af: {0}".format(", ".join(SECTIONS)))

    init_storage()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT id FROM users WHERE name = ?", (owner,)).fetchone()
    if row is None:
        sys.exit("Ingen bruger der hedder {0}. Opret den forst med adduser.".format(owner))
    owner_id = row[0]

    # Er filen her allerede? Navn plus stoerrelse er godt nok til at
    # kunne koere kommandoen to gange uden at faa dubletter.
    seen = {(name, size) for name, size in
            conn.execute("SELECT audio_name, audio_size FROM tracks")}

    found = sorted(p for p in folder.rglob("*")
                   if p.is_file() and p.suffix.lower() in AUDIO_EXT)
    if not found:
        sys.exit("Ingen lydfiler i {0}".format(folder))

    added = skipped = 0
    for path in found:
        size = path.stat().st_size
        name = clean_name(path.name)
        if (name, size) in seen:
            skipped += 1
            continue

        # Coveret er enten en fil med samme navn, eller et cover.* /
        # folder.* der ligger i samme mappe.
        cover_src = None
        for candidate in list(path.parent.glob(path.stem + ".*")) + \
                         list(path.parent.glob("cover.*")) + \
                         list(path.parent.glob("folder.*")):
            if candidate.suffix.lower() in IMAGE_EXT:
                cover_src = candidate
                break

        title = re.sub(r"[_\-]+", " ", path.stem).strip() or path.stem
        stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)

        print("  {0:<44} {1:>9}{2}".format(
            title[:44], human_size(size), "  + cover" if cover_src else ""))
        if dry:
            added += 1
            continue

        track_id = uuid.uuid4().hex
        audio_file = track_id + path.suffix.lower()
        (shutil.move if move else shutil.copy2)(str(path), str(MEDIA / audio_file))

        cover_file = ""
        if cover_src and cover_src.stat().st_size <= MAX_COVER_BYTES:
            cover_file = track_id + cover_src.suffix.lower()
            shutil.copy2(str(cover_src), str(COVERS / cover_file))

        conn.execute(
            "INSERT INTO tracks (id, section, title, bpm, mkey, note, audio_file,"
            " audio_name, audio_size, cover_file, uploader_id, created_at)"
            " VALUES (?,?,?,'','','',?,?,?,?,?,?)",
            (track_id, section, title[:120], audio_file, name, size,
             cover_file, owner_id, stamp.isoformat(timespec="seconds")),
        )
        seen.add((name, size))
        added += 1

    if not dry:
        conn.commit()
    conn.close()
    print("\n{0}{1} lagt i {2}, {3} sprunget over (fandtes i forvejen).".format(
        "PROEVEKOERSEL: " if dry else "", added, SECTIONS[section], skipped))


def cli_filename(argv):
    """Til at se hvorfor et filnavn blev laest som det blev."""
    if not argv:
        sys.exit('Brug: python app.py filnavn "Midnight Drive 95BPM Fm.wav" ...')
    for name in argv:
        r = parse_filename(name)
        print("{0}\n  titel {1!r}  bpm {2!r}  toneart {3!r}  ({4})".format(
            name, r["title"], r["bpm"], r["mkey"], r["parsed"]))


if __name__ == "__main__":
    args = sys.argv[1:]
    command = args[0] if args else ""

    if command == "init":
        init_storage()
        print("Klar: {0}".format(DATA))
    elif command == "adduser":
        cli_adduser(args[1:])
    elif command == "passwd":
        cli_passwd(args[1:])
    elif command == "users":
        cli_users()
    elif command == "import":
        cli_import(args[1:])
    elif command == "filnavn":
        cli_filename(args[1:])
    else:
        init_storage()
        app.secret_key = secret_key()
        # Udviklingsserveren koerer altid over http paa localhost, saa en
        # Secure-cookie ville aldrig blive sendt tilbage. I drift koeres
        # der med gunicorn bag nginx, hvor den bliver staaende.
        app.config["SESSION_COOKIE_SECURE"] = False
        app.run(host="127.0.0.1", port=8090, debug="--debug" in args)
else:
    init_storage()
    app.secret_key = secret_key()
