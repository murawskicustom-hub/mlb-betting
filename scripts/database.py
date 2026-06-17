import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'mlb.db')


def get_connection():
    # timeout=30: Python waits up to 30s for the lock before raising
    conn = sqlite3.connect(os.path.abspath(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL mode: readers never block writers; writers never block readers.
    # Persistent on the database file — no-op if already set.
    conn.execute('PRAGMA journal_mode=WAL')
    # NORMAL is safe with WAL and avoids the full fsync on every commit.
    conn.execute('PRAGMA synchronous=NORMAL')
    # SQLite-level retry for up to 30 s if a concurrent writer holds the lock.
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


def _add_col(conn, table: str, col: str, definition: str) -> None:
    """Add a column to an existing table if it does not already exist."""
    try:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {definition}')
    except Exception:
        pass  # column already exists


def _seed_park_factors(conn) -> None:
    """
    Seed park_factors with 2024-2025 multi-year run factors.
    Source: Statcast / Fangraphs published park factors (3-year rolling average).
    1.00 = neutral. >1.00 = hitter-friendly. <1.00 = pitcher-friendly.
    Only inserts rows that don't already exist (INSERT OR IGNORE).
    """
    factors = [
        # venue_id, venue_name, park_run_factor
        # --- Hitter-friendly ---
        (19,   'Coors Field',                  1.12),  # COL: extreme altitude
        (5355, 'Las Vegas Ballpark',            1.05),  # OAK: hot/dry, new venue
        (5340, 'Estadio Alfredo Harp Helu',    1.05),  # Mexico City series, high altitude
        (2681, 'Citizens Bank Park',            1.05),  # PHI
        (15,   'Chase Field',                   1.04),  # ARI: desert, retractable
        (2602, 'Great American Ball Park',      1.04),  # CIN
        (3,    'Fenway Park',                   1.03),  # BOS
        (4,    'Rate Field',                    1.03),  # CWS (Guaranteed Rate/Rate Field)
        (4705, 'Truist Park',                   1.02),  # ATL
        (3313, 'Yankee Stadium',                1.02),  # NYY: short porch
        (17,   'Wrigley Field',                 1.02),  # CHC
        (14,   'Rogers Centre',                 1.01),  # TOR: turf
        (32,   'American Family Field',         1.01),  # MIL
        # --- Neutral ---
        (5325, 'Globe Life Field',              1.00),  # TEX: roof park
        (22,   'UNIQLO Field at Dodger Stadium',1.00),  # LAD
        (1,    'Angel Stadium',                 0.99),  # LAA
        (3309, 'Nationals Park',                0.99),  # WSH
        (2889, 'Busch Stadium',                 0.99),  # STL
        (2,    'Oriole Park at Camden Yards',   0.99),  # BAL
        (3312, 'Target Field',                  0.99),  # MIN
        (2392, 'Daikin Park',                   0.98),  # HOU (Minute Maid renamed)
        (12,   'Tropicana Field',               0.98),  # TB: dome
        (3289, 'Citi Field',                    0.97),  # NYM
        (5,    'Progressive Field',             0.97),  # CLE
        (2394, 'Comerica Park',                 0.97),  # DET
        (7,    'Kauffman Stadium',              0.97),  # KC
        # --- Pitcher-friendly ---
        (31,   'PNC Park',                      0.96),  # PIT
        (4169, 'loanDepot park',                0.95),  # MIA: pitcher friendly, roof
        (2680, 'Petco Park',                    0.94),  # SD: marine layer
        (680,  'T-Mobile Park',                 0.94),  # SEA: very pitcher friendly
        (2395, 'Oracle Park',                   0.94),  # SF: marine layer, deep
        (2529, 'Sutter Health Park',            0.97),  # OAK/ATL Athletics 2025
    ]
    for venue_id, venue_name, factor in factors:
        conn.execute(
            'INSERT OR IGNORE INTO park_factors (venue_id, venue_name, park_run_factor) VALUES (?, ?, ?)',
            (venue_id, venue_name, factor)
        )


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
            CREATE UNIQUE INDEX IF NOT EXISTS uq_odds_snapshots
            ON odds_snapshots (game_pk, book, market, outcome_type, snapshot_time_utc)
        """)
        # recommendations
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_game_pk      ON recommendations (game_pk)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_generated_at ON recommendations (generated_at_utc)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_color_market  ON recommendations (confidence_color, market)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_result        ON recommendations (result)")

        # algo columns: migration-safe for existing DBs
        _add_col(conn, 'recommendations', 'algo',              "TEXT NOT NULL DEFAULT 'devig'")
        _add_col(conn, 'recommendations', 'model_probability', 'REAL')
        _add_col(conn, 'recommendations', 'model_notes',       'TEXT')
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_algo ON recommendations (algo)")

        # units tracking: migration-safe
        _add_col(conn, 'recommendations', 'unit_profit', 'REAL')
        _add_col(conn, 'personal_bets',   'unit_stake',  'REAL')
        _add_col(conn, 'personal_bets',   'unit_profit', 'REAL')

        # pitcher_stats
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pitcher_stats (
                pitcher_id       INTEGER,
                season           TEXT,
                as_of_date       TEXT,
                ip               REAL,
                k_pct            REAL,
                bb_pct           REAL,
                hr_per_9         REAL,
                fip              REAL,
                era              REAL,
                throws           TEXT,
                last_updated_utc TEXT,
                UNIQUE (pitcher_id, as_of_date)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ps_pitcher_date ON pitcher_stats (pitcher_id, as_of_date)")

        # team_offense_stats
        conn.execute("""
            CREATE TABLE IF NOT EXISTS team_offense_stats (
                team_id          INTEGER,
                season           TEXT,
                as_of_date       TEXT,
                wrc_plus_proxy   REAL,
                runs_per_game    REAL,
                ops              REAL,
                vs_lhp_ops       REAL,
                vs_rhp_ops       REAL,
                last_updated_utc TEXT,
                UNIQUE (team_id, as_of_date)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tos_team_date ON team_offense_stats (team_id, as_of_date)")

        # park_factors: static 2024-2025 published values
        conn.execute("""
            CREATE TABLE IF NOT EXISTS park_factors (
                venue_id        INTEGER PRIMARY KEY,
                venue_name      TEXT,
                park_run_factor REAL NOT NULL DEFAULT 1.0
            )
        """)
        _seed_park_factors(conn)

        # personal_bets
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bet_game_pk   ON personal_bets (game_pk)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bet_placed_at ON personal_bets (placed_at_utc)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bet_rec_id    ON personal_bets (recommendation_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bet_result    ON personal_bets (result)")

        # linescores: one row per game, upserted after games finish
        conn.execute("""
            CREATE TABLE IF NOT EXISTS linescores (
                game_pk                 INTEGER PRIMARY KEY,
                home_runs_by_inning     TEXT,
                away_runs_by_inning     TEXT,
                home_f5_runs            INTEGER,
                away_f5_runs            INTEGER,
                first_inning_home_runs  INTEGER,
                first_inning_away_runs  INTEGER,
                yrfi                    INTEGER,
                f5_total_runs           INTEGER,
                f5_home_win             INTEGER,
                status                  TEXT,
                last_updated_utc        TEXT
            )
        """)

        # odds_pulls
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
        # settings
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key            TEXT PRIMARY KEY,
                value          TEXT,
                updated_at_utc TEXT
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO settings (key, value, updated_at_utc)
            VALUES ('bankroll_dollars', '2500', datetime('now'))
        """)
