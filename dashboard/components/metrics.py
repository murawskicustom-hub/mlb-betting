"""
metrics.py — pure database query functions, no Streamlit imports.
Each function takes an open sqlite3 connection and returns plain Python values.
"""

from datetime import datetime, timedelta
import pytz

EASTERN = pytz.timezone('US/Eastern')


def _today_et() -> str:
    return datetime.now(EASTERN).strftime('%Y-%m-%d')


def _days_ago_et(n: int) -> str:
    return (datetime.now(EASTERN) - timedelta(days=n)).strftime('%Y-%m-%d')


# ── Home page metrics ─────────────────────────────────────────────────────────

def todays_rec_counts(conn) -> dict:
    """Count bettable Algo 1 (devig, is_shadow=0) recs generated today (Eastern)."""
    today = _today_et()
    rows = conn.execute("""
        SELECT confidence_color, COUNT(*) as n
        FROM recommendations
        WHERE substr(generated_at_utc, 1, 10) = ?
          AND confidence_color IN ('green', 'yellow', 'red')
          AND algo = 'devig'
          AND is_shadow = 0
        GROUP BY confidence_color
    """, (today,)).fetchall()
    counts = {r[0]: r[1] for r in rows}
    return {
        'green':  counts.get('green',  0),
        'yellow': counts.get('yellow', 0),
        'red':    counts.get('red',    0),
    }


def todays_model_count(conn) -> int:
    """Count of Algo 2 (model_v1) shadow picks generated today (Eastern)."""
    today = _today_et()
    row = conn.execute("""
        SELECT COUNT(*) FROM recommendations
        WHERE substr(generated_at_utc, 1, 10) = ?
          AND algo = 'model_v1'
          AND result IS NULL
    """, (today,)).fetchone()
    return row[0] if row else 0


def avg_clv(conn, days: int) -> float | None:
    """Average CLV% for graded green/yellow recs in the last N days. All algos."""
    since = _days_ago_et(days)
    row = conn.execute("""
        SELECT AVG(r.clv_percent)
        FROM recommendations r
        WHERE r.confidence_color IN ('green', 'yellow')
          AND r.clv_percent IS NOT NULL
          AND r.graded_at_utc IS NOT NULL
          AND substr(r.graded_at_utc, 1, 10) >= ?
    """, (since,)).fetchone()
    return row[0]


def avg_clv_by_algo(conn, days: int, algo: str) -> float | None:
    """Average CLV% for graded green/yellow recs of a specific algo in the last N days."""
    since = _days_ago_et(days)
    row = conn.execute("""
        SELECT AVG(r.clv_percent)
        FROM recommendations r
        WHERE r.confidence_color IN ('green', 'yellow')
          AND r.clv_percent IS NOT NULL
          AND r.graded_at_utc IS NOT NULL
          AND r.algo = ?
          AND substr(r.graded_at_utc, 1, 10) >= ?
    """, (algo, since)).fetchone()
    return row[0]


def personal_pl(conn, days: int | None = None) -> float:
    """Sum of profit_loss_dollars for graded personal bets."""
    if days is None:
        row = conn.execute("""
            SELECT COALESCE(SUM(profit_loss_dollars), 0)
            FROM personal_bets WHERE result IS NOT NULL
        """).fetchone()
    else:
        since = _days_ago_et(days)
        row = conn.execute("""
            SELECT COALESCE(SUM(profit_loss_dollars), 0)
            FROM personal_bets
            WHERE result IS NOT NULL
              AND substr(graded_at_utc, 1, 10) >= ?
        """, (since,)).fetchone()
    return row[0] or 0.0


def model_pl_dollars(conn) -> dict:
    """
    Cumulative paper P/L for model_v1 recs at $25/pick.
    Returns {'total_pl': float, 'graded': int, 'wins': int, 'losses': int, 'pushes': int}.
    """
    rows = conn.execute("""
        SELECT result,
               SUM((result_payout_at_stake_1 - 1) * recommended_stake_dollars_at_2500) as pl,
               COUNT(*) as n
        FROM recommendations
        WHERE algo = 'model_v1'
          AND result IS NOT NULL
          AND result_payout_at_stake_1 IS NOT NULL
        GROUP BY result
    """).fetchall()
    total_pl = 0.0
    graded = wins = losses = pushes = 0
    for r in rows:
        pl = r['pl'] or 0.0
        total_pl += pl
        graded += r['n']
        if r['result'] == 'win':
            wins = r['n']
        elif r['result'] == 'loss':
            losses = r['n']
        elif r['result'] in ('push', 'void'):
            pushes += r['n']
    return {
        'total_pl': round(total_pl, 2),
        'graded':   graded,
        'wins':     wins,
        'losses':   losses,
        'pushes':   pushes,
    }


def last_update_times(conn) -> dict:
    """Last game update, last snapshot, and requests remaining."""
    game_upd = conn.execute(
        "SELECT MAX(last_updated_utc) FROM games"
    ).fetchone()[0]
    snap_upd = conn.execute(
        "SELECT MAX(snapshot_time_utc) FROM odds_snapshots"
    ).fetchone()[0]
    req_rem = conn.execute(
        "SELECT requests_remaining FROM odds_pulls WHERE success=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return {
        'games_last_updated':    game_upd,
        'last_snapshot':         snap_upd,
        'requests_remaining':    req_rem[0] if req_rem else None,
    }


def clv_trend(conn, days: int = 30) -> list[dict]:
    """
    Daily average CLV for Algo 1 (devig) green/yellow graded recs, last N days.
    Returns list of {date, avg_clv, cumulative_avg_clv}.
    """
    since = _days_ago_et(days)
    rows = conn.execute("""
        SELECT substr(graded_at_utc, 1, 10) as date,
               AVG(clv_percent) as avg_clv,
               COUNT(*) as n
        FROM recommendations
        WHERE confidence_color IN ('green', 'yellow')
          AND algo = 'devig'
          AND clv_percent IS NOT NULL
          AND graded_at_utc IS NOT NULL
          AND substr(graded_at_utc, 1, 10) >= ?
        GROUP BY date
        ORDER BY date
    """, (since,)).fetchall()

    results = []
    cum_total = 0.0
    cum_n = 0
    for r in rows:
        cum_total += r['avg_clv'] * r['n']
        cum_n += r['n']
        results.append({
            'date':           r['date'],
            'avg_clv':        r['avg_clv'],
            'cumulative_avg': cum_total / cum_n if cum_n else 0,
            'n':              r['n'],
        })
    return results


def rec_distribution(conn, days: int = 30) -> list[dict]:
    """Graded Algo 1 (devig) recs last N days grouped by (color, result)."""
    since = _days_ago_et(days)
    rows = conn.execute("""
        SELECT confidence_color, result, COUNT(*) as n
        FROM recommendations
        WHERE graded_at_utc IS NOT NULL
          AND result IS NOT NULL
          AND algo = 'devig'
          AND substr(graded_at_utc, 1, 10) >= ?
        GROUP BY confidence_color, result
    """, (since,)).fetchall()
    return [dict(r) for r in rows]


def daily_activity(conn, days: int = 7) -> list[dict]:
    """One row per day for the last N days."""
    out = []
    for i in range(days - 1, -1, -1):
        date = (datetime.now(EASTERN) - timedelta(days=i)).strftime('%Y-%m-%d')
        games = conn.execute(
            "SELECT COUNT(*) FROM games WHERE game_date = ?", (date,)
        ).fetchone()[0]
        recs = conn.execute("""
            SELECT confidence_color, COUNT(*) FROM recommendations
            WHERE substr(generated_at_utc,1,10) = ?
              AND algo = 'devig'
              AND is_shadow = 0
            GROUP BY confidence_color
        """, (date,)).fetchall()
        rec_map = {r[0]: r[1] for r in recs}
        bets = conn.execute(
            "SELECT COUNT(*) FROM personal_bets WHERE substr(placed_at_utc,1,10) = ?",
            (date,)
        ).fetchone()[0]
        pl = conn.execute("""
            SELECT COALESCE(SUM(profit_loss_dollars), 0)
            FROM personal_bets
            WHERE result IS NOT NULL AND substr(graded_at_utc,1,10) = ?
        """, (date,)).fetchone()[0]
        out.append({
            'date':   date,
            'games':  games,
            'green':  rec_map.get('green', 0),
            'yellow': rec_map.get('yellow', 0),
            'red':    rec_map.get('red', 0),
            'bets':   bets,
            'pl':     pl or 0.0,
        })
    return out


# ── Today page ────────────────────────────────────────────────────────────────

def recs_for_date(conn, date_str: str) -> list[dict]:
    """All recommendations for a given Eastern date, with game + pitcher info."""
    rows = conn.execute("""
        SELECT r.id, r.game_pk, r.market, r.side, r.line,
               r.target_price_american, r.fair_price_american,
               r.edge_percent, r.confidence_color, r.is_shadow,
               r.recommended_stake_dollars_at_2500,
               r.num_books_in_consensus,
               r.algo,
               r.model_probability,
               r.model_notes,
               g.away_team, g.home_team, g.game_datetime_utc, g.game_date,
               away_p.pitcher_name AS away_pitcher,
               home_p.pitcher_name AS home_pitcher
        FROM recommendations r
        JOIN games g ON g.game_pk = r.game_pk
        LEFT JOIN probable_pitchers away_p
               ON away_p.game_pk = r.game_pk AND away_p.team_side = 'away'
        LEFT JOIN probable_pitchers home_p
               ON home_p.game_pk = r.game_pk AND home_p.team_side = 'home'
        WHERE g.game_date = ?
          AND r.result IS NULL
        ORDER BY r.edge_percent DESC
    """, (date_str,)).fetchall()
    return [dict(r) for r in rows]


def games_for_date(conn, date_str: str) -> list[dict]:
    """All games for a given Eastern date with pitcher info."""
    rows = conn.execute("""
        SELECT g.game_pk, g.away_team, g.home_team, g.game_datetime_utc,
               g.status,
               away_p.pitcher_name AS away_pitcher,
               home_p.pitcher_name AS home_pitcher
        FROM games g
        LEFT JOIN probable_pitchers away_p
               ON away_p.game_pk = g.game_pk AND away_p.team_side = 'away'
        LEFT JOIN probable_pitchers home_p
               ON home_p.game_pk = g.game_pk AND home_p.team_side = 'home'
        WHERE g.game_date = ?
        ORDER BY g.game_datetime_utc
    """, (date_str,)).fetchall()
    return [dict(r) for r in rows]


# ── Performance page ──────────────────────────────────────────────────────────

def clv_by_group(conn, group_col: str) -> list[dict]:
    """AVG CLV grouped by a column (confidence_color, market, etc.)."""
    rows = conn.execute(f"""
        SELECT {group_col} as group_key,
               AVG(clv_percent) as avg_clv,
               COUNT(*) as n
        FROM recommendations
        WHERE clv_percent IS NOT NULL
          AND confidence_color IN ('green', 'yellow')
        GROUP BY {group_col}
        ORDER BY avg_clv DESC
    """).fetchall()
    return [dict(r) for r in rows]


def win_rate_table(conn, group_col: str) -> list[dict]:
    """Win rate, ROI, avg edge, avg CLV grouped by a column."""
    rows = conn.execute(f"""
        SELECT {group_col} as group_key,
               COUNT(*) as total,
               SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN result='push' THEN 1 ELSE 0 END) as pushes,
               SUM(CASE WHEN result='void' THEN 1 ELSE 0 END) as voids,
               AVG(edge_percent) as avg_edge,
               AVG(clv_percent)  as avg_clv,
               AVG(result_payout_at_stake_1 - 1) as avg_roi
        FROM recommendations
        WHERE result IS NOT NULL
          AND confidence_color IN ('green', 'yellow')
        GROUP BY {group_col}
    """).fetchall()
    return [dict(r) for r in rows]


def calibration_data(conn, algo: str = 'devig') -> list[dict]:
    """
    Compare system implied win probability vs actual win rate per color.
    For algo='model_v1', uses model_probability as the predicted probability.
    For algo='devig', uses the implied probability from target_price_american.
    """
    if algo == 'model_v1':
        rows = conn.execute("""
            SELECT confidence_color,
                   AVG(model_probability) as avg_implied_prob,
                   CAST(SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) AS REAL) /
                        NULLIF(SUM(CASE WHEN result IN ('win','loss') THEN 1 ELSE 0 END), 0)
                     as actual_win_rate,
                   COUNT(*) as n
            FROM recommendations
            WHERE result IS NOT NULL
              AND result IN ('win', 'loss', 'push', 'void')
              AND confidence_color IN ('green', 'yellow', 'red')
              AND algo = 'model_v1'
              AND model_probability IS NOT NULL
            GROUP BY confidence_color
        """).fetchall()
    else:
        rows = conn.execute("""
            SELECT confidence_color,
                   AVG(
                     CASE WHEN target_price_american < 0
                       THEN CAST(ABS(target_price_american) AS REAL) /
                            (ABS(target_price_american) + 100.0)
                       ELSE 100.0 / (target_price_american + 100.0)
                     END
                   ) as avg_implied_prob,
                   CAST(SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) AS REAL) /
                        NULLIF(SUM(CASE WHEN result IN ('win','loss') THEN 1 ELSE 0 END), 0)
                     as actual_win_rate,
                   COUNT(*) as n
            FROM recommendations
            WHERE result IS NOT NULL
              AND result IN ('win', 'loss', 'push', 'void')
              AND confidence_color IN ('green', 'yellow', 'red')
              AND algo = 'devig'
            GROUP BY confidence_color
        """).fetchall()
    return [dict(r) for r in rows]


def clv_by_group_and_algo(conn, group_col: str, algo: str = 'devig') -> list[dict]:
    """AVG CLV grouped by a column for a specific algo."""
    rows = conn.execute(f"""
        SELECT {group_col} as group_key,
               AVG(clv_percent) as avg_clv,
               COUNT(*) as n
        FROM recommendations
        WHERE clv_percent IS NOT NULL
          AND confidence_color IN ('green', 'yellow')
          AND algo = ?
        GROUP BY {group_col}
        ORDER BY avg_clv DESC
    """, (algo,)).fetchall()
    return [dict(r) for r in rows]


def win_rate_table_by_algo(conn, group_col: str, algo: str = 'devig') -> list[dict]:
    """Win rate, ROI, avg edge, avg CLV grouped by a column, filtered by algo."""
    rows = conn.execute(f"""
        SELECT {group_col} as group_key,
               COUNT(*) as total,
               SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN result='push' THEN 1 ELSE 0 END) as pushes,
               SUM(CASE WHEN result='void' THEN 1 ELSE 0 END) as voids,
               AVG(edge_percent) as avg_edge,
               AVG(clv_percent)  as avg_clv,
               AVG(result_payout_at_stake_1 - 1) as avg_roi
        FROM recommendations
        WHERE result IS NOT NULL
          AND confidence_color IN ('green', 'yellow')
          AND algo = ?
        GROUP BY {group_col}
    """, (algo,)).fetchall()
    return [dict(r) for r in rows]


def unit_summary(conn, algo: str) -> dict:
    """Headline P/L, win rate, avg CLV, ROI for all graded recs of a given algo."""
    row = conn.execute("""
        SELECT COUNT(*) as graded,
               SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN result IN ('push','void') THEN 1 ELSE 0 END) as pushes,
               COALESCE(SUM(unit_profit), 0) as total_units,
               AVG(clv_percent) as avg_clv,
               COALESCE(SUM(
                   CASE WHEN recommended_stake_pct >= 0.02 THEN 2.0
                        WHEN recommended_stake_pct >= 0.01 THEN 1.0
                        ELSE 0.5 END
               ), 0) as total_staked
        FROM recommendations
        WHERE algo = ? AND result IS NOT NULL AND unit_profit IS NOT NULL
    """, (algo,)).fetchone()

    total_units = row['total_units'] or 0.0
    total_staked = row['total_staked'] or 0.0
    roi = round(total_units / total_staked * 100, 1) if total_staked > 0 else None

    return {
        'graded':       row['graded'],
        'wins':         row['wins'] or 0,
        'losses':       row['losses'] or 0,
        'pushes':       row['pushes'] or 0,
        'total_units':  round(total_units, 2),
        'avg_clv':      row['avg_clv'],
        'roi_pct':      roi,
        'total_staked': round(total_staked, 1),
    }


def unit_breakdown(conn, algo: str, group_col: str) -> list[dict]:
    """Breakdown table: units P/L, win rate, avg CLV by group."""
    rows = conn.execute(f"""
        SELECT {group_col} as group_key,
               COUNT(*) as picks,
               SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN result IN ('push','void') THEN 1 ELSE 0 END) as pushes,
               COALESCE(SUM(unit_profit), 0) as unit_profit,
               COALESCE(SUM(
                   CASE WHEN recommended_stake_pct >= 0.02 THEN 2.0
                        WHEN recommended_stake_pct >= 0.01 THEN 1.0
                        ELSE 0.5 END
               ), 0) as units_staked,
               AVG(clv_percent)  as avg_clv,
               AVG(edge_percent) as avg_edge
        FROM recommendations
        WHERE algo = ? AND result IS NOT NULL AND unit_profit IS NOT NULL
        GROUP BY {group_col}
        ORDER BY unit_profit DESC
    """, (algo,)).fetchall()
    return [dict(r) for r in rows]


def unit_trend_by_day(conn, algo: str, days: int = 90) -> list[dict]:
    """
    Cumulative unit P/L per day.
    actual = weighted by stake color; flat = 1u on every pick.
    """
    since = _days_ago_et(days)
    rows = conn.execute("""
        SELECT substr(graded_at_utc, 1, 10) as date,
               SUM(unit_profit) as daily_units,
               SUM(result_payout_at_stake_1 - 1) as daily_flat
        FROM recommendations
        WHERE algo = ?
          AND result IS NOT NULL
          AND unit_profit IS NOT NULL
          AND graded_at_utc IS NOT NULL
          AND substr(graded_at_utc, 1, 10) >= ?
        GROUP BY date ORDER BY date
    """, (algo, since)).fetchall()

    results = []
    cum_units = 0.0
    cum_flat  = 0.0
    for r in rows:
        cum_units += r['daily_units'] or 0
        cum_flat  += r['daily_flat']  or 0
        results.append({
            'date':      r['date'],
            'cum_units': round(cum_units, 3),
            'cum_flat':  round(cum_flat,  3),
        })
    return results


def calibration_by_prob_bucket(conn) -> list[dict]:
    """Algo 2 calibration: model_probability buckets vs actual win rate."""
    rows = conn.execute("""
        SELECT CASE
                   WHEN model_probability >= 0.75 THEN '75%+'
                   WHEN model_probability >= 0.70 THEN '70-75%'
                   WHEN model_probability >= 0.65 THEN '65-70%'
                   WHEN model_probability >= 0.60 THEN '60-65%'
                   ELSE '<60%'
               END as bucket,
               AVG(model_probability) as avg_pred_prob,
               CAST(SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) AS REAL) /
                   NULLIF(SUM(CASE WHEN result IN ('win','loss') THEN 1 ELSE 0 END), 0)
                   as actual_win_rate,
               SUM(CASE WHEN result IN ('win','loss') THEN 1 ELSE 0 END) as n
        FROM recommendations
        WHERE algo = 'model_v1'
          AND result IS NOT NULL
          AND model_probability IS NOT NULL
          AND market NOT IN ('yrfi', 'nrfi')
        GROUP BY bucket
        ORDER BY avg_pred_prob DESC
    """).fetchall()
    return [dict(r) for r in rows]


def yrfi_calibration(conn) -> list[dict]:
    """YRFI/NRFI model calibration: predicted probability vs actual outcome."""
    rows = conn.execute("""
        SELECT CASE
                   WHEN model_probability >= 0.70 THEN '70%+ (YRFI green)'
                   WHEN model_probability >= 0.60 THEN '60-70% (YRFI yellow)'
                   WHEN model_probability <= 0.30 THEN '<=30% (NRFI green)'
                   WHEN model_probability <= 0.40 THEN '31-40% (NRFI yellow)'
                   ELSE NULL
               END as bucket,
               market,
               AVG(model_probability) as avg_pred_prob,
               CAST(SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) AS REAL) /
                   NULLIF(SUM(CASE WHEN result IN ('win','loss') THEN 1 ELSE 0 END), 0)
                   as actual_win_rate,
               SUM(CASE WHEN result IN ('win','loss') THEN 1 ELSE 0 END) as n
        FROM recommendations
        WHERE algo = 'model_v1'
          AND market IN ('yrfi', 'nrfi')
          AND result IS NOT NULL
          AND model_probability IS NOT NULL
        GROUP BY bucket
        HAVING bucket IS NOT NULL
        ORDER BY avg_pred_prob DESC
    """).fetchall()
    return [dict(r) for r in rows]


def head_to_head_recs(conn) -> list[dict]:
    """Games where both algos made a rec on the same market/side, graded."""
    rows = conn.execute("""
        SELECT r1.game_pk, r1.market, r1.side,
               ROUND(r1.edge_percent, 2)  as algo1_edge,
               ROUND(r2.edge_percent, 2)  as algo2_edge,
               r1.result,
               ROUND(r1.clv_percent, 2)   as algo1_clv,
               ROUND(r2.clv_percent, 2)   as algo2_clv,
               g.away_team, g.home_team, g.game_date
        FROM recommendations r1
        JOIN recommendations r2
            ON  r2.game_pk = r1.game_pk
            AND r2.market  = r1.market
            AND r2.side    = r1.side
            AND r2.algo    = 'model_v1'
            AND r2.id      = (SELECT MAX(id) FROM recommendations
                              WHERE game_pk=r1.game_pk AND market=r1.market
                                AND side=r1.side AND algo='model_v1')
        JOIN games g ON g.game_pk = r1.game_pk
        WHERE r1.algo   = 'devig'
          AND r1.result IS NOT NULL
          AND r1.id     = (SELECT MAX(id) FROM recommendations
                           WHERE game_pk=r1.game_pk AND market=r1.market
                             AND side=r1.side AND algo='devig')
        ORDER BY g.game_date DESC
    """).fetchall()
    return [dict(r) for r in rows]


def pick_ledger(conn, algo: str, start_date: str = None, end_date: str = None) -> list[dict]:
    """
    One row per recommendation for the given algo, newest first.
    Joins games for team names and game_date.
    Date filters (YYYY-MM-DD) applied in SQL; color/market/result filtered in the caller.
    """
    params = [algo]
    date_clauses = ''
    if start_date:
        date_clauses += ' AND g.game_date >= ?'
        params.append(start_date)
    if end_date:
        date_clauses += ' AND g.game_date <= ?'
        params.append(end_date)

    rows = conn.execute(f"""
        SELECT r.id,
               g.game_date,
               g.away_team, g.home_team,
               r.market, r.side, r.line,
               r.target_price_american,
               r.fair_price_american,
               r.edge_percent,
               r.confidence_color,
               r.clv_percent,
               r.result,
               r.unit_profit,
               r.model_probability
        FROM recommendations r
        JOIN games g ON g.game_pk = r.game_pk
        WHERE r.algo = ?
          {date_clauses}
        ORDER BY g.game_date DESC, r.id DESC
    """, params).fetchall()
    return [dict(r) for r in rows]


def rec_volume_by_day(conn, days: int = 30) -> list[dict]:
    since = _days_ago_et(days)
    rows = conn.execute("""
        SELECT substr(generated_at_utc,1,10) as date, COUNT(*) as n
        FROM recommendations
        WHERE confidence_color IN ('green','yellow')
          AND substr(generated_at_utc,1,10) >= ?
        GROUP BY date ORDER BY date
    """, (since,)).fetchall()
    return [dict(r) for r in rows]


# ── My Bets page ──────────────────────────────────────────────────────────────

def all_bets(conn) -> list[dict]:
    rows = conn.execute("""
        SELECT b.id, b.placed_at_utc, b.book, b.market, b.side, b.line,
               b.actual_price_american, b.stake_dollars,
               b.closing_price_american, b.clv_percent,
               b.result, b.payout_dollars, b.profit_loss_dollars,
               b.recommendation_id,
               g.away_team, g.home_team, g.game_date, g.game_datetime_utc
        FROM personal_bets b
        JOIN games g ON g.game_pk = b.game_pk
        ORDER BY b.placed_at_utc DESC
    """).fetchall()
    return [dict(r) for r in rows]


def upcoming_games_for_picker(conn) -> list[dict]:
    """Games available for bet entry — upcoming + recent 7 days."""
    rows = conn.execute("""
        SELECT game_pk, away_team, home_team, game_date, game_datetime_utc
        FROM games
        WHERE game_date >= date('now', '-7 days', 'localtime')
        ORDER BY game_datetime_utc
    """).fetchall()
    return [dict(r) for r in rows]


def unbet_recs_for_game(conn, game_pk: int) -> list[dict]:
    """Recommendations for a game that haven't been linked to a personal bet yet."""
    rows = conn.execute("""
        SELECT r.id, r.market, r.side, r.line, r.target_price_american,
               r.confidence_color
        FROM recommendations r
        WHERE r.game_pk = ?
          AND r.is_shadow = 0
          AND NOT EXISTS (
              SELECT 1 FROM personal_bets b WHERE b.recommendation_id = r.id
          )
    """, (game_pk,)).fetchall()
    return [dict(r) for r in rows]


# ── Settings page ─────────────────────────────────────────────────────────────

def data_status(conn) -> dict:
    def count(tbl, where=''):
        q = f"SELECT COUNT(*) FROM {tbl}" + (f" WHERE {where}" if where else "")
        return conn.execute(q).fetchone()[0]

    last_runs = {}
    for slot in ('morning', 'midday', 'pregame', 'closing'):
        import os, glob
        from pathlib import Path
        logs_dir = Path(__file__).resolve().parents[2] / 'logs' / 'scheduled'
        pattern = str(logs_dir / f'*_{slot}.log')
        files = sorted(glob.glob(pattern))
        last_runs[slot] = os.path.basename(files[-1])[:10] if files else None

    return {
        'total_games':          count('games'),
        'total_snapshots':      count('odds_snapshots'),
        'total_recs':           count('recommendations'),
        'pending_recs':         count('recommendations', 'result IS NULL'),
        'graded_recs':          count('recommendations', 'result IS NOT NULL'),
        'total_bets':           count('personal_bets'),
        'pending_bets':         count('personal_bets', 'result IS NULL'),
        'graded_bets':          count('personal_bets', 'result IS NOT NULL'),
        'last_slot_runs':       last_runs,
    }
