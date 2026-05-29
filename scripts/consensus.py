"""
consensus.py — build vig-free consensus fair lines from multi-book odds snapshots.
"""

from collections import defaultdict
from statistics import median
from logger import get_logger
from devig import american_to_probability, probability_to_american, devig_two_way

log = get_logger('consensus')


# ── Snapshot retrieval ────────────────────────────────────────────────────────

def get_latest_snapshots(conn, game_pk: int, market: str,
                         snapshot_time_utc: str = None) -> list:
    """
    Return the most recent odds_snapshots row per (book, outcome_type) for the
    given game/market.

    If snapshot_time_utc is None: use the absolute latest snapshot available.
    If provided: use the most recent snapshot AT OR BEFORE that timestamp
                 (lets us reproduce historical analyses).
    """
    if snapshot_time_utc is None:
        rows = conn.execute("""
            SELECT book, outcome_type, line, price_american, price_decimal,
                   snapshot_time_utc, api_last_update_utc
            FROM odds_snapshots
            WHERE game_pk = ? AND market = ?
              AND (book, outcome_type, snapshot_time_utc) IN (
                  SELECT book, outcome_type, MAX(snapshot_time_utc)
                  FROM odds_snapshots
                  WHERE game_pk = ? AND market = ?
                  GROUP BY book, outcome_type
              )
        """, (game_pk, market, game_pk, market)).fetchall()
    else:
        rows = conn.execute("""
            SELECT book, outcome_type, line, price_american, price_decimal,
                   snapshot_time_utc, api_last_update_utc
            FROM odds_snapshots
            WHERE game_pk = ? AND market = ?
              AND snapshot_time_utc <= ?
              AND (book, outcome_type, snapshot_time_utc) IN (
                  SELECT book, outcome_type, MAX(snapshot_time_utc)
                  FROM odds_snapshots
                  WHERE game_pk = ? AND market = ? AND snapshot_time_utc <= ?
                  GROUP BY book, outcome_type
              )
        """, (game_pk, market, snapshot_time_utc,
              game_pk, market, snapshot_time_utc)).fetchall()

    return [dict(r) for r in rows]


# ── Consensus computation ─────────────────────────────────────────────────────

def _modal_line(line_counts: dict):
    """Return the most common line value; median of tied winners."""
    max_count = max(line_counts.values())
    candidates = sorted(k for k, v in line_counts.items() if v == max_count)
    mid = len(candidates) // 2
    return candidates[mid]


def compute_consensus_fair_price(snapshots: list, market: str) -> dict | None:
    """
    Given raw snapshots for one (game, market), return consensus fair prices.

    For moneyline: two sides are 'home' and 'away'.
    For total:     two sides are 'over' and 'under'; must agree on the same line.

    Returns dict with keys:
        consensus_line          (None for moneyline, float for total)
        sides                   list of side keys (e.g. ['home','away'] or ['over','under'])
        fair_prob               {side: float}
        fair_price_american     {side: int}
        num_books               int
    Or None if consensus cannot be computed.
    """
    if market == 'moneyline':
        sides = ('home', 'away')
        consensus_line = None

        # Group by book: need both sides present
        by_book: dict[str, dict] = defaultdict(dict)
        for row in snapshots:
            if row['outcome_type'] in sides:
                by_book[row['book']][row['outcome_type']] = row['price_american']

        devigged_probs: dict[str, list] = defaultdict(list)
        included_books = 0

        for book, prices in by_book.items():
            if not all(s in prices for s in sides):
                continue   # book is missing one side
            prob_home = american_to_probability(prices['home'])
            prob_away = american_to_probability(prices['away'])
            dv_home, dv_away = devig_two_way(prob_home, prob_away)
            devigged_probs['home'].append(dv_home)
            devigged_probs['away'].append(dv_away)
            included_books += 1

        if included_books == 0:
            return None

        fair_prob  = {s: median(devigged_probs[s]) for s in sides}
        fair_price = {s: probability_to_american(fair_prob[s]) for s in sides}

        return {
            'consensus_line':      consensus_line,
            'sides':               list(sides),
            'fair_prob':           fair_prob,
            'fair_price_american': fair_price,
            'num_books':           included_books,
        }

    elif market == 'total':
        sides = ('over', 'under')

        # Group by (book, line): need both over+under at the same line
        by_book_line: dict[tuple, dict] = defaultdict(dict)
        for row in snapshots:
            if row['outcome_type'] in sides and row['line'] is not None:
                key = (row['book'], row['line'])
                by_book_line[key][row['outcome_type']] = row['price_american']

        # Find modal line across all (book, line) groups
        line_counts: dict[float, int] = defaultdict(int)
        for (book, line), prices in by_book_line.items():
            if all(s in prices for s in sides):
                line_counts[line] += 1

        if not line_counts:
            return None

        consensus_line = _modal_line(line_counts)

        # Only use books that have both sides at the modal line
        devigged_probs: dict[str, list] = defaultdict(list)
        included_books = 0

        for (book, line), prices in by_book_line.items():
            if line != consensus_line:
                continue
            if not all(s in prices for s in sides):
                continue
            prob_over  = american_to_probability(prices['over'])
            prob_under = american_to_probability(prices['under'])
            dv_over, dv_under = devig_two_way(prob_over, prob_under)
            devigged_probs['over'].append(dv_over)
            devigged_probs['under'].append(dv_under)
            included_books += 1

        if included_books == 0:
            return None

        fair_prob  = {s: median(devigged_probs[s]) for s in sides}
        fair_price = {s: probability_to_american(fair_prob[s]) for s in sides}

        return {
            'consensus_line':      consensus_line,
            'sides':               list(sides),
            'fair_prob':           fair_prob,
            'fair_price_american': fair_price,
            'num_books':           included_books,
        }

    else:
        log.warning(f'compute_consensus_fair_price: unsupported market "{market}"')
        return None


# ── Edge finder ───────────────────────────────────────────────────────────────

def find_edges_for_game(conn, game_pk: int, market: str,
                        min_books: int = 4) -> list[dict]:
    """
    Return a list of per-(book, outcome) edge dicts for one game/market.
    Returns [] if consensus is too thin or data is missing.
    """
    from devig import edge_percent

    snapshots = get_latest_snapshots(conn, game_pk, market)
    if not snapshots:
        return []

    consensus = compute_consensus_fair_price(snapshots, market)
    if consensus is None:
        return []

    if consensus['num_books'] < min_books:
        log.debug(
            f'Consensus too thin: game_pk={game_pk} market={market} — '
            f'only {consensus["num_books"]} book(s), need {min_books}'
        )
        return []

    # Build a lookup: (book, outcome_type, line) → latest snapshot row
    latest: dict[tuple, dict] = {}
    for row in snapshots:
        key = (row['book'], row['outcome_type'], row.get('line'))
        # Keep only the latest per key (snapshots already filtered, but be safe)
        if key not in latest or row['snapshot_time_utc'] > latest[key]['snapshot_time_utc']:
            latest[key] = row

    results = []
    for (book, outcome_type, line), row in latest.items():
        if outcome_type not in consensus['fair_prob']:
            continue
        # For totals, only score the consensus line
        if market == 'total' and line != consensus['consensus_line']:
            continue

        fair_p_american = consensus['fair_price_american'][outcome_type]
        ep = edge_percent(row['price_american'], fair_p_american)

        results.append({
            'game_pk':              game_pk,
            'market':               market,
            'side':                 outcome_type,
            'line':                 consensus['consensus_line'],
            'book':                 book,
            'offered_price_american': row['price_american'],
            'fair_price_american':  fair_p_american,
            'fair_probability':     consensus['fair_prob'][outcome_type],
            'edge_percent':         ep,
            'num_books_in_consensus': consensus['num_books'],
            'snapshot_time_utc':    row['snapshot_time_utc'],
            'api_last_update_utc':  row['api_last_update_utc'],
        })

    return results
