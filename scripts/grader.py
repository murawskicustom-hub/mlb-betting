"""
grader.py — grades pending recommendations and bets.

Call grade_pending(conn) with an open database connection.
Safe to run repeatedly: only touches rows where result IS NULL and is_fade=0
(fades are never graded — they never had a side/units to win or lose).

Sport-agnostic: moneyline/total/spread grading works unchanged for any sport
that reports home_score/away_score. NFL games (and results.detail_json) will
carry a status string sourced from ESPN (STATUS_FINAL, STATUS_POSTPONED,
etc.) rather than MLB's Stats API detailedState strings — this module keys
off those ESPN status names.
"""

from datetime import datetime, timezone
from logger import get_logger
from devig import unit_payout

log = get_logger('grader')

VOID_STATUSES = {'STATUS_POSTPONED', 'STATUS_CANCELED', 'STATUS_SUSPENDED'}


def _is_final(status: str) -> bool:
    # ESPN uses STATUS_FINAL for both regulation and overtime finishes.
    return bool(status) and status.startswith('STATUS_FINAL')


# ── Payout helper ──────────────────────────────────────────────────────────────

def payout_at_stake_1(result: str, price_american: int) -> float:
    """
    Returns the total return for a $1 stake (stake included).
    Win  +120 → 2.20   Win -110 → 1.909   Loss → 0.0   Push/Void → 1.0
    """
    if result == 'loss':
        return 0.0
    if result in ('push', 'void'):
        return 1.0
    p = price_american
    if p >= 0:
        return round((p / 100) + 1, 6)
    else:
        return round((100 / abs(p)) + 1, 6)


# ── Grading logic ─────────────────────────────────────────────────────────────

def grade_moneyline(side: str, home_score: int, away_score: int) -> str:
    if home_score == away_score:
        return 'push'   # ties are possible in the NFL regular season
    if side == 'home':
        return 'win' if home_score > away_score else 'loss'
    else:
        return 'win' if away_score > home_score else 'loss'


def grade_total(side: str, line: float, home_score: int, away_score: int) -> str:
    total = home_score + away_score
    if side == 'over':
        if total > line:  return 'win'
        if total < line:  return 'loss'
        return 'push'
    else:  # under
        if total < line:  return 'win'
        if total > line:  return 'loss'
        return 'push'


def grade_spread(side: str, line: float, home_score: int, away_score: int) -> str:
    """
    `line` is the spread for the *picked* side.
    home -1.5: covered if home wins by 2+
    away +1.5: covered if away wins or loses by 1
    General: diff = picked_score + line - opponent_score
             win if diff > 0, push if == 0, loss if < 0
    """
    if side == 'home':
        diff = (home_score + line) - away_score
    else:
        diff = (away_score + line) - home_score

    if diff > 0:   return 'win'
    if diff < 0:   return 'loss'
    return 'push'


def compute_result(market: str, side: str, line, home_score: int, away_score: int,
                   game_status: str) -> str | None:
    """
    Returns result string, or None if the row should be skipped (game not yet
    in a gradeable state, or an unknown market).
    """
    if game_status in VOID_STATUSES:
        return 'void'

    if not _is_final(game_status):
        return None   # game not finished

    if market in ('total', 'spread') and line is None:
        log.warning(f'{market} pick has no line — cannot grade, skipped')
        return None

    if market == 'moneyline':
        return grade_moneyline(side, home_score, away_score)
    elif market == 'total':
        return grade_total(side, line, home_score, away_score)
    elif market == 'spread':
        return grade_spread(side, line, home_score, away_score)
    else:
        log.warning(f'Unknown market "{market}" — cannot grade')
        return None


# ── Main entry point ──────────────────────────────────────────────────────────

def grade_pending(conn) -> dict:
    """
    Grade all ungraded recommendations and bets whose game is finished.
    Fade rows (is_fade=1) are never included — they carry no side/units.
    Returns a summary dict.
    """
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    rec_counts = {'win': 0, 'loss': 0, 'push': 0, 'void': 0}
    bet_count  = 0
    rec_graded = 0

    # ── Grade recommendations ─────────────────────────────────────────────────
    pending_recs = conn.execute("""
        SELECT r.id, r.game_id, r.market, r.side, r.line, r.target_price_american, r.units,
               g.status, g.home_score, g.away_score
        FROM recommendations r
        JOIN games g ON g.game_id = r.game_id
        WHERE r.result IS NULL AND r.is_fade = 0
    """).fetchall()

    log.info(f'Grader: {len(pending_recs)} ungraded recommendation(s) found')

    for row in pending_recs:
        status = row['status']
        if status not in VOID_STATUSES and not _is_final(status):
            continue  # game not finished

        result = compute_result(
            row['market'], row['side'], row['line'],
            row['home_score'], row['away_score'], status
        )
        if result is None:
            continue

        units    = row['units'] or 0.0
        u_profit = unit_payout(result, row['target_price_american'], units)

        conn.execute("""
            UPDATE recommendations
            SET result = ?, unit_profit = ?, graded_at_utc = ?
            WHERE id = ?
        """, (result, u_profit, now_utc, row['id']))

        rec_counts[result] = rec_counts.get(result, 0) + 1
        rec_graded += 1

    # ── Grade bets ─────────────────────────────────────────────────────────────
    pending_bets = conn.execute("""
        SELECT b.id, b.game_id, b.market, b.side, b.line, b.price_american, b.units,
               g.status, g.home_score, g.away_score
        FROM bets b
        JOIN games g ON g.game_id = b.game_id
        WHERE b.result IS NULL
    """).fetchall()

    log.info(f'Grader: {len(pending_bets)} ungraded bet(s) found')

    for row in pending_bets:
        status = row['status']
        if status not in VOID_STATUSES and not _is_final(status):
            continue

        result = compute_result(
            row['market'], row['side'], row['line'],
            row['home_score'], row['away_score'], status
        )
        if result is None:
            continue

        units    = row['units'] or 0.0
        u_profit = unit_payout(result, row['price_american'], units)

        conn.execute("""
            UPDATE bets
            SET result = ?, unit_profit = ?, graded_at_utc = ?
            WHERE id = ?
        """, (result, u_profit, now_utc, row['id']))

        bet_count += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info(
        f'Grader done: recommendations graded={rec_graded} '
        f'(W={rec_counts["win"]} L={rec_counts["loss"]} '
        f'P={rec_counts.get("push",0)} V={rec_counts.get("void",0)}), '
        f'bets graded={bet_count}'
    )

    return {
        'rec_graded':  rec_graded,
        'rec_counts':  rec_counts,
        'bets_graded': bet_count,
    }
