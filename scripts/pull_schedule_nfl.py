"""
pull_schedule_nfl.py — pull NFL schedule/scores from ESPN's public scoreboard
API into the games table. No API key required.

Usage:
    python pull_schedule_nfl.py <season> <week>
    python pull_schedule_nfl.py 2026 1

game_id is synthesized as "nfl:{espn_event_id}" so it's stable across pulls
(ESPN's event id never changes for a given game) and namespaced by sport.
Idempotent: upserts on game_id, safe to re-run every slot.
"""

import sys
import argparse
from datetime import datetime, timezone

import requests
import pytz

from database import init_db, get_connection
from logger import get_logger

SCOREBOARD_URL = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard'
EASTERN = pytz.timezone('US/Eastern')

log = get_logger('pull_schedule_nfl')


def utc_str_to_eastern_date(utc_str: str) -> str | None:
    """
    Convert an ISO UTC datetime string ('YYYY-MM-DDTHH:MMZ') to a date string
    in US/Eastern ('YYYY-MM-DD'). NFL night games (e.g. TNF at 8:20pm ET) land
    after midnight UTC, so truncating the UTC string would misdate them to the
    next calendar day — this is what the weekly orchestrator's day-of-week
    slot filters (thursday_lock/sunday_lock/monday_lock) key off of.
    """
    if not utc_str:
        return None
    try:
        dt_utc = datetime.strptime(utc_str, '%Y-%m-%dT%H:%MZ').replace(tzinfo=timezone.utc)
    except ValueError:
        dt_utc = datetime.strptime(utc_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(EASTERN).strftime('%Y-%m-%d')


def fetch_week(season: int, week: int, seasontype: int = 2):
    """Call ESPN's scoreboard endpoint for one week. Returns parsed JSON or None."""
    params = {'year': season, 'week': week, 'seasontype': seasontype}
    try:
        resp = requests.get(SCOREBOARD_URL, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f'ESPN scoreboard request failed for {season} week {week}: {e}')
        return None


def parse_event(event: dict, season: int, week: int) -> dict | None:
    try:
        comp = event['competitions'][0]
        competitors = comp['competitors']
        home = next(c for c in competitors if c['homeAway'] == 'home')
        away = next(c for c in competitors if c['homeAway'] == 'away')
    except (KeyError, IndexError, StopIteration) as e:
        log.warning(f'event {event.get("id")}: missing expected field {e} — skipped')
        return None

    status_type = comp.get('status', {}).get('type', {})
    game_date_utc = event.get('date', '')
    game_date = utc_str_to_eastern_date(game_date_utc)

    def _score(c):
        try:
            return int(c.get('score'))
        except (TypeError, ValueError):
            return None

    return {
        'game_id':    f'nfl:{event["id"]}',
        'sport':      'nfl',
        'season':     season,
        'week':       week,
        'game_date':  game_date,
        'start_utc':  game_date_utc,
        'home_team':  home['team']['abbreviation'],
        'away_team':  away['team']['abbreviation'],
        'venue':      comp.get('venue', {}).get('fullName'),
        'status':     status_type.get('name', 'STATUS_SCHEDULED'),
        'home_score': _score(home),
        'away_score': _score(away),
    }


def upsert_game(conn, g: dict, now_utc: str) -> str:
    g = dict(g, updated_utc=now_utc)
    existing = conn.execute(
        'SELECT game_id FROM games WHERE game_id = ?', (g['game_id'],)
    ).fetchone()
    if existing:
        conn.execute("""
            UPDATE games SET
                season = :season, week = :week, game_date = :game_date,
                start_utc = :start_utc, home_team = :home_team, away_team = :away_team,
                venue = :venue, status = :status, home_score = :home_score,
                away_score = :away_score, updated_utc = :updated_utc
            WHERE game_id = :game_id
        """, g)
        return 'updated'
    conn.execute("""
        INSERT INTO games (
            game_id, sport, season, week, game_date, start_utc,
            home_team, away_team, venue, status, home_score, away_score, updated_utc
        ) VALUES (
            :game_id, :sport, :season, :week, :game_date, :start_utc,
            :home_team, :away_team, :venue, :status, :home_score, :away_score, :updated_utc
        )
    """, g)
    return 'inserted'


def log_pull(conn, pull_time_utc, sport, source, success, error=None):
    conn.execute("""
        INSERT INTO pulls (pull_time_utc, sport, source, requests_remaining, requests_used, success, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (pull_time_utc, sport, source, None, 1, 1 if success else 0, error))


def pull_week(season: int, week: int) -> dict:
    init_db()
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    data = fetch_week(season, week)
    with get_connection() as conn:
        if data is None:
            log_pull(conn, now_utc, 'nfl', 'espn_scoreboard', False, error='request failed')
            return {'inserted': 0, 'updated': 0, 'skipped': 0}

        events = data.get('events', [])
        log.info(f'{season} week {week}: ESPN returned {len(events)} event(s)')

        inserted = updated = skipped = 0
        for event in events:
            g = parse_event(event, season, week)
            if g is None:
                skipped += 1
                continue
            result = upsert_game(conn, g, now_utc)
            if result == 'inserted':
                inserted += 1
            else:
                updated += 1

        log_pull(conn, now_utc, 'nfl', 'espn_scoreboard', True)

    log.info(f'{season} week {week}: {inserted} inserted, {updated} updated, {skipped} skipped')
    return {'inserted': inserted, 'updated': updated, 'skipped': skipped}


def main():
    parser = argparse.ArgumentParser(description='Pull NFL schedule/scores from ESPN into games.')
    parser.add_argument('season', type=int, help='e.g. 2026')
    parser.add_argument('week', type=int, help='regular-season week number, e.g. 1')
    args = parser.parse_args()

    result = pull_week(args.season, args.week)
    print(
        f'Pulled NFL schedule for {args.season} week {args.week}: '
        f'{result["inserted"] + result["updated"]} games '
        f'({result["inserted"]} new, {result["updated"]} updated, {result["skipped"]} skipped).'
    )
    sys.exit(0)


if __name__ == '__main__':
    main()
