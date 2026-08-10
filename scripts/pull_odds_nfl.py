"""
pull_odds_nfl.py — STUB. Real odds come from The Odds API, deferred until an
API key is set up (see PLATFORM_HANDOFF.md / the schema-rebuild plan, Phase 9).

Until then, this reads a small hand-edited manual odds file
(data/manual_odds.csv) so BotContext.odds is never empty and the rest of the
pipeline (bots -> persistence -> dashboard) stays testable end-to-end. Swap
this file's body for a real The Odds API call when ODDS_API_KEY exists —
that's a one-file change with zero orchestrator/schema impact.

data/manual_odds.csv columns (edit by hand, one row per side):
    game_id,book,market,outcome_type,line,price_american

Usage:
    python pull_odds_nfl.py <season> <week>
"""

import sys
import csv
import argparse
from pathlib import Path
from datetime import datetime, timezone

from database import init_db, get_connection
from logger import get_logger

MANUAL_ODDS_PATH = Path(__file__).resolve().parent.parent / 'data' / 'manual_odds.csv'

log = get_logger('pull_odds_nfl')


def _write_template_if_missing():
    if MANUAL_ODDS_PATH.exists():
        return
    MANUAL_ODDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANUAL_ODDS_PATH, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['game_id', 'book', 'market', 'outcome_type', 'line', 'price_american'])
        f.write('# example: nfl:401872656,manual,moneyline,home,,-150\n')
    log.info(f'Wrote empty template to {MANUAL_ODDS_PATH} — edit it by hand until ODDS_API_KEY is set up.')


def log_pull(conn, pull_time_utc, success, error=None):
    conn.execute("""
        INSERT INTO pulls (pull_time_utc, sport, source, requests_remaining, requests_used, success, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (pull_time_utc, 'nfl', 'manual_odds_stub', None, 0, 1 if success else 0, error))


def pull_odds(season: int, week: int) -> dict:
    init_db()
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    _write_template_if_missing()

    rows_written = 0
    with get_connection() as conn:
        if not MANUAL_ODDS_PATH.exists():
            log_pull(conn, now_utc, False, error='manual_odds.csv missing')
            return {'rows_written': 0}

        with open(MANUAL_ODDS_PATH, encoding='utf-8') as f:
            reader = csv.DictReader(row for row in f if row.strip() and not row.lstrip().startswith('#'))
            for r in reader:
                if not r.get('game_id'):
                    continue
                price = (r.get('price_american') or '').strip()
                line = (r.get('line') or '').strip()
                conn.execute("""
                    INSERT OR REPLACE INTO odds_snapshots
                        (game_id, sport, book, market, outcome_type, line, price_american, price_decimal, snapshot_time_utc)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    r['game_id'], 'nfl', r.get('book', 'manual'), r['market'], r['outcome_type'],
                    float(line) if line else None,
                    int(price) if price else None,
                    None, now_utc,
                ))
                rows_written += 1

        log_pull(conn, now_utc, True)

    log.info(f'manual odds stub: {rows_written} row(s) written from {MANUAL_ODDS_PATH.name}')
    return {'rows_written': rows_written}


def main():
    parser = argparse.ArgumentParser(description='STUB odds pull — reads data/manual_odds.csv until The Odds API is wired up.')
    parser.add_argument('season', type=int)
    parser.add_argument('week', type=int)
    args = parser.parse_args()

    result = pull_odds(args.season, args.week)
    print(f'[STUB] Manual odds for {args.season} week {args.week}: {result["rows_written"]} row(s) written. '
          f'Edit {MANUAL_ODDS_PATH} by hand, or wire up The Odds API in Phase 9.')
    sys.exit(0)


if __name__ == '__main__':
    main()
