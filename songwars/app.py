#!/usr/bin/env python3
"""
Songwars.

Spillere joiner en lobby med en kode, drejer genrehjulet paa skift - alle
faar hver sin genre og et beat i den - laver et nummer ovenpaa i deres eget
program, afleverer det, og alle stemmer 1-5 flammer. Ingen konti: man er et
kaldenavn i en signeret cookie.

    python app.py              udviklingsserver paa :8092
    python app.py ryd          slet udloebne lobbyer og gamle afleveringer nu

I drift koeres den med gunicorn bag nginx. Se README.md og DEPLOY.md.

Koden er delt saadan:

    core.py       konfiguration og faelles smaating
    database.py   skema, forbindelse, rate limiting
    game.py       spillets regler: faser, hjulet, point, /state
    admin.py      admin-siden og beats fra YouTube
    app.py        Flask-appen, sikkerhed, oprydning og spillernes ruter
"""

import hmac
import logging
import os
import re
import secrets
import sqlite3
import sys
import unicodedata
from hashlib import sha1

from flask import (
    Flask, abort, flash, g, jsonify, redirect, render_template, request,
    send_file, session, url_for,
)
from flask.logging import default_handler
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

import audio
from core import (
    AVATARS, BEATS, COLOR_NAMES, COLORS, CLEANUP_EVERY, CODE_LEN, CODE_RE,
    COOKIE_SECURE, CSP, GRACE_SECONDS, HEX_RE, KEEP_SECONDS, MAX_BEAT,
    MAX_PLAYERS, MAX_UPLOAD, MINUTES_RANGE, NAME_MAX, POLL_TOUCH_SECONDS,
    ROOT, SECRET_KEY, SUBS, TEAM_RANGE, TMP, check_config,
    clean_text, client_ip, clock, fail, json_error, now, read_avatar,
    remove_file, save_upload, too_many, wants_json,
)
from database import connect, db, init_db, limited
from game import (
    VIEWER, advance, begin_round, closes_at, genres_with_beats, listen_step,
    new_code, open_voting, phase_of, skips_needed, spin, start_problem,
    state_for, team_genre, team_on_turn, war_summary,
)
from admin import bp as admin_bp, is_admin

app = Flask(__name__)
logging.getLogger("songwars").addHandler(default_handler)
logging.getLogger("songwars").setLevel(logging.INFO)
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


app.register_blueprint(admin_bp)


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


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


@app.before_request
def guard():
    # Foer formularen laeses: ellers er graensen allerede brugt.
    if request.endpoint == "admin.admin_beat_create" and is_admin():
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
    if request.endpoint in ("lobby_state", "admin.admin_imports") or resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-store"
    return resp


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


def me_in(lobby, depth=0):
    """Spilleren i den her lobby, ifoelge den signerede cookie."""
    pid = session.get("p", {}).get(lobby["code"])
    if pid is None:
        return carried_over(lobby, depth)
    row = db().execute("SELECT * FROM players WHERE id = ? AND lobby_id = ?",
                       (pid, lobby["id"])).fetchone()
    if row is None:                                   # smidt ud, eller slettet
        mine = dict(session.get("p", {}))
        mine.pop(lobby["code"], None)
        session["p"] = mine
    return row


def carried_over(lobby, depth):
    """En ny runde med samme spillere: alle fra forrige runde er kopieret
    med (players.from_id). Den der var med dengang, er med nu - uden at
    skrive koden igen. Virker ogsaa hvis man har sprunget en runde over."""
    if not lobby["parent_id"] or depth > 20:
        return None
    parent = db().execute("SELECT * FROM lobbies WHERE id = ?", (lobby["parent_id"],)).fetchone()
    prev = me_in(parent, depth + 1) if parent is not None else None
    if prev is None:
        return None
    row = db().execute("SELECT * FROM players WHERE lobby_id = ? AND from_id = ?",
                       (lobby["id"], prev["id"])).fetchone()
    if row is not None:
        remember(lobby["code"], row["id"])
    return row


REMEMBER_LOBBIES = 40


def screen_in(lobby, depth=0):
    """Storskaermen i den her lobby, ifoelge cookien ("s": {kode: id}). En
    skaerm foelger med i nye runder ligesom spillerne (screens.from_id)."""
    sid = session.get("s", {}).get(lobby["code"])
    if sid is not None:
        row = db().execute("SELECT * FROM screens WHERE id = ? AND lobby_id = ?",
                           (sid, lobby["id"])).fetchone()
        if row is not None:
            return row
    if not lobby["parent_id"] or depth > 20:
        return None
    parent = db().execute("SELECT * FROM lobbies WHERE id = ?", (lobby["parent_id"],)).fetchone()
    prev = screen_in(parent, depth + 1) if parent is not None else None
    if prev is None:
        return None
    row = db().execute("SELECT * FROM screens WHERE lobby_id = ? AND from_id = ?",
                       (lobby["id"], prev["id"])).fetchone()
    if row is not None:
        remember_screen(lobby["code"], row["id"])
    return row


def remember_screen(code, screen_id):
    mine = dict(session.get("s", {}))
    mine[code] = screen_id
    if len(mine) > 12:
        for old in list(mine)[:-12]:
            mine.pop(old)
    session["s"] = mine
    session.permanent = True


def viewer_or_403(lobby):
    """Storskaermen - eller en spiller i lobbyen, der aabner den paa sin egen
    skaerm. Den sidste faar en skaerm med det samme, saa den ogsaa foelger
    med i naeste runde."""
    screen = screen_in(lobby)
    if screen is not None:
        return screen
    if me_in(lobby) is None:
        abort(403)
    sid = db().execute("INSERT INTO screens (lobby_id, created_at) VALUES (?, ?)",
                       (lobby["id"], now())).lastrowid
    db().commit()
    remember_screen(lobby["code"], sid)
    return db().execute("SELECT * FROM screens WHERE id = ?", (sid,)).fetchone()


def remember(code, player_id):
    mine = dict(session.get("p", {}))
    mine[code] = player_id
    # Kun de seneste lobbyer - de er ogsaa listen i "Tidligere wars". Cookien
    # skal ikke vokse uden graense (40 koder er omkring 700 bytes).
    if len(mine) > REMEMBER_LOBBIES:
        for old in list(mine)[:-REMEMBER_LOBBIES]:
            mine.pop(old)
    session["p"] = mine
    session.permanent = True


@app.get("/")
def home():
    return render_template("home.html", genres=genres_with_beats(db()),
                           code=norm_code(request.args.get("kode")),
                           minutes_range=MINUTES_RANGE, team_range=TEAM_RANGE,
                           name_max=NAME_MAX, code_len=CODE_LEN,
                           avatars=AVATARS, colors=[(c, COLOR_NAMES[c]) for c in COLORS])


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
    # Genren vaelges ikke laengere: hjulet giver hvert hold sin egen.
    return {"minutes": minutes, "team_size": team_size}, None


@app.post("/ny")
def create_lobby():
    avatar, color = read_avatar(request.form)
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
        "INSERT INTO lobbies (code, minutes, team_size, created_at) VALUES (?,?,?,?)",
        (code, settings["minutes"], settings["team_size"], t))
    lid = cur.lastrowid
    pid = conn.execute(
        "INSERT INTO players (lobby_id, name, joined_at, seen_at, avatar, color) VALUES (?,?,?,?,?,?)",
        (lid, name, t, t, avatar, color)).lastrowid
    conn.execute("UPDATE lobbies SET owner_id = ? WHERE id = ?", (pid, lid))
    conn.commit()
    remember(code, pid)
    return redirect(url_for("lobby_page", code=code))


@app.get("/tidligere")
def past_wars():
    """De runder man selv har vaeret med i - fra den her browser - med alle
    numrene. Kun ens egne: et resultat kan kun ses af dem der var med, og
    numrene slettes efter KEEP_SECONDS."""
    conn = db()
    wars = []
    for code in list(session.get("p", {})):
        row = conn.execute("SELECT * FROM lobbies WHERE code = ?", (code,)).fetchone()
        if row is None:
            continue
        lobby = advance(row)
        me = me_in(lobby)
        if me is None or lobby["status"] != "done":
            continue
        wars.append(war_summary(conn, lobby, me))
    wars.sort(key=lambda w: w["at"], reverse=True)
    return render_template("wars.html", wars=wars, keep_days=KEEP_SECONDS // 86400,
                           colors=COLORS)


@app.post("/alene")
def play_alone():
    """Singleplayer fra forsiden: en lobby med en spiller, der gaar i gang
    med det samme. Hjulet, beatet og uret er de samme - der er bare ingen
    afstemning."""
    if limited("create", client_ip()):
        return too_many("Du har startet mange runder på kort tid. Vent lidt.")
    name = clean_text(request.form.get("name"), NAME_MAX)
    if not name:
        return fail(400, "Skriv et kaldenavn.", url_for("home") + "#spil")
    settings, error = read_settings(dict(request.form, team_size="1"))
    if error:
        return fail(400, error, url_for("home") + "#spil")
    avatar, color = read_avatar(request.form)
    conn = db()
    t = now()
    code = new_code(conn)
    lid = conn.execute(
        "INSERT INTO lobbies (code, minutes, team_size, created_at) VALUES (?,?,?,?)",
        (code, settings["minutes"], 1, t)).lastrowid
    pid = conn.execute(
        "INSERT INTO players (lobby_id, name, joined_at, seen_at, avatar, color) VALUES (?,?,?,?,?,?)",
        (lid, name, t, t, avatar, color)).lastrowid
    conn.execute("UPDATE lobbies SET owner_id = ? WHERE id = ?", (pid, lid))
    conn.commit()
    remember(code, pid)
    lobby = conn.execute("SELECT * FROM lobbies WHERE id = ?", (lid,)).fetchone()
    players = conn.execute("SELECT * FROM players WHERE lobby_id = ?", (lid,)).fetchall()
    problem = start_problem(conn, lobby, players, practice=True)
    if problem:
        flash(problem)
    else:
        begin_round(conn, lobby, players, practice=True)
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
    avatar, color = read_avatar(request.form)
    try:
        pid = conn.execute(
            "INSERT INTO players (lobby_id, name, joined_at, seen_at, avatar, color) VALUES (?,?,?,?,?,?)",
            (lobby["id"], name, t, t, avatar, color)).lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        return fail(409, "Navnet {0} er taget i den lobby. Vælg et andet.".format(name), back)
    remember(code, pid)
    return redirect(url_for("lobby_page", code=code))


@app.post("/skaerm")
def attach_screen():
    """Koble en storskaerm paa en lobby med koden. Det kan man saa laenge
    lobbyen venter paa spillere - samme regel som for at joine. Er man selv
    med i lobbyen, kan man ogsaa bagefter."""
    code = norm_code(request.form.get("code"))
    back = url_for("home", kode=code) if code else url_for("home")
    if limited("join", client_ip()):
        return too_many()
    if not code:
        return fail(400, "Koden er {0} tegn — bogstaver og tal.".format(CODE_LEN), back)
    row = db().execute("SELECT * FROM lobbies WHERE code = ?", (code,)).fetchone()
    if row is None:
        return fail(404, "Der er ingen lobby med koden {0}.".format(code), back)
    lobby = advance(row)
    if screen_in(lobby) is None and me_in(lobby) is None:
        if lobby["status"] != "lobby":
            return fail(409, "Runden i {0} er allerede gået i gang. En storskærm skal kobles på, "
                             "før runden starter.".format(code), back)
        sid = db().execute("INSERT INTO screens (lobby_id, created_at) VALUES (?, ?)",
                           (lobby["id"], now())).lastrowid
        db().commit()
        remember_screen(code, sid)
    return redirect(url_for("screen_page", code=code))


@app.get("/l/<code>/skaerm")
def screen_page(code):
    lobby = lobby_or_404(code)
    viewer_or_403(lobby)
    return render_template("lobby.html", lobby=lobby, me=VIEWER, screen=True,
                           genres=[], minutes_range=MINUTES_RANGE, team_range=TEAM_RANGE,
                           max_upload=MAX_UPLOAD)


@app.get("/l/<code>/skaerm/state")
def screen_state(code):
    lobby = lobby_or_404(code)
    if screen_in(lobby) is None:
        return json_error(403, "Skærmen er ikke koblet på den lobby.", gone=True)
    return jsonify(state_for(db(), lobby, VIEWER, now(), screen=True))


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
    return render_template("lobby.html", lobby=lobby, me=me, screen=False,
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
    db().execute("UPDATE lobbies SET minutes = ?, team_size = ? WHERE id = ?",
                 (settings["minutes"], settings["team_size"], lobby["id"]))
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
    if lobby["status"] in ("spinning", "running", "voting"):
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
    # "Spil alene": ejeren er alene i lobbyen og vil ikke vente.
    practice = request.form.get("solo") == "1"
    problem = start_problem(conn, lobby, players, practice)
    if problem:
        return fail(409, problem, back)
    begin_round(conn, lobby, players, practice)
    return redirect(back)


@app.post("/l/<code>/spin")
def spin_wheel(code):
    """Den der har tur, drejer hjulet. Svaret kommer med i naeste /state."""
    lobby = lobby_or_404(code)
    me = member_or_403(lobby)
    if limited("spin", me["id"]):
        return too_many()
    if lobby["status"] != "spinning":
        return json_error(409, "Hjulet er drejet færdigt.")
    if me["team_id"] is None:
        return json_error(403, "Du er ikke på et hold i den her runde.")
    t = now()
    team = team_on_turn(db(), lobby["id"])
    if team is None or team["id"] != me["team_id"]:
        return json_error(409, "Det er ikke din tur.")
    if t < lobby["turn_at"]:
        return json_error(409, "Vent til hjulet står stille.")
    if spin(db(), lobby["id"], t, by=me["id"], team_id=me["team_id"]) is False:
        return json_error(409, "Det er ikke din tur.")
    return jsonify({"ok": True})


@app.post("/l/<code>/ny-runde")
def next_round(code):
    """En ny runde med de samme spillere og samme indstillinger. Alle fra
    runden er med i den nye lobby (players.from_id), og pointene laegges
    sammen. Trykker ejeren to gange, er det samme nye runde."""
    lobby = lobby_or_404(code)
    me = owner_or_403(lobby)
    back = url_for("lobby_page", code=lobby["code"])
    conn = db()
    if lobby["status"] != "done":
        return fail(409, "Runden er ikke færdig endnu.", back)
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    child = conn.execute("SELECT code FROM lobbies WHERE parent_id = ?", (lobby["id"],)).fetchone()
    if child:
        conn.rollback()
        return redirect(url_for("lobby_page", code=child["code"]))
    t = now()
    new = new_code(conn)
    lid = conn.execute(
        "INSERT INTO lobbies (code, minutes, team_size, created_at, parent_id, round_no)"
        " VALUES (?,?,?,?,?,?)",
        (new, lobby["minutes"], lobby["team_size"], t, lobby["id"], (lobby["round_no"] or 1) + 1)).lastrowid
    owner = None
    players = conn.execute("SELECT * FROM players WHERE lobby_id = ? ORDER BY joined_at",
                           (lobby["id"],)).fetchall()
    for i, pl in enumerate(players):
        pid = conn.execute(
            "INSERT INTO players (lobby_id, name, joined_at, seen_at, from_id, avatar, color)"
            " VALUES (?,?,?,?,?,?,?)",
            (lid, pl["name"], t + i / 1000, pl["seen_at"], pl["id"], pl["avatar"], pl["color"])).lastrowid
        if pl["id"] == me["id"]:
            owner = pid
    conn.execute("UPDATE lobbies SET owner_id = ? WHERE id = ?", (owner, lid))
    conn.execute("INSERT INTO screens (lobby_id, from_id, created_at)"
                 " SELECT ?, id, ? FROM screens WHERE lobby_id = ?", (lid, t, lobby["id"]))
    conn.commit()
    remember(new, owner)
    if lobby["practice"]:
        # Singleplayer: ingen at vente paa - hjulet gaar i gang med det samme.
        fresh = conn.execute("SELECT * FROM lobbies WHERE id = ?", (lid,)).fetchone()
        players = conn.execute("SELECT * FROM players WHERE lobby_id = ?", (lid,)).fetchall()
        if start_problem(conn, fresh, players, practice=True) is None:
            begin_round(conn, fresh, players, practice=True)
    return redirect(url_for("lobby_page", code=new))


@app.post("/l/<code>/afslut")
def finish_phase(code):
    """Ejeren kan lukke en fase foer tid - fx naar alle er klar, eller en
    spiller er gaaet hjem. Fra lav-fasen gaar det til den ekstra tid (saa de
    andre stadig faar den), fra den ekstra tid til afstemningen, og fra
    afstemningen til resultatet."""
    lobby = lobby_or_404(code)
    owner_or_403(lobby)
    back = url_for("lobby_page", code=lobby["code"])
    conn = db()
    phase = phase_of(lobby)
    t = now()
    if lobby["practice"] and phase in ("reveal", "make", "extra"):
        if phase == "reveal":
            conn.execute("UPDATE lobbies SET make_at = MIN(make_at, ?), deadline = MIN(deadline, ?)"
                         " WHERE id = ?", (t, t, lobby["id"]))
        elif phase == "make":
            conn.execute("UPDATE lobbies SET deadline = ? WHERE id = ?", (t, lobby["id"]))
        open_voting(conn, lobby["id"], t)
    elif phase in ("reveal", "make"):
        conn.execute("UPDATE lobbies SET make_at = MIN(make_at, ?), deadline = ?"
                     " WHERE id = ? AND status = 'running'", (t, t, lobby["id"]))
    elif phase == "extra":
        open_voting(conn, lobby["id"], t)
    elif phase == "vote":
        conn.execute("UPDATE lobbies SET status = 'done', done_at = ? WHERE id = ? AND status = 'voting'",
                     (t, lobby["id"]))
    else:
        return fail(409, "Der er ikke noget at afslutte lige nu.", back)
    conn.commit()
    advance(conn.execute("SELECT * FROM lobbies WHERE id = ?", (lobby["id"],)).fetchone())
    return redirect(back)


def safe_download_name(*parts):
    text = " - ".join(p for p in parts if p)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9 ._-]+", "", text).strip()
    return text[:120] or "beat"


@app.get("/l/<code>/beat")
def beat_file(code):
    """Holdets eget beat - hvert hold har sit."""
    lobby = lobby_or_404(code)
    me = member_or_403(lobby)
    if lobby["status"] not in ("running", "voting", "done") or me["team_id"] is None:
        abort(404)
    team = db().execute("SELECT * FROM teams WHERE id = ?", (me["team_id"],)).fetchone()
    genre, _title, beat_id = team_genre(lobby, team)
    beat = db().execute("SELECT * FROM beats WHERE id = ?", (beat_id,)).fetchone() if beat_id else None
    if beat is None:
        abort(404)
    download = request.args.get("hent") == "1"
    return send_file(
        BEATS / beat["file"], conditional=True,
        mimetype="audio/mpeg" if beat["kind"] == "mp3" else "audio/wav",
        as_attachment=download,
        download_name=safe_download_name("Songwars", genre, beat["title"])
        + "." + beat["kind"] if download else None)


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
    if now() > closes_at(lobby) + GRACE_SECONDS:
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
    if fresh["status"] != "running" or now() > closes_at(fresh) + GRACE_SECONDS:
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
    me = me_in(lobby) or (screen_in(lobby) and VIEWER)
    if not me:
        abort(403)
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
    sub = conn.execute("SELECT team_id, position FROM submissions WHERE id = ? AND lobby_id = ?",
                       (sid, lobby["id"])).fetchone()
    if sub is None:
        return json_error(404, "Nummeret findes ikke.")
    if lobby["listen_idx"] is not None and (sub["position"] or 0) > lobby["listen_idx"]:
        return json_error(409, "Nummeret er ikke spillet endnu. Hør det først.")
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


@app.post("/l/<code>/spring-over")
def skip_track(code):
    """"Spring over" under lyt sammen. Vil halvdelen af spillerne det,
    starter det naeste nummer med det samme - for alle."""
    lobby = lobby_or_404(code)
    me = member_or_403(lobby)
    if limited("vote", me["id"]):
        return too_many()
    if lobby["status"] != "voting" or lobby["listen_idx"] is None:
        return json_error(409, "Der spiller ikke noget lige nu.")
    if me["team_id"] is None:
        return json_error(403, "Du var ikke med i runden.")
    data = request.get_json(silent=True) or {}
    if data.get("position") != lobby["listen_idx"]:
        return json_error(409, "Det nummer er allerede forbi.")
    conn = db()
    conn.execute("INSERT OR IGNORE INTO skips (lobby_id, position, player_id) VALUES (?,?,?)",
                 (lobby["id"], lobby["listen_idx"], me["id"]))
    skips = conn.execute("SELECT COUNT(*) FROM skips WHERE lobby_id = ? AND position = ?",
                         (lobby["id"], lobby["listen_idx"])).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM players WHERE lobby_id = ? AND team_id IS NOT NULL",
                         (lobby["id"],)).fetchone()[0]
    skipped = skips >= skips_needed(total)
    if skipped:
        t = now()
        listen_step(conn, lobby, t, skipped_at=t)
    conn.commit()
    return jsonify({"ok": True, "skipped": skipped})


@app.get("/healthz")
def healthz():
    db().execute("SELECT 1").fetchone()
    return "ok", 200, {"Content-Type": "text/plain"}


@app.errorhandler(RequestEntityTooLarge)
def too_large(_e):
    limit = MAX_BEAT if request.endpoint == "admin.admin_beat_create" else MAX_UPLOAD
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
