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

Delete-then-insert, not upsert: the features table's unique index is
(game_id, as_of_date, key) — deliberately date-inclusive so most feature
types (tendency/EPA stats) keep a real history. Injuries are different: the
key already encodes the player's name, and there's no "this player
recovered" signal, only a fresh list of who's CURRENTLY hurt. Upserting on
that index meant every day's pull was a new row rather than a replacement,
so a player's preseason-camp injury note from a month ago would still show
up in a bot's prompt today even though they've long since returned — this
was silently bloating Coach Bo's grounded facts (and Degen Darren's notes)
with stale, resolved injuries. Each pull now deletes all existing
'injury:{team}:%' rows for that team's current game_id before writing the
fresh list, so only what ESPN reports as current ever survives.

Also computes and writes a numeric injury_impact:{team}:offense and
injury_impact:{team}:defense score per team — a position-weighted,
severity-weighted sum meant for a bot (bots/the_accountant.py) that wants a
number, not prose, to fold into its own projection. ESPN's athlete detail
(fetched here anyway, to get the display name) already includes the
player's position at no extra API cost, so this needed no new data source.
POSITION_WEIGHT and SEVERITY_WEIGHT below are a reasonable starting rubric —
a starting QB matters far more than a backup long-snapper, and "Out" matters
far more than "Questionable" (who often plays anyway) — NOT fit against any
real results, since there's no graded history yet to fit them against.
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

# (weight, side) per position abbreviation — how much losing this position
# for a game plausibly shifts a team's own EPA/play, and whether that's an
# offensive or defensive concern. Special-teams positions (K/P/LS) are
# excluded (weight 0): off_epa/def_epa are computed from pass/rush plays
# only (see pull_tendencies_nfl.py), so a kicker's injury doesn't touch
# either signal. Unknown/unlisted positions fall back to a small default.
POSITION_WEIGHT: dict[str, tuple[float, str]] = {
    'QB': (1.00, 'offense'),
    'RB': (0.45, 'offense'), 'FB': (0.15, 'offense'),
    'WR': (0.45, 'offense'), 'TE': (0.35, 'offense'),
    'T': (0.35, 'offense'), 'OT': (0.35, 'offense'), 'G': (0.25, 'offense'),
    'OG': (0.25, 'offense'), 'C': (0.30, 'offense'), 'OL': (0.30, 'offense'),
    'DE': (0.35, 'defense'), 'EDGE': (0.35, 'defense'), 'DT': (0.30, 'defense'),
    'NT': (0.25, 'defense'), 'DL': (0.30, 'defense'),
    'LB': (0.35, 'defense'), 'ILB': (0.35, 'defense'), 'OLB': (0.35, 'defense'), 'MLB': (0.35, 'defense'),
    'CB': (0.40, 'defense'), 'S': (0.35, 'defense'), 'SS': (0.35, 'defense'),
    'FS': (0.35, 'defense'), 'DB': (0.35, 'defense'),
    'K': (0.0, None), 'P': (0.0, None), 'LS': (0.0, None), 'PK': (0.0, None),
}
DEFAULT_POSITION_WEIGHT = (0.15, None)   # unknown position: small, attributed to neither side

# How much of a position's weight actually applies, by designation severity —
# "Questionable" players frequently play anyway, so count for much less than
# "Out"/IR, which are certain absences.
SEVERITY_WEIGHT: dict[str, float] = {
    'O': 1.0, 'IR': 1.0, 'SUSP': 1.0,
    'PUP': 0.9, 'NFI': 0.9,
    'D': 0.7,
    'Q': 0.3,
}

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
        position = None
        if athlete_ref:
            athlete = _get(athlete_ref)
            if athlete:
                athlete_name = athlete.get('displayName')
                position = athlete.get('position', {}).get('abbreviation')
        out.append({
            'athlete_name': athlete_name or f'athlete_{item.get("$ref", "?").rsplit("/", 1)[-1]}',
            'designation': abbrev,
            'position': position,
            'status': detail.get('status', ''),
            'short_comment': detail.get('shortComment', ''),
            'date': detail.get('date', ''),
        })
    return out


def compute_injury_impact(injuries: list[dict]) -> tuple[float, float]:
    """(offense_impact, defense_impact) for one team's injury list — see
    module docstring for the position/severity weighting rationale."""
    offense_impact = 0.0
    defense_impact = 0.0
    for inj in injuries:
        weight, side = POSITION_WEIGHT.get(inj.get('position') or '', DEFAULT_POSITION_WEIGHT)
        if side is None or weight == 0.0:
            continue
        severity = SEVERITY_WEIGHT.get(inj['designation'], 0.3)
        impact = weight * severity
        if side == 'offense':
            offense_impact += impact
        else:
            defense_impact += impact
    return offense_impact, defense_impact


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

            # Clear this team's existing injury rows (including its impact
            # scores) for this game before writing the fresh list — see
            # module docstring on why an upsert alone isn't enough (no
            # "recovered" signal, and as_of_date is part of the unique index
            # so a plain upsert would never collide with yesterday's rows
            # anyway).
            conn.execute(
                "DELETE FROM features WHERE game_id = ? AND (key LIKE ? OR key LIKE ?)",
                (game_id, f'injury:{team}:%', f'injury_impact:{team}:%'),
            )

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

            offense_impact, defense_impact = compute_injury_impact(injuries)
            for side, impact in (('offense', offense_impact), ('defense', defense_impact)):
                conn.execute(
                    upsert_sql('features',
                               ['game_id', 'sport', 'as_of_date', 'key', 'value', 'value_text'],
                               ['game_id', 'as_of_date', 'key']),
                    (game_id, 'nfl', as_of_date, f'injury_impact:{team}:{side}', impact, None),
                )
                total_written += 1

            log.info(f'{team}: {len(injuries)} real-designation injuries written '
                      f'(injury impact: offense={offense_impact:.2f}, defense={defense_impact:.2f})')

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
