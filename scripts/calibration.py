"""
calibration.py — weekly bot health/calibration report.

Two questions this answers, for every bot:
1. Calibration: when a bot sizes a pick bigger (more units), does it actually
   win more often? A well-calibrated bot's win rate should rise with its own
   unit tier — if a higher tier is winning LESS than a lower one, that's a
   real miscalibration signal, not just a bad week (see bots/the_accountant.py's
   confidence-ramp retune, which was exactly this: 5u picks winning less than
   1u picks after Week 1).
2. Silence: did a bot fail to produce ANY decision (pick or fade) for a game
   that's already kicked off this week? A bot going silent on real games is
   itself a signal something's broken upstream (schema drift, a stuck
   settings counter, a crashed API call) — this is precisely the shape of
   bug that cost Week 2 silently for two weeks before anyone noticed.

Units-tier calibration (not fair_prob-bucket calibration) is used as the
common check across all three bots, since units is the one confidence signal
every bot actually populates — coach_bo and degen_darren don't store a
meaningful edge_pct/fair_prob, only the_accountant does.

No Streamlit or Discord imports here — this is pure DB-in, dict-out logic so
both the dashboard and notify.py's weekly report can call the same function
and never disagree with each other.
"""

from datetime import datetime, timezone

BOT_DISPLAY_NAMES = {
    'coach_bo':       'Coach Bo',
    'the_accountant': 'The Accountant',
    'degen_darren':   'Degen Darren',
}

# A tier needs at least this many graded picks before its win rate is trusted
# enough to flag an inversion — otherwise a single unlucky 5u pick would flag
# every week regardless of real calibration.
MIN_TIER_SAMPLE = 5


def _current_season_week(conn, sport: str) -> tuple[int, int]:
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (f'{sport}_current_week',)).fetchone()
    week = int(row['value']) if row else 1
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (f'{sport}_current_season',)).fetchone()
    season = int(row['value']) if row else datetime.now(timezone.utc).year
    return season, week


def _bot_tier_calibration(conn, sport: str, bot_key: str) -> dict:
    rows = conn.execute("""
        SELECT units, result, unit_profit
        FROM recommendations
        WHERE sport = ? AND bot_key = ? AND is_fade = 0
          AND result IS NOT NULL AND result != 'push'
    """, (sport, bot_key)).fetchall()

    by_tier: dict[float, dict] = {}
    total_profit = 0.0
    for r in rows:
        total_profit += r['unit_profit'] or 0.0
        tier = by_tier.setdefault(r['units'], {'n': 0, 'wins': 0, 'profit': 0.0})
        tier['n'] += 1
        tier['wins'] += 1 if r['result'] == 'win' else 0
        tier['profit'] += r['unit_profit'] or 0.0

    tiers_sorted = sorted(by_tier.items())
    tier_list = [
        {'units': u, 'n': t['n'], 'win_rate': t['wins'] / t['n'], 'profit': t['profit']}
        for u, t in tiers_sorted
    ]

    # Inversion check: among tiers with enough sample, does a bigger unit size
    # ever have a meaningfully lower win rate than a smaller one?
    inverted = False
    trustworthy = [t for t in tier_list if t['n'] >= MIN_TIER_SAMPLE]
    for i in range(len(trustworthy)):
        for j in range(i + 1, len(trustworthy)):
            if trustworthy[j]['units'] > trustworthy[i]['units'] and \
               trustworthy[j]['win_rate'] < trustworthy[i]['win_rate'] - 0.05:
                inverted = True

    return {
        'total_graded': len(rows),
        'total_units': total_profit,
        'win_rate': (sum(t['wins'] for t in by_tier.values()) / len(rows)) if rows else None,
        'by_tier': tier_list,
        'inverted': inverted,
    }


def _silent_games(conn, sport: str, bot_key: str, season: int, week: int) -> list[dict]:
    """Games this week that have already kicked off but this bot has NO
    recommendation row for at all — neither a pick nor a fade."""
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    started = conn.execute("""
        SELECT game_id, home_team, away_team FROM games
        WHERE sport = ? AND season = ? AND week = ? AND start_utc <= ?
    """, (sport, season, week, now_utc)).fetchall()
    if not started:
        return []
    game_ids = [g['game_id'] for g in started]
    placeholders = ','.join('?' * len(game_ids))
    covered = {
        r['game_id'] for r in conn.execute(
            f'SELECT DISTINCT game_id FROM recommendations WHERE sport = ? AND bot_key = ? AND game_id IN ({placeholders})',
            [sport, bot_key, *game_ids],
        )
    }
    return [dict(g) for g in started if g['game_id'] not in covered]


def compute_report(conn, sport: str = 'nfl') -> dict:
    """Full calibration/health report across all three bots."""
    season, week = _current_season_week(conn, sport)
    bots = {}
    for bot_key, display_name in BOT_DISPLAY_NAMES.items():
        calibration = _bot_tier_calibration(conn, sport, bot_key)
        silent = _silent_games(conn, sport, bot_key, season, week)
        bots[bot_key] = {
            'display_name': display_name,
            **calibration,
            'silent_games': silent,
        }
    return {'season': season, 'week': week, 'bots': bots}
