"""
degen_darren.py — value hunter.

Leans on line movement (open vs current), public-vs-sharp splits,
unpriced injury/news, plus real NFL fandom. Bets the widest variety of
markets and expects to have the highest pick volume of the three bots
(hates seeing red, takes smaller "safer" edges rather than skip a game).
Real value-hunting logic is out of scope for the platform build (see
PLATFORM_HANDOFF.md) — this is a STUB that proves the registry ->
persistence -> dashboard pipeline end-to-end.

STUB behavior: returns no picks yet (fades every game) — there's no line-
movement/splits data source wired up until Phase 3/9 ingestion lands.
Replace generate() with real value-hunting logic when ready.
"""

from .base import Bot, Pick, BotContext
from . import registry


class DegenDarren(Bot):
    key = 'degen_darren'
    display_name = 'Degen Darren'
    sports = ('nfl',)

    def generate(self, ctx: BotContext) -> list[Pick]:
        # STUB: no line-movement/splits/news data source wired up yet, so
        # every game is a fade for now. This also exercises the
        # orchestrator's fade-row logic against a bot that fades 100%.
        return []


registry.register(DegenDarren())
