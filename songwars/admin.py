"""
Songwars - admin: genrer og beats, ogsaa hentet fra YouTube.

En Flask-blueprint ("admin"), saa endpoints hedder admin.<navn>. Kun admin
logger ind, og kun her.
"""

import hmac
import os
import secrets
import sqlite3
import threading
from functools import wraps

from flask import (
    Blueprint, abort, flash, jsonify, redirect, render_template, request,
    send_file, session, url_for,
)

import audio
import youtube
from core import (
    ADMIN_PASSWORD, BEATS, KEEP_SECONDS, MAX_BEAT, TITLE_MAX, TMP,
    YT_MAX_SECONDS, YT_PARALLEL, YT_STALE_SECONDS, clean_text, client_ip,
    clock, fail, log, now, remove_file, save_upload, too_many,
)
from database import connect, db, limited

bp = Blueprint("admin", __name__)


ADMIN_SECONDS = 12 * 3600


def is_admin():
    at = session.get("admin")
    return isinstance(at, (int, float)) and now() - at < ADMIN_SECONDS


def admin_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not is_admin():
            return redirect(url_for("admin.admin"))
        return view(*a, **kw)
    return wrapped


@bp.get("/admin")
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
        "lobbies": conn.execute("SELECT COUNT(*) FROM lobbies WHERE status IN ('lobby','spinning','running','voting')").fetchone()[0],
        "subs": conn.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM submissions").fetchone(),
    }
    return render_template("admin.html", genres=genres, beats=beats, stats=stats,
                           max_beat=MAX_BEAT, keep_days=KEEP_SECONDS // 86400,
                           imports=recent_imports(conn), yt_minutes=YT_MAX_SECONDS // 60)


@bp.post("/admin/login")
def admin_login():
    if limited("admin", client_ip()):
        return too_many("For mange forkerte forsøg. Vent et kvarter.")
    sent = request.form.get("password", "")
    if not hmac.compare_digest(sent.encode(), ADMIN_PASSWORD.encode()):
        flash("Forkert adgangskode.")
        return redirect(url_for("admin.admin"))
    # Ny cookie-identitet ved login, men behold de lobbyer man er med i.
    players = session.get("p", {})
    session.clear()
    session["p"] = players
    session["admin"] = now()
    session.permanent = True
    return redirect(url_for("admin.admin"))


@bp.post("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("home"))


@bp.post("/admin/genre")
@admin_required
def admin_genre_create():
    name = clean_text(request.form.get("name"), 40)
    if not name:
        return fail(400, "Genren skal have et navn.", url_for("admin.admin"))
    try:
        db().execute("INSERT INTO genres (name, created_at) VALUES (?, ?)", (name, now()))
        db().commit()
    except sqlite3.IntegrityError:
        return fail(409, "Genren {0} findes allerede.".format(name), url_for("admin.admin"))
    return redirect(url_for("admin.admin"))


@bp.post("/admin/genre/<int:gid>/omdoeb")
@admin_required
def admin_genre_rename(gid):
    name = clean_text(request.form.get("name"), 40)
    if not name:
        return fail(400, "Genren skal have et navn.", url_for("admin.admin"))
    try:
        db().execute("UPDATE genres SET name = ? WHERE id = ?", (name, gid))
        db().commit()
    except sqlite3.IntegrityError:
        return fail(409, "Der er allerede en genre der hedder {0}.".format(name), url_for("admin.admin"))
    return redirect(url_for("admin.admin"))


@bp.post("/admin/genre/<int:gid>/slet")
@admin_required
def admin_genre_delete(gid):
    conn = db()
    if conn.execute("SELECT 1 FROM beats WHERE genre_id = ?", (gid,)).fetchone():
        return fail(409, "Genren har beats. Slet dem først.", url_for("admin.admin"))
    conn.execute("DELETE FROM genres WHERE id = ?", (gid,))
    # Lobbyer der havde valgt den, falder tilbage til tilfaeldig.
    conn.execute("UPDATE lobbies SET genre_id = NULL WHERE genre_id = ? AND status = 'lobby'", (gid,))
    conn.commit()
    return redirect(url_for("admin.admin"))


@bp.post("/admin/beat")
@admin_required
def admin_beat_create():
    title = clean_text(request.form.get("title"), TITLE_MAX)
    try:
        gid = int(request.form.get("genre", ""))
    except ValueError:
        gid = None
    if not title:
        return fail(400, "Beatet skal have en titel.", url_for("admin.admin"))
    if gid is None or not db().execute("SELECT 1 FROM genres WHERE id = ?", (gid,)).fetchone():
        return fail(400, "Vælg en genre.", url_for("admin.admin"))
    storage = request.files.get("file")
    if storage is None or not storage.filename:
        return fail(400, "Vælg en mp3- eller wav-fil.", url_for("admin.admin"))
    try:
        tmp, kind, seconds, size = save_upload(storage, MAX_BEAT)
    except audio.NotAudio as exc:
        return fail(415, str(exc), url_for("admin.admin"))
    bid = secrets.token_hex(16)
    os.replace(tmp, BEATS / (bid + "." + kind))
    db().execute(
        "INSERT INTO beats (id, genre_id, title, file, kind, size, seconds, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (bid, gid, title, bid + "." + kind, kind, size, seconds, now()))
    db().commit()
    flash("{0} er lagt op ({1}).".format(title, clock(seconds)))
    return redirect(url_for("admin.admin"))


def spawn(fn, *args):
    """Koer i baggrunden. Testene skifter den ud, saa det sker med det samme."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def run_import(import_id):
    """Hent, tjek og gem et beat fra YouTube. Koerer i sin egen traad med
    sin egen forbindelse til databasen - ikke i en forespoergsel."""
    conn = connect()
    try:
        job = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()
        if job is None:
            return
        try:
            path, yt_title = youtube.fetch(job["video"], TMP, MAX_BEAT, YT_MAX_SECONDS)
        except youtube.FetchError as exc:
            finish_import(conn, import_id, "failed", str(exc))
            return
        try:
            if path.stat().st_size > MAX_BEAT:
                raise audio.NotAudio("Filen er over {0} MB.".format(MAX_BEAT // (1024 * 1024)))
            kind, _mime, seconds = audio.inspect(path)
        except (audio.NotAudio, OSError) as exc:
            remove_file(path)
            finish_import(conn, import_id, "failed", "Filen fra YouTube kunne ikke bruges: {0}".format(exc))
            return
        title = job["title"] or clean_text(youtube.clean_title(yt_title), TITLE_MAX)
        bid = secrets.token_hex(16)
        size = path.stat().st_size
        os.replace(path, BEATS / (bid + "." + kind))
        if not conn.execute("SELECT 1 FROM genres WHERE id = ?", (job["genre_id"],)).fetchone():
            remove_file(BEATS / (bid + "." + kind))
            finish_import(conn, import_id, "failed", "Genren er slettet, mens beatet blev hentet.")
            return
        conn.execute(
            "INSERT INTO beats (id, genre_id, title, file, kind, size, seconds, created_at, source)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (bid, job["genre_id"], title, bid + "." + kind, kind, size, seconds, now(),
             "youtube:" + job["video"]))
        conn.execute("UPDATE imports SET status = 'done', message = ?, beat_id = ?, title = ?,"
                     " updated_at = ? WHERE id = ?",
                     ("{0} er lagt op ({1}).".format(title, clock(seconds)), bid, title, now(), import_id))
        conn.commit()
    except Exception:                                  # aldrig en hentning der haenger i "koerer"
        log.exception("YouTube-hentning %s fejlede", import_id)
        try:
            finish_import(conn, import_id, "failed", "Noget gik galt på serveren. Se loggen.")
        except sqlite3.Error:
            pass
    finally:
        conn.close()


def finish_import(conn, import_id, status, message):
    conn.execute("UPDATE imports SET status = ?, message = ?, updated_at = ? WHERE id = ?",
                 (status, message, now(), import_id))
    conn.commit()


def recent_imports(conn):
    """Hentninger fra den sidste time - og dem der stadig koerer. En der har
    "koert" i et kvarter, er doed (serveren er genstartet midt i)."""
    t = now()
    conn.execute("UPDATE imports SET status = 'failed', message = ?, updated_at = ?"
                 " WHERE status = 'running' AND created_at < ?",
                 ("Hentningen blev afbrudt — serveren blev nok genstartet. Prøv igen.", t,
                  t - YT_STALE_SECONDS))
    conn.commit()
    rows = conn.execute(
        "SELECT i.*, g.name AS genre FROM imports i LEFT JOIN genres g ON g.id = i.genre_id"
        " WHERE i.status = 'running' OR i.updated_at > ? ORDER BY i.created_at DESC, i.id DESC LIMIT 12",
        (t - 3600,)).fetchall()
    return [{"id": r["id"], "status": r["status"], "message": r["message"],
             "title": r["title"], "genre": r["genre"] or "",
             "url": youtube.watch_url(r["video"])} for r in rows]


@bp.post("/admin/youtube")
@admin_required
def admin_youtube():
    back = url_for("admin.admin")
    vid = youtube.video_id(request.form.get("url"))
    title = clean_text(request.form.get("title"), TITLE_MAX)
    try:
        gid = int(request.form.get("genre", ""))
    except ValueError:
        gid = None
    if vid is None:
        return fail(400, "Det er ikke et link til en YouTube-video. Brug linket fra adresselinjen "
                         "eller Del-knappen.", back)
    conn = db()
    if gid is None or not conn.execute("SELECT 1 FROM genres WHERE id = ?", (gid,)).fetchone():
        return fail(400, "Vælg en genre.", back)
    have = conn.execute("SELECT title FROM beats WHERE source = ?", ("youtube:" + vid,)).fetchone()
    if have:
        return fail(409, "Den video er lagt op før — som {0}.".format(have["title"]), back)
    recent_imports(conn)                               # rydder doede hentninger op foerst
    if conn.execute("SELECT 1 FROM imports WHERE video = ? AND status = 'running'", (vid,)).fetchone():
        return fail(409, "Den video bliver hentet lige nu.", back)
    if conn.execute("SELECT COUNT(*) FROM imports WHERE status = 'running'").fetchone()[0] >= YT_PARALLEL:
        return fail(429, "Der hentes allerede {0} beats. Vent til de er færdige.".format(YT_PARALLEL), back)
    t = now()
    import_id = conn.execute(
        "INSERT INTO imports (video, title, genre_id, created_at, updated_at) VALUES (?,?,?,?,?)",
        (vid, title, gid, t, t)).lastrowid
    conn.commit()
    spawn(run_import, import_id)
    flash("Henter beatet fra YouTube … Det tager typisk 10–30 sekunder.")
    return redirect(back)


@bp.get("/admin/hentninger")
@admin_required
def admin_imports():
    return jsonify({"imports": recent_imports(db())})


@bp.post("/admin/beat/<bid>/slet")
@admin_required
def admin_beat_delete(bid):
    conn = db()
    beat = conn.execute("SELECT * FROM beats WHERE id = ?", (bid,)).fetchone()
    if beat is None:
        abort(404)
    busy = conn.execute(
        "SELECT COUNT(*) FROM lobbies l WHERE l.status IN ('spinning', 'running', 'voting')"
        " AND (l.beat_id = ? OR EXISTS (SELECT 1 FROM teams t WHERE t.lobby_id = l.id AND t.beat_id = ?))",
        (bid, bid)).fetchone()[0]
    if busy:
        return fail(409, "{0} bruges i en runde lige nu. Prøv igen når den er slut."
                    .format(beat["title"]), url_for("admin.admin"))
    conn.execute("DELETE FROM beats WHERE id = ?", (bid,))
    conn.commit()
    remove_file(BEATS / beat["file"])
    return redirect(url_for("admin.admin"))


@bp.get("/admin/beat/<bid>/lyd")
@admin_required
def admin_beat_audio(bid):
    beat = db().execute("SELECT * FROM beats WHERE id = ?", (bid,)).fetchone()
    if beat is None:
        abort(404)
    return send_file(BEATS / beat["file"], conditional=True,
                     mimetype="audio/mpeg" if beat["kind"] == "mp3" else "audio/wav")
