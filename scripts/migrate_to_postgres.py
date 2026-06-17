"""
migrate_to_postgres.py — Stage 2 cloud migration: stand up the Postgres schema
in Neon and copy all data from local SQLite.

SAFETY: read-only against SQLite. Never writes to or deletes the SQLite DB.
Postgres side: drops+recreates the public tables (this is the initial stand-up;
SQLite remains the source of truth). Preserves original id values exactly and
resets identity sequences afterward. Dates/strings copied byte-identical.

Usage: python migrate_to_postgres.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=str(Path(__file__).resolve().parent.parent / '.env'))

import psycopg2
from psycopg2.extras import execute_values

from database import get_connection as get_sqlite

SCHEMA_SQL = Path(__file__).resolve().parent / 'schema_postgres.sql'

# Tables in dependency-friendly order (recommendations before personal_bets).
TABLES = [
    'games', 'probable_pitchers', 'odds_snapshots', 'recommendations',
    'pitcher_stats', 'team_offense_stats', 'park_factors', 'personal_bets',
    'linescores', 'odds_pulls', 'settings',
]

# Tables whose 'id' is an identity column needing a sequence reset.
IDENTITY_TABLES = ['odds_snapshots', 'recommendations', 'personal_bets', 'odds_pulls']

CHUNK = 1000


def sqlite_columns(scur, table):
    scur.execute(f'PRAGMA table_info({table})')
    return [r[1] for r in scur.fetchall()]


def create_schema(pg):
    ddl = SCHEMA_SQL.read_text(encoding='utf-8')
    cur = pg.cursor()
    # Idempotent stand-up: drop existing public tables first.
    for t in TABLES:
        cur.execute(f'DROP TABLE IF EXISTS {t} CASCADE')
    cur.execute(ddl)
    pg.commit()
    cur.execute("SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' ORDER BY table_name")
    created = [r[0] for r in cur.fetchall()]
    print(f'Schema created. Tables in Postgres ({len(created)}): {", ".join(created)}')
    return created


def migrate_table(sconn, pg, table):
    scur = sconn.cursor()
    cols = sqlite_columns(scur, table)
    collist = ', '.join(cols)
    scur.execute(f'SELECT {collist} FROM {table}')
    rows = scur.fetchall()

    pgcur = pg.cursor()
    placeholders = '(' + ', '.join(['%s'] * len(cols)) + ')'
    sql = f'INSERT INTO {table} ({collist}) VALUES %s'

    inserted = 0
    for i in range(0, len(rows), CHUNK):
        batch = [tuple(r) for r in rows[i:i + CHUNK]]
        execute_values(pgcur, sql, batch, template=placeholders, page_size=CHUNK)
        inserted += len(batch)
    pg.commit()
    print(f'  {table:<22} copied {inserted} row(s)')
    return inserted


def reset_sequences(pg):
    cur = pg.cursor()
    print('Resetting identity sequences:')
    for t in IDENTITY_TABLES:
        cur.execute(f"SELECT MAX(id) FROM {t}")
        maxid = cur.fetchone()[0]
        seq = None
        cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (t,))
        seq = cur.fetchone()[0]
        if maxid is None:
            cur.execute(f"SELECT setval(%s, 1, false)", (seq,))
            print(f'  {t:<22} empty -> seq reset to 1 (not called)')
        else:
            cur.execute("SELECT setval(%s, %s, true)", (seq, maxid))
            # verify next value
            print(f'  {t:<22} seq {seq} -> setval({maxid}); next id = {maxid + 1}')
    pg.commit()


def main():
    url = os.environ.get('DATABASE_URL')
    if not url:
        print('DATABASE_URL not set'); sys.exit(1)

    sconn = get_sqlite()
    pg = psycopg2.connect(url, connect_timeout=30)

    print('=== Creating schema ===')
    create_schema(pg)

    print('\n=== Migrating data ===')
    total = 0
    for t in TABLES:
        total += migrate_table(sconn, pg, t)
    print(f'Total rows copied: {total}')

    print()
    reset_sequences(pg)

    sconn.close()
    pg.close()
    print('\nMigration complete.')


if __name__ == '__main__':
    main()
