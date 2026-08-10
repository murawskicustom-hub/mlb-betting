"""
clv_calculator.py — fills in closing_price_american and clv_percent for
recommendations and bets once a game has reached kickoff.

Call compute_clv(conn) with an open database connection.
Safe to run repeatedly: only touches rows where closing_price_american IS NULL.
Fade rows (is_fade=1) are skipped — they have no side/price to compare.
"""

from logger import get_logger

log = get_logger('clv')

# ESPN status names that mean the game has reached or passed kickoff.
STARTED_STATUSES = (
    'STATUS_IN_PROGRESS', 'STATUS_HALFTIME', 'STATUS_END_PERIOD',
    'STATUS_FINAL', 'STATUS_FINAL_OVERTIME',
    'STATUS_POSTPONED', 'STATUS_CANCELED', 'STATUS_SUSPENDED',
)

# Closing-line book priority order
BOOK_PRIORITY = ('draftkings', 'fanduel', 'betmgm')


def american_to_decimal(american: int) -> float:
    if american >= 0:
        return (american / 100) + 1
    else:
        return (100 / abs(american)) + 1


def find_closing_snapshot(conn, game_id: str, market: str, outcome_type: str,
                          start_utc: str) -> tuple[int | None, str | None]:
    """
    Find the most-recent odds snapshot for this (game, market, outcome_type)
    at or before kickoff — the last odds captured before the game started,
    regardless of how far in advance the pull ran. Priority: DraftKings ->
    FanDuel -> BetMGM -> any book. Returns (price_american, book_key) or
    (None, None) if no snapshot found.
    """
    window_end = start_utc

    for book in list(BOOK_PRIORITY) + [None]:
        if book is not None:
            row = conn.execute("""
                SELECT price_american, book
                FROM odds_snapshots
                WHERE game_id       = ?
                  AND market        = ?
                  AND outcome_type  = ?
                  AND book          = ?
                  AND snapshot_time_utc <= ?
                ORDER BY snapshot_time_utc DESC
                LIMIT 1
            """, (game_id, market, outcome_type, book, window_end)).fetchone()
        else:
            row = conn.execute("""
                SELECT price_american, book
                FROM odds_snapshots
                WHERE game_id       = ?
                  AND market        = ?
                  AND outcome_type  = ?
                  AND snapshot_time_utc <= ?
                ORDER BY snapshot_time_utc DESC
                LIMIT 1
            """, (game_id, market, outcome_type, window_end)).fetchone()

        if row:
            return row['price_american'], row['book']

    return None, None


def compute_clv_pct(our_price: int, closing_price: int) -> float:
    """
    CLV% = ((our_decimal / closing_decimal) - 1) x 100
    Positive = we beat the close (good). Negative = close was better than us.
    """
    our_dec     = american_to_decimal(our_price)
    closing_dec = american_to_decimal(closing_price)
    return round(((our_dec / closing_dec) - 1) * 100, 4)


def compute_clv(conn) -> dict:
    """
    Fill closing_price_american and clv_percent for all eligible rows.
    Returns a summary dict.
    """
    rec_filled  = 0
    rec_skipped = 0
    bet_filled  = 0
    bet_skipped = 0

    # ── Recommendations ───────────────────────────────────────────────────────
    pending_recs = conn.execute(f"""
        SELECT r.id, r.game_id, r.market, r.side, r.target_price_american,
               g.start_utc, g.status
        FROM recommendations r
        JOIN games g ON g.game_id = r.game_id
        WHERE r.closing_price_american IS NULL
          AND r.is_fade = 0
          AND g.status IN ({','.join('?'*len(STARTED_STATUSES))})
    """, STARTED_STATUSES).fetchall()

    log.info(f'CLV: {len(pending_recs)} recommendation(s) need closing line')

    for row in pending_recs:
        if row['target_price_american'] is None:
            log.debug(f'rec id={row["id"]}: no target_price_american — cannot compute CLV, skipped')
            rec_skipped += 1
            continue

        closing_price, closing_book = find_closing_snapshot(
            conn, row['game_id'], row['market'], row['side'], row['start_utc']
        )

        if closing_price is None:
            log.debug(
                f'rec id={row["id"]} game_id={row["game_id"]} {row["market"]}/{row["side"]}: '
                f'no closing snapshot found before kickoff'
            )
            rec_skipped += 1
            continue

        clv = compute_clv_pct(row['target_price_american'], closing_price)
        conn.execute("""
            UPDATE recommendations
            SET closing_price_american = ?, clv_percent = ?
            WHERE id = ?
        """, (closing_price, clv, row['id']))

        log.info(
            f'rec id={row["id"]} game_id={row["game_id"]} {row["market"]}/{row["side"]}: '
            f'closing={closing_price} (via {closing_book}), '
            f'target={row["target_price_american"]}, CLV={clv:+.2f}%'
        )
        rec_filled += 1

    # ── Bets ───────────────────────────────────────────────────────────────────
    pending_bets = conn.execute(f"""
        SELECT b.id, b.game_id, b.market, b.side, b.price_american,
               g.start_utc, g.status
        FROM bets b
        JOIN games g ON g.game_id = b.game_id
        WHERE b.closing_price_american IS NULL
          AND g.status IN ({','.join('?'*len(STARTED_STATUSES))})
    """, STARTED_STATUSES).fetchall()

    log.info(f'CLV: {len(pending_bets)} bet(s) need closing line')

    for row in pending_bets:
        if row['price_american'] is None:
            log.debug(f'bet id={row["id"]}: no price_american — cannot compute CLV, skipped')
            bet_skipped += 1
            continue

        closing_price, closing_book = find_closing_snapshot(
            conn, row['game_id'], row['market'], row['side'], row['start_utc']
        )

        if closing_price is None:
            log.debug(
                f'bet id={row["id"]} game_id={row["game_id"]} {row["market"]}/{row["side"]}: '
                f'no closing snapshot in window'
            )
            bet_skipped += 1
            continue

        clv = compute_clv_pct(row['price_american'], closing_price)
        conn.execute("""
            UPDATE bets
            SET closing_price_american = ?, clv_percent = ?
            WHERE id = ?
        """, (closing_price, clv, row['id']))

        log.info(
            f'bet id={row["id"]} game_id={row["game_id"]} {row["market"]}/{row["side"]}: '
            f'closing={closing_price} (via {closing_book}), '
            f'price={row["price_american"]}, CLV={clv:+.2f}%'
        )
        bet_filled += 1

    log.info(
        f'CLV done: recs filled={rec_filled} skipped={rec_skipped}, '
        f'bets filled={bet_filled} skipped={bet_skipped}'
    )

    return {
        'rec_filled':  rec_filled,
        'rec_skipped': rec_skipped,
        'bet_filled':  bet_filled,
        'bet_skipped': bet_skipped,
    }
