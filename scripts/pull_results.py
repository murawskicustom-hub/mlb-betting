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

KNOWN_STATUSES = {
    'Scheduled', 'Pre-Game', 'Warmup', 'In Progress',
    'Final', 'Completed Early',
    'Postponed', 'Cancelled', 'Suspended', 'Delayed',
}

log = get_logger('pull_results')


def fetch_schedule(date_str):
    params = {'sportId': 1, 'date': date_str}
    try:
        resp = requests.get(SCHEDULE_URL, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f'API request failed for {date_str}: {e}')
        return None


def utc_str_to_eastern_date(utc_str):
    dt_utc = datetime.strptime(utc_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(EASTERN).strftime('%Y-%m-%d')


def parse_game(game):
    """Parse a game dict into a flat row dict. Returns None if gamePk missing."""
    try:
        game_pk = game['gamePk']
    except KeyError:
        log.warning('Game entry missing gamePk — skipped')
        return None

    try:
        game_datetime_utc = game['gameDate']
        game_date = utc_str_to_eastern_date(game_datetime_utc)

        teams = game['teams']
        home = teams['home']['team']
        away = teams['away']['team']

        status = game.get('status', {}).get('detailedState', 'Unknown')
        home_score = teams['home'].get('score')
        away_score = teams['away'].get('score')

    except KeyError as e:
        log.warning(f'game_pk={game_pk}: missing expected field {e} — skipped')
        return None

    return {
        'game_pk': game_pk,
        'game_date': game_date,
        'game_datetime_utc': game_datetime_utc,
        'home_team': home['name'],
        'away_team': away['name'],
        'home_team_id': home['id'],
        'away_team_id': away['id'],
        'venue': game.get('venue', {}).get('name'),
        'venue_id': game.get('venue', {}).get('id'),
        'status': status,
        'home_score': home_score,
        'away_score': away_score,
    }


def process_date(date_str, conn, now_utc, unexpected_statuses):
    data = fetch_schedule(date_str)
    if data is None:
        return None

    raw_games = []
    for date_block in data.get('dates', []):
        raw_games.extend(date_block.get('games', []))

    if not raw_games:
        log.info(f'{date_str}: no games found')
        return {'updated': 0, 'inserted': 0, 'unfinished': 0}

    updated = inserted = unfinished = 0

    for game in raw_games:
        row = parse_game(game)
        if row is None:
            continue

        status = row['status']
        if status not in KNOWN_STATUSES:
            unexpected_statuses[status] = unexpected_statuses.get(status, 0) + 1
            log.warning(f'{date_str} game_pk={row["game_pk"]}: unexpected status "{status}"')

        if status != 'Final':
            unfinished += 1

        exists = conn.execute(
            'SELECT game_pk FROM games WHERE game_pk = ?', (row['game_pk'],)
        ).fetchone()

        if exists:
            conn.execute("""
                UPDATE games SET
                    status           = :status,
                    home_score       = :home_score,
                    away_score       = :away_score,
                    last_updated_utc = :last_updated_utc
                WHERE game_pk = :game_pk
            """, {**row, 'last_updated_utc': now_utc})
            updated += 1
        else:
            # Game not in DB — full insert so nothing is silently lost
            conn.execute("""
                INSERT INTO games (
                    game_pk, game_date, game_datetime_utc,
                    home_team, away_team, home_team_id, away_team_id,
                    venue, venue_id, status, home_score, away_score, last_updated_utc
                ) VALUES (
                    :game_pk, :game_date, :game_datetime_utc,
                    :home_team, :away_team, :home_team_id, :away_team_id,
                    :venue, :venue_id, :status, :home_score, :away_score, :last_updated_utc
                )
            """, {**row, 'last_updated_utc': now_utc})
            inserted += 1
            log.info(f'{date_str} game_pk={row["game_pk"]}: inserted missing game '
                     f'({row["away_team"]} @ {row["home_team"]})')

    log.info(f'{date_str}: {updated} updated, {inserted} inserted (missing), '
             f'{unfinished} unfinished')
    return {'updated': updated, 'inserted': inserted, 'unfinished': unfinished}


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
        description='Update game results (scores, status) in the MLB database.',
        epilog=(
            'Examples:\n'
            '  python pull_results.py                        # yesterday\n'
            '  python pull_results.py 2026-05-28             # single date\n'
            '  python pull_results.py 2026-03-26 2026-05-28  # date range\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'dates', nargs='*', metavar='YYYY-MM-DD',
        help='Optional date or date range (1 or 2 arguments). Default: yesterday.',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    init_db()

    yesterday = (datetime.now(EASTERN) - timedelta(days=1)).strftime('%Y-%m-%d')

    if len(args.dates) == 0:
        dates = [yesterday]
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

    log.info(f'Updating results for {len(dates)} date(s): {dates[0]}'
             + (f' to {dates[-1]}' if len(dates) > 1 else ''))

    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    total_updated = total_inserted = total_unfinished = 0
    unexpected_statuses = {}
    start_time = time.time()

    with get_connection() as conn:
        for i, date_str in enumerate(dates):
            if i > 0:
                time.sleep(0.2)
            result = process_date(date_str, conn, now_utc, unexpected_statuses)
            if result is None:
                continue
            total_updated += result['updated']
            total_inserted += result['inserted']
            total_unfinished += result['unfinished']

    elapsed = time.time() - start_time

    if unexpected_statuses:
        log.warning(f'Unexpected statuses encountered: {unexpected_statuses}')

    summary_lines = [
        f'Results updated for {dates[0]}'
        + (f' to {dates[-1]}' if len(dates) > 1 else '') + ':',
        f'  {total_updated} rows updated, {total_inserted} missing games inserted',
        f'  {total_unfinished} games not yet Final',
    ]
    if unexpected_statuses:
        summary_lines.append(f'  Unexpected statuses: {unexpected_statuses}')
    summary_lines.append(f'  Elapsed: {elapsed:.1f}s')

    print('\n'.join(summary_lines))


if __name__ == '__main__':
    main()
