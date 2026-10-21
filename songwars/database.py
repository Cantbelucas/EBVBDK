"""
Songwars - databasen.

Skemaet, kolonner der er kommet til siden (de tilfoejes ved opstart),
forbindelsen pr. forespoergsel og rate limiting, der ligger i SQLite, saa
begge gunicorn-workers taeller sammen.
"""

import sqlite3

from flask import g

import core
from core import LIMITS, now

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
    turn_at     REAL,                    -- hjulet: hvornaar naeste tur begynder
    vote_deadline REAL,                  -- afstemningen lukker af sig selv
    parent_id   INTEGER,                 -- forrige runde i samme serie
    practice    INTEGER NOT NULL DEFAULT 0,   -- 1 = singleplayer: ingen afstemning
    round_no    INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    id          INTEGER PRIMARY KEY,
    lobby_id    INTEGER NOT NULL REFERENCES lobbies(id) ON DELETE CASCADE,
    number      INTEGER NOT NULL,
    spin_order  INTEGER,
    genre_id    INTEGER,
    genre_name  TEXT NOT NULL DEFAULT '',
    beat_id     TEXT REFERENCES beats(id) ON DELETE SET NULL,
    beat_title  TEXT NOT NULL DEFAULT '',
    spun_at     REAL,
    spun_by     INTEGER                  -- NULL = serveren drejede for holdet
);

CREATE TABLE IF NOT EXISTS players (
    id          INTEGER PRIMARY KEY,
    lobby_id    INTEGER NOT NULL REFERENCES lobbies(id) ON DELETE CASCADE,
    name        TEXT NOT NULL COLLATE NOCASE,
    team_id     INTEGER REFERENCES teams(id) ON DELETE SET NULL,
    joined_at   REAL NOT NULL,
    seen_at     REAL NOT NULL,
    from_id     INTEGER,                 -- samme spiller i forrige runde
    avatar      TEXT,                    -- emoji fra core.AVATARS
    color       TEXT,                    -- noegle i core.COLORS
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

-- Beats der hentes fra YouTube. Hentningen koerer i baggrunden; admin-
-- siden viser status herfra.
CREATE TABLE IF NOT EXISTS imports (
    id          INTEGER PRIMARY KEY,
    video       TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    genre_id    INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',   -- running, done, failed
    message     TEXT NOT NULL DEFAULT '',
    beat_id     TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

-- Storskaerme: en skaerm der viser lobbyen uden at vaere en spiller.
CREATE TABLE IF NOT EXISTS screens (
    id          INTEGER PRIMARY KEY,
    lobby_id    INTEGER NOT NULL REFERENCES lobbies(id) ON DELETE CASCADE,
    from_id     INTEGER,                 -- samme skaerm i forrige runde
    created_at  REAL NOT NULL
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
    conn = sqlite3.connect(core.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


# Kolonner der er kommet til siden foerste version. CREATE TABLE IF NOT
# EXISTS roerer ikke en tabel der findes, saa de tilfoejes her.
COLUMNS = {
    "lobbies": [("turn_at", "REAL"), ("vote_deadline", "REAL"), ("parent_id", "INTEGER"),
                ("round_no", "INTEGER NOT NULL DEFAULT 1"),
                ("practice", "INTEGER NOT NULL DEFAULT 0")],
    "players": [("from_id", "INTEGER"), ("avatar", "TEXT"), ("color", "TEXT")],
    "beats": [("source", "TEXT")],              # youtube:<id>, saa samme video ikke kommer to gange
    "teams": [
        ("spin_order", "INTEGER"),
        ("genre_id", "INTEGER"),
        ("genre_name", "TEXT NOT NULL DEFAULT ''"),
        ("beat_id", "TEXT REFERENCES beats(id) ON DELETE SET NULL"),
        ("beat_title", "TEXT NOT NULL DEFAULT ''"),
        ("spun_at", "REAL"),
        ("spun_by", "INTEGER"),
    ],
}


def init_db():
    conn = connect()
    # WAL: polling-laesninger venter ikke paa en der skriver.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    for table, columns in COLUMNS.items():
        have = {r["name"] for r in conn.execute("PRAGMA table_info({0})".format(table))}
        for name, kind in columns:
            if name in have:
                continue
            try:
                conn.execute("ALTER TABLE {0} ADD COLUMN {1} {2}".format(table, name, kind))
            except sqlite3.OperationalError as exc:
                # Den anden gunicorn-worker naaede det foerst.
                if "duplicate column" not in str(exc):
                    raise
    conn.commit()
    conn.close()


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
