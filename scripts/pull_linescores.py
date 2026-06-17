"""
pull_linescores.py — Fetch inning-by-inning linescore data from MLB Stats API.

Usage:
    python pull_linescores.py YYYY-MM-DD            # single date
    python pull_linescores.py --from YYYY-MM-DD --to YYYY-MM-DD  # date range

Data source: MLB Stats API (free, no key)
  https://statsapi.mlb.com/api/v1/game/{game_pk}/linescore

Only processes games with status 'Final' or 'Completed Early'.
Upserts into the linescores table using INSERT OR REPLACE.
"""

import sys
import json
import time
import argparse
from datetime import date, timedelta
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import get_connection, init_db, upsert_sql
from logger import get_logger

log = get_logger('pull_linescores')

STATS_API_BASE   = 'https://statsapi.mlb.com/api/v1'
REQUEST_DELAY_S  = 0.3
FINAL_STATUSES   = {'Final', 'Completed Early'}


def _fetch_json(url: str) -> dict | None:
    try:
        with urlopen(url, timeout=10) as r:
            return json.loads(r.read().decode('utf-8'))
    except (URLError, Exception) as e:
        log.warning(f'HTTP error for {url}: {e}')
        return None


def _parse_linescore(data: dict) -> dict | None:
    """
    Parse MLB Stats API linescore response.
    Returns computed fields dict, or None if game has fewer than 5 innings.
    """
    innings = data.get('innings', [])
    if not innings:
        return None

    home_by_inn = []
    away_by_inn = []

    for inn in innings:
        h_runs = inn.get('home', {}).get('runs')
        a_runs = inn.get('away', {}).get('runs')
        home_by_inn.append(h_runs if h_runs is not None else 0)
        away_by_inn.append(a_runs if a_runs is not None else 0)

    if not home_by_inn:
        return None

    first_h = home_by_inn[0] if len(home_by_inn) >= 1 else None
    first_a = away_by_inn[0] if len(away_by_inn) >= 1 else None
    yrfi    = 1 if (first_h or 0) + (first_a or 0) > 0 else 0

    if len(home_by_inn) >= 5:
        home_f5 = sum(home_by_inn[:5])
        away_f5 = sum(away_by_inn[:5])
        f5_total = home_f5 + away_f5
        if home_f5 > away_f5:
            f5_home_win = 1
        elif away_f5 > home_f5:
            f5_home_win = 0
        else:
            f5_home_win = None  # tie → push
    else:
        home_f5 = away_f5 = f5_total = f5_home_win = None
        log.warning(f'  only {len(home_by_inn)} innings — F5 fields set to NULL')

    return {
        'home_runs_by_inning':    json.dumps(home_by_inn),
        'away_runs_by_inning':    json.dumps(away_by_inn),
        'home_f5_runs':           home_f5,
        'away_f5_runs':           away_f5,
        'first_inning_home_runs': first_h,
        'first_inning_away_runs': first_a,
        'yrfi':                   yrfi,
        'f5_total_runs':          f5_total,
        'f5_home_win':            f5_home_win,
    }


def pull_linescores_for_date(conn, date_str: str) -> int:
    """Pull and upsert linescores for all finished games on date_str. Returns upsert count."""
    games = conn.execute("""
        SELECT game_pk, status FROM games
        WHERE game_date = ? AND status IN ('Final', 'Completed Early')
        ORDER BY game_pk
    """, (date_str,)).fetchall()

    if not games:
        log.info(f'  {date_str}: no finished games in DB')
        return 0

    log.info(f'  {date_str}: {len(games)} finished game(s)')
    upserted = 0
    now_utc = __import__('datetime').datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    for row in games:
        game_pk = row['game_pk']
        url     = f'{STATS_API_BASE}/game/{game_pk}/linescore'
        data    = _fetch_json(url)
        time.sleep(REQUEST_DELAY_S)

        if data is None:
            log.warning(f'    game_pk={game_pk}: no data returned')
            continue

        parsed = _parse_linescore(data)
        if parsed is None:
            log.warning(f'    game_pk={game_pk}: empty innings list')
            continue

        _ls_cols = [
            'game_pk',
            'home_runs_by_inning', 'away_runs_by_inning',
            'home_f5_runs', 'away_f5_runs',
            'first_inning_home_runs', 'first_inning_away_runs',
            'yrfi', 'f5_total_runs', 'f5_home_win',
            'status', 'last_updated_utc',
        ]
        conn.execute(upsert_sql('linescores', _ls_cols, ['game_pk']), (
            game_pk,
            parsed['home_runs_by_inning'], parsed['away_runs_by_inning'],
            parsed['home_f5_runs'],        parsed['away_f5_runs'],
            parsed['first_inning_home_runs'], parsed['first_inning_away_runs'],
            parsed['yrfi'], parsed['f5_total_runs'], parsed['f5_home_win'],
            row['status'], now_utc,
        ))
        upserted += 1

        inn_str = ', '.join(str(x) for x in json.loads(parsed['home_runs_by_inning']))
        log.debug(
            f'    game_pk={game_pk}: home=[{inn_str}] '
            f'F5 {parsed["home_f5_runs"]}-{parsed["away_f5_runs"]} '
            f'YRFI={parsed["yrfi"]}'
        )

    log.info(f'  {date_str}: upserted {upserted}/{len(games)} linescores')
    return upserted


def _date_range(from_date: date, to_date: date):
    d = from_date
    while d <= to_date:
        yield d
        d += timedelta(days=1)


def main():
    parser = argparse.ArgumentParser(description='Pull MLB linescore data')
    parser.add_argument('date', nargs='?', help='Single date YYYY-MM-DD')
    parser.add_argument('--from', dest='from_date', help='Start of range YYYY-MM-DD')
    parser.add_argument('--to',   dest='to_date',   help='End of range YYYY-MM-DD')
    args = parser.parse_args()

    if args.date:
        dates = [date.fromisoformat(args.date)]
    elif args.from_date and args.to_date:
        dates = list(_date_range(
            date.fromisoformat(args.from_date),
            date.fromisoformat(args.to_date),
        ))
    else:
        parser.print_help()
        sys.exit(1)

    init_db()

    total_upserted = 0
    with get_connection() as conn:
        for d in dates:
            total_upserted += pull_linescores_for_date(conn, d.isoformat())

    log.info(f'pull_linescores complete: {total_upserted} total upserts across {len(dates)} date(s)')
    sys.exit(0)


if __name__ == '__main__':
    main()
