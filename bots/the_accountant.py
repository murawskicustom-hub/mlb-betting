"""
the_accountant.py — pure predictive model, indifferent to market value.

Leans on efficiency metrics (EPA/play, success rate), situational base
rates, injury-adjusted projections. Bets its own model's projected side/
total on every game and prop market whenever the model itself is confident
enough — never compares its projection to the market looking for "value"
(that's Degen Darren's job). Passes only when the model is a genuine
statistical toss-up. Real model logic is out of scope for the platform
build (see PLATFORM_HANDOFF.md) — this is a STUB that proves the
registry -> persistence -> dashboard pipeline end-to-end.

STUB behavior: emits one placeholder total pick per game in the slate at a
fixed mid-tier confidence. Replace generate() with the real efficiency
model when ready.
"""

from .base import Bot, Pick, BotContext
from .config import tier_for
from . import registry


class TheAccountant(Bot):
    key = 'the_accountant'
    display_name = 'The Accountant'
    sports = ('nfl',)

    def generate(self, ctx: BotContext) -> list[Pick]:
        picks: list[Pick] = []
        edge_pct, fair_prob = 0.05, 0.58   # STUB placeholder inputs

        for game in ctx.games:
            tier = tier_for(self.key, edge_pct, fair_prob)
            if tier is None:
                continue  # genuine toss-up per this bot's own thresholds -> fade
            units, confidence = tier
            picks.append(Pick(
                sport=ctx.sport,
                game_id=game['game_id'],
                market='total',
                side='over',
                line=None,
                fair_price_american=None,
                edge_pct=edge_pct,
                confidence=confidence,
                units=units,
                is_shadow=True,
                notes='STUB pick — real efficiency model not yet implemented',
            ))
        return picks


registry.register(TheAccountant())
