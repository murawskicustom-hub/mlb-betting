"""
pull_stats.py -- Pull current-season pitcher and team offense stats from the MLB Stats API.

Also backfills pitcher_throws in probable_pitchers from the people endpoint.

Run in the morning orchestrator slot only (stats don't change intraday).
Usage: python pull_stats.py [YYYY]   (default: current year)
"""

import sys
import time
import requests
from datetime import datetime, timezone
import pytz

from database import init_db, get_connection
from logger import get_logger

EASTERN = pytz.timezone('US/Eastern')
log = get_logger('pull_stats')

BASE_URL = 'https://statsapi.mlb.com/api/v1'

# League-average constants for 2025 (used as regression targets and proxies).
# Update annually if needed.
LEAGUE_AVG_OPS        = 0.720
LEAGUE_AVG_ERA        = 4.20
LEAGUE_AVG_FIP        = 4.10
LEAGUE_AVG_K_PCT      = 0.220
LEAGUE_AVG_BB_PCT     = 0.085
LEAGUE_AVG_HR_PER_9   = 1.10
LEAGUE_AVG_RUNS_GAME  = 4.50

# FIP constant (regressed against ERA scale each season; stable near 3.10)
FIP_CONSTANT = 3.10


# --------------------------------------------------------------------------- #
# Pitcher stats
# --------------------------------------------------------------------------- #

def _compute_fip(ip: float, k: int, bb: int, hr: int) -> float | None:
    """FIP = ((13*HR + 3*BB - 2*K) / IP) + FIP_CONSTANT.  Returns None if IP=0."""
    if not ip or ip <= 0:
        return None
    return round(((13 * hr + 3 * bb - 2 * k) / ip) + FIP_CONSTANT, 3)


def fetch_pitcher_stats(pitcher_id: int, season: str) -> dict | None:
    """
    Fetch current-season pitching stats for one pitcher.
    Returns parsed stats dict or None if unavailable.
    """
    url = f'{BASE_URL}/people/{pitcher_id}/stats'
    params = {'stats': 'season', 'group': 'pitching', 'season': season}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning(f'pitcher_stats fetch failed for id={pitcher_id}: {e}')
        return None

    splits = data.get('stats', [{}])[0].get('splits', [])
    if not splits:
        return None
    s = splits[0].get('stat', {})

    ip  = float(s.get('inningsPitched', 0) or 0)
    k   = int(s.get('strikeOuts', 0) or 0)
    bb  = int(s.get('baseOnBalls', 0) or 0)
    hr  = int(s.get('homeRuns', 0) or 0)
    era = float(s.get('era', 0) or 0)

    # Per-PA denominators (batters faced is more accurate but PA is a safe proxy)
    bf = int(s.get('battersFaced', 0) or 0)
    if bf <= 0:
        bf = max(1, int(ip * 4.3))  # estimate ~4.3 batters/IP if BF not reported

    k_pct    = round(k / bf, 4) if bf else None
    bb_pct   = round(bb / bf, 4) if bf else None
    hr_per_9 = round(hr / ip * 9, 4) if ip > 0 else None
    fip      = _compute_fip(ip, k, bb, hr)

    return {
        'ip':      ip,
        'k_pct':   k_pct,
        'bb_pct':  bb_pct,
        'hr_per_9': hr_per_9,
        'fip':     fip,
        'era':     era,
    }


def fetch_pitcher_throws(pitcher_id: int) -> str | None:
    """Fetch handedness from people endpoint. Returns 'L', 'R', 'S', or None."""
    url = f'{BASE_URL}/people/{pitcher_id}'
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        people = data.get('people', [{}])
        if people:
            return people[0].get('pitchHand', {}).get('code')
    except Exception as e:
        log.warning(f'pitcher_throws fetch failed for id={pitcher_id}: {e}')
    return None


def upsert_pitcher_stats(conn, pitcher_id: int, season: str, as_of_date: str,
                         stats: dict, throws: str | None) -> None:
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    conn.execute("""
        INSERT INTO pitcher_stats
            (pitcher_id, season, as_of_date, ip, k_pct, bb_pct, hr_per_9,
             fip, era, throws, last_updated_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(pitcher_id, as_of_date) DO UPDATE SET
            ip=excluded.ip, k_pct=excluded.k_pct, bb_pct=excluded.bb_pct,
            hr_per_9=excluded.hr_per_9, fip=excluded.fip, era=excluded.era,
            throws=excluded.throws, last_updated_utc=excluded.last_updated_utc
    """, (
        pitcher_id, season, as_of_date,
        stats['ip'], stats['k_pct'], stats['bb_pct'], stats['hr_per_9'],
        stats['fip'], stats['era'], throws, now_utc,
    ))


def pull_pitcher_stats(conn, season: str) -> dict:
    """
    Pull stats for all upcoming probable pitchers.
    Also backfills pitcher_throws in probable_pitchers.
    """
    today = datetime.now(EASTERN).strftime('%Y-%m-%d')

    # All upcoming probable pitchers with a known pitcher_id
    rows = conn.execute("""
        SELECT DISTINCT pp.pitcher_id, pp.pitcher_name, pp.pitcher_throws
        FROM probable_pitchers pp
        JOIN games g ON g.game_pk = pp.game_pk
        WHERE g.game_date >= ?
          AND pp.pitcher_id IS NOT NULL
          AND pp.pitcher_id > 0
    """, (today,)).fetchall()

    pitcher_ids = [dict(r) for r in rows]
    log.info(f'Pulling stats for {len(pitcher_ids)} upcoming probable pitchers')

    collected = throws_backfilled = 0
    for row in pitcher_ids:
        pid = row['pitcher_id']
        time.sleep(0.15)  # be polite to the free API

        # Fetch throws if missing
        throws = row['pitcher_throws']
        if not throws:
            throws = fetch_pitcher_throws(pid)
            if throws:
                conn.execute(
                    "UPDATE probable_pitchers SET pitcher_throws = ? WHERE pitcher_id = ?",
                    (throws, pid)
                )
                throws_backfilled += 1
                log.debug(f"  Backfilled throws={throws} for pitcher_id={pid} ({row['pitcher_name']})")

        stats = fetch_pitcher_stats(pid, season)
        if stats is None:
            log.debug(f"  No stats for pitcher_id={pid} ({row['pitcher_name']})")
            continue

        upsert_pitcher_stats(conn, pid, season, today, stats, throws)
        collected += 1
        log.debug(
            f"  pitcher_id={pid} ({row['pitcher_name']}): "
            f"IP={stats['ip']:.1f} K%={stats['k_pct']:.1%} "
            f"BB%={stats['bb_pct']:.1%} FIP={stats['fip']}"
        )

    log.info(f'Pitcher stats: {collected} collected, {throws_backfilled} handedness backfills')
    return {'collected': collected, 'throws_backfilled': throws_backfilled}


# --------------------------------------------------------------------------- #
# Team offense stats
# --------------------------------------------------------------------------- #

def fetch_all_team_hitting(season: str) -> dict[int, dict]:
    """
    Fetch season hitting stats for all MLB teams.
    Returns {team_id: stats_dict}.
    """
    url = f'{BASE_URL}/teams/stats'
    params = {'stats': 'season', 'group': 'hitting', 'season': season, 'sportId': 1}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error(f'Team hitting stats fetch failed: {e}')
        return {}

    out = {}
    for entry in data.get('stats', [{}])[0].get('splits', []):
        tid = entry.get('team', {}).get('id')
        if not tid:
            continue
        s = entry.get('stat', {})
        out[tid] = {
            'runs_per_game': float(s.get('runsByDividedByGames', 0) or s.get('runsPerGame', 0) or 0),
            'ops':           float(s.get('ops', 0) or 0),
            'obp':           float(s.get('obp', 0) or 0),
            'slg':           float(s.get('slg', 0) or 0),
        }
    return out


def fetch_team_split_ops(team_id: int, split: str, season: str) -> float | None:
    """
    Fetch team OPS against left or right-handed pitching.
    split: 'vsLeft' or 'vsRight'
    """
    url = f'{BASE_URL}/teams/{team_id}/stats'
    params = {'stats': split, 'group': 'hitting', 'season': season}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        splits = data.get('stats', [{}])[0].get('splits', [])
        if splits:
            return float(splits[0].get('stat', {}).get('ops', 0) or 0) or None
    except Exception as e:
        log.debug(f'Split OPS ({split}) for team_id={team_id} failed: {e}')
    return None


def pull_team_offense_stats(conn, season: str) -> dict:
    """Pull current-season team hitting stats for all 30 teams."""
    today = datetime.now(EASTERN).strftime('%Y-%m-%d')

    all_hitting = fetch_all_team_hitting(season)
    if not all_hitting:
        log.warning('No team hitting stats returned from API')
        return {'teams': 0}

    # Compute league-average OPS from the data itself (more accurate than hard-coded)
    ops_values = [v['ops'] for v in all_hitting.values() if v['ops'] > 0]
    league_avg_ops = sum(ops_values) / len(ops_values) if ops_values else LEAGUE_AVG_OPS
    log.info(f'League avg OPS from API: {league_avg_ops:.3f} ({len(ops_values)} teams)')

    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    teams_written = 0

    for team_id, stats in all_hitting.items():
        time.sleep(0.1)

        ops = stats['ops'] or 0
        rpg = stats['runs_per_game'] or 0

        # wRC+ proxy: indexed OPS vs league (100 = league average)
        wrc_plus_proxy = round(ops / league_avg_ops * 100, 1) if league_avg_ops > 0 else 100.0

        vs_lhp = fetch_team_split_ops(team_id, 'vsLeft',  season)
        time.sleep(0.1)
        vs_rhp = fetch_team_split_ops(team_id, 'vsRight', season)

        conn.execute("""
            INSERT INTO team_offense_stats
                (team_id, season, as_of_date, wrc_plus_proxy, runs_per_game,
                 ops, vs_lhp_ops, vs_rhp_ops, last_updated_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(team_id, as_of_date) DO UPDATE SET
                wrc_plus_proxy=excluded.wrc_plus_proxy,
                runs_per_game=excluded.runs_per_game,
                ops=excluded.ops,
                vs_lhp_ops=excluded.vs_lhp_ops,
                vs_rhp_ops=excluded.vs_rhp_ops,
                last_updated_utc=excluded.last_updated_utc
        """, (team_id, season, today, wrc_plus_proxy, rpg, ops, vs_lhp, vs_rhp, now_utc))

        teams_written += 1
        log.debug(
            f'  team_id={team_id}: RPG={rpg:.2f} OPS={ops:.3f} '
            f'wRC+={wrc_plus_proxy:.0f} vsLHP={vs_lhp} vsRHP={vs_rhp}'
        )

    log.info(f'Team offense stats: {teams_written} teams written')
    return {'teams': teams_written}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    season = sys.argv[1] if len(sys.argv) > 1 else str(datetime.now(EASTERN).year)
    log.info(f'pull_stats: season={season}')
    init_db()

    with get_connection() as conn:
        pitcher_result = pull_pitcher_stats(conn, season)
        team_result    = pull_team_offense_stats(conn, season)

    print(
        f'pull_stats done: '
        f'{pitcher_result["collected"]} pitcher stat rows, '
        f'{pitcher_result["throws_backfilled"]} handedness backfills, '
        f'{team_result["teams"]} team offense rows'
    )


if __name__ == '__main__':
    main()
