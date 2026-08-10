import sqlite3
import os
import re
from pathlib import Path

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'mlb.db')

# Load .env so DATABASE_URL / DB_BACKEND are visible no matter which entry point
# imports this module (pull scripts, dashboard, ad-hoc tools).
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(Path(__file__).resolve().parent.parent / '.env'))
except Exception:
    pass


def db_backend() -> str:
    """Active backend: 'sqlite' (default / source of truth) or 'postgres'.

    Controlled by the DB_BACKEND env var. Stage 3 keeps SQLite as the default;
    Postgres is opt-in for testing the cloud path without cutting over.
    """
    return os.environ.get('DB_BACKEND', 'sqlite').strip().lower()


# ─────────────────────────────────────────────────────────────────────────────
# Postgres compatibility layer
#
# Design choice: a thin connection/cursor wrapper that rewrites SQLite-dialect
# queries for psycopg2 at execute() time. Chosen over a full SQLAlchemy rewrite
# because it leaves EVERY existing query string and call site untouched — the
# business logic, parameters, and results are identical; only the connection
# layer differs. The wrapper handles the four dialect gaps the audit found:
#   1. placeholders   ?  -> %s   and   :name -> %(name)s
#   2. last_insert_rowid()       -> lastval()
#   3. datetime('now')           -> now()::text
#   4. INSERT OR IGNORE          -> INSERT ... ON CONFLICT DO NOTHING
# (INSERT OR REPLACE is handled separately via upsert_sql(); ON CONFLICT ... DO
#  UPDATE already parses identically on both engines.)
# ─────────────────────────────────────────────────────────────────────────────

class _Row:
    """Mimics sqlite3.Row: supports row[0], row['col'], dict(row), iteration."""
    __slots__ = ('_v', '_idx')

    def __init__(self, values, idx):
        self._v = values
        self._idx = idx

    def __getitem__(self, k):
        if isinstance(k, int):
            return self._v[k]
        return self._v[self._idx[k]]

    def keys(self):
        return list(self._idx.keys())

    def get(self, k, default=None):
        try:
            return self[k]
        except (KeyError, IndexError):
            return default

    def __iter__(self):
        return iter(self._v)

    def __len__(self):
        return len(self._v)


def _translate_date_now(sql: str) -> str:
    """Rewrite SQLite date('now' [, '<n> days'] [, 'localtime']) for Postgres.

    SQLite returns a 'YYYY-MM-DD' string; we emit a TEXT 'YYYY-MM-DD' too so the
    comparison stays text-vs-text and the semantics are identical. Day-offset
    modifiers map to INTERVAL; 'localtime'/'utc' are ignored at date granularity.
    """
    pat = re.compile(r"date\('now'((?:\s*,\s*'[^']*')*)\)")

    def repl(m):
        mods = re.findall(r"'([^']*)'", m.group(1))
        expr = 'CURRENT_DATE'
        for mod in mods:
            dm = re.match(r"\s*([+-]?\d+)\s+days?\s*$", mod)
            if dm:
                expr = f"({expr} + INTERVAL '{int(dm.group(1))} days')"
        return f"to_char({expr}, 'YYYY-MM-DD')"

    return pat.sub(repl, sql)


def _translate(sql: str, style: str, has_params: bool) -> str:
    """Rewrite one SQLite-dialect query string for psycopg2/Postgres."""
    # A literal % must be doubled only when psycopg2 will do interpolation
    # (i.e. when params are supplied). Done before placeholders are inserted.
    if has_params:
        sql = sql.replace('%', '%%')
    if style == 'named':
        sql = re.sub(r':(\w+)', r'%(\1)s', sql)
    else:
        sql = sql.replace('?', '%s')
    sql = sql.replace('last_insert_rowid()', 'lastval()')
    sql = re.sub(r"datetime\('now'\)", "now()::text", sql)
    sql = _translate_date_now(sql)
    if re.search(r'INSERT\s+OR\s+IGNORE', sql, flags=re.I):
        sql = re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO', 'INSERT INTO', sql, flags=re.I)
        sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    return sql


class _PGCursor:
    def __init__(self, cur):
        self._c = cur
        self._idx = {}

    def execute(self, sql, params=None):
        if isinstance(params, dict):
            style, has = 'named', len(params) > 0
        elif params is None:
            style, has = 'qmark', False
        else:
            style, has = 'qmark', len(params) > 0
        sql2 = _translate(sql, style, has)
        if has:
            self._c.execute(sql2, params)
        else:
            self._c.execute(sql2)
        self._idx = ({d.name: i for i, d in enumerate(self._c.description)}
                     if self._c.description else {})
        return self

    def fetchone(self):
        r = self._c.fetchone()
        return None if r is None else _Row(r, self._idx)

    def fetchall(self):
        return [_Row(r, self._idx) for r in self._c.fetchall()]

    def __iter__(self):
        for r in self._c:
            yield _Row(r, self._idx)

    @property
    def rowcount(self):
        return self._c.rowcount

    @property
    def description(self):
        return self._c.description

    def close(self):
        self._c.close()


class _PGConnection:
    """sqlite3.Connection-like wrapper over a psycopg2 connection."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=None):
        return _PGCursor(self._raw.cursor()).execute(sql, params)

    def cursor(self):
        return _PGCursor(self._raw.cursor())

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Mirror sqlite3's `with conn`: commit on success, rollback on error.
        # Unlike sqlite3 we also close — every caller uses a self-contained
        # `with get_connection()` block, so closing avoids leaking Neon sessions.
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        self._raw.close()
        return False


def upsert_sql(table: str, columns: list, conflict_cols: list) -> str:
    """Backend-appropriate REPLACE-style upsert (qmark placeholders).

    SQLite -> INSERT OR REPLACE; Postgres -> INSERT ... ON CONFLICT DO UPDATE.
    The wrapper translates the ? placeholders for Postgres at execute time.
    """
    cols = ', '.join(columns)
    ph = ', '.join(['?'] * len(columns))
    if db_backend() == 'postgres':
        updates = ', '.join(f'{c}=EXCLUDED.{c}' for c in columns if c not in conflict_cols)
        return (f'INSERT INTO {table} ({cols}) VALUES ({ph}) '
                f'ON CONFLICT ({", ".join(conflict_cols)}) DO UPDATE SET {updates}')
    return f'INSERT OR REPLACE INTO {table} ({cols}) VALUES ({ph})'


_pg_adapters_done = False


def _register_pg_adapters():
    """Make psycopg2 return float (not Decimal) for numeric/aggregate results.

    SQLite returns float for REAL and never Decimal; our Postgres schema has no
    NUMERIC columns, so the only Decimals come from aggregates (SUM/AVG). Casting
    them to float keeps cross-backend arithmetic identical with zero call-site
    changes.
    """
    global _pg_adapters_done
    if _pg_adapters_done:
        return
    from psycopg2 import extensions
    dec2float = extensions.new_type(
        extensions.DECIMAL.values, 'DEC2FLOAT',
        lambda v, cur: float(v) if v is not None else None)
    extensions.register_type(dec2float)
    _pg_adapters_done = True


def list_tables(conn) -> list:
    """Return user table names for the active backend (sqlite_master vs Postgres)."""
    if db_backend() == 'postgres':
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' ORDER BY table_name").fetchall()
    else:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return [r[0] for r in rows]


def table_exists(conn, table: str) -> bool:
    return table in list_tables(conn)


def get_connection():
    if db_backend() == 'postgres':
        import psycopg2
        _register_pg_adapters()
        url = os.environ.get('DATABASE_URL')
        if not url:
            raise RuntimeError('DB_BACKEND=postgres but DATABASE_URL is not set')
        return _PGConnection(psycopg2.connect(url, connect_timeout=30))

    # ── SQLite (default / source of truth) ──
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


def init_db():
    # On Postgres the schema is created and managed by schema_postgres.sql
    # (applied once manually). Running the SQLite DDL here would be wrong
    # dialect, so skip it entirely for the cloud backend.
    if db_backend() == 'postgres':
        return
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sports (
                sport        TEXT PRIMARY KEY,
                display_name TEXT,
                active       INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO sports (sport, display_name, active)
            VALUES ('nfl', 'NFL', 1)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS games (
                game_id          TEXT PRIMARY KEY,
                sport            TEXT NOT NULL REFERENCES sports(sport),
                season           INTEGER,
                week             INTEGER,
                game_date        TEXT,
                start_utc        TEXT,
                home_team        TEXT,
                away_team        TEXT,
                venue            TEXT,
                status           TEXT,
                home_score       INTEGER,
                away_score       INTEGER,
                updated_utc      TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_games_sport_date ON games (sport, game_date)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS odds_snapshots (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id             TEXT NOT NULL,
                sport               TEXT NOT NULL,
                book                TEXT,
                market              TEXT,
                outcome_type        TEXT,
                line                REAL,
                price_american      INTEGER,
                price_decimal       REAL,
                snapshot_time_utc   TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_odds_snapshots_game_market_time
            ON odds_snapshots (game_id, market, snapshot_time_utc)
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_odds_snapshots
            ON odds_snapshots (game_id, book, market, outcome_type, snapshot_time_utc)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS features (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id     TEXT NOT NULL,
                sport       TEXT NOT NULL,
                as_of_date  TEXT,
                key         TEXT,
                value       REAL,
                value_text  TEXT
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_features
            ON features (game_id, as_of_date, key)
        """)

        # recommendations — one row per bot pick (or per fade, is_fade=1)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recommendations (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_key                 TEXT NOT NULL,
                sport                   TEXT NOT NULL,
                game_id                 TEXT NOT NULL,
                generated_at_utc        TEXT,
                market                  TEXT,
                side                    TEXT,
                line                    REAL,
                target_price_american   INTEGER,
                fair_price_american     INTEGER,
                edge_percent            REAL,
                confidence              TEXT,
                units                   REAL,
                is_shadow               INTEGER DEFAULT 0,
                is_fade                 INTEGER DEFAULT 0,
                num_books_in_consensus  INTEGER,
                closing_price_american  INTEGER,
                clv_percent             REAL,
                result                  TEXT,
                unit_profit             REAL,
                graded_at_utc           TEXT,
                model_probability       REAL,
                notes                   TEXT,
                config_version          TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_bot_sport ON recommendations (bot_key, sport)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_game      ON recommendations (game_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_result    ON recommendations (result)")

        # bets — manually-placed bets, optionally linked to a recommendation
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sport           TEXT NOT NULL,
                game_id         TEXT,
                market          TEXT,
                side            TEXT,
                line            REAL,
                price_american  INTEGER,
                units           REAL,
                placed_at_utc   TEXT,
                result          TEXT,
                unit_profit     REAL,
                notes           TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bet_game   ON bets (game_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bet_result ON bets (result)")

        # results — generic box score; sport-specific detail goes in detail_json
        conn.execute("""
            CREATE TABLE IF NOT EXISTS results (
                game_id           TEXT PRIMARY KEY,
                sport             TEXT NOT NULL,
                detail_json       TEXT,
                home_final        INTEGER,
                away_final        INTEGER,
                status            TEXT,
                last_updated_utc  TEXT
            )
        """)

        # pulls — ingestion audit log
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pulls (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                pull_time_utc      TEXT,
                sport              TEXT,
                source             TEXT,
                requests_remaining INTEGER,
                requests_used      INTEGER,
                success            INTEGER,
                error              TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key            TEXT PRIMARY KEY,
                value          TEXT,
                updated_at_utc TEXT
            )
        """)
