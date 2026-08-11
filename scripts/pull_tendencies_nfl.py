"""
pull_tendencies_nfl.py — pull season-to-date coaching tendency stats (pass
rate over expected, 4th-down go-for-it rate) from nflverse play-by-play into
the features table. No API key required.

Usage:
    python pull_tendencies_nfl.py <season> <week>

Source: play_by_play_{season}.csv.gz from the nflverse-data "pbp" GitHub
release — nflverse already computes pass_oe (PROE: how much more/less a team
passes than expectation, given down/distance/score/time) per play, so this
is a groupby-mean, not a model we build ourselves. 4th-down aggression is
computed here: (plays where the offense ran/passed on 4th down, i.e. went
for it) / (all 4th-down plays where a real decision was made — excludes
kneels/spikes). Aggregated season-to-date through the given week, not just
that week, since coaching tendencies are a slow-moving signal and a
single-week sample is noisy.

Like pull_features_nfl.py, this file doesn't exist for a season until
nflverse has processed real games, so a 404 before Week 1 is expected and
handled via the pulls audit log rather than crashing.
"""

import sys
import argparse
from datetime import datetime, timezone

import requests
import pandas as pd
from io import BytesIO

from database import init_db, get_connection, upsert_sql
from logger import get_logger

PBP_URL_TMPL = 'https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz'

# Only pull the columns needed — the full file has ~370 columns per play.
USECOLS = ['week', 'posteam', 'down', 'pass', 'rush', 'qb_kneel', 'qb_spike',
           'punt_attempt', 'field_goal_attempt', 'pass_oe']

log = get_logger('pull_tendencies_nfl')


def fetch_pbp(season: int) -> pd.DataFrame | None:
    url = PBP_URL_TMPL.format(season=season)
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404:
            log.warning(f'{url} not published yet (expected before {season}\'s games are played)')
            return None
        resp.raise_for_status()
        return pd.read_csv(BytesIO(resp.content), compression='gzip', usecols=USECOLS, low_memory=False)
    except requests.RequestException as e:
        log.error(f'nflverse pbp request failed: {e}')
        return None


def compute_tendencies(df: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    """Season-to-date (weeks 1..week inclusive) PROE and 4th-down aggression per team."""
    df = df[(df['week'] <= week) & df['posteam'].notna()].copy()

    proe = df.groupby('posteam')['pass_oe'].mean().rename('proe')

    fourth = df[df['down'] == 4].copy()
    went_for_it = ((fourth['pass'] == 1) | (fourth['rush'] == 1)) & \
                  (fourth['qb_kneel'] != 1) & (fourth['qb_spike'] != 1)
    real_fourth = (fourth['punt_attempt'] == 1) | (fourth['field_goal_attempt'] == 1) | went_for_it
    fourth = fourth.assign(went_for_it=went_for_it, real_fourth=real_fourth)
    fourth = fourth[fourth['real_fourth']]
    agg_rate = fourth.groupby('posteam').apply(
        lambda g: g['went_for_it'].sum() / len(g) if len(g) > 0 else None, include_groups=False
    ).rename('fourth_down_agg_rate')

    return pd.concat([proe, agg_rate], axis=1).reset_index().rename(columns={'posteam': 'team'})


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
    """, (pull_time_utc, 'nfl', 'nflverse_pbp_tendencies', None, 1, 1 if success else 0, error))


def pull_tendencies(season: int, week: int) -> dict:
    init_db()
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    as_of_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    df = fetch_pbp(season)
    with get_connection() as conn:
        if df is None:
            log_pull(conn, now_utc, False, error=f'play_by_play_{season}.csv.gz not available')
            return {'teams': 0, 'features_written': 0}

        tendencies = compute_tendencies(df, season, week)
        total_written = 0
        teams_matched = 0
        for _, row in tendencies.iterrows():
            team = row['team']
            game_id = game_id_for_team(conn, team, season, week)
            if game_id is None:
                continue  # team not playing this week — still fine, just nothing to attach it to
            teams_matched += 1
            for col in ('proe', 'fourth_down_agg_rate'):
                val = row[col]
                if pd.isna(val):
                    continue
                conn.execute(
                    upsert_sql('features',
                               ['game_id', 'sport', 'as_of_date', 'key', 'value', 'value_text'],
                               ['game_id', 'as_of_date', 'key']),
                    (game_id, 'nfl', as_of_date, f'tendency:{team}:{col}', float(val), None),
                )
                total_written += 1

        log_pull(conn, now_utc, True)

    return {'teams': teams_matched, 'features_written': total_written}


def main():
    parser = argparse.ArgumentParser(description='Pull NFL coaching tendency stats (PROE, 4th-down aggression) from nflverse into features.')
    parser.add_argument('season', type=int)
    parser.add_argument('week', type=int)
    args = parser.parse_args()

    result = pull_tendencies(args.season, args.week)
    print(f'Pulled NFL tendencies for {args.season} week {args.week}: '
          f'{result["teams"]} team(s) matched, {result["features_written"]} feature row(s) written.')
    sys.exit(0)


if __name__ == '__main__':
    main()
