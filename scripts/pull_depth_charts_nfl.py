"""
pull_depth_charts_nfl.py — pull current starters from ESPN's depth chart
endpoint into the features table. No API key required.

Usage:
    python pull_depth_charts_nfl.py <season> <week> [TEAM_ABBR ...]

Scopable to specific teams (like pull_injuries_nfl.py) so a lock slot only
re-pulls the teams playing that slot — depth charts can change mid-week due
to a new injury, so this is worth refreshing at lock time, not just during
tuesday_research.

Each starter becomes one features row: key = 'depth:{TEAM}:{position}',
value_text = starter's name. Only rank-1 (the starter) at each position is
kept — backups aren't useful signal for "who's actually playing." Special
Teams groups are skipped; offense and defense are kept regardless of the
team's specific scheme label (e.g. "3WR 1TE", "Base 3-4 D").
"""

import sys
import argparse
from datetime import datetime, timezone

import requests

from database import init_db, get_connection, upsert_sql
from logger import get_logger

CORE_SITE = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl'

# Same mapping pull_injuries_nfl.py uses.
ESPN_TEAM_IDS = {
    'ARI': 22, 'ATL': 1,  'BAL': 33, 'BUF': 2,  'CAR': 29, 'CHI': 3,  'CIN': 4,
    'CLE': 5,  'DAL': 6,  'DEN': 7,  'DET': 8,  'GB': 9,   'HOU': 34, 'IND': 11,
    'JAX': 30, 'KC': 12,  'LV': 13,  'LAC': 24, 'LAR': 14, 'MIA': 15, 'MIN': 16,
    'NE': 17,  'NO': 18,  'NYG': 19, 'NYJ': 20, 'PHI': 21, 'PIT': 23, 'SF': 25,
    'SEA': 26, 'TB': 27,  'TEN': 10, 'WSH': 28,
}

log = get_logger('pull_depth_charts_nfl')


def _get(url: str) -> dict | None:
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.warning(f'request failed for {url}: {e}')
        return None


def fetch_starters(team_id: int) -> dict[str, str]:
    """Return {position_key: starter_name} for offense + defense (no ST)."""
    data = _get(f'{CORE_SITE}/teams/{team_id}/depthcharts')
    if data is None:
        return {}
    starters = {}
    for group in data.get('depthchart', []):
        if 'special' in group.get('name', '').lower():
            continue
        for pos_key, pos_data in group.get('positions', {}).items():
            athletes = pos_data.get('athletes', [])
            if athletes:
                starters[pos_key] = athletes[0].get('displayName', '?')
    return starters


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
    """, (pull_time_utc, 'nfl', 'espn_depthcharts', None, 1, 1 if success else 0, error))


def pull_depth_charts(season: int, week: int, teams: list[str] | None = None) -> dict:
    init_db()
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    as_of_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    with get_connection() as conn:
        team_list = teams or teams_for_week(conn, season, week)
        if not team_list:
            log.warning(f'no teams found for {season} week {week} — run pull_schedule_nfl.py first')
            log_pull(conn, now_utc, False, error='no teams for week')
            return {'teams': 0, 'starters_written': 0}

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

            starters = fetch_starters(team_id)
            for pos_key, name in starters.items():
                conn.execute(
                    upsert_sql('features',
                               ['game_id', 'sport', 'as_of_date', 'key', 'value', 'value_text'],
                               ['game_id', 'as_of_date', 'key']),
                    (game_id, 'nfl', as_of_date, f'depth:{team}:{pos_key}', None, name),
                )
                total_written += 1
            log.info(f'{team}: {len(starters)} starters written')

        log_pull(conn, now_utc, True)

    return {'teams': len(team_list), 'starters_written': total_written}


def main():
    parser = argparse.ArgumentParser(description='Pull NFL depth chart starters from ESPN into features.')
    parser.add_argument('season', type=int)
    parser.add_argument('week', type=int)
    parser.add_argument('teams', nargs='*', metavar='TEAM_ABBR',
                         help='Optional team abbreviations to scope the pull; default = all teams playing that week.')
    args = parser.parse_args()

    result = pull_depth_charts(args.season, args.week, args.teams or None)
    print(f'Pulled NFL depth charts for {args.season} week {args.week}: '
          f'{result["teams"]} team(s), {result["starters_written"]} starter row(s) written.')
    sys.exit(0)


if __name__ == '__main__':
    main()
