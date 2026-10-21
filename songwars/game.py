"""
Songwars - spillets regler.

Faserne i en runde gemmes som status + tidsstempler, og den aktuelle fase
regnes ud ved hver forespoergsel (advance). Der er ingen baggrundsjob og
ingen timer i klienten der bestemmer noget - fristen haandhaeves her.

    status    fase
    lobby     lobby       venter paa spillere
    spinning  spin        holdene drejer hjulet paa skift
    running   spin        det sidste hjul staar ikke stille endnu (turn_at)
              reveal      hver sin genre og sit beat        (indtil make_at)
              make        nedtaelling til deadline
              extra       ekstra tid til at aflevere       (indtil deadline + EXTRA + grace)
    voting    vote        alle stemmer                     (indtil vote_deadline)
    done      result      ranglisten
    closed    closed      udloebet foer runden blev faerdig

Her er ogsaa hjulet, holdene, pladserne, stillingen over flere runder og
det /state sender til klienten. Ruterne ligger i app.py.
"""

import secrets

from flask import url_for

from core import (
    CODE_ALPHABET, CODE_LEN, EXTRA_SECONDS, GRACE_SECONDS, LOBBY_SECONDS,
    MAX_UPLOAD, ONLINE_SECONDS, PLACE_POINTS, REVEAL_SECONDS, SPIN_INTRO,
    SPIN_MS, SPIN_REST, SPIN_SECONDS, VOTE_MIN_SECONDS,
    VOTE_SECONDS_PER_TRACK, ms, now,
)
from database import db


def new_code(conn):
    for _ in range(50):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
        if not conn.execute("SELECT 1 FROM lobbies WHERE code = ?", (code,)).fetchone():
            return code
    raise RuntimeError("kunne ikke finde en ledig kode")


def phase_of(lobby, t=None):
    t = now() if t is None else t
    status = lobby["status"]
    if status == "running":
        if lobby["turn_at"] is not None and t < lobby["turn_at"]:
            return "spin"                             # det sidste hjul drejer stadig
        if t < lobby["make_at"]:
            return "reveal"
        return "make" if t < lobby["deadline"] else "extra"
    return {"lobby": "lobby", "spinning": "spin", "voting": "vote", "done": "result",
            "closed": "closed"}[status]


def closes_at(lobby):
    """Sidste chance for at aflevere: tiden + den ekstra tid. Graceperioden
    for en upload der er undervejs, kommer oven i."""
    return lobby["deadline"] + EXTRA_SECONDS


def all_delivered(conn, lid):
    teams = conn.execute("SELECT COUNT(*) FROM teams WHERE lobby_id = ?", (lid,)).fetchone()[0]
    subs = conn.execute("SELECT COUNT(*) FROM submissions WHERE lobby_id = ?", (lid,)).fetchone()[0]
    return teams > 0 and subs >= teams


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

    if status in ("lobby", "spinning", "running", "voting") and t >= lobby["created_at"] + LOBBY_SECONDS:
        # En afstemning i gang faar sit resultat; alt andet lukker bare.
        new = "done" if status == "voting" else "closed"
        conn.execute("UPDATE lobbies SET status = ?, done_at = ? WHERE id = ? AND status = ?",
                     (new, t, lid, status))
        conn.commit()

    elif status == "running" and (t >= closes_at(lobby) + GRACE_SECONDS
                                  or (t >= lobby["deadline"] and all_delivered(conn, lid))):
        # Afleveringen lukker naar den ekstra tid er gaaet - eller med det
        # samme naar tiden er gaaet, hvis alle hold allerede har afleveret.
        won = conn.execute(
            "UPDATE lobbies SET status = 'voting', voting_at = ? WHERE id = ? AND status = 'running'",
            (t, lid)).rowcount
        if won:
            subs = [r["id"] for r in conn.execute(
                "SELECT id FROM submissions WHERE lobby_id = ?", (lid,))]
            conn.execute("UPDATE lobbies SET vote_deadline = ? WHERE id = ?",
                         (t + max(VOTE_MIN_SECONDS, VOTE_SECONDS_PER_TRACK * len(subs)), lid))
            # Raekkefoelgen i afstemningen er tilfaeldig - ikke den
            # raekkefoelge numrene blev afleveret i, og ikke holdnumrene.
            secrets.SystemRandom().shuffle(subs)
            for position, sid in enumerate(subs, start=1):
                conn.execute("UPDATE submissions SET position = ? WHERE id = ?", (position, sid))
            if not subs:
                conn.execute("UPDATE lobbies SET status = 'done', done_at = ? WHERE id = ?", (t, lid))
        conn.commit()

    elif status == "spinning" and lobby["turn_at"] is not None \
            and t >= lobby["turn_at"] + SPIN_SECONDS:
        # Holdet der har tur, har ikke drejet. Serveren goer det - ogsaa for
        # flere hold i traek, hvis ingen har kigget i et stykke tid. Hvert
        # spin faar det tidspunkt, det skulle have haft.
        turn_at = lobby["turn_at"]
        while isinstance(turn_at, float) and t >= turn_at + SPIN_SECONDS:
            turn_at = spin(conn, lid, turn_at + SPIN_SECONDS, by=None)

    elif status == "voting" and ((lobby["vote_deadline"] is not None and t >= lobby["vote_deadline"])
                                 or voting_complete(conn, lid)):
        conn.execute("UPDATE lobbies SET status = 'done', done_at = ? WHERE id = ? AND status = 'voting'",
                     (t, lid))
        conn.commit()

    return conn.execute("SELECT * FROM lobbies WHERE id = ?", (lid,)).fetchone()


def team_on_turn(conn, lid):
    return conn.execute(
        "SELECT * FROM teams WHERE lobby_id = ? AND spun_at IS NULL"
        " ORDER BY spin_order LIMIT 1", (lid,)).fetchone()


def wheel_genres(conn, lid):
    """Det hjulet viser: alle genrer med beats, plus dem der allerede er
    trukket i lobbyen (hvis en admin har fjernet et beat siden)."""
    names = {r["name"] for r in genres_with_beats(conn)}
    names |= {r["genre_name"] for r in conn.execute(
        "SELECT genre_name FROM teams WHERE lobby_id = ? AND genre_name != ''", (lid,))}
    return sorted(names, key=str.casefold)


def spin(conn, lid, at, by=None, team_id=None):
    """Drejer hjulet for holdet der har tur, og giver det en genre ingen
    andre har, og et tilfaeldigt beat i den. Hjulet viser bare det
    resultat - det er serveren der trækker.

    Returnerer hvornaar naeste tur begynder, None naar alle har drejet,
    eller False hvis det ikke var `team_id`s tur (eller hjulet ikke stod
    stille endnu). Det hele sker i en BEGIN IMMEDIATE, saa to workers
    ikke kan dreje samme tur."""
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    lobby = conn.execute("SELECT * FROM lobbies WHERE id = ?", (lid,)).fetchone()
    team = team_on_turn(conn, lid)
    if (lobby["status"] != "spinning" or team is None or at < lobby["turn_at"]
            or (team_id is not None and team["id"] != team_id)):
        conn.rollback()
        return False

    rng = secrets.SystemRandom()
    taken = {r["genre_id"] for r in conn.execute(
        "SELECT genre_id FROM teams WHERE lobby_id = ? AND genre_id IS NOT NULL", (lid,))}
    available = genres_with_beats(conn)
    free = [g for g in available if g["id"] not in taken]
    if not free:
        # Kun hvis en admin har fjernet beats midt i hjulet: saa hellere
        # en genre to gange end en runde der sidder fast.
        free = available
    if not free:
        conn.execute("UPDATE lobbies SET status = 'closed', done_at = ? WHERE id = ?", (at, lid))
        conn.commit()
        return None
    genre = rng.choice(free)
    beat = rng.choice(conn.execute("SELECT * FROM beats WHERE genre_id = ?", (genre["id"],)).fetchall())
    conn.execute(
        "UPDATE teams SET genre_id = ?, genre_name = ?, beat_id = ?, beat_title = ?,"
        " spun_at = ?, spun_by = ? WHERE id = ?",
        (genre["id"], genre["name"], beat["id"], beat["title"], at, by, team["id"]))

    still = at + SPIN_MS / 1000 + SPIN_REST         # hjulet staar stille igen
    if team_on_turn(conn, lid) is None:
        make_at = still + REVEAL_SECONDS
        conn.execute(
            "UPDATE lobbies SET status = 'running', turn_at = ?, make_at = ?, deadline = ?"
            " WHERE id = ?", (still, make_at, make_at + lobby["minutes"] * 60, lid))
        conn.commit()
        return None
    conn.execute("UPDATE lobbies SET turn_at = ? WHERE id = ?", (still, lid))
    conn.commit()
    return still


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
    teams = max(1, len(players) // size)
    if teams > len(available):
        return ("Der bliver {0} hold, men der er kun {1} genrer med beats — og alle skal have "
                "hver sin. En admin kan lægge beats op i flere genrer, eller I kan spille på "
                "større hold.".format(teams, len(available)))
    left = lobby["created_at"] + LOBBY_SECONDS - now()
    spinning = SPIN_INTRO + teams * (SPIN_SECONDS + SPIN_MS / 1000 + SPIN_REST)
    if left < spinning + REVEAL_SECONDS + lobby["minutes"] * 60 + 15 * 60:
        return ("Lobbyen udløber om {0} min. — der er ikke tid til en runde på {1} min. "
                "Lav en ny lobby.".format(max(0, int(left // 60)), lobby["minutes"]))
    return None


def state_for(conn, lobby, me, t):
    lid = lobby["id"]
    phase = phase_of(lobby, t)
    is_owner = me["id"] == lobby["owner_id"]

    teams = {r["id"]: r["number"] for r in conn.execute(
        "SELECT id, number FROM teams WHERE lobby_id = ?", (lid,))}
    players = conn.execute(
        "SELECT * FROM players WHERE lobby_id = ? ORDER BY joined_at", (lid,)).fetchall()
    my_team = me["team_id"]

    state = {
        "phase": phase,
        "now": ms(t),
        "code": lobby["code"],
        "expires": ms(lobby["created_at"] + LOBBY_SECONDS),
        "me": {"id": me["id"], "name": me["name"], "owner": is_owner,
               "team": teams.get(my_team)},
        "settings": {"minutes": lobby["minutes"], "team_size": lobby["team_size"]},
        "round_no": lobby["round_no"],
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
        state["series"] = standings(conn, lobby, me)
        return state

    if phase == "closed":
        return state

    # Fra hjulet og frem: holdene er trukket, og genrerne kommer en ad gangen.
    members = {}
    for p in players:
        if p["team_id"] in teams:
            members.setdefault(teams[p["team_id"]], []).append(p["name"])
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM teams WHERE lobby_id = ?", (lid,))}
    genre_of = {r["number"]: team_genre(lobby, r) for r in rows.values()}
    mine_row = rows.get(my_team)
    genre, beat_title, beat_id = team_genre(lobby, mine_row) if mine_row else ("", "", None)
    state["round"] = {
        "genre": genre,
        "beat": beat_title,
        "beat_url": url_for("beat_file", code=lobby["code"]) if beat_id else None,
        "make_at": ms(lobby["make_at"]),
        "deadline": ms(lobby["deadline"]),
        "closes": ms(closes_at(lobby)) if lobby["deadline"] else None,
        "extra": EXTRA_SECONDS,
        "grace": GRACE_SECONDS,
        "minutes": lobby["minutes"],
        "teams": [{"number": n, "members": members.get(n, []), "genre": genre_of[n][0],
                   "mine": n == teams.get(my_team)} for n in sorted(set(teams.values()))],
        "solo": lobby["team_size"] == 1,
    }

    if phase == "spin":
        on_turn = team_on_turn(conn, lid)
        state["spin"] = {
            "wheel": wheel_genres(conn, lid),
            "order": [{
                "number": r["number"],
                "members": members.get(r["number"], []),
                "mine": r["id"] == my_team,
                "genre": r["genre_name"] or None,
                "spun_at": ms(r["spun_at"]),
                "auto": r["spun_at"] is not None and r["spun_by"] is None,
            } for r in sorted(rows.values(), key=lambda r: r["spin_order"] or 0)],
            "current": on_turn["number"] if on_turn else None,
            "my_turn": bool(on_turn) and on_turn["id"] == my_team,
            "turn_at": ms(lobby["turn_at"]),
            "turn_seconds": SPIN_SECONDS,
            "spin_ms": SPIN_MS,
            "spin_url": url_for("spin_wheel", code=lobby["code"]),
        }
        return state

    subs = conn.execute(
        "SELECT s.*, p.name AS by_name FROM submissions s"
        " LEFT JOIN players p ON p.id = s.player_id WHERE s.lobby_id = ?", (lid,)).fetchall()

    if phase in ("reveal", "make", "extra"):
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
            "deadline": ms(lobby["vote_deadline"]),
            "vote_url": url_for("vote", code=lobby["code"]),
        }
        return state

    # Resultat. Navnene kommer frem.
    places = team_places(conn, lid)
    ranked = []
    for s in subs:
        average, n, place = places.get(s["team_id"], (None, 0, None))
        ranked.append({
            "id": s["id"],
            "label": "Nummer {0}".format(s["position"]) if s["position"] else "",
            "team": teams[s["team_id"]],
            "genre": genre_of[teams[s["team_id"]]][0],
            "members": members.get(teams[s["team_id"]], []),
            "by": s["by_name"],
            "average": average,
            "votes": n,
            "place": place,
            "seconds": round(s["seconds"], 1),
            "url": url_for("track_file", code=lobby["code"], sid=s["id"]),
            "mine": s["team_id"] == my_team,
        })
    ranked.sort(key=lambda r: (r["average"] is None, -(r["average"] or 0), -r["votes"]))
    delivered = {teams[s["team_id"]] for s in subs}
    state["result"] = {
        "ranking": ranked,
        "missing": [{"team": n, "members": members.get(n, [])}
                    for n in sorted(set(teams.values())) if n not in delivered],
    }
    state["series"] = standings(conn, lobby, me)
    child = conn.execute("SELECT code FROM lobbies WHERE parent_id = ?", (lid,)).fetchone()
    state["next"] = ({"code": child["code"], "url": url_for("lobby_page", code=child["code"])}
                     if child else None)
    return state


def team_places(conn, lid):
    """{hold-id: (gennemsnit, stemmer, plads)} for numrene i en runde. Lige
    gennemsnit giver samme plads; et nummer uden stemmer har ingen plads."""
    rows = conn.execute(
        "SELECT s.id, s.team_id, SUM(v.flames) AS total, COUNT(v.flames) AS n FROM submissions s"
        " LEFT JOIN votes v ON v.submission_id = s.id WHERE s.lobby_id = ? GROUP BY s.id",
        (lid,)).fetchall()
    scored = [(r["team_id"], round(r["total"] / r["n"], 2) if r["n"] else None, r["n"]) for r in rows]
    scored.sort(key=lambda r: (r[1] is None, -(r[1] or 0), -r[2]))
    places, place, last = {}, 0, object()
    for i, (team, average, n) in enumerate(scored, start=1):
        if average != last:
            place, last = i, average
        places[team] = (average, n, place if average is not None else None)
    return places


def series_of(conn, lobby):
    """Runderne i samme serie, aeldste foerst."""
    chain, cur = [lobby], lobby
    while cur["parent_id"] and len(chain) < 50:
        cur = conn.execute("SELECT * FROM lobbies WHERE id = ?", (cur["parent_id"],)).fetchone()
        if cur is None:
            break
        chain.append(cur)
    return chain[::-1]


def standings(conn, lobby, me):
    """Stillingen i en serie af runder: point efter plads (PLACE_POINTS) til
    alle paa holdet. Holdene trækkes paa ny hver runde, saa pointene foelger
    personen - fulgt gennem players.from_id. None hvis der kun er en runde."""
    chain = series_of(conn, lobby)
    if len(chain) < 2:
        return None
    root, names, points, wins, rounds = {}, {}, {}, {}, 0
    for lb in chain:
        players = conn.execute("SELECT id, name, team_id, from_id FROM players WHERE lobby_id = ?"
                               " ORDER BY joined_at", (lb["id"],)).fetchall()
        for pl in players:
            person = root.get(pl["from_id"], pl["id"])
            root[pl["id"]] = person
            names[person] = pl["name"]
            points.setdefault(person, 0)
            wins.setdefault(person, 0)
        if lb["status"] != "done":
            continue
        rounds += 1
        places = team_places(conn, lb["id"])
        for pl in players:
            place = places.get(pl["team_id"], (None, 0, None))[2]
            if place:
                points[root[pl["id"]]] += PLACE_POINTS.get(place, 0)
                wins[root[pl["id"]]] += 1 if place == 1 else 0
    me_person = root.get(me["id"])
    table = [{"name": names[p], "points": points[p], "wins": wins[p], "me": p == me_person}
             for p in names]
    table.sort(key=lambda r: (-r["points"], -r["wins"], r["name"].casefold()))
    return {"rounds": rounds, "round_no": lobby["round_no"], "standings": table}


def team_genre(lobby, team):
    """(genre, beattitel, beat-id) for et hold. Runder fra foer hjulet havde
    en genre for hele lobbyen - de bruger den."""
    if team["genre_name"]:
        return team["genre_name"], team["beat_title"], team["beat_id"]
    return lobby["genre_name"], lobby["beat_title"], lobby["beat_id"]
