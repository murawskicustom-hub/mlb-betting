"""
model_v1.py -- Transparent statistical model for MLB win probability and run totals.

Design: FIP-based pitcher quality + team offense + park + home-field.
Logs every calculation chain to logs/model/ for post-game debugging.

Entry points:
    get_pitcher_quality(conn, pitcher_id, as_of_date) -> dict
    get_team_offense(conn, team_id, as_of_date)       -> dict
    get_park_factor(conn, venue_id)                   -> float
    model_game(conn, game_pk, as_of_date)             -> dict | None
"""

import math
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from logger import get_logger

log = get_logger('model_v1')

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_LOG_DIR = PROJECT_ROOT / 'logs' / 'model'

# ---------------------------------------------------------------------- #
# League-average constants (2025 -- update annually)
# ---------------------------------------------------------------------- #
LEAGUE_AVG_FIP       = 4.10   # used as regression target for pitchers
LEAGUE_AVG_RUNS_GAME = 4.50   # runs per game per team, league average
LEAGUE_AVG_OPS       = 0.720

# FIP is a "runs allowed per 9 innings" scale value.
# We convert FIP -> expected runs allowed / 9 innings:
#   runs_per_9 ~= FIP (FIP is already on ERA scale)
# Then runs_allowed_per_game = FIP * (outs_recorded / 27)
# For a starting pitcher going ~6 IP plus bullpen, use a blended approach.

# Regression weight: IP / (IP + IP_REGRESSION_DENOM)
# A pitcher with 40 IP gets 50% weight vs league average.
IP_REGRESSION_DENOM = 40.0

# Home-field advantage: +3.5 percentage points to home win probability, pre-normalization.
# Based on historical MLB data (~54% home win rate).
HOME_FIELD_ADVANTAGE = 0.035

# F5 home-field advantage is halved: no bottom-9 walk-off dynamic, and the SP
# dominates the full 5-inning window symmetrically for both sides.
HOME_FIELD_ADVANTAGE_F5 = HOME_FIELD_ADVANTAGE / 2   # ~0.0175

# F5 run-total scaling: a typical SP records roughly 5 of 9 innings.
# We scale full-game expected runs by this ratio to estimate F5 run totals.
F5_INNINGS_RATIO = 5.0 / 9.0   # ~0.556

# Pythagorean exponent
PYTHAG_EXP = 1.83

# Minimum IP required for any real signal; below this we use pure league average.
MIN_IP_FOR_SIGNAL = 10.0

# YRFI/NRFI model constants
# Calibrated so league-avg OPS (0.720) × neutral park (1.0) × scalar → p_no_run ≈ 0.700,
# yielding raw_yrfi = 1 - 0.700^2 ≈ 0.51, matching the 2024-25 league YRFI rate.
LEAGUE_YRFI_RATE       = 0.52   # 2024-2025 MLB first-inning run rate
FIRST_INN_SCALAR       = 0.42   # OPS × park × scalar → first-inning scoring proxy
FIRST_INN_NO_RUN_FLOOR = 0.40   # elite offense in hitter park: P(no run) floor
FIRST_INN_NO_RUN_CAP   = 0.80   # weak offense vs elite SP: P(no run) cap (yrfi_min≈36%)


# ---------------------------------------------------------------------- #
# Utility
# ---------------------------------------------------------------------- #

def _log_game(game_pk: int, payload: dict) -> None:
    """Write the full calculation chain to logs/model/YYYY-MM-DD_game_{pk}.json."""
    MODEL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    path  = MODEL_LOG_DIR / f'{today}_game_{game_pk}.json'
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception as e:
        log.warning(f'Could not write model log for game_pk={game_pk}: {e}')


def pythagorean_win_pct(runs_scored: float, runs_allowed: float,
                        exp: float = PYTHAG_EXP) -> float:
    """Pythagorean expectation: RS^exp / (RS^exp + RA^exp)."""
    if runs_scored <= 0 and runs_allowed <= 0:
        return 0.5
    rs_p = max(runs_scored, 0.01) ** exp
    ra_p = max(runs_allowed, 0.01) ** exp
    return rs_p / (rs_p + ra_p)


# ---------------------------------------------------------------------- #
# Data accessors
# ---------------------------------------------------------------------- #

def get_pitcher_quality(conn, pitcher_id: int, as_of_date: str) -> dict:
    """
    Return pitcher quality dict for the given pitcher and date.
    Falls back to the most recent row if as_of_date not available.

    Keys: fip, ip, k_pct, bb_pct, hr_per_9, throws, reliability_weight,
          regressed_fip, low_ip_flag
    """
    row = conn.execute("""
        SELECT fip, ip, k_pct, bb_pct, hr_per_9, throws
        FROM pitcher_stats
        WHERE pitcher_id = ? AND as_of_date <= ?
        ORDER BY as_of_date DESC
        LIMIT 1
    """, (pitcher_id, as_of_date)).fetchone()

    if row is None:
        return {
            'fip': None, 'ip': 0, 'k_pct': None, 'bb_pct': None,
            'hr_per_9': None, 'throws': None,
            'reliability_weight': 0.0,
            'regressed_fip': LEAGUE_AVG_FIP,
            'low_ip_flag': True,
        }

    ip  = row['ip'] or 0.0
    fip = row['fip']

    # Regression: weight = IP / (IP + 40).  Low-IP pitchers pulled toward league avg.
    reliability = ip / (ip + IP_REGRESSION_DENOM) if ip > 0 else 0.0

    if fip is None or ip < MIN_IP_FOR_SIGNAL:
        regressed_fip = LEAGUE_AVG_FIP
        low_ip_flag   = True
    else:
        regressed_fip = reliability * fip + (1 - reliability) * LEAGUE_AVG_FIP
        low_ip_flag   = ip < MIN_IP_FOR_SIGNAL

    return {
        'fip':               fip,
        'ip':                ip,
        'k_pct':             row['k_pct'],
        'bb_pct':            row['bb_pct'],
        'hr_per_9':          row['hr_per_9'],
        'throws':            row['throws'],
        'reliability_weight': round(reliability, 3),
        'regressed_fip':     round(regressed_fip, 3),
        'low_ip_flag':       low_ip_flag,
    }


def get_team_offense(conn, team_id: int, as_of_date: str) -> dict:
    """
    Return team offense dict for the given team and date.
    Falls back to the most recent available row.

    Keys: runs_per_game, ops, vs_lhp_ops, vs_rhp_ops, wrc_plus_proxy
    """
    row = conn.execute("""
        SELECT runs_per_game, ops, vs_lhp_ops, vs_rhp_ops, wrc_plus_proxy
        FROM team_offense_stats
        WHERE team_id = ? AND as_of_date <= ?
        ORDER BY as_of_date DESC
        LIMIT 1
    """, (team_id, as_of_date)).fetchone()

    if row is None:
        return {
            'runs_per_game': LEAGUE_AVG_RUNS_GAME,
            'ops':           LEAGUE_AVG_OPS,
            'vs_lhp_ops':    None,
            'vs_rhp_ops':    None,
            'wrc_plus_proxy': 100.0,
            'missing':       True,
        }

    rpg = row['runs_per_game'] or LEAGUE_AVG_RUNS_GAME
    return {
        'runs_per_game':  rpg,
        'ops':            row['ops'],
        'vs_lhp_ops':     row['vs_lhp_ops'],
        'vs_rhp_ops':     row['vs_rhp_ops'],
        'wrc_plus_proxy': row['wrc_plus_proxy'],
        'missing':        False,
    }


def get_park_factor(conn, venue_id: int) -> float:
    """Return park run factor for the venue. Defaults to 1.0 if unknown."""
    if not venue_id:
        return 1.0
    row = conn.execute(
        'SELECT park_run_factor FROM park_factors WHERE venue_id = ?', (venue_id,)
    ).fetchone()
    return row[0] if row else 1.0


# ---------------------------------------------------------------------- #
# Core model functions
# ---------------------------------------------------------------------- #

def pitcher_quality_score(pitcher_quality: dict) -> float:
    """
    Expected runs allowed per 9 innings, regressed toward league average.
    FIP is already on ERA scale, so this equals regressed_fip.
    """
    return pitcher_quality['regressed_fip']


def expected_runs(team_offense: dict, opposing_pitcher_runs_per_9: float,
                  park_factor: float, is_home: bool,
                  opp_throws: str | None) -> float:
    """
    Expected runs scored per game for a team, given:
      - team_offense:   team's offensive stats
      - opposing_pitcher_runs_per_9: pitcher's expected RA/9 (higher = easier to score against)
      - park_factor:    venue run factor
      - is_home:        home field adjustment
      - opp_throws:     'L' or 'R' -- selects offensive split if available

    Model logic:
      1. Team baseline = team runs_per_game
      2. Pitcher adjustment: scale by (opp_pitcher_runs_per_9 / league_avg_era)
         -- if pitcher allows more than average, offense scores more; less = fewer
      3. Multiply by park_factor
      4. Apply home/away adjustment (+2% home, -2% away)

    Uses OPS split vs pitcher handedness when available to scale baseline.
    """
    baseline = team_offense['runs_per_game'] or LEAGUE_AVG_RUNS_GAME

    # Handedness-based split adjustment
    if opp_throws in ('L', 'R'):
        split_ops = team_offense['vs_lhp_ops'] if opp_throws == 'L' else team_offense['vs_rhp_ops']
        base_ops  = team_offense['ops'] or LEAGUE_AVG_OPS
        if split_ops and base_ops and base_ops > 0:
            split_scale = split_ops / base_ops
            baseline = baseline * split_scale

    # Pitcher quality adjustment: ratio vs league average pitcher
    pitcher_adj = opposing_pitcher_runs_per_9 / LEAGUE_AVG_FIP
    adjusted    = baseline * pitcher_adj

    # Park factor
    adjusted *= park_factor

    # Home-field: home teams score ~2% more, away ~2% less (symmetric around 0%)
    # The win-probability function applies the full HFA; here we just slightly skew runs
    if is_home:
        adjusted *= 1.02
    else:
        adjusted *= 0.98

    return max(adjusted, 0.5)   # floor at 0.5 to avoid degenerate inputs


def win_probability_from_runs(home_runs: float, away_runs: float) -> tuple[float, float]:
    """
    Convert expected runs to win probabilities via Pythagorean expectation,
    then apply a flat home-field advantage shift.

    Returns (home_win_prob, away_win_prob) summing to 1.0.
    """
    home_pct_raw = pythagorean_win_pct(home_runs, away_runs)
    home_pct = home_pct_raw + HOME_FIELD_ADVANTAGE
    home_pct = min(max(home_pct, 0.01), 0.99)
    return (round(home_pct, 4), round(1.0 - home_pct, 4))


def f5_win_probability_from_runs(home_f5: float, away_f5: float) -> tuple[float, float]:
    """
    F5 win probability: same Pythagorean conversion but with halved HFA.
    No walk-off bottom-9 dynamic; SP dominates symmetrically for both sides.
    """
    home_pct_raw = pythagorean_win_pct(home_f5, away_f5)
    home_pct = home_pct_raw + HOME_FIELD_ADVANTAGE_F5
    home_pct = min(max(home_pct, 0.01), 0.99)
    return (round(home_pct, 4), round(1.0 - home_pct, 4))


# ---------------------------------------------------------------------- #
# Top-level game model
# ---------------------------------------------------------------------- #

def model_game(conn, game_pk: int, as_of_date: str) -> dict | None:
    """
    Run the full model for one game. Returns result dict or None if data insufficient.

    Result keys:
        game_pk, home_win_prob, away_win_prob,
        game_total_estimate, home_runs_exp, away_runs_exp,
        home_pitcher_q, away_pitcher_q,
        home_offense, away_offense,
        park_factor, data_ok, abstain_reason
    """
    game = conn.execute("""
        SELECT g.game_pk, g.home_team_id, g.away_team_id, g.venue_id,
               hp.pitcher_id AS home_pid, ap.pitcher_id AS away_pid,
               hp.pitcher_name AS home_pitcher_name,
               ap.pitcher_name AS away_pitcher_name
        FROM games g
        LEFT JOIN probable_pitchers hp ON hp.game_pk=g.game_pk AND hp.team_side='home'
        LEFT JOIN probable_pitchers ap ON ap.game_pk=g.game_pk AND ap.team_side='away'
        WHERE g.game_pk = ?
    """, (game_pk,)).fetchone()

    if game is None:
        return None

    home_pid = game['home_pid']
    away_pid = game['away_pid']
    park_factor = get_park_factor(conn, game['venue_id'])

    home_pitcher_q = get_pitcher_quality(conn, home_pid, as_of_date) if home_pid else None
    away_pitcher_q = get_pitcher_quality(conn, away_pid, as_of_date) if away_pid else None

    home_offense = get_team_offense(conn, game['home_team_id'], as_of_date)
    away_offense = get_team_offense(conn, game['away_team_id'], as_of_date)

    # Abstain if either pitcher is unknown or has very low IP
    if home_pitcher_q is None or away_pitcher_q is None:
        reason = 'missing pitcher data'
        _log_game(game_pk, {'game_pk': game_pk, 'abstain': True, 'reason': reason})
        return {'game_pk': game_pk, 'data_ok': False, 'abstain_reason': reason}

    if home_pitcher_q['low_ip_flag'] or away_pitcher_q['low_ip_flag']:
        reason = (
            f"low_ip: home={home_pitcher_q['ip']:.0f}IP "
            f"away={away_pitcher_q['ip']:.0f}IP (min={MIN_IP_FOR_SIGNAL})"
        )
        _log_game(game_pk, {'game_pk': game_pk, 'abstain': True, 'reason': reason,
                             'home_pitcher_q': home_pitcher_q,
                             'away_pitcher_q': away_pitcher_q})
        return {'game_pk': game_pk, 'data_ok': False, 'abstain_reason': reason}

    home_pitcher_runs9 = pitcher_quality_score(home_pitcher_q)
    away_pitcher_runs9 = pitcher_quality_score(away_pitcher_q)

    home_runs_exp = expected_runs(
        home_offense, away_pitcher_runs9, park_factor, is_home=True,
        opp_throws=away_pitcher_q['throws']
    )
    away_runs_exp = expected_runs(
        away_offense, home_pitcher_runs9, park_factor, is_home=False,
        opp_throws=home_pitcher_q['throws']
    )

    home_win, away_win = win_probability_from_runs(home_runs_exp, away_runs_exp)
    game_total = round(home_runs_exp + away_runs_exp, 2)

    # F5 projections: SP-dominant model, no bullpen blending.
    # Scale full-game expected runs by F5_INNINGS_RATIO (~5/9).
    # Park factor is already embedded in home/away_runs_exp, so ratio preserves it.
    home_f5_runs = max(round(home_runs_exp * F5_INNINGS_RATIO, 2), 0.3)
    away_f5_runs = max(round(away_runs_exp * F5_INNINGS_RATIO, 2), 0.3)
    f5_home_win, f5_away_win = f5_win_probability_from_runs(home_f5_runs, away_f5_runs)
    f5_total = round(home_f5_runs + away_f5_runs, 2)

    payload = {
        'game_pk':           game_pk,
        'as_of_date':        as_of_date,
        'data_ok':           True,
        'park_factor':       park_factor,
        'home': {
            'pitcher_name':    game['home_pitcher_name'],
            'pitcher_id':      home_pid,
            'fip':             home_pitcher_q['fip'],
            'regressed_fip':   home_pitcher_q['regressed_fip'],
            'ip':              home_pitcher_q['ip'],
            'reliability':     home_pitcher_q['reliability_weight'],
            'throws':          home_pitcher_q['throws'],
            'offense_rpg':     home_offense['runs_per_game'],
            'opp_throws_used': away_pitcher_q['throws'],
            'expected_runs':   home_runs_exp,
            'win_prob':        home_win,
            'f5_runs':         home_f5_runs,
            'f5_win_prob':     f5_home_win,
        },
        'away': {
            'pitcher_name':    game['away_pitcher_name'],
            'pitcher_id':      away_pid,
            'fip':             away_pitcher_q['fip'],
            'regressed_fip':   away_pitcher_q['regressed_fip'],
            'ip':              away_pitcher_q['ip'],
            'reliability':     away_pitcher_q['reliability_weight'],
            'throws':          away_pitcher_q['throws'],
            'offense_rpg':     away_offense['runs_per_game'],
            'opp_throws_used': home_pitcher_q['throws'],
            'expected_runs':   away_runs_exp,
            'win_prob':        away_win,
            'f5_runs':         away_f5_runs,
            'f5_win_prob':     f5_away_win,
        },
        'game_total_estimate': game_total,
        'f5_total_estimate':   f5_total,
    }
    _log_game(game_pk, payload)

    return {
        'game_pk':             game_pk,
        'data_ok':             True,
        'home_win_prob':       home_win,
        'away_win_prob':       away_win,
        'game_total_estimate': game_total,
        'home_runs_exp':       round(home_runs_exp, 2),
        'away_runs_exp':       round(away_runs_exp, 2),
        # F5 projections
        'f5_home_win_prob':    f5_home_win,
        'f5_away_win_prob':    f5_away_win,
        'f5_total_estimate':   f5_total,
        'home_f5_runs_exp':    home_f5_runs,
        'away_f5_runs_exp':    away_f5_runs,
        # Shared data
        'home_pitcher_q':      home_pitcher_q,
        'away_pitcher_q':      away_pitcher_q,
        'home_offense':        home_offense,
        'away_offense':        away_offense,
        'park_factor':         park_factor,
    }


def build_model_notes(game_result: dict, market: str, side: str) -> str:
    """
    Build a short human-readable notes string naming the top 2 drivers.
    e.g. 'SP edge: Skubal regFIP=3.20 vs Gibson 4.51; park 0.94 suppressive'
    F5 markets prefix notes with 'F5:' and show F5 total estimate.
    """
    hp = game_result['home_pitcher_q']
    ap = game_result['away_pitcher_q']
    pf = game_result['park_factor']
    is_f5 = market in ('f5_moneyline', 'f5_total')

    parts = []

    # Pitcher edge — same signal for F5 (SP is the entire story for 5 innings)
    h_fip = hp['regressed_fip']
    a_fip = ap['regressed_fip']
    fip_diff = abs(h_fip - a_fip)
    if fip_diff >= 0.3:
        adv_side = 'home' if h_fip < a_fip else 'away'
        adv_fip  = min(h_fip, a_fip)
        dis_fip  = max(h_fip, a_fip)
        parts.append(f'SP edge: {adv_side} regFIP={adv_fip:.2f} vs {dis_fip:.2f}')

    # Park factor
    if pf >= 1.05:
        parts.append(f'park={pf:.2f} hitter-friendly')
    elif pf <= 0.96:
        parts.append(f'park={pf:.2f} suppressive')

    # Total estimate — use F5 estimate for F5 markets
    if is_f5:
        f5_total = game_result.get('f5_total_estimate', '?')
        total_str = f'{f5_total:.1f}' if isinstance(f5_total, float) else str(f5_total)
        parts.append(f'F5 total={total_str}')
    else:
        total = game_result['game_total_estimate']
        parts.append(f'model total={total:.1f}')

    note = '; '.join(parts[:3]) if parts else 'model run'
    return ('F5: ' + note) if is_f5 else note


def yrfi_probability(home_pitcher_q: dict, away_pitcher_q: dict,
                     home_offense: dict, away_offense: dict,
                     park_factor: float) -> float:
    """
    P(YRFI) = 1 - P(no home run in 1st) × P(no away run in 1st)

    P(no run for team in 1st) = clamp(1 - team_ops * park * FIRST_INN_SCALAR,
                                      FIRST_INN_NO_RUN_FLOOR,
                                      FIRST_INN_NO_RUN_CAP)

    Low-IP SP means we know less about suppression — regress toward league average.
    Both low-IP: regress P(YRFI) 75% toward LEAGUE_YRFI_RATE.
    One low-IP:  regress 50%.
    """
    h_ops = home_offense.get('ops') or LEAGUE_AVG_OPS
    a_ops = away_offense.get('ops') or LEAGUE_AVG_OPS

    p_no_home_run = max(FIRST_INN_NO_RUN_FLOOR,
                        min(FIRST_INN_NO_RUN_CAP,
                            1.0 - a_ops * park_factor * FIRST_INN_SCALAR))
    p_no_away_run = max(FIRST_INN_NO_RUN_FLOOR,
                        min(FIRST_INN_NO_RUN_CAP,
                            1.0 - h_ops * park_factor * FIRST_INN_SCALAR))

    raw_yrfi = 1.0 - (p_no_home_run * p_no_away_run)

    home_low = home_pitcher_q.get('low_ip_flag', True)
    away_low = away_pitcher_q.get('low_ip_flag', True)

    if home_low and away_low:
        raw_yrfi = 0.25 * raw_yrfi + 0.75 * LEAGUE_YRFI_RATE
    elif home_low or away_low:
        raw_yrfi = 0.50 * raw_yrfi + 0.50 * LEAGUE_YRFI_RATE

    return round(max(0.0, min(1.0, raw_yrfi)), 4)
