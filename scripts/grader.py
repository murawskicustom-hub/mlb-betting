"""
grader.py — grades pending recommendations and personal bets.

Call grade_pending(conn) with an open database connection.
Safe to run repeatedly: only touches rows where result IS NULL.
"""

from datetime import datetime, timezone
from logger import get_logger

log = get_logger('grader')

# Statuses that mean the game was cancelled before it became official
VOID_STATUSES   = {'Postponed', 'Cancelled', 'Suspended'}
GRADED_STATUSES = {'Final', 'Completed Early'}
F5_MARKETS      = {'f5_moneyline', 'f5_total'}


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
    # win
    p = price_american
    if p >= 0:
        return round((p / 100) + 1, 6)
    else:
        return round((100 / abs(p)) + 1, 6)


# ── Grading logic ─────────────────────────────────────────────────────────────

def grade_moneyline(side: str, home_score: int, away_score: int) -> str:
    if side == 'home':
        return 'win' if home_score > away_score else 'loss'
    else:
        return 'win' if away_score > home_score else 'loss'
    # MLB has no ties (extra innings), so push is not possible here


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
    Returns result string, or None if the row should be skipped (F5, or
    game not yet in a gradeable state).
    """
    if game_status in VOID_STATUSES:
        return 'void'

    if game_status not in GRADED_STATUSES:
        return None   # game not finished

    if market in F5_MARKETS:
        return None   # F5: caller logs warning and skips

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
    Grade all ungraded recommendations and personal bets whose game is finished.
    Returns a summary dict.
    """
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    rec_counts  = {'win': 0, 'loss': 0, 'push': 0, 'void': 0}
    bet_count   = 0
    f5_skipped  = 0
    rec_graded  = 0

    # ── Grade recommendations ─────────────────────────────────────────────────
    pending_recs = conn.execute("""
        SELECT r.id, r.game_pk, r.market, r.side, r.line, r.target_price_american,
               g.status, g.home_score, g.away_score
        FROM recommendations r
        JOIN games g ON g.game_pk = r.game_pk
        WHERE r.result IS NULL
    """).fetchall()

    log.info(f'Grader: {len(pending_recs)} ungraded recommendation(s) found')

    for row in pending_recs:
        market = row['market']
        status = row['status']

        if market in F5_MARKETS:
            log.warning(
                f'rec id={row["id"]} game_pk={row["game_pk"]}: F5 market — '
                f'cannot grade without inning-by-inning data, skipping'
            )
            f5_skipped += 1
            continue

        if status not in GRADED_STATUSES | VOID_STATUSES:
            continue   # game not finished

        result = compute_result(
            market, row['side'], row['line'],
            row['home_score'], row['away_score'], status
        )
        if result is None:
            continue

        payout = payout_at_stake_1(result, row['target_price_american'])

        conn.execute("""
            UPDATE recommendations
            SET result = ?, result_payout_at_stake_1 = ?, graded_at_utc = ?
            WHERE id = ?
        """, (result, payout, now_utc, row['id']))

        rec_counts[result] = rec_counts.get(result, 0) + 1
        rec_graded += 1

    # ── Grade personal bets ───────────────────────────────────────────────────
    pending_bets = conn.execute("""
        SELECT b.id, b.game_pk, b.market, b.side, b.line, b.actual_price_american,
               b.stake_dollars,
               g.status, g.home_score, g.away_score
        FROM personal_bets b
        JOIN games g ON g.game_pk = b.game_pk
        WHERE b.result IS NULL
    """).fetchall()

    log.info(f'Grader: {len(pending_bets)} ungraded personal bet(s) found')

    for row in pending_bets:
        market = row['market']
        status = row['status']

        if market in F5_MARKETS:
            log.warning(
                f'bet id={row["id"]} game_pk={row["game_pk"]}: F5 market — skipping'
            )
            f5_skipped += 1
            continue

        if status not in GRADED_STATUSES | VOID_STATUSES:
            continue

        result = compute_result(
            market, row['side'], row['line'],
            row['home_score'], row['away_score'], status
        )
        if result is None:
            continue

        payout_per_1 = payout_at_stake_1(result, row['actual_price_american'])
        stake        = row['stake_dollars']
        payout       = round(stake * payout_per_1, 2)
        pl           = round(payout - stake, 2)

        conn.execute("""
            UPDATE personal_bets
            SET result = ?, payout_dollars = ?, profit_loss_dollars = ?, graded_at_utc = ?
            WHERE id = ?
        """, (result, payout, pl, now_utc, row['id']))

        bet_count += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info(
        f'Grader done: recommendations graded={rec_graded} '
        f'(W={rec_counts["win"]} L={rec_counts["loss"]} '
        f'P={rec_counts.get("push",0)} V={rec_counts.get("void",0)}), '
        f'personal bets graded={bet_count}, F5 skipped={f5_skipped}'
    )

    return {
        'rec_graded':     rec_graded,
        'rec_counts':     rec_counts,
        'bets_graded':    bet_count,
        'f5_skipped':     f5_skipped,
    }
