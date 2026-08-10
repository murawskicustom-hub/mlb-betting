"""
pull_features_nfl.py — pull team-level EPA/efficiency stats from nflverse's
public GitHub release data into the features table. No API key required.

Usage:
    python pull_features_nfl.py <season> <week>

Source: stats_team_week_{season}.csv from the nflverse-data "stats_team"
GitHub release — a per-team-per-week aggregate of play-by-play stats
(passing/rushing EPA, sacks, turnovers, etc.). This file only exists once
nflverse's automation has processed real games for that season, so a 404
early in the season (before any games are played) is expected and handled
by logging a failed pull rather than crashing — the orchestrator can retry
the following week.

nflverse team abbreviations differ from ESPN's for two teams (LA vs LAR,
WAS vs WSH); TEAM_ABBR_MAP normalizes to the ESPN abbreviations already used
in the games table.
"""

import sys
import argparse
from datetime import datetime, timezone
from io import StringIO

import requests
import csv

from database import init_db, get_connection
from logger import get_logger

STATS_URL_TMPL = 'https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_{season}.csv'

TEAM_ABBR_MAP = {'LA': 'LAR', 'WAS': 'WSH'}

# A handful of representative EPA/efficiency columns — bots read whichever
# keys they need from the features bag; this is not an exhaustive stat set.
FEATURE_COLUMNS = [
    'passing_epa', 'rushing_epa', 'receiving_epa',
    'def_sacks', 'def_interceptions', 'def_tds',
    'passing_yards', 'rushing_yards', 'passing_interceptions',
]

log = get_logger('pull_features_nfl')


def fetch_stats_csv(season: int) -> list[dict] | None:
    url = STATS_URL_TMPL.format(season=season)
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 404:
            log.warning(f'{url} not published yet (expected before {season}\'s games are played)')
            return None
        resp.raise_for_status()
        return list(csv.DictReader(StringIO(resp.text)))
    except requests.RequestException as e:
        log.error(f'nflverse stats request failed: {e}')
        return None


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
    """, (pull_time_utc, 'nfl', 'nflverse_stats_team', None, 1, 1 if success else 0, error))


def pull_features(season: int, week: int) -> dict:
    init_db()
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    as_of_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    rows = fetch_stats_csv(season)
    with get_connection() as conn:
        if rows is None:
            log_pull(conn, now_utc, False, error=f'stats_team_week_{season}.csv not available')
            return {'teams': 0, 'features_written': 0}

        week_rows = [r for r in rows if r.get('week') == str(week)]
        log.info(f'{season} week {week}: {len(week_rows)} team-week row(s) in nflverse data')

        total_written = 0
        teams_matched = 0
        for r in week_rows:
            team = TEAM_ABBR_MAP.get(r['team'], r['team'])
            game_id = game_id_for_team(conn, team, season, week)
            if game_id is None:
                log.warning(f'no game_id for {team} in {season} week {week} — skipped')
                continue
            teams_matched += 1
            for col in FEATURE_COLUMNS:
                raw = r.get(col)
                if raw in (None, ''):
                    continue
                try:
                    value = float(raw)
                except ValueError:
                    continue
                conn.execute("""
                    INSERT OR REPLACE INTO features (game_id, sport, as_of_date, key, value, value_text)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (game_id, 'nfl', as_of_date, f'{team}:{col}', value, None))
                total_written += 1

        log_pull(conn, now_utc, True)

    return {'teams': teams_matched, 'features_written': total_written}


def main():
    parser = argparse.ArgumentParser(description='Pull NFL team EPA/efficiency stats from nflverse into features.')
    parser.add_argument('season', type=int)
    parser.add_argument('week', type=int)
    args = parser.parse_args()

    result = pull_features(args.season, args.week)
    print(f'Pulled NFL features for {args.season} week {args.week}: '
          f'{result["teams"]} team(s) matched, {result["features_written"]} feature row(s) written.')
    sys.exit(0)


if __name__ == '__main__':
    main()
