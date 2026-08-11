"""
pull_tendencies_nfl.py — pull season-to-date coaching tendency and efficiency
stats (PROE, 4th-down go-for-it rate, offensive EPA/play, defensive EPA/play
allowed overall and split vs pass/vs run) from nflverse play-by-play into the
features table. No API key required.

Usage:
    python pull_tendencies_nfl.py <season> <week>

Source: play_by_play_{season}.csv.gz from the nflverse-data "pbp" GitHub
release — nflverse already computes pass_oe (PROE) and epa (Expected Points
Added) per play, so these are groupby-means, not models we build ourselves.
4th-down aggression is computed here: (plays where the offense ran/passed on
4th down, i.e. went for it) / (all 4th-down plays where a real decision was
made — excludes kneels/spikes). Defensive EPA figures are grouped by defteam
instead of posteam: mean EPA per play the team's defense has ALLOWED.
Positive means the opposing offense gained value there (a weakness);
negative means the defense suppressed it (a strength). The pass/rush split
exists so a bot can spot a scheme mismatch (a pass-heavy offense facing a
defense that's been bleeding EPA against the pass); the overall (unsplit)
off_epa/def_epa figures exist as a single efficiency number for a bot that
wants a plain net-strength estimate rather than a scheme story.

nflverse's posteam/defteam use 'LA' and 'WAS' where the games table (built
from ESPN's schedule) uses 'LAR' and 'WSH' — TEAM_ABBR_MAP normalizes this
before matching to a game_id, same mapping pull_features_nfl.py used to use.
Without it these two teams silently get zero rows written.

Season-to-date + prior-season blending: entering Week 1 (and for the first
few weeks generally), a team has little or no CURRENT-season sample, which
makes season-to-date-only numbers empty or noisy exactly when a bettor most
needs a read. To cover that, this pull also fetches the prior season's full
play-by-play and blends it in as a prior, weighted down linearly as the
current season's own sample accumulates: weight on current season =
min(games_played_this_season / BLEND_GAMES, 1.0). By BLEND_GAMES games in,
a team's number is 100% current-season; before that it's a blend, and at
Week 1 (0 games played) it's 100% last season. BLEND_GAMES=4 is a reasonable
starting choice, not a calibrated one — worth revisiting once real graded
weeks give us something to check it against.

Like pull_features_nfl.py used to, this file doesn't exist for a season
until nflverse has processed real games, so a 404 before Week 1 is expected
and handled via the pulls audit log rather than crashing — for the CURRENT
season that just means "no current-season sample yet," not a failure; the
prior-season fetch still proceeds and the blend falls back to 100% prior.
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

# nflverse pbp uses these two abbreviations where ESPN (and thus the games
# table) uses 'LAR' and 'WSH'.
TEAM_ABBR_MAP = {'LA': 'LAR', 'WAS': 'WSH'}

# Only pull the columns needed — the full file has ~370 columns per play.
USECOLS = ['week', 'posteam', 'defteam', 'down', 'pass', 'rush', 'qb_kneel', 'qb_spike',
           'punt_attempt', 'field_goal_attempt', 'pass_oe', 'epa']

TENDENCY_COLS = ('proe', 'fourth_down_agg_rate', 'off_epa', 'def_epa', 'def_epa_vs_pass', 'def_epa_vs_rush')

# Games of current-season sample before a team's number is fully weighted to
# the current season rather than blended with last season's full-season number.
BLEND_GAMES = 4

log = get_logger('pull_tendencies_nfl')


def fetch_pbp(season: int) -> pd.DataFrame | None:
    url = PBP_URL_TMPL.format(season=season)
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404:
            log.warning(f'{url} not published yet (expected before {season}\'s games are played)')
            return None
        resp.raise_for_status()
        df = pd.read_csv(BytesIO(resp.content), compression='gzip', usecols=USECOLS, low_memory=False)
        df['posteam'] = df['posteam'].replace(TEAM_ABBR_MAP)
        df['defteam'] = df['defteam'].replace(TEAM_ABBR_MAP)
        return df
    except requests.RequestException as e:
        log.error(f'nflverse pbp request failed: {e}')
        return None


def compute_tendencies(df: pd.DataFrame, week: int | None) -> pd.DataFrame:
    """PROE, 4th-down aggression, offensive EPA/play, and defensive EPA/play
    allowed (overall, vs pass, vs run) per team. If week is given, restricted
    to weeks 1..week inclusive (season-to-date); if None, the whole frame is
    used as-is (a full prior season)."""
    if week is not None:
        df = df[df['week'] <= week].copy()
    off = df[df['posteam'].notna()].copy()

    proe = off.groupby('posteam')['pass_oe'].mean()
    proe.index.name = 'team'
    proe = proe.rename('proe')

    off_with_epa = off[off['epa'].notna()]
    off_epa = off_with_epa.groupby('posteam')['epa'].mean()
    off_epa.index.name = 'team'
    off_epa = off_epa.rename('off_epa')

    games_played = off.groupby('posteam')['week'].nunique()
    games_played.index.name = 'team'
    games_played = games_played.rename('games_played')

    fourth = off[off['down'] == 4].copy()
    went_for_it = ((fourth['pass'] == 1) | (fourth['rush'] == 1)) & \
                  (fourth['qb_kneel'] != 1) & (fourth['qb_spike'] != 1)
    real_fourth = (fourth['punt_attempt'] == 1) | (fourth['field_goal_attempt'] == 1) | went_for_it
    fourth = fourth.assign(went_for_it=went_for_it, real_fourth=real_fourth)
    fourth = fourth[fourth['real_fourth']]
    agg_rate = fourth.groupby('posteam').apply(
        lambda g: g['went_for_it'].sum() / len(g) if len(g) > 0 else None, include_groups=False
    )
    agg_rate.index.name = 'team'
    agg_rate = agg_rate.rename('fourth_down_agg_rate')

    defense = df[df['defteam'].notna() & df['epa'].notna()].copy()
    def_epa = defense.groupby('defteam')['epa'].mean()
    def_epa.index.name = 'team'
    def_epa = def_epa.rename('def_epa')
    def_pass = defense[defense['pass'] == 1].groupby('defteam')['epa'].mean()
    def_pass.index.name = 'team'
    def_pass = def_pass.rename('def_epa_vs_pass')
    def_rush = defense[defense['rush'] == 1].groupby('defteam')['epa'].mean()
    def_rush.index.name = 'team'
    def_rush = def_rush.rename('def_epa_vs_rush')

    return pd.concat([proe, off_epa, agg_rate, def_epa, def_pass, def_rush, games_played], axis=1).reset_index()


def blend_with_prior(current: pd.DataFrame, prior: pd.DataFrame) -> pd.DataFrame:
    """Blend current-season-to-date stats with the prior full season's,
    weighted by how many current-season games a team has actually played
    (see BLEND_GAMES). A team missing from `current` entirely (e.g. before
    Week 1's games are even played) gets 100% prior."""
    merged = prior.set_index('team').add_suffix('_prior').join(
        current.set_index('team').add_suffix('_cur'), how='outer'
    )
    out_rows = []
    for team, row in merged.iterrows():
        games = row.get('games_played_cur')
        games = 0.0 if pd.isna(games) else games
        weight_cur = min(games / BLEND_GAMES, 1.0)
        out = {'team': team}
        for col in TENDENCY_COLS:
            cur_val = row.get(f'{col}_cur')
            prior_val = row.get(f'{col}_prior')
            cur_val = None if pd.isna(cur_val) else cur_val
            prior_val = None if pd.isna(prior_val) else prior_val
            if cur_val is None and prior_val is None:
                out[col] = None
            elif cur_val is None:
                out[col] = prior_val
            elif prior_val is None:
                out[col] = cur_val
            else:
                out[col] = weight_cur * cur_val + (1 - weight_cur) * prior_val
        out_rows.append(out)
    return pd.DataFrame(out_rows)


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

    cur_df = fetch_pbp(season)
    prior_df = fetch_pbp(season - 1)

    with get_connection() as conn:
        if cur_df is None and prior_df is None:
            log_pull(conn, now_utc, False, error=f'neither {season} nor {season - 1} pbp available')
            return {'teams': 0, 'features_written': 0}

        current = compute_tendencies(cur_df, week) if cur_df is not None else pd.DataFrame(
            columns=['team', *TENDENCY_COLS, 'games_played'])
        prior = compute_tendencies(prior_df, None) if prior_df is not None else pd.DataFrame(
            columns=['team', *TENDENCY_COLS, 'games_played'])
        blended = blend_with_prior(current, prior)

        total_written = 0
        teams_matched = 0
        for _, row in blended.iterrows():
            team = row['team']
            game_id = game_id_for_team(conn, team, season, week)
            if game_id is None:
                continue  # team not playing this week — still fine, just nothing to attach it to
            teams_matched += 1
            for col in TENDENCY_COLS:
                val = row[col]
                if val is None or pd.isna(val):
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
    parser = argparse.ArgumentParser(description='Pull NFL coaching tendency and efficiency stats from nflverse into features.')
    parser.add_argument('season', type=int)
    parser.add_argument('week', type=int)
    args = parser.parse_args()

    result = pull_tendencies(args.season, args.week)
    print(f'Pulled NFL tendencies for {args.season} week {args.week}: '
          f'{result["teams"]} team(s) matched, {result["features_written"]} feature row(s) written.')
    sys.exit(0)


if __name__ == '__main__':
    main()
