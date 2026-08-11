"""
market_lines.py — shared helper for reading a consensus market line out of a
BotContext's odds snapshots.

Spread and total bets need an actual number to bet against — that's inherent
to how those markets are graded, not "market value hunting" (Degen Darren's
job is comparing books against each other looking for a mispriced number;
this just answers "what IS the number for this market", the same way a
moneyline pick already gets priced against a target_price_american in
scripts/run_slot.py). Takes the median line across every book reporting that
outcome, so one outlier book doesn't skew what a bot thinks it's betting
against.
"""

import statistics


def consensus_line(ctx, game_id: str, market: str, outcome_type: str) -> float | None:
    lines = [
        row['line'] for row in ctx.odds.get(game_id, [])
        if row.get('market') == market and row.get('outcome_type') == outcome_type
        and row.get('line') is not None
    ]
    if not lines:
        return None
    return statistics.median(lines)


def opening_and_current(ctx, game_id: str, market: str, outcome_type: str, field: str) -> tuple[float, float] | None:
    """(opening_value, current_value) for `field` ('line' or 'price_american'),
    each the median across books at that snapshot time. A single pull_odds_*
    run writes every row with the same snapshot_time_utc, so grouping by
    distinct snapshot_time_utc values naturally separates "this run's pull"
    from "an earlier run's pull" without needing to track pull identity
    separately. Returns None if fewer than two distinct snapshot times exist
    yet for this outcome — there's no real movement to measure from a single
    pull, and reporting a fake zero-movement reading would be dishonest, not
    just uninteresting.
    """
    rows = [
        row for row in ctx.odds.get(game_id, [])
        if row.get('market') == market and row.get('outcome_type') == outcome_type
        and row.get(field) is not None and row.get('snapshot_time_utc')
    ]
    if not rows:
        return None
    times = sorted(set(row['snapshot_time_utc'] for row in rows))
    if len(times) < 2:
        return None
    opening_time, current_time = times[0], times[-1]
    opening_vals = [row[field] for row in rows if row['snapshot_time_utc'] == opening_time]
    current_vals = [row[field] for row in rows if row['snapshot_time_utc'] == current_time]
    return statistics.median(opening_vals), statistics.median(current_vals)
