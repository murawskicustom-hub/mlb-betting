"""
verify_migration.py — Stage 2 verification gate. Read-only on both backends.
Exits non-zero if ANY check fails.

Checks:
  a. Row count per table (SQLite vs Postgres)
  b. Content spot-check: 5 rows by id/key for recommendations, personal_bets,
     games, linescores — compare every column
  c. FK integrity: personal_bets.recommendation_id -> recommendations.id
  d. Aggregate: SUM(unit_profit) for graded recs + count by algo, both backends
  e. Sequence check: each identity sequence > current max id
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dotenv import load_dotenv
load_dotenv(dotenv_path=str(Path(__file__).resolve().parent.parent / '.env'))

import psycopg2
from database import get_connection as get_sqlite

TABLES = ['games', 'probable_pitchers', 'odds_snapshots', 'recommendations',
          'pitcher_stats', 'team_offense_stats', 'park_factors', 'personal_bets',
          'linescores', 'odds_pulls', 'settings']

IDENTITY_TABLES = ['odds_snapshots', 'recommendations', 'personal_bets', 'odds_pulls']

# (table, key column, list of 5 sample keys chosen at runtime)
SPOTCHECK = {'recommendations': 'id', 'personal_bets': 'id',
             'games': 'game_pk', 'linescores': 'game_pk'}

failures = []


def norm(v):
    """Normalize for cross-backend equality: floats rounded to kill ULP noise."""
    if isinstance(v, float):
        return round(v, 9)
    return v


def check_row_counts(s, p):
    print('\n=== (a) ROW COUNTS ===')
    print(f'{"table":<22} {"SQLite":>8} {"Postgres":>9}  match')
    ok = True
    for t in TABLES:
        s.execute(f'SELECT COUNT(*) FROM {t}'); sc = s.fetchone()[0]
        p.execute(f'SELECT COUNT(*) FROM {t}'); pc = p.fetchone()[0]
        m = sc == pc
        ok &= m
        print(f'{t:<22} {sc:>8} {pc:>9}  {"OK" if m else "*** MISMATCH ***"}')
    if not ok:
        failures.append('(a) row count mismatch')


def check_spotcheck(s, p):
    print('\n=== (b) CONTENT SPOT-CHECK (every column, 5 rows/table) ===')
    for table, key in SPOTCHECK.items():
        s.execute(f'PRAGMA table_info({table})')
        cols = [r[1] for r in s.fetchall()]
        collist = ', '.join(cols)
        s.execute(f'SELECT {key} FROM {table} ORDER BY {key} LIMIT 5')
        keys = [r[0] for r in s.fetchall()]
        if not keys:
            print(f'  {table}: no rows to check'); continue
        mism = 0
        for k in keys:
            s.execute(f'SELECT {collist} FROM {table} WHERE {key} = ?', (k,))
            srow = s.fetchone()
            p.execute(f'SELECT {collist} FROM {table} WHERE {key} = %s', (k,))
            prow = p.fetchone()
            if prow is None:
                print(f'  {table} {key}={k}: MISSING in Postgres'); mism += 1; continue
            for c, sv, pv in zip(cols, srow, prow):
                if norm(sv) != norm(pv):
                    print(f'  {table} {key}={k} col={c}: SQLite={sv!r} PG={pv!r}'); mism += 1
        status = 'OK' if mism == 0 else f'*** {mism} DIFF ***'
        print(f'  {table:<18} keys={keys}  {status}')
        if mism:
            failures.append(f'(b) {table} spot-check diffs')


def check_fk(s, p):
    print('\n=== (c) FK INTEGRITY: personal_bets.recommendation_id -> recommendations.id ===')
    p.execute("""
        SELECT pb.id, pb.recommendation_id
        FROM personal_bets pb
        WHERE pb.recommendation_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM recommendations r WHERE r.id = pb.recommendation_id)
    """)
    orphans = p.fetchall()
    p.execute("SELECT COUNT(*) FROM personal_bets WHERE recommendation_id IS NOT NULL")
    nnn = p.fetchone()[0]
    if orphans:
        print(f'  *** {len(orphans)} orphaned recommendation_id(s): {orphans}')
        failures.append('(c) FK orphans')
    else:
        print(f'  OK — all {nnn} non-NULL recommendation_id value(s) resolve to a recommendations.id')


def check_aggregates(s, p):
    print('\n=== (d) AGGREGATE CHECKS ===')
    # SUM(unit_profit) for graded recs (result IS NOT NULL)
    s.execute("SELECT COALESCE(SUM(unit_profit),0) FROM recommendations WHERE result IS NOT NULL")
    s_sum = round(s.fetchone()[0], 6)
    p.execute("SELECT COALESCE(SUM(unit_profit),0) FROM recommendations WHERE result IS NOT NULL")
    p_sum = round(float(p.fetchone()[0]), 6)
    m1 = s_sum == p_sum
    print(f'  SUM(unit_profit) graded:  SQLite={s_sum}  PG={p_sum}  {"OK" if m1 else "*** MISMATCH ***"}')
    if not m1: failures.append('(d) unit_profit sum mismatch')

    # count by algo
    s.execute("SELECT algo, COUNT(*) FROM recommendations GROUP BY algo ORDER BY algo")
    s_algo = dict(s.fetchall())
    p.execute("SELECT algo, COUNT(*) FROM recommendations GROUP BY algo ORDER BY algo")
    p_algo = dict(p.fetchall())
    m2 = s_algo == p_algo
    print(f'  count by algo:            SQLite={s_algo}  PG={p_algo}  {"OK" if m2 else "*** MISMATCH ***"}')
    if not m2: failures.append('(d) algo count mismatch')


def check_sequences(p):
    print('\n=== (e) SEQUENCE > MAX(id) ===')
    cur = p.cursor()
    ok = True
    for t in IDENTITY_TABLES:
        cur.execute(f"SELECT MAX(id) FROM {t}")
        maxid = cur.fetchone()[0] or 0
        cur.execute("SELECT pg_get_serial_sequence(%s,'id')", (t,))
        seq = cur.fetchone()[0]
        cur.execute("SELECT last_value, is_called FROM " + seq)
        last_value, is_called = cur.fetchone()
        nextval = last_value + 1 if is_called else last_value
        good = nextval > maxid
        ok &= good
        print(f'  {t:<22} max_id={maxid:<6} next_id={nextval:<6} {"OK" if good else "*** WILL COLLIDE ***"}')
    if not ok:
        failures.append('(e) sequence not above max id')


def main():
    s = get_sqlite().cursor()
    p = psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=30).cursor()

    check_row_counts(s, p)
    check_spotcheck(s, p)
    check_fk(s, p)
    check_aggregates(s, p)
    check_sequences(p.connection)

    print('\n' + '=' * 55)
    if failures:
        print('VERIFICATION FAILED:')
        for f in failures:
            print('  -', f)
        sys.exit(1)
    print('VERIFICATION PASSED — all checks (a)-(e) green.')
    print('=' * 55)


if __name__ == '__main__':
    main()
