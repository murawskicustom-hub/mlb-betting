"""
analyzer.py — turns per-book edges into recommendations written to the DB.

Entry points:
    generate_recommendations_for_game(conn, game_pk) -> list[int]
    generate_all_recommendations(conn) -> dict
"""

from datetime import datetime, timezone
from logger import get_logger
from devig import edge_percent, american_to_decimal
from consensus import find_edges_for_game

log = get_logger('analyzer')

REFERENCE_BANKROLL = 2500.0
MARKETS_TO_ANALYZE = ('moneyline', 'total')   # spreads and F5 excluded in v1

# Dedup threshold: only write a new rec if edge moved by more than this
EDGE_MOVE_THRESHOLD = 0.5   # percentage points


# ── Classification helpers ────────────────────────────────────────────────────

def classify_color(edge_pct: float) -> tuple[str, int]:
    """
    Returns (confidence_color, is_shadow).
    'none' means don't write a recommendation at all.

    Thresholds:
        >= 4.0%  → green  (real bet)
        >= 2.0%  → yellow (real bet, smaller stake)
        >= 0.5%  → red    (shadow only — some signal, too thin to bet)
        <  0.5%  → none   (noise, skip entirely)
    """
    if edge_pct >= 4.0:
        return ('green', 0)
    elif edge_pct >= 2.0:
        return ('yellow', 0)
    elif edge_pct >= 0.5:
        return ('red', 1)
    else:
        return ('none', 1)


def recommended_stake_pct(color: str) -> float:
    return {'green': 0.02, 'yellow': 0.01, 'red': 0.0}.get(color, 0.0)


# ── Per-game recommendation writer ───────────────────────────────────────────

def _best_price_for_side(edges: list[dict], side: str) -> dict | None:
    """
    Among all (book, side) edges, return the row with the best offered decimal
    price for that side (highest decimal = most favorable to bettor).
    """
    candidates = [e for e in edges if e['side'] == side]
    if not candidates:
        return None
    return max(candidates, key=lambda e: american_to_decimal(e['offered_price_american']))


def _existing_ungraded_rec(conn, game_pk: int, market: str,
                            side: str, line) -> dict | None:
    """Return the most-recently generated ungraded rec for this combination, or None."""
    row = conn.execute("""
        SELECT id, edge_percent
        FROM recommendations
        WHERE game_pk = ? AND market = ? AND side = ?
          AND (line IS NULL AND ? IS NULL OR line = ?)
          AND result IS NULL
        ORDER BY generated_at_utc DESC
        LIMIT 1
    """, (game_pk, market, side, line, line)).fetchone()
    return dict(row) if row else None


def generate_recommendations_for_game(conn, game_pk: int) -> list[int]:
    """
    Analyze one game's odds. Write recommendation rows for any +EV sides found.
    Returns list of inserted rec IDs.
    """
    inserted_ids = []
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    for market in MARKETS_TO_ANALYZE:
        edges = find_edges_for_game(conn, game_pk, market)
        if not edges:
            continue

        # Determine which sides exist in this market
        sides = list({e['side'] for e in edges})

        for side in sides:
            best = _best_price_for_side(edges, side)
            if best is None:
                continue

            # Recompute edge at the BEST available price vs consensus fair
            best_ep = edge_percent(
                best['offered_price_american'],
                best['fair_price_american']
            )

            color, is_shadow = classify_color(best_ep)
            if color == 'none':
                continue   # strictly negative EV, don't log

            # Dedup check
            existing = _existing_ungraded_rec(conn, game_pk, market, side,
                                              best.get('line'))
            if existing is not None:
                if abs(best_ep - existing['edge_percent']) <= EDGE_MOVE_THRESHOLD:
                    log.debug(
                        f'game_pk={game_pk} {market}/{side}: '
                        f'edge unchanged ({best_ep:.2f}% vs {existing["edge_percent"]:.2f}%), '
                        f'skipping duplicate'
                    )
                    continue
                else:
                    log.info(
                        f'game_pk={game_pk} {market}/{side}: '
                        f'edge moved {existing["edge_percent"]:.2f}% → {best_ep:.2f}%, '
                        f'writing updated recommendation'
                    )

            stake_pct    = recommended_stake_pct(color)
            stake_dollars = round(stake_pct * REFERENCE_BANKROLL, 2)

            conn.execute("""
                INSERT INTO recommendations (
                    game_pk, generated_at_utc, market, side, line,
                    target_price_american, fair_price_american,
                    edge_percent, confidence_color,
                    recommended_stake_pct, recommended_stake_dollars_at_2500,
                    is_shadow, num_books_in_consensus
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                game_pk, now_utc, market, side, best.get('line'),
                best['offered_price_american'], best['fair_price_american'],
                round(best_ep, 4), color,
                stake_pct, stake_dollars,
                is_shadow, best['num_books_in_consensus'],
            ))

            rec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            inserted_ids.append(rec_id)

            log.info(
                f'Rec #{rec_id}: game_pk={game_pk} {market}/{side}'
                + (f' line={best["line"]}' if best.get('line') else '')
                + f' | best={best["offered_price_american"]:+d} '
                f'fair={best["fair_price_american"]:+d} '
                f'edge={best_ep:.2f}% [{color.upper()}]'
            )

    return inserted_ids


# ── Full-slate runner ─────────────────────────────────────────────────────────

def generate_all_recommendations(conn) -> dict:
    """
    Analyze every upcoming (not yet started) game that has odds in the DB.
    Returns a summary dict with counts by market and color.
    """
    upcoming_games = conn.execute("""
        SELECT DISTINCT g.game_pk
        FROM games g
        JOIN odds_snapshots o ON o.game_pk = g.game_pk
        WHERE g.status IN ('Scheduled', 'Pre-Game', 'Warmup')
        ORDER BY g.game_datetime_utc
    """).fetchall()

    game_pks = [r['game_pk'] for r in upcoming_games]
    log.info(f'Analyzer: {len(game_pks)} upcoming game(s) with odds to analyze')

    total_written = 0
    color_market_counts: dict[str, int] = {}
    games_no_rec = []

    for game_pk in game_pks:
        ids = generate_recommendations_for_game(conn, game_pk)
        if ids:
            total_written += len(ids)
            # Tally by color+market from what we just inserted
            recs = conn.execute("""
                SELECT market, confidence_color FROM recommendations
                WHERE id IN ({})
            """.format(','.join('?' * len(ids))), ids).fetchall()
            for r in recs:
                key = f'{r["confidence_color"]}_{r["market"]}'
                color_market_counts[key] = color_market_counts.get(key, 0) + 1
        else:
            games_no_rec.append(game_pk)

    log.info(
        f'Analyzer done: {len(game_pks)} games analyzed, '
        f'{total_written} recommendations written, '
        f'{len(games_no_rec)} games produced no recommendations'
    )

    return {
        'games_analyzed':    len(game_pks),
        'total_written':     total_written,
        'by_color_market':   color_market_counts,
        'games_no_rec':      games_no_rec,
    }
