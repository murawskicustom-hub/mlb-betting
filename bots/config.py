"""
config.py — per-bot dual-axis confidence tiers.

Each tier is (units, min_edge_pct, min_fair_prob): a bot only fires that unit
size when BOTH the edge and the probability floor clear. This is a
platform-mandated *mechanic* (see PLATFORM_HANDOFF.md) — the previous MLB
build tiered on edge alone and it mislabeled high-edge longshots as
high-confidence. Bots must never hardcode their own thresholds; they call
tier_for() and pass along whatever it returns (or skip the pick if it
returns None).

Bump the numbers here (not inside a bot file) when retuning a bot, and bump
BOT_CONFIG_VERSION[key] alongside it so recommendations.config_version can
segment "before" from "after" in the dashboard.
"""

BOT_TIERS: dict[str, list[tuple[int, float, float]]] = {
    'coach_bo': [
        (1, 0.03, 0.52),
        (2, 0.03, 0.52),
        (3, 0.05, 0.55),
        (4, 0.07, 0.58),
        (5, 0.07, 0.62),
    ],
    'the_accountant': [
        (1, 0.02, 0.53),
        (3, 0.05, 0.58),
        (5, 0.08, 0.65),
    ],
    'degen_darren': [
        (1, 0.02, 0.51),
        (2, 0.02, 0.51),
        (3, 0.04, 0.55),
        (5, 0.08, 0.60),
    ],
}

BOT_CONFIG_VERSION: dict[str, str] = {
    'coach_bo': 'coach_bo:v1',
    'the_accountant': 'the_accountant:v1',
    'degen_darren': 'degen_darren:v1',
}


def tier_for(bot_key: str, edge_pct: float, fair_prob: float) -> tuple[int, str] | None:
    """
    Return (units, confidence_label) for the highest tier whose thresholds
    both clear, or None if no tier clears (the bot should pass/fade).
    """
    best = None
    for units, min_edge, min_prob in BOT_TIERS.get(bot_key, []):
        if edge_pct >= min_edge and fair_prob >= min_prob:
            if best is None or units > best[0]:
                best = (units, f'{units}u')
    return best
