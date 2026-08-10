"""
pull_injuries_nfl.py — pull current injury designations from ESPN's public
core API into the features table. No API key required.

Usage:
    python pull_injuries_nfl.py <season> <week> [TEAM_ABBR ...]

If no team abbreviations are given, teams are derived from whichever games
are already in the DB for that season/week (run pull_schedule_nfl.py first).
Passing an explicit team list is how a lock slot scopes the pull to just the
teams playing that slot (e.g. thursday_lock passes only the two TNF teams)
instead of hitting all 32 teams every time.

Each injury becomes one features row keyed to that team's game_id for the
given week: key = 'injury:{TEAM}:{athlete_name}', value_text = "{status} -
{short_comment}". ESPN's per-athlete injury log includes routine "Active"
entries (roster moves, not real designations) — those are filtered out;
only real designations (Questionable/Doubtful/Out/IR/etc.) are kept.
"""

import sys
import argparse
from datetime import datetime, timezone

import requests

from database import init_db, get_connection, upsert_sql
from logger import get_logger

CORE_BASE = 'https://sports.core.api.espn.com/v2/sports/football/leagues/nfl'

# ESPN's numeric team ids (from /apis/site/v2/sports/football/nfl/teams).
ESPN_TEAM_IDS = {
    'ARI': 22, 'ATL': 1,  'BAL': 33, 'BUF': 2,  'CAR': 29, 'CHI': 3,  'CIN': 4,
    'CLE': 5,  'DAL': 6,  'DEN': 7,  'DET': 8,  'GB': 9,   'HOU': 34, 'IND': 11,
    'JAX': 30, 'KC': 12,  'LV': 13,  'LAC': 24, 'LAR': 14, 'MIA': 15, 'MIN': 16,
    'NE': 17,  'NO': 18,  'NYG': 19, 'NYJ': 20, 'PHI': 21, 'PIT': 23, 'SF': 25,
    'SEA': 26, 'TB': 27,  'TEN': 10, 'WSH': 28,
}

# Injury "type" abbreviations that represent a real designation worth
# recording. Excludes 'A' (Active) which is a routine roster-status log
# entry, not an injury concern.
REAL_DESIGNATIONS = {'Q', 'D', 'O', 'IR', 'PUP', 'SUSP', 'NFI'}

log = get_logger('pull_injuries_nfl')


def _get(url: str, params: dict | None = None) -> dict | None:
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.warning(f'request failed for {url}: {e}')
        return None


def fetch_team_injuries(team_id: int, season: int, max_items: int = 25) -> list[dict]:
    """Return up to max_items real-designation injury detail dicts for a team."""
    idx = _get(f'{CORE_BASE}/teams/{team_id}/injuries', params={'limit': max_items})
    if idx is None:
        return []

    out = []
    for item in idx.get('items', [])[:max_items]:
        ref = item.get('$ref')
        if not ref:
            continue
        detail = _get(ref)
        if detail is None:
            continue
        abbrev = detail.get('type', {}).get('abbreviation', '')
        if abbrev not in REAL_DESIGNATIONS:
            continue
        athlete_ref = detail.get('athlete', {}).get('$ref')
        athlete_name = None
        if athlete_ref:
            athlete = _get(athlete_ref)
            athlete_name = athlete.get('displayName') if athlete else None
        out.append({
            'athlete_name': athlete_name or f'athlete_{item.get("$ref", "?").rsplit("/", 1)[-1]}',
            'designation': abbrev,
            'status': detail.get('status', ''),
            'short_comment': detail.get('shortComment', ''),
            'date': detail.get('date', ''),
        })
    return out


def teams_for_week(conn, season: int, week: int) -> list[str]:
    rows = conn.execute(
        'SELECT home_team, away_team FROM games WHERE sport = ? AND season = ? AND week = ?',
        ('nfl', season, week),
    ).fetchall()
    teams = set()
    for r in rows:
        teams.add(r['home_team'])
        teams.add(r['away_team'])
    return sorted(teams)


def game_id_for_team(conn, team: str, season: int, week: int) -> str | None:
    row = conn.execute("""
        SELECT game_id FROM games
        WHERE sport = ? AND season = ? AND week = ? AND (home_team = ? OR away_team = ?)
    """, ('nfl', season, week, team, team)).fetchone()
    return row['game_id'] if row else None


def log_pull(conn, pull_time_utc, success, error=None):
    conn.execute("""
        INSERT INTO pulls (pull_time_utc, sport, source, requests_remaining, requests_used, success, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (pull_time_utc, 'nfl', 'espn_injuries', None, 1, 1 if success else 0, error))


def pull_injuries(season: int, week: int, teams: list[str] | None = None) -> dict:
    init_db()
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    as_of_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    with get_connection() as conn:
        team_list = teams or teams_for_week(conn, season, week)
        if not team_list:
            log.warning(f'no teams found for {season} week {week} — run pull_schedule_nfl.py first')
            log_pull(conn, now_utc, False, error='no teams for week')
            return {'teams': 0, 'injuries_written': 0}

        total_written = 0
        for team in team_list:
            team_id = ESPN_TEAM_IDS.get(team)
            if team_id is None:
                log.warning(f'unknown ESPN team id for "{team}" — skipped')
                continue
            game_id = game_id_for_team(conn, team, season, week)
            if game_id is None:
                log.warning(f'no game_id found for {team} in {season} week {week} — skipped')
                continue

            injuries = fetch_team_injuries(team_id, season)
            for inj in injuries:
                conn.execute(
                    upsert_sql('features',
                               ['game_id', 'sport', 'as_of_date', 'key', 'value', 'value_text'],
                               ['game_id', 'as_of_date', 'key']),
                    (
                        game_id, 'nfl', as_of_date,
                        f"injury:{team}:{inj['athlete_name']}",
                        None,
                        f"{inj['designation']} - {inj['short_comment']}".strip(' -'),
                    ),
                )
                total_written += 1
            log.info(f'{team}: {len(injuries)} real-designation injuries written')

        log_pull(conn, now_utc, True)

    return {'teams': len(team_list), 'injuries_written': total_written}


def main():
    parser = argparse.ArgumentParser(description='Pull NFL injury designations from ESPN into features.')
    parser.add_argument('season', type=int)
    parser.add_argument('week', type=int)
    parser.add_argument('teams', nargs='*', metavar='TEAM_ABBR',
                         help='Optional team abbreviations to scope the pull; default = all teams playing that week.')
    args = parser.parse_args()

    result = pull_injuries(args.season, args.week, args.teams or None)
    print(f'Pulled NFL injuries for {args.season} week {args.week}: '
          f'{result["teams"]} team(s), {result["injuries_written"]} injury row(s) written.')
    sys.exit(0)


if __name__ == '__main__':
    main()
