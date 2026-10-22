"""
Songwars - tests.

    python tests/test_songwars.py

Koerer hele appen igennem med Flasks testklient: opret og join, hjulet,
aflevering med frister, afstemning, resultat, admin, YouTube og oprydning.
Uret er falsk, saa frister kan testes uden at vente. Intet rammer nettet:
YouTube-hentningen er skiftet ud med en falsk.

Lydfilerne i fixtures/ er korte toner lavet med ffmpeg. wav-filerne og de
tilfaeldige bytes laves her, saa de ikke skal ligge i git.
"""

import io
import math
import os
import random
import re
import shutil
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_DIR = HERE.parent
FIX = HERE / "fixtures"
DATA = Path(tempfile.mkdtemp(prefix="songwars-test-"))

os.environ.update({
    "SONGWARS_DATA": str(DATA),
    "SONGWARS_SECRET_KEY": "t" * 64,
    "SONGWARS_ADMIN_PASSWORD": "admin-password-123",
    "SONGWARS_COOKIE_SECURE": "0",
    "SONGWARS_IP_HEADER": "CF-Connecting-IP",
})
sys.path.insert(0, str(APP_DIR))
import admin  # noqa: E402
import app  # noqa: E402
import core  # noqa: E402
import database  # noqa: E402
import game  # noqa: E402

clock = [time.time()]
core._clock = lambda: clock[0]

ok_n = bad_n = 0


def check(label, cond, extra=""):
    global ok_n, bad_n
    if cond:
        ok_n += 1
        print("ok  ", label)
    else:
        bad_n += 1
        print("FEJL", label, extra)


def csrf(cl):
    cl.get("/")
    with cl.session_transaction() as s:
        return s["csrf"]


ip_counter = [0]


def client(ip=None):
    cl = app.app.test_client()
    if ip is None:
        ip_counter[0] += 1
        ip = "10.0.0.%d" % ip_counter[0]
    cl.environ_base["HTTP_CF_CONNECTING_IP"] = ip
    cl.token = csrf(cl)
    return cl


def post(cl, url, data=None, **kw):
    data = dict(data or {})
    data.setdefault("csrf", cl.token)
    return cl.post(url, data=data, **kw)


def flashes(cl):
    with cl.session_transaction() as s:
        return [m for _, m in s.pop("_flashes", [])]


def state(cl, code):
    r = cl.get("/l/%s/state" % code)
    return r.status_code, r.get_json()


def make_wav(seconds, width, rate=44100, channels=2):
    """En tone som wav: 16 eller 24 bit."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        peak = 2 ** (8 * width - 1) - 1
        frames = bytearray()
        for i in range(int(seconds * rate)):
            v = int(peak * 0.3 * math.sin(2 * math.pi * 330 * i / rate))
            frames += struct.pack("<i", v)[:width] * channels
        w.writeframes(bytes(frames))
    return buf.getvalue()


wav = make_wav(3, 2)
wav24 = make_wav(3, 3)
mp3 = (FIX / "tone.mp3").read_bytes()
random_bytes = random.Random(7).randbytes(300000)

# ---------- konfiguration ----------
import subprocess  # noqa: E402
env = dict(os.environ, SONGWARS_SECRET_KEY="kort", SONGWARS_ADMIN_PASSWORD="x")
r = subprocess.run([sys.executable, "-c", "import app"], cwd=str(APP_DIR), env=env,
                   capture_output=True, text=True, encoding="utf-8")
check("starter ikke uden hemmeligheder", r.returncode != 0 and "SONGWARS_SECRET_KEY" in r.stderr
      and "SONGWARS_ADMIN_PASSWORD" in r.stderr, r.stderr[-300:])

# ---------- sikkerhedsheaders og CSRF ----------
anon = client()
r = anon.get("/")
check("CSP-header sat", "default-src 'none'" in r.headers.get("Content-Security-Policy", "")
      and "style-src 'self'" in r.headers["Content-Security-Policy"])
html = r.get_data(as_text=True)
check("ingen inline-scripts eller handlers paa forsiden",
      not re.search(r"<script(?![^>]*\bsrc=)|\son[a-z]+=|\sstyle=\"", html))
r = anon.post("/ny", data={"name": "x", "minutes": "5", "team_size": "1"})
check("POST uden CSRF afvises", r.status_code == 302 and "Siden var for gammel" in " ".join(flashes(anon)))

# ---------- admin ----------
adm = client("9.9.9.9")
r = post(adm, "/admin/genre", {"name": "Trap"})
check("admin-handling uden login -> login", r.status_code == 302 and r.headers["Location"].endswith("/admin"))
r = post(adm, "/admin/login", {"password": "forkert"})
check("forkert adgangskode", "Forkert adgangskode." in flashes(adm))
for _ in range(6):
    post(adm, "/admin/login", {"password": "forkert"})
r = post(adm, "/admin/login", {"password": "admin-password-123"})
check("rate limit paa admin-login (ogsaa med rigtig kode)", r.status_code == 429)
clock[0] += 1000
r = post(adm, "/admin/login", {"password": "admin-password-123"})
check("login efter ventetid", r.status_code == 302 and adm.get("/admin").status_code == 200
      and "Beats og genrer" in adm.get("/admin").get_data(as_text=True))
adm.token = csrf(adm)                                  # ny session efter login

post(adm, "/admin/genre", {"name": "Trap"})
post(adm, "/admin/genre", {"name": "Lofi"})
post(adm, "/admin/genre", {"name": "Tom genre"})
r = post(adm, "/admin/genre", {"name": "trap"})
check("genre-navn er unikt uden store/smaa bogstaver", "findes allerede" in " ".join(flashes(adm)))
gid = {r["name"]: r["id"] for r in database.connect().execute("SELECT id, name FROM genres")}

def beat(title, genre, blob, name):
    return post(adm, "/admin/beat", {"title": title, "genre": str(gid[genre]),
                                     "file": (io.BytesIO(blob), name)}, content_type="multipart/form-data")

beat("Trap beat 1", "Trap", mp3, "b1.mp3")
beat("Trap beat 2", "Trap", wav24, "b2.wav")
beat("Lofi beat", "Lofi", wav24, "lofi.wav")
r = beat("Falsk", "Lofi", (FIX / "m4a_as.mp3").read_bytes(), "falsk.mp3")
check("admin: forklaedt m4a afvist", "hverken mp3 eller wav" in " ".join(flashes(adm)))
big = wav + b"\0" * (60 * 1024 * 1024)
big = big[:4] + (len(big) - 8).to_bytes(4, "little") + big[8:]
r = beat("Stor", "Lofi", big, "stor.wav")
check("admin maa uploade beats over 50 MB", r.status_code == 302 and
      database.connect().execute("SELECT COUNT(*) FROM beats WHERE title='Stor'").fetchone()[0] == 1, r.status_code)
files = sorted(p.name for p in core.BEATS.iterdir())
check("beats gemt under tilfaeldige navne", all(re.fullmatch(r"[0-9a-f]{32}\.(mp3|wav)", f) for f in files) and len(files) == 4, files)
r = post(adm, "/admin/genre/%d/slet" % gid["Trap"])
check("genre med beats kan ikke slettes", "har beats" in " ".join(flashes(adm)))

# ---------- opret og join ----------
owner = client()
r = post(owner, "/ny", {"name": "Lucas", "minutes": "5", "team_size": "2", "genre": "",
                        "avatar": "🦊", "color": "blaa"})
code = r.headers["Location"].rsplit("/", 1)[-1]
check("lobby oprettet med 5-tegns kode", r.status_code == 302 and re.fullmatch(r"[A-Z2-9]{5}", code), code)
s, st = state(owner, code)
check("ejer ser lobby-fasen", s == 200 and st["phase"] == "lobby" and st["me"]["owner"])
check("start blokeret: for faa spillere", "mindst 4 spillere" in (st["start_problem"] or ""), st["start_problem"])

players = {"Lucas": owner}
for name in ("Fjolli", "Cc", "Mads", "Sofie"):
    cl = client()
    extra = {"avatar": "<script>", "color": "url(x)"} if name == "Fjolli" else {"avatar": "🎧", "color": "groen"}
    r = post(cl, "/join", dict({"code": code.lower(), "name": name}, **extra))
    players[name] = cl
check("join med kode i smaa bogstaver", state(players["Sofie"], code)[0] == 200)
faces = {p_["name"]: (p_["avatar"], p_["color"]) for p_ in state(owner, code)[1]["players"]}
check("avatar og farve gemmes - kun dem fra listen", faces["Lucas"] == ("🦊", "blaa")
      and faces["Cc"] == ("🎧", "groen") and faces["Fjolli"] == (None, None), faces)

# Storskaerm: kobles paa med koden, foer runden starter.
scr = client()
r = post(scr, "/skaerm", {"code": code})
check("storskaerm kobles paa foer start", r.status_code == 302 and r.headers["Location"].endswith("/l/%s/skaerm" % code))
r = scr.get("/l/%s/skaerm" % code)
check("storskaermens side svarer", r.status_code == 200 and "is-screen" in r.get_data(as_text=True)
      and "upload-form" not in r.get_data(as_text=True) and "start-btn" not in r.get_data(as_text=True))
r = scr.get("/l/%s/skaerm/state" % code)
check("storskaermen faar /state uden at vaere spiller", r.status_code == 200 and r.get_json()["screen"]
      and r.get_json()["me"]["name"] == "Storskærm" and not r.get_json()["me"]["owner"])
check("storskaermen er ikke med paa spillerlisten", len(r.get_json()["players"]) == 5)
check("storskaermen kan ikke spille", scr.get("/l/%s/state" % code).status_code == 403
      and post(scr, "/l/%s/start" % code).status_code == 403)
outsider = client()
check("en tilfaeldig kan ikke se skaermens state", outsider.get("/l/%s/skaerm/state" % code).status_code == 403)
dup = client()
post(dup, "/join", {"code": code, "name": "fjolli"})
check("navnet er unikt i lobbyen", "er taget" in " ".join(flashes(dup)))
nope = client()
post(nope, "/join", {"code": "ZZZZZ", "name": "x"})
check("ukendt kode", "ingen lobby med koden" in " ".join(flashes(nope)))
s, _ = state(nope, code)
check("ikke-medlem faar 403 paa /state", s == 403)

spammer = client("6.6.6.6")
for i in range(14):
    r = post(spammer, "/join", {"code": "AAAA%d" % (i % 8 + 2), "name": "s"})
check("rate limit paa join", r.status_code == 429)

s, st = state(owner, code)
check("alle ser hvem der er med", [p["name"] for p in st["players"]] == ["Lucas", "Fjolli", "Cc", "Mads", "Sofie"])
s, st2 = state(players["Cc"], code)
check("andre er ikke ejer", not st2["me"]["owner"] and st2["players"][0]["owner"])

r = post(players["Cc"], "/l/%s/start" % code)
check("kun ejeren kan starte", r.status_code == 403)
r = post(players["Cc"], "/l/%s/indstillinger" % code, {"minutes": "1", "team_size": "1"})
check("kun ejeren kan aendre indstillinger", r.status_code == 403)

# smid ud
r = post(owner, "/l/%s/smid-ud" % code, {"player": str(st["players"][4]["id"])})
check("ejeren smider Sofie ud", len(state(owner, code)[1]["players"]) == 4)
s, _ = state(players["Sofie"], code)
check("Sofie faar 403 bagefter", s == 403)

post(owner, "/l/%s/indstillinger" % code, {"minutes": "3", "team_size": "2"})
s, st = state(owner, code)
check("indstillinger gemt", st["settings"] == {"minutes": 3, "team_size": 2}, st["settings"])

# ---------- start og genrehjulet ----------
r = post(owner, "/l/%s/start" % code)
s, st = state(owner, code)
check("hjulet efter start", st["phase"] == "spin" and st["spin"]["wheel"] == ["Lofi", "Trap"]
      and len(st["spin"]["order"]) == 2 and st["spin"]["current"] in (1, 2), st.get("spin"))
order = [o["number"] for o in st["spin"]["order"]]
check("raekkefoelgen er hvert hold en gang", sorted(order) == [1, 2], order)
turn = {}
for name, cl in players.items():
    if name == "Sofie":
        continue
    turn[name] = state(cl, code)[1]["spin"]["my_turn"]
spinner = [n for n, mine in turn.items() if mine]
waiter = [n for n, mine in turn.items() if not mine]
check("kun det hold der har tur, kan dreje", len(spinner) == 2 and len(waiter) == 2, turn)

def spin_req(cl):
    return cl.post("/l/%s/spin" % code, headers={"X-CSRF-Token": cl.token, "X-Requested-With": "fetch"})

r = spin_req(players[waiter[0]])
check("dreje uden for tur afvises", r.status_code == 409 and "ikke din tur" in r.get_json()["error"], r.get_json())
r = spin_req(players[spinner[0]])
check("dreje foer intro er forbi afvises", r.status_code == 409 and "står stille" in r.get_json()["error"], r.get_json())
clock[0] += core.SPIN_INTRO
r = spin_req(players[spinner[0]])
check("holdet der har tur, drejer", r.status_code == 200, r.get_json())
r = spin_req(players[spinner[1]])
check("samme hold kan ikke dreje to gange", r.status_code == 409)
s, st = state(owner, code)
first = st["spin"]["order"][0]
check("foerste hold har en genre, drejet af en spiller", first["genre"] in ("Trap", "Lofi") and not first["auto"]
      and st["spin"]["current"] == st["spin"]["order"][1]["number"], st["spin"])
r = spin_req(players[waiter[0]])
check("naeste hold venter til hjulet staar stille", r.status_code == 409 and "står stille" in r.get_json()["error"])

# Ingen drejer: serveren goer det efter SPIN_SECONDS.
clock[0] += core.SPIN_MS / 1000 + core.SPIN_REST + core.SPIN_SECONDS + 0.1
s, st = state(owner, code)
genres = [o["genre"] for o in st["spin"]["order"]]
check("serveren drejer selv for holdet der ikke goer", st["spin"]["order"][1]["auto"] and st["phase"] == "spin", st["spin"])
check("alle hold har hver sin genre", sorted(genres) == ["Lofi", "Trap"], genres)
row = database.connect().execute("SELECT turn_at FROM lobbies WHERE code=?", (code,)).fetchone()
clock[0] = row[0] + 0.1
s, st = state(owner, code)
mine_genre = [t["genre"] for t in st["round"]["teams"] if t["mine"]][0]
beats_in = {"Trap": ("Trap beat 1", "Trap beat 2"), "Lofi": ("Lofi beat", "Stor")}
check("reveal efter hjulet: holdets egen genre og et beat i den", st["phase"] == "reveal"
      and st["round"]["genre"] == mine_genre and st["round"]["beat"] in beats_in[mine_genre], st.get("round"))
teams = st["round"]["teams"]
check("to hold af to", len(teams) == 2 and sorted(len(t["members"]) for t in teams) == [2, 2], teams)
r = post(owner, "/l/%s/start" % code)
check("dobbelt start goer intet", len(state(owner, code)[1]["round"]["teams"]) == 2)
late = client()
post(late, "/join", {"code": code, "name": "Sen"})
check("join efter start afvises", "allerede gået i gang" in " ".join(flashes(late)))
r = post(players["Mads"], "/l/%s/forlad" % code)
check("man kan ikke forlade en runde", "Runden er i gang" in " ".join(flashes(players["Mads"]))
      and state(players["Mads"], code)[0] == 200)

# beat kan hentes
cc_genre = state(players["Cc"], code)[1]["round"]["genre"]
r = players["Cc"].get(st["round"]["beat_url"] + "?hent=1")
check("beatet kan hentes som fil, i holdets genre", r.status_code == 200 and "attachment" in r.headers.get("Content-Disposition", "")
      and "Songwars - " + cc_genre in r.headers["Content-Disposition"], r.headers.get("Content-Disposition"))
r = nope.get(st["round"]["beat_url"])
check("ikke-medlem kan ikke hente beatet", r.status_code == 403)
r = post(adm, "/admin/beat/%s/slet" % database.connect().execute(
    "SELECT t.beat_id FROM teams t JOIN lobbies l ON l.id = t.lobby_id WHERE l.code=? LIMIT 1", (code,)).fetchone()[0])
check("beat i brug kan ikke slettes", "bruges i en runde" in " ".join(flashes(adm)))

# ---------- aflevering ----------
clock[0] += core.REVEAL_SECONDS + 1
s, st = state(owner, code)
check("make-fase efter reveal", st["phase"] == "make" and st["make"]["delivered"] == [])

by_team = {}
for name, cl in players.items():
    if name == "Sofie":
        continue
    by_team.setdefault(state(cl, code)[1]["me"]["team"], []).append(name)
t1, t2 = sorted(by_team)
a, b = by_team[t1]
c, d = by_team[t2]

def upload(cl, blob, filename):
    return cl.post("/l/%s/aflever" % code, data={"track": (io.BytesIO(blob), filename)},
                   headers={"X-CSRF-Token": cl.token, "X-Requested-With": "fetch"},
                   content_type="multipart/form-data")

r = upload(players[a], random_bytes, "nummer.mp3")
check("tilfaeldige bytes med .mp3 afvises", r.status_code == 415, r.get_json())
r = upload(players[a], (FIX / "ogg_as.mp3").read_bytes(), "nummer.mp3")
check("ogg forklaedt som mp3 afvises", r.status_code == 415)
r = upload(players[a], (FIX / "truncated.mp3").read_bytes(), "halv.mp3")
check("ufuldstaendig mp3 afvises", r.status_code == 415 and "ufuldstændig" in r.get_json()["error"])
r = upload(players[a], wav24, "../../etc/passwd.wav")
check("gyldig wav godtages", r.status_code == 200 and r.get_json()["ok"], r.get_json())
files = [p.name for p in core.SUBS.iterdir()]
check("gemt under tilfaeldigt navn, aldrig brugerens", len(files) == 1 and re.fullmatch(r"[0-9a-f]{32}\.wav", files[0])
      and not any("passwd" in p.name for p in DATA.rglob("*")), files)
first_file = files[0]
r = upload(players[b], mp3, "v2.mp3")
files = [p.name for p in core.SUBS.iterdir()]
check("nyt upload fra holdkammerat erstatter", r.status_code == 200 and len(files) == 1 and files[0] != first_file
      and files[0].endswith(".mp3"), files)
s, st = state(players[a], code)
check("holdet ser sin aflevering", st["make"]["mine"]["by"] == b and st["make"]["delivered"] == [t1])
s, st_other = state(players[c], code)
check("andet hold ser kun at der er afleveret", st_other["make"]["mine"] is None and st_other["make"]["delivered"] == [t1])
r = players[c].get(st["make"]["mine"]["url"])
check("andet hold kan ikke lytte med under runden", r.status_code == 403)
r = players[a].get(st["make"]["mine"]["url"])
check("eget hold kan lytte", r.status_code == 200 and r.mimetype == "audio/mpeg")

big = b"x" * (51 * 1024 * 1024)
r = upload(players[c], big, "stor.wav")
check("over 50 MB afvises med 413", r.status_code == 413, r.status_code)
check("ingen rester i tmp efter afviste filer", list(core.TMP.iterdir()) == [], list(core.TMP.iterdir()))

for _ in range(12):
    upload(players[d], random_bytes, "x.mp3")
r = upload(players[d], wav24, "ok.wav")
check("rate limit paa upload pr. spiller", r.status_code == 429)

# fristen haandhaeves paa serveren - med ekstra tid efter den
deadline = database.connect().execute("SELECT deadline FROM lobbies WHERE code=?", (code,)).fetchone()[0]
clock[0] = deadline + 1
s, st = state(players[c], code)
check("ekstra tid naar tiden er gaaet", st["phase"] == "extra"
      and st["round"]["closes"] == core.ms(deadline + core.EXTRA_SECONDS) and st["make"]["delivered"] == [t1], st.get("round"))
clock[0] = deadline + core.EXTRA_SECONDS + core.GRACE_SECONDS - 1
r = upload(players[c], wav24, "lige-i-tide.wav")
check("upload i den ekstra tid (inkl. graceperioden) godtages", r.status_code == 200, r.get_json())
s, st = state(players[c], code)
check("alle hold har afleveret -> afstemning med det samme", st["phase"] == "vote", st["phase"])
late_scr = client()
r = post(late_scr, "/skaerm", {"code": code})
check("storskaerm kan ikke kobles paa efter start", "allerede gået i gang" in " ".join(flashes(late_scr)))
r = players[a].get("/l/%s/skaerm" % code)
check("en spiller kan aabne storskaermen bagefter", r.status_code == 200)
st_scr = scr.get("/l/%s/skaerm/state" % code).get_json()
r = scr.get(st_scr["vote"]["tracks"][0]["url"])
check("storskaermen kan afspille numrene under afstemningen", r.status_code == 200
      and st_scr["vote"]["my_need"] == 0 and not any(t_["mine"] for t_ in st_scr["vote"]["tracks"]))
check("en udenforstaaende kan ikke afspille numrene", nope.get(st_scr["vote"]["tracks"][0]["url"]).status_code == 403)
r = upload(players[c], wav, "for-sent.wav")
check("upload efter fristen afvises", r.status_code == 409 and "lukket" in r.get_json()["error"], r.get_json())

# ---------- afstemning ----------
s, st = state(players[a], code)
check("afstemning starter af sig selv efter fristen", st["phase"] == "vote" and len(st["vote"]["tracks"]) == 2)
labels = [t["label"] for t in st["vote"]["tracks"]]
check("numrene er anonyme: kun Nummer N", labels == ["Nummer 1", "Nummer 2"]
      and not any(k in str(st["vote"]) for k in (a, b, c, d, "by")), st["vote"])
mine_id = [t["id"] for t in st["vote"]["tracks"] if t["mine"]][0]
theirs_id = [t["id"] for t in st["vote"]["tracks"] if not t["mine"]][0]
r = players[c].get([t["url"] for t in st["vote"]["tracks"]][0])
cd = r.headers.get("Content-Disposition", "")
check("lydfilen siger ikke hvem der lavede den", r.status_code == 200 and re.fullmatch(r"inline; filename=nummer\.(mp3|wav)", cd)
      and not any(n in cd for n in (a, b, c, d)), cd)

def vote(cl, track, flames):
    return cl.post("/l/%s/stem" % code, json={"track": track, "flames": flames},
                   headers={"X-CSRF-Token": cl.token, "X-Requested-With": "fetch"})


def skip(cl, position):
    return cl.post("/l/%s/spring-over" % code, json={"position": position},
                   headers={"X-CSRF-Token": cl.token, "X-Requested-With": "fetch"})


# ---------- lyt sammen ----------
lst = st["vote"]["listen"]
voting_at = database.connect().execute("SELECT voting_at FROM lobbies WHERE code=?", (code,)).fetchone()[0]
check("lyt sammen: Nummer 1 starter om et ojeblik - ingen frist endnu", lst["position"] == 1 and not lst["done"]
      and lst["started"] == core.ms(voting_at + core.LISTEN_INTRO) and st["vote"]["deadline"] is None
      and lst["needed"] == 2 and lst["skips"] == 0, lst)
track2 = [t_ for t_ in st["vote"]["tracks"] if t_["position"] == 2][0]
judge = c if track2["mine"] else a
r = vote(players[judge], track2["id"], 4)
check("lyt sammen: man kan ikke stemme paa et nummer, der ikke er spillet endnu",
      r.status_code == 409 and "ikke spillet" in r.get_json()["error"], r.get_json())
clock[0] = voting_at + core.LISTEN_INTRO + 1
r = skip(players[a], 1)
check("spring over: en af fire er ikke nok", r.status_code == 200 and r.get_json()["skipped"] is False)
skip(players[a], 1)
lst = state(players[a], code)[1]["vote"]["listen"]
check("spring over: taeller kun en gang pr. spiller", lst["skips"] == 1 and lst["skipped"] and lst["position"] == 1, lst)
r = skip(players[b], 1)
skip_at = clock[0]
lst = state(players[c], code)[1]["vote"]["listen"]
check("spring over: halvdelen -> naeste nummer med det samme, for alle", r.get_json()["skipped"]
      and lst["position"] == 2 and lst["started"] == core.ms(skip_at) and lst["skips"] == 0 and not lst["skipped"], lst)
r = skip(players[c], 1)
check("spring over: et nummer der er forbi, kan ikke springes over", r.status_code == 409)
secs2 = database.connect().execute("SELECT seconds FROM submissions WHERE id = ?", (track2["id"],)).fetchone()[0]
clock[0] = skip_at + secs2 + core.LISTEN_GAP + 0.1
s, st = state(players[a], code)
end2 = skip_at + secs2 + core.LISTEN_GAP
check("lyt sammen: efter sidste nummer kommer fristen for de sidste flammer", st["vote"]["listen"]["done"]
      and st["vote"]["deadline"] == core.ms(end2 + core.VOTE_AFTER_SECONDS),
      (st["vote"]["deadline"], core.ms(end2 + core.VOTE_AFTER_SECONDS), track2["seconds"]))

r = vote(players[a], mine_id, 5)
check("kan ikke stemme paa eget hold", r.status_code == 403)
r = vote(players[a], theirs_id, 6)
check("6 flammer afvises", r.status_code == 400)
r = vote(players[a], theirs_id, True)
check("true som flammer afvises", r.status_code == 400)
check("stem 4", vote(players[a], theirs_id, 4).status_code == 200)
check("skift stemme til 3", vote(players[a], theirs_id, 3).status_code == 200)
s, st = state(players[a], code)
check("min stemme vises, fremdrift taelles", [t["flames"] for t in st["vote"]["tracks"] if not t["mine"]] == [3]
      and st["vote"]["done_players"] == 1 and st["vote"]["voters"] == 4, st["vote"])
vote(players[b], theirs_id, 4)
vote(players[c], mine_id, 5)
s, st = state(owner, code)
check("ikke faerdig foer alle har stemt", st["phase"] == "vote")
vote(players[d], mine_id, 2)
s, st = state(owner, code)
check("resultat naar alle har stemt", st["phase"] == "result", st["phase"])
rk = st["result"]["ranking"]
check("rangliste efter gennemsnit", [r["average"] for r in rk] == [3.5, 3.5] and [r["place"] for r in rk] == [1, 1], rk)
check("navne afsloeres i resultatet", sorted(rk[0]["members"] + rk[1]["members"]) == sorted([a, b, c, d]))
stats = {s_["label"]: s_["text"] for s_ in st["result"]["stats"]}
check("rundens tal: hårdeste dommer og gavmildeste", stats.get("Hårdeste dommer") == d + " gav i snit 2,0"
      and stats.get("Gavmildeste") == c + " gav i snit 5,0", stats)
check("rundens tal: mest uenige", stats.get("Mest uenige") == "Hold %d fik både 2 og 5 flammer" % t1, stats)
check("rundens tal: først færdig og sidst ind", "Først færdig" in stats and "afleverede" in stats["Først færdig"]
      and any(k in stats for k in ("Lige på stregen", "Sidst ind")), stats)
r = vote(players[a], theirs_id, 5)
check("kan ikke stemme efter resultatet", r.status_code == 409)

# ---------- ny runde med samme spillere ----------
not_owner = next(n for n in (a, b, c, d) if n != "Lucas")
r = post(players[not_owner], "/l/%s/ny-runde" % code)
check("kun ejeren kan starte en ny runde", r.status_code == 403)
r = post(owner, "/l/%s/ny-runde" % code)
code_r2 = r.headers["Location"].rsplit("/", 1)[-1]
check("ny runde: ny lobby", r.status_code == 302 and code_r2 != code and re.fullmatch(r"[A-Z2-9]{5}", code_r2), code_r2)
r = post(owner, "/l/%s/ny-runde" % code)
check("ny runde to gange giver samme lobby", r.headers["Location"].rsplit("/", 1)[-1] == code_r2)
s, st = state(players[c], code)
check("den gamle runde peger paa den nye", st["next"] and st["next"]["code"] == code_r2, st.get("next"))
s, st = state(players[c], code_r2)
check("spillerne er med i den nye runde uden at joine", s == 200 and st["phase"] == "lobby"
      and st["round_no"] == 2 and sorted(p_["name"] for p_ in st["players"]) == sorted([a, b, c, d])
      and st["me"]["name"] == c and st["me"]["owner"] == (c == "Lucas"), (s, st and st.get("players")))
check("ejeren er stadig ejer", state(owner, code_r2)[1]["me"]["owner"])
check("indstillingerne foelger med", st["settings"] == {"minutes": 3, "team_size": 2}, st["settings"])
ser = st["series"]
check("stillingen: begge hold delte 1. plads -> 3 point til alle", ser and ser["rounds"] == 1
      and all(row["points"] == 3 and row["wins"] == 1 for row in ser["standings"]) and len(ser["standings"]) == 4
      and [row["me"] for row in ser["standings"]].count(True) == 1, ser)
s, _ = state(nope, code_r2)
check("en udenforstaaende er ikke med i den nye runde", s == 403)
# Pladserne regnes rigtigt: gennemsnit 4, 2 og 2 giver plads 1, 2 og 2 - og et nummer uden stemmer ingen plads.
conn = database.connect()
lid_x = conn.execute("INSERT INTO lobbies (code, minutes, team_size, created_at, status) VALUES ('PLADS', 1, 1, ?, 'done')",
                     (clock[0],)).lastrowid
tids = [conn.execute("INSERT INTO teams (lobby_id, number) VALUES (?, ?)", (lid_x, n)).lastrowid for n in (1, 2, 3, 4)]
voter = conn.execute("INSERT INTO players (lobby_id, name, joined_at, seen_at) VALUES (?, 'V', 0, 0)", (lid_x,)).lastrowid
for i, (tid, flames) in enumerate(zip(tids, (4, 2, 2, None))):
    sid = "%032x" % (900 + i)
    conn.execute("INSERT INTO submissions (id, lobby_id, team_id, file, kind, size, seconds, uploaded_at)"
                 " VALUES (?,?,?,?,?,?,?,?)", (sid, lid_x, tid, sid + ".wav", "wav", 1, 3, 0))
    if flames:
        conn.execute("INSERT INTO votes (submission_id, player_id, flames, at) VALUES (?,?,?,0)", (sid, voter, flames))
conn.commit()
pl = game.team_places(conn, lid_x)
check("pladser: 4 -> 1, 2 og 2 -> delt 2., ingen stemmer -> ingen plads",
      [pl[tid][2] for tid in tids] == [1, 2, 2, None], [pl[tid] for tid in tids])
conn.execute("DELETE FROM lobbies WHERE id = ?", (lid_x,)); conn.commit(); conn.close()

ny = client(); post(ny, "/join", {"code": code_r2, "name": "Ny"})
check("en ny spiller kan joine runde 2 med koden", any(p_["name"] == "Ny" for p_ in state(ny, code_r2)[1]["players"]))
check("avataren foelger med i den nye runde", {p_["name"]: p_["avatar"] for p_ in state(owner, code_r2)[1]["players"]}["Lucas"] == "🦊")
r = scr.get("/l/%s/skaerm/state" % code_r2)
check("storskaermen foelger med i den nye runde", r.status_code == 200 and r.get_json()["round_no"] == 2)
check("skaermens 'naeste runde' peger paa skaermen", scr.get("/l/%s/skaerm/state" % code).get_json()["next"]["url"]
      == "/l/%s/skaerm" % code_r2)

# ---------- solo, tom runde og ejer der lukker ----------
o2 = client()
r = post(o2, "/ny", {"name": "Ejer", "minutes": "10", "team_size": "1", "genre": ""})
code2 = r.headers["Location"].rsplit("/", 1)[-1]
p2 = client(); post(p2, "/join", {"code": code2, "name": "Anden"})
p3 = client(); post(p3, "/join", {"code": code2, "name": "Tredje"})
s, st = state(o2, code2)
check("start blokeret: flere hold end genrer med beats", "hver sin" in (st["start_problem"] or ""), st["start_problem"])
post(o2, "/l/%s/forlad" % code2)
s, st = state(p2, code2)
check("ejer forlader lobby: ejerskab gaar videre", s == 200 and st["me"]["owner"] and len(st["players"]) == 2)
p2.token = csrf(p2)
post(p2, "/l/%s/start" % code2)
clock[0] += core.SPIN_INTRO + 2 * (core.SPIN_SECONDS + core.SPIN_MS / 1000 + core.SPIN_REST) + 1
s, st = state(p2, code2)
check("solo: et hold pr. spiller, og ingen der drejer -> serveren drejer for alle", st["phase"] == "reveal"
      and st["round"]["solo"] and sorted(t["genre"] for t in st["round"]["teams"]) == ["Lofi", "Trap"], st.get("round"))
clock[0] += core.REVEAL_SECONDS + 1
post(p2, "/l/%s/afslut" % code2)
s, st = state(p3, code2)
check("ejer stopper tiden tidligt -> ekstra tid", st["phase"] == "extra"
      and st["round"]["closes"] == core.ms(clock[0] + core.EXTRA_SECONDS), st.get("round"))
deadline_before = database.connect().execute("SELECT deadline FROM lobbies WHERE code=?", (code2,)).fetchone()[0]
post(p2, "/l/%s/afslut" % code2)
check("at lukke den ekstra tid flytter ikke fristen (rundens tal bruger den)", database.connect().execute(
      "SELECT deadline FROM lobbies WHERE code=?", (code2,)).fetchone()[0] == deadline_before)
s, st = state(p3, code2)
check("ejer lukker den ekstra tid; ingen afleveringer -> direkte resultat", st["phase"] == "result"
      and st["result"]["ranking"] == [] and len(st["result"]["missing"]) == 2, st.get("result"))

# ---------- ekstra tid der loeber ud, og afstemningens frist ----------
o5 = client()
r = post(o5, "/ny", {"name": "Ejer5", "minutes": "5", "team_size": "1"})
code5 = r.headers["Location"].rsplit("/", 1)[-1]
p5 = client(); post(p5, "/join", {"code": code5, "name": "Femte"})
post(o5, "/l/%s/start" % code5)
clock[0] += core.SPIN_INTRO + 2 * (core.SPIN_SECONDS + core.SPIN_MS / 1000 + core.SPIN_REST) + core.REVEAL_SECONDS + 2
check("frist-lobby: lav-fase", state(o5, code5)[1]["phase"] == "make")
r = p5.post("/l/%s/aflever" % code5, data={"track": (io.BytesIO(wav24), "x.wav")},
            headers={"X-CSRF-Token": p5.token, "X-Requested-With": "fetch"}, content_type="multipart/form-data")
check("frist-lobby: den ene afleverer", r.status_code == 200)
deadline5 = database.connect().execute("SELECT deadline FROM lobbies WHERE code=?", (code5,)).fetchone()[0]
clock[0] = deadline5 + core.EXTRA_SECONDS + core.GRACE_SECONDS + 0.5
r = o5.post("/l/%s/aflever" % code5, data={"track": (io.BytesIO(wav24), "x.wav")},
            headers={"X-CSRF-Token": o5.token, "X-Requested-With": "fetch"}, content_type="multipart/form-data")
check("upload efter den ekstra tid afvises", r.status_code == 409 and "lukket" in r.get_json()["error"], r.get_json())
s, st = state(o5, code5)
check("den ekstra tid loeber ud -> afstemning, lyt sammen foerst", st["phase"] == "vote"
      and st["vote"]["listen"]["position"] == 1 and st["vote"]["deadline"] is None, st.get("vote"))
clock[0] += core.LISTEN_INTRO + 3 + core.LISTEN_GAP + 0.1        # nummeret er 3 sekunder
s, st = state(o5, code5)
row5 = database.connect().execute("SELECT listen_at, vote_deadline FROM lobbies WHERE code=?", (code5,)).fetchone()
check("naar nummeret er spillet: frist for de sidste flammer", st["vote"]["listen"]["done"]
      and row5[1] - row5[0] == core.VOTE_AFTER_SECONDS and st["vote"]["deadline"] == core.ms(row5[1]), tuple(row5))
clock[0] = row5[1] + 1
s, st = state(p5, code5)
check("afstemningen lukker af sig selv, selvom ingen har stemt", st["phase"] == "result", st["phase"])

# ---------- singleplayer ----------
solo = client()
r = post(solo, "/alene", {"name": "Ene", "minutes": "5", "avatar": "🌙", "color": "lilla"})
code_s = r.headers["Location"].rsplit("/", 1)[-1]
s, st = state(solo, code_s)
check("spil alene: hjulet gaar i gang med det samme", s == 200 and st["phase"] == "spin" and st["practice"]
      and len(st["spin"]["order"]) == 1 and st["players"][0]["avatar"] == "🌙", st.get("phase"))
clock[0] += core.SPIN_INTRO
r = solo.post("/l/%s/spin" % code_s, headers={"X-CSRF-Token": solo.token, "X-Requested-With": "fetch"})
check("spil alene: man drejer selv", r.status_code == 200, r.get_json())
clock[0] += core.SPIN_MS / 1000 + core.SPIN_REST + core.REVEAL_SECONDS + 1
s, st = state(solo, code_s)
check("spil alene: egen genre og beat, saa lav-fasen", st["phase"] == "make" and st["round"]["genre"]
      and st["round"]["beat_url"], st.get("phase"))
clock[0] = database.connect().execute("SELECT make_at FROM lobbies WHERE code=?", (code_s,)).fetchone()[0] + 90
r = solo.post("/l/%s/aflever" % code_s, data={"track": (io.BytesIO(wav24), "x.wav")},
              headers={"X-CSRF-Token": solo.token, "X-Requested-With": "fetch"}, content_type="multipart/form-data")
check("spil alene: aflevering", r.status_code == 200)
post(solo, "/l/%s/afslut" % code_s)
s, st = state(solo, code_s)
check("spil alene: 'Jeg er færdig' -> resultat uden afstemning", st["phase"] == "result"
      and len(st["result"]["ranking"]) == 1 and st["result"]["ranking"][0]["place"] is None, st.get("phase"))
check("spil alene: rundens tal viser kun tiden", st["result"]["stats"]
      == [{"label": "Tiden", "text": "Afleveret efter 1:30 af 5:00"}], st["result"]["stats"])
check("spil alene: opsummering i stedet for point", st["series"] is None
      and st["practice_summary"] == {"rounds": 1, "delivered": 1, "best": 90, "round_no": 1}, st.get("practice_summary"))
r = post(solo, "/l/%s/ny-runde" % code_s)
code_s2 = r.headers["Location"].rsplit("/", 1)[-1]
s, st = state(solo, code_s2)
check("spil alene: ny runde gaar i gang med det samme", st["phase"] == "spin" and st["practice"]
      and st["round_no"] == 2 and st["spin"]["current"] == 1, st.get("phase"))

# Alene i en almindelig lobby: ejeren kan vaelge at spille alene.
o6 = client()
r = post(o6, "/ny", {"name": "Ejer6", "minutes": "5", "team_size": "2"})
code6 = r.headers["Location"].rsplit("/", 1)[-1]
check("alene i en lobby: runden kan ikke starte som normalt", "mindst" in (state(o6, code6)[1]["start_problem"] or ""))
post(o6, "/l/%s/start" % code6, {"solo": "1"})
s, st = state(o6, code6)
check("alene i en lobby: 'Spil alene' starter singleplayer", st["phase"] == "spin" and st["practice"]
      and len(st["spin"]["order"]) == 1, st.get("phase"))
o7 = client()
r = post(o7, "/ny", {"name": "Ejer7", "minutes": "5", "team_size": "1"})
code7 = r.headers["Location"].rsplit("/", 1)[-1]
p7 = client(); post(p7, "/join", {"code": code7, "name": "Anden7"})
post(o7, "/l/%s/start" % code7, {"solo": "1"})
check("spil alene afvises, naar der er andre i lobbyen", "kun spille alene" in " ".join(flashes(o7))
      and state(o7, code7)[1]["phase"] == "lobby")

# ---------- udloeb og oprydning ----------
o3 = client()
r = post(o3, "/ny", {"name": "Ejer3", "minutes": "60", "team_size": "1"})
code3 = r.headers["Location"].rsplit("/", 1)[-1]
clock[0] += core.LOBBY_SECONDS - 30 * 60
x = client(); post(x, "/join", {"code": code3, "name": "X"})
s, st = state(o3, code3)
check("start blokeret naar lobbyen udloeber foer runden kan blive faerdig", "udløber om" in (st["start_problem"] or ""), st["start_problem"])
clock[0] += 31 * 60
s, st = state(o3, code3)
check("lobby lukker efter 24 timer", st["phase"] == "closed")
y = client(); post(y, "/join", {"code": code3, "name": "Y"})
check("join i udloebet lobby afvises", "allerede gået i gang" in " ".join(flashes(y)) or "slut" in " ".join(flashes(y)))
s, st = state(owner, code)
check("resultatet kan stadig ses efter 24 timer", st["phase"] == "result")

subs_before = len(list(core.SUBS.iterdir()))
clock[0] += core.KEEP_SECONDS
app._next_cleanup[0] = 0
conn = database.connect(); conn.execute("UPDATE meta SET value='0' WHERE key='cleanup_at'"); conn.commit()
owner.get("/")
conn = database.connect()
check("efter 7 dage: lobbyer og afleveringer slettet", subs_before == 4 and list(core.SUBS.iterdir()) == []
      and conn.execute("SELECT COUNT(*) FROM lobbies").fetchone()[0] == 0
      and conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 0
      and conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 0, (subs_before, list(core.SUBS.iterdir())))
check("beats bliver liggende", len(list(core.BEATS.iterdir())) == 4)
s, _ = state(owner, code)
check("slettet lobby giver 404", s == 404)

# ---------- beats fra YouTube ----------
import youtube  # noqa: E402
check("youtube: link-formater", youtube.video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1") == "dQw4w9WgXcQ"
      and youtube.video_id("youtu.be/dQw4w9WgXcQ?t=4") == "dQw4w9WgXcQ"
      and youtube.video_id("https://m.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ")
check("youtube: andre sider og playlister afvises", all(youtube.video_id(u) is None for u in (
      "https://evil.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
      "file:///etc/passwd", "https://www.youtube.com/playlist?list=PL1", "javascript:alert(1)", "")))
check("youtube: titlen renses", youtube.clean_title('(FREE) Afro Trap Type Beat 2026 - \uff02Global\uff02') == "Global"
      and youtube.clean_title("Mobb Deep - Shook Ones, Pt II [Instrumental]") == "Mobb Deep - Shook Ones, Pt II")

adm.token = csrf(adm)
post(adm, "/admin/login", {"password": "admin-password-123"}); flashes(adm)   # uret er skruet frem siden
adm.token = csrf(adm)
fake = {"mode": "ok"}
def fake_fetch(vid, workdir, max_bytes, max_seconds):
    if fake["mode"] == "bot":
        raise youtube.FetchError(youtube.explain("ERROR: [youtube] x: Sign in to confirm you're not a bot"))
    out = workdir / (vid + ".mp3")
    out.write_bytes(mp3 if fake["mode"] == "ok" else b"ikke lyd" * 100)
    return out, "(FREE) Drill Type Beat - \uff02Kold\uff02"
youtube.fetch = fake_fetch
admin.spawn = lambda fn, *a: fn(*a)                     # med det samme i testen

r = post(anon, "/admin/youtube", {"url": "https://youtu.be/aaaaaaaaaaa", "genre": str(gid["Trap"])})
check("youtube: kun admin", r.status_code == 302 and r.headers["Location"].endswith("/admin")
      and not database.connect().execute("SELECT COUNT(*) FROM imports").fetchone()[0])
r = post(adm, "/admin/youtube", {"url": "https://vimeo.com/123", "genre": str(gid["Trap"])})
check("youtube: andet end youtube afvises", "ikke et link til en YouTube-video" in " ".join(flashes(adm)))
r = post(adm, "/admin/youtube", {"url": "https://youtu.be/aaaaaaaaaaa", "genre": "999"})
check("youtube: genre skal findes", "Vælg en genre" in " ".join(flashes(adm)))

beats_before = len(list(core.BEATS.iterdir()))
r = post(adm, "/admin/youtube", {"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "title": "", "genre": str(gid["Lofi"])})
flashes(adm)
row = database.connect().execute("SELECT * FROM beats WHERE source = 'youtube:aaaaaaaaaaa'").fetchone()
check("youtube: beatet hentes og laegges op, titel fra videoen", row is not None and row["title"] == "Kold"
      and row["kind"] == "mp3" and row["genre_id"] == gid["Lofi"] and len(list(core.BEATS.iterdir())) == beats_before + 1, dict(row) if row else None)
imp = adm.get("/admin/hentninger").get_json()["imports"]
check("youtube: status vises som faerdig", imp and imp[0]["status"] == "done" and "Kold er lagt op" in imp[0]["message"], imp)
r = post(adm, "/admin/youtube", {"url": "youtu.be/aaaaaaaaaaa", "title": "Igen", "genre": str(gid["Lofi"])})
check("youtube: samme video to gange afvises", "lagt op før" in " ".join(flashes(adm)))

post(adm, "/admin/youtube", {"url": "youtu.be/bbbbbbbbbbb", "title": "Med titel", "genre": str(gid["Trap"])})
flashes(adm)
check("youtube: egen titel bruges", database.connect().execute(
      "SELECT title FROM beats WHERE source = 'youtube:bbbbbbbbbbb'").fetchone()[0] == "Med titel")

fake["mode"] = "bot"
post(adm, "/admin/youtube", {"url": "youtu.be/ccccccccccc", "genre": str(gid["Trap"])}); flashes(adm)
imp = adm.get("/admin/hentninger").get_json()["imports"]
check("youtube: bot-tjek forklares paa dansk", imp[0]["status"] == "failed" and "bot-tjek" in imp[0]["message"], imp[0])
fake["mode"] = "junk"
post(adm, "/admin/youtube", {"url": "youtu.be/ddddddddddd", "genre": str(gid["Trap"])}); flashes(adm)
imp = adm.get("/admin/hentninger").get_json()["imports"]
check("youtube: fil der ikke er lyd, afvises og slettes", imp[0]["status"] == "failed"
      and "kunne ikke bruges" in imp[0]["message"] and not (core.TMP / "ddddddddddd.mp3").exists(), imp[0])

conn = database.connect()
conn.execute("INSERT INTO imports (video, genre_id, created_at, updated_at) VALUES ('eeeeeeeeeee', ?, ?, ?)",
             (gid["Trap"], clock[0] - core.YT_STALE_SECONDS - 5, clock[0] - core.YT_STALE_SECONDS - 5))
conn.commit()
imp = adm.get("/admin/hentninger").get_json()["imports"]
check("youtube: haengende hentning markeres som afbrudt", any(i["status"] == "failed" and "afbrudt" in i["message"] for i in imp))
html = adm.get("/admin").get_data(as_text=True)
check("youtube: admin-siden viser formular og hentninger", "Hent fra YouTube" in html and "imports-list" in html)
# Ryd op, saa resten af testene ser samme beats som foer.
for r_ in database.connect().execute("SELECT id FROM beats WHERE source LIKE 'youtube:%'").fetchall():
    post(adm, "/admin/beat/%s/slet" % r_[0]); flashes(adm)

# ---------- migrering ----------
import sqlite3  # noqa: E402
old_db = DATA / "gammel.db"
c = sqlite3.connect(old_db)
c.executescript("CREATE TABLE teams (id INTEGER PRIMARY KEY, lobby_id INTEGER NOT NULL, number INTEGER NOT NULL);"
                "CREATE TABLE lobbies (id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, owner_id INTEGER,"
                " minutes INTEGER NOT NULL, team_size INTEGER NOT NULL, genre_id INTEGER,"
                " status TEXT NOT NULL DEFAULT 'lobby', beat_id TEXT, genre_name TEXT NOT NULL DEFAULT '',"
                " beat_title TEXT NOT NULL DEFAULT '', started_at REAL, make_at REAL, deadline REAL,"
                " voting_at REAL, done_at REAL, created_at REAL NOT NULL);")
c.commit(); c.close()
real_db = core.DB_PATH
core.DB_PATH = old_db
database.init_db(); database.init_db()
c = database.connect()
cols = {r["name"] for r in c.execute("PRAGMA table_info(teams)")} | {r["name"] for r in c.execute("PRAGMA table_info(lobbies)")}
c.close()
core.DB_PATH = real_db
check("gammel database faar hjulets kolonner (og to gange er ufarligt)",
      {"spin_order", "genre_name", "beat_id", "spun_at", "spun_by", "turn_at"} <= cols, cols)

# ---------- sider ----------
fresh = client()
r = post(fresh, "/ny", {"name": "Sidetest", "minutes": "5", "team_size": "1"})
c4 = r.headers["Location"].rsplit("/", 1)[-1]
pages = [fresh.get(u) for u in ("/", "/l/" + c4)] + [adm.get("/admin")]
check("sider svarer 200", [p.status_code for p in pages] == [200, 200, 200])
check("ingen inline-scripts eller -handlers nogen steder", not any(
    re.search(r"<script(?![^>]*\bsrc=)|\son[a-z]+=|\sstyle=\"", p.get_data(as_text=True)) for p in pages))
check("healthz", fresh.get("/healthz").get_data(as_text=True) == "ok")

print("\n%d ok, %d fejl" % (ok_n, bad_n))
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if bad_n else 0)
