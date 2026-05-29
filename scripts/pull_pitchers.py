import sys
import argparse
import time
import requests
from datetime import datetime, timezone, timedelta
import pytz

from database import init_db, get_connection
from logger import get_logger

EASTERN = pytz.timezone('US/Eastern')
SCHEDULE_URL = 'https://statsapi.mlb.com/api/v1/schedule'

log = get_logger('pull_pitchers')


def fetch_schedule(date_str):
    params = {'sportId': 1, 'date': date_str, 'hydrate': 'probablePitcher'}
    try:
        resp = requests.get(SCHEDULE_URL, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f'API request failed for {date_str}: {e}')
        return None


def extract_pitcher(team_data, side, game_pk, team_name):
    """
    Pull probable pitcher fields from one side of a game's teams dict.
    Returns a dict or None if no pitcher is listed.
    """
    pitcher = team_data.get('probablePitcher')
    if not pitcher or not pitcher.get('id'):
        return None

    # Note: handedness is not included in the schedule hydration — pitcher_throws stays NULL
    # until we add a separate person-lookup step.
    return {
        'game_pk': game_pk,
        'team_side': side,
        'pitcher_id': pitcher['id'],
        'pitcher_name': pitcher.get('fullName', ''),
        'pitcher_throws': None,
    }


def upsert_pitcher(conn, row, now_utc):
    row['last_updated_utc'] = now_utc
    existing = conn.execute(
        'SELECT game_pk FROM probable_pitchers WHERE game_pk = ? AND team_side = ?',
        (row['game_pk'], row['team_side'])
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE probable_pitchers SET
                pitcher_id       = :pitcher_id,
                pitcher_name     = :pitcher_name,
                pitcher_throws   = :pitcher_throws,
                last_updated_utc = :last_updated_utc
            WHERE game_pk = :game_pk AND team_side = :team_side
        """, row)
        return 'updated'
    else:
        conn.execute("""
            INSERT INTO probable_pitchers
                (game_pk, team_side, pitcher_id, pitcher_name, pitcher_throws, is_confirmed, last_updated_utc)
            VALUES
                (:game_pk, :team_side, :pitcher_id, :pitcher_name, :pitcher_throws, 0, :last_updated_utc)
        """, row)
        return 'inserted'


def process_date(date_str, conn, now_utc):
    data = fetch_schedule(date_str)
    if data is None:
        return None

    raw_games = []
    for date_block in data.get('dates', []):
        raw_games.extend(date_block.get('games', []))

    if not raw_games:
        log.info(f'{date_str}: no games found')
        return {'games': 0, 'inserted': 0, 'updated': 0, 'missing': 0}

    inserted = updated = missing_count = 0

    for game in raw_games:
        game_pk = game.get('gamePk')
        if not game_pk:
            continue

        teams = game.get('teams', {})
        home_name = teams.get('home', {}).get('team', {}).get('name', 'home team')
        away_name = teams.get('away', {}).get('team', {}).get('name', 'away team')

        game_missing = 0
        for side, team_data, name in [
            ('home', teams.get('home', {}), home_name),
            ('away', teams.get('away', {}), away_name),
        ]:
            row = extract_pitcher(team_data, side, game_pk, name)
            if row is None:
                log.debug(f'{date_str} game_pk={game_pk}: no probable pitcher listed yet for {name} ({side})')
                game_missing += 1
                continue
            result = upsert_pitcher(conn, row, now_utc)
            if result == 'inserted':
                inserted += 1
            else:
                updated += 1

        if game_missing:
            missing_count += 1

    log.info(f'{date_str}: {len(raw_games)} games — '
             f'{inserted} inserted, {updated} updated, {missing_count} games missing at least one pitcher')
    return {'games': len(raw_games), 'inserted': inserted, 'updated': updated, 'missing': missing_count}


def date_range(start_str, end_str):
    start = datetime.strptime(start_str, '%Y-%m-%d').date()
    end = datetime.strptime(end_str, '%Y-%m-%d').date()
    if start > end:
        raise ValueError(f'Start date {start_str} is after end date {end_str}')
    current = start
    while current <= end:
        yield current.strftime('%Y-%m-%d')
        current += timedelta(days=1)


def parse_args():
    parser = argparse.ArgumentParser(
        description='Pull probable starting pitchers into the MLB database.',
        epilog=(
            'Examples:\n'
            '  python pull_pitchers.py                        # today\n'
            '  python pull_pitchers.py 2026-05-30             # single date\n'
            '  python pull_pitchers.py 2026-05-29 2026-06-05  # date range\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'dates', nargs='*', metavar='YYYY-MM-DD',
        help='Optional date or date range (1 or 2 arguments). Default: today.',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    init_db()

    today = datetime.now(EASTERN).strftime('%Y-%m-%d')

    if len(args.dates) == 0:
        dates = [today]
    elif len(args.dates) == 1:
        dates = [args.dates[0]]
    elif len(args.dates) == 2:
        try:
            dates = list(date_range(args.dates[0], args.dates[1]))
        except ValueError as e:
            log.error(str(e))
            sys.exit(1)
    else:
        log.error('Too many arguments — provide 0, 1, or 2 dates.')
        sys.exit(1)

    log.info(f'Pulling probable pitchers for {len(dates)} date(s): {dates[0]}'
             + (f' to {dates[-1]}' if len(dates) > 1 else ''))

    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    total_games = total_inserted = total_updated = total_missing = 0

    with get_connection() as conn:
        for i, date_str in enumerate(dates):
            if i > 0:
                time.sleep(0.2)
            result = process_date(date_str, conn, now_utc)
            if result is None:
                continue
            total_games += result['games']
            total_inserted += result['inserted']
            total_updated += result['updated']
            total_missing += result['missing']

    print(
        f'Pulled pitchers for {dates[0]}'
        + (f' to {dates[-1]}' if len(dates) > 1 else '') + ': '
        f'{total_games} games checked, '
        f'{total_inserted} inserted, {total_updated} updated, '
        f'{total_missing} games missing at least one pitcher.'
    )


if __name__ == '__main__':
    main()
