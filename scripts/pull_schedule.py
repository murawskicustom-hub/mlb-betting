import sys
import argparse
import requests
from datetime import datetime, timezone, timedelta
import pytz

from database import init_db, get_connection
from logger import get_logger

EASTERN = pytz.timezone('US/Eastern')
SCHEDULE_URL = 'https://statsapi.mlb.com/api/v1/schedule'

log = get_logger('pull_schedule')


def fetch_schedule(date_str):
    """Call the MLB Stats API and return the parsed JSON, or None on failure."""
    params = {'sportId': 1, 'date': date_str}
    try:
        resp = requests.get(SCHEDULE_URL, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f'API request failed for {date_str}: {e}')
        return None


def utc_str_to_eastern_date(utc_str):
    """
    Convert an MLB API UTC datetime string ('YYYY-MM-DDTHH:MM:SSZ')
    to a date string in US/Eastern ('YYYY-MM-DD').
    """
    dt_utc = datetime.strptime(utc_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    dt_eastern = dt_utc.astimezone(EASTERN)
    return dt_eastern.strftime('%Y-%m-%d')


def parse_game(game):
    """
    Extract fields from a single game dict returned by the MLB API.
    Returns a dict of column values, or None if required fields are missing.
    """
    try:
        game_pk = game['gamePk']
    except KeyError:
        log.warning('Game entry missing gamePk — skipped')
        return None

    try:
        game_datetime_utc = game['gameDate']
        # Convert UTC first pitch to Eastern date — this is the canonical gameday date
        game_date = utc_str_to_eastern_date(game_datetime_utc)

        teams = game['teams']
        home = teams['home']['team']
        away = teams['away']['team']

        home_team = home['name']
        away_team = away['name']
        home_team_id = home['id']
        away_team_id = away['id']

        venue = game.get('venue', {}).get('name')
        venue_id = game.get('venue', {}).get('id')

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
        'home_team': home_team,
        'away_team': away_team,
        'home_team_id': home_team_id,
        'away_team_id': away_team_id,
        'venue': venue,
        'venue_id': venue_id,
        'status': status,
        'home_score': home_score,
        'away_score': away_score,
    }


def upsert_game(conn, game_data, now_utc):
    game_data['last_updated_utc'] = now_utc

    existing = conn.execute(
        'SELECT game_pk FROM games WHERE game_pk = ?', (game_data['game_pk'],)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE games SET
                game_date         = :game_date,
                game_datetime_utc = :game_datetime_utc,
                home_team         = :home_team,
                away_team         = :away_team,
                home_team_id      = :home_team_id,
                away_team_id      = :away_team_id,
                venue             = :venue,
                venue_id          = :venue_id,
                status            = :status,
                home_score        = :home_score,
                away_score        = :away_score,
                last_updated_utc  = :last_updated_utc
            WHERE game_pk = :game_pk
        """, game_data)
        return 'updated'
    else:
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
        """, game_data)
        return 'inserted'


def pull_date(date_str, conn, now_utc):
    """Pull schedule for a single date string and upsert into the open connection."""
    log.info(f'Pulling MLB schedule for {date_str}')
    data = fetch_schedule(date_str)
    if data is None:
        log.error(f'Aborting {date_str} — no data returned from API')
        return None, None, None

    dates = data.get('dates', [])
    if not dates:
        log.info(f'No games scheduled for {date_str}')
        return 0, 0, 0

    raw_games = dates[0].get('games', [])
    log.info(f'{date_str}: API returned {len(raw_games)} game(s)')

    inserted = updated = skipped = 0
    for game in raw_games:
        game_data = parse_game(game)
        if game_data is None:
            skipped += 1
            continue
        result = upsert_game(conn, game_data, now_utc)
        if result == 'inserted':
            inserted += 1
        else:
            updated += 1

    log.info(f'{date_str}: {inserted} inserted, {updated} updated, {skipped} skipped')
    return inserted, updated, skipped


def date_range(start_str, end_str):
    """Yield each date string from start to end inclusive."""
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
        description='Pull MLB schedule into the local database.',
        epilog=(
            'Examples:\n'
            '  python pull_schedule.py                        # today\n'
            '  python pull_schedule.py 2026-05-30             # single date\n'
            '  python pull_schedule.py 2026-05-29 2026-06-05  # date range\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'dates', nargs='*', metavar='YYYY-MM-DD',
        help='Optional date or date range (1 or 2 arguments).'
    )
    return parser.parse_args()


def main():
    args = parse_args()
    init_db()

    if len(args.dates) == 0:
        dates = [datetime.now(EASTERN).strftime('%Y-%m-%d')]
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

    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    total_inserted = total_updated = total_skipped = 0

    with get_connection() as conn:
        for date_str in dates:
            ins, upd, skp = pull_date(date_str, conn, now_utc)
            if ins is None:
                continue
            total_inserted += ins
            total_updated += upd
            total_skipped += skp

    if len(dates) == 1:
        print(
            f'Pulled schedule for {dates[0]}: {total_inserted + total_updated} games '
            f'({total_inserted} new, {total_updated} updated).'
        )
    else:
        print(
            f'Pulled schedule for {dates[0]} to {dates[-1]}: '
            f'{total_inserted + total_updated} games across {len(dates)} dates '
            f'({total_inserted} new, {total_updated} updated).'
        )


if __name__ == '__main__':
    main()
