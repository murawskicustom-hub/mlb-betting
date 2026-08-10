"""
metrics.py — pure database query functions, no Streamlit imports.
Each function takes an open connection and returns plain Python values.

Every query is sport-scoped (sport='nfl' today, more sports later per
PLATFORM_HANDOFF.md) and bot-scoped where relevant. Fades (is_fade=1) are
always excluded from win/loss/units math — they carry no side/units to grade
— but ARE queryable for "why did X sit this one out" views.
"""

from datetime import datetime, timedelta
import pytz

EASTERN = pytz.timezone('US/Eastern')

BOT_DISPLAY_NAMES = {
    'coach_bo':       'Coach Bo',
    'the_accountant': 'The Accountant',
    'degen_darren':   'Degen Darren',
}

# Admin isn't a registered bot — there's no bot_key on any recommendations row
# for it. It's a virtual competitor computed from the bets table (the human's
# manually-logged personal bets), so it can stand shoulder-to-shoulder with
# the 3 bots in the standings without needing a 4th synthetic bot_key.
ADMIN_KEY = 'admin'
ADMIN_DISPLAY_NAME = 'Admin (You)'


def _today_et() -> str:
    return datetime.now(EASTERN).strftime('%Y-%m-%d')


# ── Settings ──────────────────────────────────────────────────────────────────

def current_season_week(conn, sport: str = 'nfl') -> tuple[int, int]:
    row = conn.execute(
        'SELECT value FROM settings WHERE key = ?', (f'{sport}_current_season',)
    ).fetchone()
    season = int(row['value']) if row else datetime.now(EASTERN).year
    row = conn.execute(
        'SELECT value FROM settings WHERE key = ?', (f'{sport}_current_week',)
    ).fetchone()
    week = int(row['value']) if row else 1
    return season, week


# ── Season standings — the 3-way competition ────────────────────────────────────

def season_standings(conn, sport: str) -> list[dict]:
    """Units-based standings for every registered bot PLUS Admin (the human
    competitor, computed from the bets table), ordered by total units.
    Anyone with no graded picks yet still appears, at 0."""
    rows = conn.execute("""
        SELECT bot_key,
               COUNT(*) as graded,
               SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN result IN ('push','void') THEN 1 ELSE 0 END) as pushes,
               COALESCE(SUM(unit_profit), 0) as total_units,
               AVG(clv_percent) as avg_clv
        FROM recommendations
        WHERE sport = ? AND is_fade = 0 AND result IS NOT NULL
        GROUP BY bot_key
    """, (sport,)).fetchall()
    by_bot = {r['bot_key']: r for r in rows}

    out = []
    for bot_key, display_name in BOT_DISPLAY_NAMES.items():
        r = by_bot.get(bot_key)
        out.append({
            'bot_key':      bot_key,
            'display_name': display_name,
            'graded':       r['graded'] if r else 0,
            'wins':         (r['wins'] or 0) if r else 0,
            'losses':       (r['losses'] or 0) if r else 0,
            'pushes':       (r['pushes'] or 0) if r else 0,
            'total_units':  round(r['total_units'] or 0.0, 2) if r else 0.0,
            'avg_clv':      r['avg_clv'] if r else None,
        })

    admin_row = conn.execute("""
        SELECT COUNT(*) as graded,
               SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN result IN ('push','void') THEN 1 ELSE 0 END) as pushes,
               COALESCE(SUM(unit_profit), 0) as total_units,
               AVG(clv_percent) as avg_clv
        FROM bets
        WHERE sport = ? AND result IS NOT NULL
    """, (sport,)).fetchone()
    out.append({
        'bot_key':      ADMIN_KEY,
        'display_name': ADMIN_DISPLAY_NAME,
        'graded':       admin_row['graded'] or 0,
        'wins':         admin_row['wins'] or 0,
        'losses':       admin_row['losses'] or 0,
        'pushes':       admin_row['pushes'] or 0,
        'total_units':  round(admin_row['total_units'] or 0.0, 2),
        'avg_clv':      admin_row['avg_clv'],
    })

    out.sort(key=lambda x: x['total_units'], reverse=True)
    return out


def current_week_pick_counts(conn, sport: str, season: int, week: int) -> list[dict]:
    """Picks vs fades per bot for the given week, for every registered bot,
    plus Admin (picks = distinct games bet on; fades = slate games with no bet,
    mirroring the bots' fade semantics so the comparison stays apples-to-apples)."""
    rows = conn.execute("""
        SELECT r.bot_key,
               SUM(CASE WHEN r.is_fade = 0 THEN 1 ELSE 0 END) as picks,
               SUM(CASE WHEN r.is_fade = 1 THEN 1 ELSE 0 END) as fades
        FROM recommendations r
        JOIN games g ON g.game_id = r.game_id
        WHERE r.sport = ? AND g.season = ? AND g.week = ?
        GROUP BY r.bot_key
    """, (sport, season, week)).fetchall()
    by_bot = {r['bot_key']: r for r in rows}

    out = []
    for bot_key, display_name in BOT_DISPLAY_NAMES.items():
        r = by_bot.get(bot_key)
        out.append({
            'bot_key':      bot_key,
            'display_name': display_name,
            'picks':        r['picks'] if r else 0,
            'fades':        r['fades'] if r else 0,
        })

    total_games = conn.execute(
        'SELECT COUNT(*) FROM games WHERE sport = ? AND season = ? AND week = ?',
        (sport, season, week),
    ).fetchone()[0]
    admin_games_bet = conn.execute("""
        SELECT COUNT(DISTINCT b.game_id) FROM bets b
        JOIN games g ON g.game_id = b.game_id
        WHERE b.sport = ? AND g.season = ? AND g.week = ?
    """, (sport, season, week)).fetchone()[0]
    out.append({
        'bot_key':      ADMIN_KEY,
        'display_name': ADMIN_DISPLAY_NAME,
        'picks':        admin_games_bet,
        'fades':        max(total_games - admin_games_bet, 0),
    })
    return out


# ── This Week page ───────────────────────────────────────────────────────────

def week_recs(conn, sport: str, season: int, week: int) -> list[dict]:
    """All recommendations (picks AND fades) for a given week, with game info."""
    rows = conn.execute("""
        SELECT r.*, g.away_team, g.home_team, g.start_utc, g.game_date, g.status
        FROM recommendations r
        JOIN games g ON g.game_id = r.game_id
        WHERE r.sport = ? AND g.season = ? AND g.week = ?
        ORDER BY g.start_utc, r.bot_key
    """, (sport, season, week)).fetchall()
    return [dict(r) for r in rows]


def games_for_week(conn, sport: str, season: int, week: int) -> list[dict]:
    rows = conn.execute("""
        SELECT * FROM games WHERE sport = ? AND season = ? AND week = ?
        ORDER BY start_utc
    """, (sport, season, week)).fetchall()
    return [dict(r) for r in rows]


# ── Performance page ──────────────────────────────────────────────────────────

def bot_summary(conn, sport: str, bot_key: str) -> dict:
    """Headline P/L, win rate, avg CLV, ROI for one bot's graded picks."""
    row = conn.execute("""
        SELECT COUNT(*) as graded,
               SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN result IN ('push','void') THEN 1 ELSE 0 END) as pushes,
               COALESCE(SUM(unit_profit), 0) as total_units,
               COALESCE(SUM(units), 0) as total_staked,
               AVG(clv_percent) as avg_clv
        FROM recommendations
        WHERE sport = ? AND bot_key = ? AND is_fade = 0 AND result IS NOT NULL
    """, (sport, bot_key)).fetchone()

    total_units  = row['total_units'] or 0.0
    total_staked = row['total_staked'] or 0.0
    roi = round(total_units / total_staked * 100, 1) if total_staked > 0 else None

    return {
        'graded':       row['graded'] or 0,
        'wins':         row['wins'] or 0,
        'losses':       row['losses'] or 0,
        'pushes':       row['pushes'] or 0,
        'total_units':  round(total_units, 2),
        'avg_clv':      row['avg_clv'],
        'roi_pct':      roi,
        'total_staked': round(total_staked, 1),
    }


def bot_breakdown(conn, sport: str, bot_key: str, group_col: str) -> list[dict]:
    """Breakdown table: units P/L, win rate, avg CLV by group (market, confidence, ...)."""
    rows = conn.execute(f"""
        SELECT {group_col} as group_key,
               COUNT(*) as picks,
               SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN result IN ('push','void') THEN 1 ELSE 0 END) as pushes,
               COALESCE(SUM(unit_profit), 0) as unit_profit,
               COALESCE(SUM(units), 0) as units_staked,
               AVG(clv_percent) as avg_clv,
               AVG(edge_percent) as avg_edge
        FROM recommendations
        WHERE sport = ? AND bot_key = ? AND is_fade = 0 AND result IS NOT NULL
        GROUP BY {group_col}
        ORDER BY unit_profit DESC
    """, (sport, bot_key)).fetchall()
    return [dict(r) for r in rows]


def unit_trend_by_week(conn, sport: str, bot_key: str) -> list[dict]:
    """Cumulative unit P/L per (season, week), for the chart."""
    rows = conn.execute("""
        SELECT g.season, g.week, SUM(r.unit_profit) as week_units
        FROM recommendations r
        JOIN games g ON g.game_id = r.game_id
        WHERE r.sport = ? AND r.bot_key = ? AND r.is_fade = 0 AND r.result IS NOT NULL
        GROUP BY g.season, g.week
        ORDER BY g.season, g.week
    """, (sport, bot_key)).fetchall()

    out = []
    cum = 0.0
    for r in rows:
        cum += r['week_units'] or 0
        out.append({
            'label':     f"S{r['season']} W{r['week']}",
            'week_units': round(r['week_units'] or 0, 3),
            'cum_units':  round(cum, 3),
        })
    return out


def pick_ledger(conn, sport: str, bot_key: str, start_date: str = None, end_date: str = None) -> list[dict]:
    """One row per recommendation (pick or fade) for the given bot, newest first."""
    params = [sport, bot_key]
    date_clauses = ''
    if start_date:
        date_clauses += ' AND g.game_date >= ?'
        params.append(start_date)
    if end_date:
        date_clauses += ' AND g.game_date <= ?'
        params.append(end_date)

    rows = conn.execute(f"""
        SELECT r.id, g.game_date, g.away_team, g.home_team,
               r.market, r.side, r.line, r.target_price_american, r.fair_price_american,
               r.edge_percent, r.confidence, r.units, r.is_fade,
               r.clv_percent, r.result, r.unit_profit
        FROM recommendations r
        JOIN games g ON g.game_id = r.game_id
        WHERE r.sport = ? AND r.bot_key = ?
          {date_clauses}
        ORDER BY g.game_date DESC, r.id DESC
    """, params).fetchall()
    return [dict(r) for r in rows]


# ── Admin (the human competitor) — mirrors the bot_* functions above but is
# sourced from the bets table instead of recommendations, since Admin's
# "picks" are whatever they log manually on My Bets. ────────────────────────

def admin_summary(conn, sport: str) -> dict:
    """Headline P/L, win rate, avg CLV, ROI for Admin's graded bets."""
    row = conn.execute("""
        SELECT COUNT(*) as graded,
               SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN result IN ('push','void') THEN 1 ELSE 0 END) as pushes,
               COALESCE(SUM(unit_profit), 0) as total_units,
               COALESCE(SUM(units), 0) as total_staked,
               AVG(clv_percent) as avg_clv
        FROM bets
        WHERE sport = ? AND result IS NOT NULL
    """, (sport,)).fetchone()

    total_units  = row['total_units'] or 0.0
    total_staked = row['total_staked'] or 0.0
    roi = round(total_units / total_staked * 100, 1) if total_staked > 0 else None

    return {
        'graded':       row['graded'] or 0,
        'wins':         row['wins'] or 0,
        'losses':       row['losses'] or 0,
        'pushes':       row['pushes'] or 0,
        'total_units':  round(total_units, 2),
        'avg_clv':      row['avg_clv'],
        'roi_pct':      roi,
        'total_staked': round(total_staked, 1),
    }


def admin_breakdown(conn, sport: str, group_col: str) -> list[dict]:
    """Breakdown table: units P/L, win rate, avg CLV by group (market, ...) for Admin."""
    rows = conn.execute(f"""
        SELECT {group_col} as group_key,
               COUNT(*) as picks,
               SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN result IN ('push','void') THEN 1 ELSE 0 END) as pushes,
               COALESCE(SUM(unit_profit), 0) as unit_profit,
               COALESCE(SUM(units), 0) as units_staked,
               AVG(clv_percent) as avg_clv
        FROM bets
        WHERE sport = ? AND result IS NOT NULL
        GROUP BY {group_col}
        ORDER BY unit_profit DESC
    """, (sport,)).fetchall()
    return [dict(r) for r in rows]


def admin_unit_trend_by_week(conn, sport: str) -> list[dict]:
    """Cumulative unit P/L per (season, week) for Admin, for the chart."""
    rows = conn.execute("""
        SELECT g.season, g.week, SUM(b.unit_profit) as week_units
        FROM bets b
        JOIN games g ON g.game_id = b.game_id
        WHERE b.sport = ? AND b.result IS NOT NULL
        GROUP BY g.season, g.week
        ORDER BY g.season, g.week
    """, (sport,)).fetchall()

    out = []
    cum = 0.0
    for r in rows:
        cum += r['week_units'] or 0
        out.append({
            'label':      f"S{r['season']} W{r['week']}",
            'week_units': round(r['week_units'] or 0, 3),
            'cum_units':  round(cum, 3),
        })
    return out


def admin_pick_ledger(conn, sport: str, start_date: str = None, end_date: str = None) -> list[dict]:
    """One row per bet for Admin, newest first — shaped like pick_ledger() so
    the Performance page can render both with the same table code."""
    params = [sport]
    date_clauses = ''
    if start_date:
        date_clauses += ' AND g.game_date >= ?'
        params.append(start_date)
    if end_date:
        date_clauses += ' AND g.game_date <= ?'
        params.append(end_date)

    rows = conn.execute(f"""
        SELECT b.id, g.game_date, g.away_team, g.home_team,
               b.market, b.side, b.line, b.price_american as target_price_american,
               NULL as fair_price_american, NULL as edge_percent,
               NULL as confidence, b.units, 0 as is_fade,
               b.clv_percent, b.result, b.unit_profit
        FROM bets b
        JOIN games g ON g.game_id = b.game_id
        WHERE b.sport = ?
          {date_clauses}
        ORDER BY g.game_date DESC, b.id DESC
    """, params).fetchall()
    return [dict(r) for r in rows]


# ── My Bets page ──────────────────────────────────────────────────────────────

def all_bets(conn, sport: str) -> list[dict]:
    rows = conn.execute("""
        SELECT b.*, g.away_team, g.home_team, g.game_date, g.start_utc
        FROM bets b
        LEFT JOIN games g ON g.game_id = b.game_id
        WHERE b.sport = ?
        ORDER BY b.placed_at_utc DESC
    """, (sport,)).fetchall()
    return [dict(r) for r in rows]


def upcoming_games_for_picker(conn, sport: str) -> list[dict]:
    """Games available for bet entry — upcoming + recent 7 days."""
    since = (datetime.now(EASTERN) - timedelta(days=7)).strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT game_id, away_team, home_team, game_date, start_utc
        FROM games
        WHERE sport = ? AND game_date >= ?
        ORDER BY start_utc
    """, (sport, since)).fetchall()
    return [dict(r) for r in rows]


def unbet_recs_for_game(conn, game_id: str) -> list[dict]:
    """Non-shadow, non-fade recommendations for a game not yet linked to a bet."""
    rows = conn.execute("""
        SELECT r.id, r.bot_key, r.market, r.side, r.line, r.target_price_american, r.confidence
        FROM recommendations r
        WHERE r.game_id = ? AND r.is_shadow = 0 AND r.is_fade = 0
          AND NOT EXISTS (SELECT 1 FROM bets b WHERE b.recommendation_id = r.id)
    """, (game_id,)).fetchall()
    return [dict(r) for r in rows]


# ── Settings page ─────────────────────────────────────────────────────────────

def last_update_times(conn, sport: str = 'nfl') -> dict:
    game_upd = conn.execute(
        'SELECT MAX(updated_utc) FROM games WHERE sport = ?', (sport,)
    ).fetchone()[0]
    snap_upd = conn.execute(
        'SELECT MAX(snapshot_time_utc) FROM odds_snapshots WHERE sport = ?', (sport,)
    ).fetchone()[0]
    last_pull = conn.execute("""
        SELECT pull_time_utc FROM pulls WHERE sport = ? AND success = 1 ORDER BY id DESC LIMIT 1
    """, (sport,)).fetchone()
    return {
        'games_last_updated': game_upd,
        'last_snapshot':      snap_upd,
        'last_pull_utc':      last_pull[0] if last_pull else None,
    }


def data_status(conn, sport: str = 'nfl') -> dict:
    def count(tbl, where=''):
        q = f'SELECT COUNT(*) FROM {tbl}' + (f' WHERE {where}' if where else '')
        return conn.execute(q).fetchone()[0]

    import os
    import glob
    from pathlib import Path
    logs_dir = Path(__file__).resolve().parents[2] / 'logs' / 'scheduled'
    last_runs = {}
    for slot in ('tuesday_research', 'thursday_lock', 'sunday_lock', 'monday_lock', 'tuesday_grade'):
        pattern = str(logs_dir / f'*_{slot}.log')
        files = sorted(glob.glob(pattern))
        last_runs[slot] = os.path.basename(files[-1])[:10] if files else None

    season, week = current_season_week(conn, sport)

    return {
        'season':          season,
        'week':            week,
        'total_games':     count('games', f"sport='{sport}'"),
        'total_snapshots': count('odds_snapshots', f"sport='{sport}'"),
        'total_recs':      count('recommendations', f"sport='{sport}' AND is_fade=0"),
        'total_fades':     count('recommendations', f"sport='{sport}' AND is_fade=1"),
        'pending_recs':    count('recommendations', f"sport='{sport}' AND is_fade=0 AND result IS NULL"),
        'graded_recs':     count('recommendations', f"sport='{sport}' AND is_fade=0 AND result IS NOT NULL"),
        'total_bets':      count('bets', f"sport='{sport}'"),
        'pending_bets':    count('bets', f"sport='{sport}' AND result IS NULL"),
        'graded_bets':     count('bets', f"sport='{sport}' AND result IS NOT NULL"),
        'last_slot_runs':  last_runs,
    }
