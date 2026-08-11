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
