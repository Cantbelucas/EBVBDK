"""
Songwars - konfiguration og faelles smaating.

Alt kommer fra miljoeet (.env via docker compose). Her ligger ogsaa uret
(now), tekst- og tidsformatering, fejlsvar til formularer og fetch, og
upload til en midlertidig fil. Intet her kender databasen eller spillet.
"""

import logging
import os
import re
import secrets
import sys
import time
import unicodedata
from pathlib import Path

from flask import (
    flash, jsonify, make_response, redirect, render_template, request,
    url_for,
)

import audio

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("songwars")


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
CACHE = DATA / "cache"          # deno og yt-dlp (DENO_DIR, XDG_CACHE_HOME i Dockerfile)

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
# Naar tiden er gaaet, er der ekstra tid til at aflevere - synlig, med sit
# eget ur. Har alle hold afleveret, springes den over.
EXTRA_SECONDS = env_int("SONGWARS_EXTRA_SECONDS", 120, 0, 600)
# Afstemningen lukker af sig selv: saa mange minutter pr. nummer, mindst fem.
# Ellers kan en spiller der er gaaet hjem uden at stemme, holde alle hen.
VOTE_SECONDS_PER_TRACK = env_int("SONGWARS_VOTE_MINUTES_PER_TRACK", 2, 1, 15) * 60
VOTE_MIN_SECONDS = 5 * 60
# Point i en serie af runder med de samme spillere: plads -> point.
PLACE_POINTS = {1: 3, 2: 2, 3: 1}
# Saa laenge har et hold til at dreje hjulet. Bagefter drejer serveren det
# for dem, saa en der er gaaet fra skaermen ikke holder runden op.
SPIN_SECONDS = env_int("SONGWARS_SPIN_SECONDS", 20, 5, 120)
# Beats fra YouTube: hoejst saa lange, og hoejst saa mange ad gangen.
YT_MAX_SECONDS = env_int("SONGWARS_YT_MAX_MINUTES", 15, 1, 60) * 60
YT_PARALLEL = 2
YT_STALE_SECONDS = 15 * 60      # en hentning der "koerer" saa laenge, er doed
SPIN_MS = 5800             # hjulet drejer saa laenge i klienten
SPIN_REST = 2.2            # ... og staar stille saa laenge, foer naeste tur
SPIN_INTRO = 3             # raekkefoelgen vises, foer den foerste tur

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
    "spin": (30, 600),
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

    for folder in (DATA, BEATS, SUBS, TMP, CACHE):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".skrivbar"
            probe.write_text("ok")
            probe.unlink()
        except OSError as exc:
            sys.exit("Songwars kan ikke skrive i {0} ({1}).\n"
                     "Containeren koerer som uid 10001. Paa vaerten:\n"
                     "    sudo chown -R 10001:10001 data".format(folder, exc))


# Uret. Testene skifter _clock ud, saa frister kan testes uden at vente.
_clock = time.time


def now():
    return _clock()


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


def too_many(message="For mange forsøg. Vent lidt, og prøv igen."):
    if wants_json():
        resp = json_error(429, message)
    else:
        resp = make_response((render_template("error.html", title="Ro på",
                                                  message=message), 429))
    resp.headers["Retry-After"] = "60"
    return resp


def remove_file(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("kunne ikke slette %s", path)


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
