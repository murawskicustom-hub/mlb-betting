"""
clv_calculator.py — fills in closing_price_american and clv_percent for
recommendations and personal bets once a game has reached first pitch.

Call compute_clv(conn) with an open database connection.
Safe to run repeatedly: only touches rows where closing_price_american IS NULL.
"""

from datetime import datetime, timezone, timedelta
from logger import get_logger

log = get_logger('clv')

# Statuses that indicate the game has reached or passed first pitch
STARTED_STATUSES = ('In Progress', 'Final', 'Completed Early',
                    'Postponed', 'Cancelled', 'Suspended')

# Closing-line book priority order
BOOK_PRIORITY = ('draftkings', 'fanduel', 'betmgm')

# How far before first pitch we search for the closing snapshot
CLOSING_WINDOW_MINUTES = 30

F5_MARKETS = {'f5_moneyline', 'f5_total'}


def american_to_decimal(american: int) -> float:
    if american >= 0:
        return (american / 100) + 1
    else:
        return (100 / abs(american)) + 1


def find_closing_snapshot(conn, game_pk: int, market: str, outcome_type: str,
                          game_datetime_utc: str) -> tuple[int | None, str | None]:
    """
    Find the most-recent odds snapshot for this (game, market, outcome_type)
    within the CLOSING_WINDOW_MINUTES before first pitch.

    Priority: DraftKings → FanDuel → BetMGM → any book.
    Returns (price_american, book_key) or (None, None) if no snapshot found.
    """
    # Window: [first_pitch - 30min, first_pitch]
    try:
        fp = datetime.strptime(game_datetime_utc, '%Y-%m-%dT%H:%M:%SZ').replace(
            tzinfo=timezone.utc)
    except ValueError:
        log.warning(f'Could not parse game_datetime_utc="{game_datetime_utc}"')
        return None, None

    window_start = (fp - timedelta(minutes=CLOSING_WINDOW_MINUTES)).strftime(
        '%Y-%m-%dT%H:%M:%SZ')
    window_end = game_datetime_utc  # first pitch itself is the ceiling

    # Try priority books first, then fall back to any book
    books_to_try = list(BOOK_PRIORITY) + [None]   # None = any book

    for book in books_to_try:
        if book is not None:
            row = conn.execute("""
                SELECT price_american, book
                FROM odds_snapshots
                WHERE game_pk       = ?
                  AND market        = ?
                  AND outcome_type  = ?
                  AND book          = ?
                  AND snapshot_time_utc >= ?
                  AND snapshot_time_utc <= ?
                ORDER BY snapshot_time_utc DESC
                LIMIT 1
            """, (game_pk, market, outcome_type, book, window_start, window_end)).fetchone()
        else:
            row = conn.execute("""
                SELECT price_american, book
                FROM odds_snapshots
                WHERE game_pk       = ?
                  AND market        = ?
                  AND outcome_type  = ?
                  AND snapshot_time_utc >= ?
                  AND snapshot_time_utc <= ?
                ORDER BY snapshot_time_utc DESC
                LIMIT 1
            """, (game_pk, market, outcome_type, window_start, window_end)).fetchone()

        if row:
            return row['price_american'], row['book']

    return None, None


def compute_clv_pct(our_price: int, closing_price: int) -> float:
    """
    CLV% = ((our_decimal / closing_decimal) - 1) × 100
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
        SELECT r.id, r.game_pk, r.market, r.side, r.target_price_american,
               g.game_datetime_utc, g.status
        FROM recommendations r
        JOIN games g ON g.game_pk = r.game_pk
        WHERE r.closing_price_american IS NULL
          AND g.status IN ({','.join('?'*len(STARTED_STATUSES))})
    """, STARTED_STATUSES).fetchall()

    log.info(f'CLV: {len(pending_recs)} recommendation(s) need closing line')

    for row in pending_recs:
        if row['market'] in F5_MARKETS:
            log.warning(
                f'rec id={row["id"]}: F5 market — no F5 odds in snapshots, skipping CLV'
            )
            rec_skipped += 1
            continue

        closing_price, closing_book = find_closing_snapshot(
            conn, row['game_pk'], row['market'], row['side'],
            row['game_datetime_utc']
        )

        if closing_price is None:
            log.debug(
                f'rec id={row["id"]} game_pk={row["game_pk"]} {row["market"]}/{row["side"]}: '
                f'no closing snapshot found in {CLOSING_WINDOW_MINUTES}-min window'
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
            f'rec id={row["id"]} game_pk={row["game_pk"]} {row["market"]}/{row["side"]}: '
            f'closing={closing_price} (via {closing_book}), '
            f'target={row["target_price_american"]}, CLV={clv:+.2f}%'
        )
        rec_filled += 1

    # ── Personal bets ─────────────────────────────────────────────────────────
    pending_bets = conn.execute(f"""
        SELECT b.id, b.game_pk, b.market, b.side, b.actual_price_american,
               g.game_datetime_utc, g.status
        FROM personal_bets b
        JOIN games g ON g.game_pk = b.game_pk
        WHERE b.closing_price_american IS NULL
          AND g.status IN ({','.join('?'*len(STARTED_STATUSES))})
    """, STARTED_STATUSES).fetchall()

    log.info(f'CLV: {len(pending_bets)} personal bet(s) need closing line')

    for row in pending_bets:
        if row['market'] in F5_MARKETS:
            log.warning(f'bet id={row["id"]}: F5 market — skipping CLV')
            bet_skipped += 1
            continue

        closing_price, closing_book = find_closing_snapshot(
            conn, row['game_pk'], row['market'], row['side'],
            row['game_datetime_utc']
        )

        if closing_price is None:
            log.debug(
                f'bet id={row["id"]} game_pk={row["game_pk"]} {row["market"]}/{row["side"]}: '
                f'no closing snapshot in window'
            )
            bet_skipped += 1
            continue

        clv = compute_clv_pct(row['actual_price_american'], closing_price)
        conn.execute("""
            UPDATE personal_bets
            SET closing_price_american = ?, clv_percent = ?
            WHERE id = ?
        """, (closing_price, clv, row['id']))

        log.info(
            f'bet id={row["id"]} game_pk={row["game_pk"]} {row["market"]}/{row["side"]}: '
            f'closing={closing_price} (via {closing_book}), '
            f'actual={row["actual_price_american"]}, CLV={clv:+.2f}%'
        )
        bet_filled += 1

    log.info(
        f'CLV done: recs filled={rec_filled} skipped={rec_skipped}, '
        f'bets filled={bet_filled} skipped={bet_skipped}'
    )

    return {
        'rec_filled':   rec_filled,
        'rec_skipped':  rec_skipped,
        'bet_filled':   bet_filled,
        'bet_skipped':  bet_skipped,
    }
