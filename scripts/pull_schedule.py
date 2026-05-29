import sys
import requests
from datetime import datetime, timezone
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
        log.error(f'API request failed: {e}')
        return None


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
        game_date = game['gameDate'][:10]  # "YYYY-MM-DDTHH:MM:SSZ" -> "YYYY-MM-DD"
        game_datetime_utc = game['gameDate']

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


def main():
    init_db()

    today = datetime.now(EASTERN).strftime('%Y-%m-%d')
    log.info(f'Pulling MLB schedule for {today}')

    data = fetch_schedule(today)
    if data is None:
        log.error('Aborting — no data returned from API')
        sys.exit(1)

    dates = data.get('dates', [])
    if not dates:
        log.info(f'No games scheduled for {today}')
        print(f'Pulled schedule for {today}: 0 games.')
        return

    raw_games = dates[0].get('games', [])
    log.info(f'API returned {len(raw_games)} game(s)')

    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    inserted = updated = skipped = 0

    with get_connection() as conn:
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

    log.info(f'Done: {inserted} inserted, {updated} updated, {skipped} skipped')
    print(
        f'Pulled schedule for {today}: {inserted + updated} games '
        f'({inserted} new, {updated} updated).'
    )


if __name__ == '__main__':
    main()
