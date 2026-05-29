import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'mlb.db')


def get_connection():
    conn = sqlite3.connect(os.path.abspath(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS games (
                game_pk             INTEGER PRIMARY KEY,
                game_date           TEXT,
                game_datetime_utc   TEXT,
                home_team           TEXT,
                away_team           TEXT,
                home_team_id        INTEGER,
                away_team_id        INTEGER,
                venue               TEXT,
                venue_id            INTEGER,
                status              TEXT,
                home_score          INTEGER,
                away_score          INTEGER,
                last_updated_utc    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS probable_pitchers (
                game_pk          INTEGER,
                team_side        TEXT,
                pitcher_id       INTEGER,
                pitcher_name     TEXT,
                pitcher_throws   TEXT,
                is_confirmed     INTEGER DEFAULT 0,
                last_updated_utc TEXT,
                UNIQUE (game_pk, team_side)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS odds_snapshots (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                game_pk             INTEGER,
                book                TEXT,
                market              TEXT,
                outcome_type        TEXT,
                line                REAL,
                price_american      INTEGER,
                price_decimal       REAL,
                snapshot_time_utc   TEXT,
                api_last_update_utc TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_odds_snapshots_game_market_time
            ON odds_snapshots (game_pk, market, snapshot_time_utc)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS odds_pulls (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                pull_time_utc      TEXT,
                endpoint           TEXT,
                games_returned     INTEGER,
                requests_remaining INTEGER,
                requests_used      INTEGER,
                success            INTEGER,
                error_message      TEXT
            )
        """)
