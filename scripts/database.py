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
        # ── recommendations ──────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recommendations (
                id                              INTEGER PRIMARY KEY AUTOINCREMENT,
                game_pk                         INTEGER,
                generated_at_utc                TEXT,
                market                          TEXT,
                side                            TEXT,
                line                            REAL,
                target_price_american           INTEGER,
                fair_price_american             INTEGER,
                edge_percent                    REAL,
                confidence_color                TEXT,
                recommended_stake_pct           REAL,
                recommended_stake_dollars_at_2500 REAL,
                is_shadow                       INTEGER DEFAULT 0,
                num_books_in_consensus          INTEGER NOT NULL DEFAULT 0,
                closing_price_american          INTEGER,
                clv_percent                     REAL,
                result                          TEXT,
                result_payout_at_stake_1        REAL,
                graded_at_utc                   TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_game_pk         ON recommendations (game_pk)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_generated_at    ON recommendations (generated_at_utc)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_color_market     ON recommendations (confidence_color, market)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_result           ON recommendations (result)")

        # ── personal_bets ─────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS personal_bets (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                game_pk                 INTEGER,
                placed_at_utc           TEXT,
                book                    TEXT,
                market                  TEXT,
                side                    TEXT,
                line                    REAL,
                actual_price_american   INTEGER,
                stake_dollars           REAL,
                recommendation_id       INTEGER,
                closing_price_american  INTEGER,
                clv_percent             REAL,
                result                  TEXT,
                payout_dollars          REAL,
                profit_loss_dollars     REAL,
                graded_at_utc           TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bet_game_pk         ON personal_bets (game_pk)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bet_placed_at       ON personal_bets (placed_at_utc)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bet_rec_id          ON personal_bets (recommendation_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bet_result          ON personal_bets (result)")

        # ── odds_pulls ────────────────────────────────────────────────────────
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
