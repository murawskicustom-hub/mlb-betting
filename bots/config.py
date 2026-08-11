"""
config.py — per-bot dual-axis confidence tiers.

Each tier is (units, min_edge_pct, min_fair_prob): a bot only fires that unit
size when BOTH the edge and the probability floor clear. This is a
platform-mandated *mechanic* (see PLATFORM_HANDOFF.md) — the previous MLB
build tiered on edge alone and it mislabeled high-edge longshots as
high-confidence. Bots with a numeric edge/probability estimate must never
hardcode their own thresholds; they call tier_for() and pass along whatever
it returns (or skip the pick if it returns None).

coach_bo and degen_darren are NOT in BOT_TIERS on purpose. coach_bo's edge is
qualitative (a football story, not a numeric edge_pct/fair_prob), so his
dual-axis gate — is there a real story, and how strong is it — is baked
directly into the LLM call in bots/coach_bo.py instead. degen_darren's edge
is line-movement magnitude (points or implied-probability delta), which
isn't an edge_pct/fair_prob pair either — his sizing tiers live directly in
bots/degen_darren.py. Same mandate, different mechanism, in both cases.

Bump the numbers here (not inside a bot file) when retuning a bot, and bump
BOT_CONFIG_VERSION[key] alongside it so recommendations.config_version can
segment "before" from "after" in the dashboard.
"""

BOT_TIERS: dict[str, list[tuple[int, float, float]]] = {
    'the_accountant': [
        (1, 0.02, 0.53),
        (3, 0.05, 0.58),
        (5, 0.08, 0.65),
    ],
}

BOT_CONFIG_VERSION: dict[str, str] = {
    'coach_bo': 'coach_bo:llm-v2-all-markets',
    'the_accountant': 'the_accountant:epa-model-v2-all-markets',
    'degen_darren': 'degen_darren:line-movement-v1',
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
