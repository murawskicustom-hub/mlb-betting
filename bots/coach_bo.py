"""
coach_bo.py — "ball knowledge" bettor.

Leans on scheme/personnel matchups, coaching tendencies, intangibles (short
weeks, revenge games), injuries to key starters, weather — stats only as
backup. Needs an explainable football story to pick a game; no story, no
pick (a fade). Real handicapping logic is out of scope for the platform
build (see PLATFORM_HANDOFF.md) — this is a STUB that proves the
registry -> persistence -> dashboard pipeline end-to-end.

STUB behavior: picks the home moneyline in the first game of the slate with
a placeholder edge/probability, fades every other game. Replace generate()
with real scheme/personnel/intangibles logic when ready.
"""

from .base import Bot, Pick, BotContext
from .config import tier_for
from . import registry


class CoachBo(Bot):
    key = 'coach_bo'
    display_name = 'Coach Bo'
    sports = ('nfl',)

    def generate(self, ctx: BotContext) -> list[Pick]:
        if not ctx.games:
            return []

        game = ctx.games[0]
        edge_pct, fair_prob = 0.05, 0.55   # STUB placeholder inputs
        tier = tier_for(self.key, edge_pct, fair_prob)
        if tier is None:
            return []
        units, confidence = tier

        return [Pick(
            sport=ctx.sport,
            game_id=game['game_id'],
            market='moneyline',
            side='home',
            line=None,
            fair_price_american=None,
            edge_pct=edge_pct,
            confidence=confidence,
            units=units,
            is_shadow=True,
            notes='STUB pick — real scheme/matchup logic not yet implemented',
        )]


registry.register(CoachBo())
