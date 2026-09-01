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
    Flask, abort, flash, g, redirect, render_template, request,
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
DB_PATH = DATA / "ebvb.db"

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aif", ".aiff"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}

# Slug -> label. Raekkefoelgen her er raekkefoelgen i fanerne.
SECTIONS = {"beats": "Beats", "music": "Music"}

MAX_BYTES = 512 * 1024 * 1024          # skal matche client_max_body_size i nginx
MAX_COVER_BYTES = 12 * 1024 * 1024

MONTHS = ("jan", "feb", "mar", "apr", "maj", "jun",
          "jul", "aug", "sep", "okt", "nov", "dec")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES
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
    created_at TEXT NOT NULL
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
    created_at  TEXT NOT NULL
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


def init_storage():
    for folder in (DATA, MEDIA, COVERS):
        folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
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


@app.errorhandler(413)
def too_large(_e):
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
