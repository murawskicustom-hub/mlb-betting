"""
base.py — the platform/bot contract.

A Bot reads from a BotContext and returns Picks. It NEVER writes to the
database — persistence, including the fade row written for every game a
bot didn't pick, is owned entirely by the orchestrator (scripts/run_slot.py).
This is what makes "add a bot" a one-file change: no orchestrator, schema,
or dashboard edit required.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class Pick:
    sport: str
    game_id: str
    market: str                       # 'moneyline' | 'spread' | 'total' | <bot-defined>
    side: str                         # 'home' | 'away' | 'over' | 'under' | ...
    line: float | None
    fair_price_american: int | None   # bot's fair price for this side, if it has one
    edge_pct: float | None            # vs market, or None for market-less picks
    confidence: str                   # tier label, e.g. '3u' — platform-defined via config.tier_for
    units: float                      # 1-5 shared unit scale
    is_shadow: bool = False           # tracked-only vs bettable
    notes: str = ''


@dataclass
class BotContext:
    conn: Any                         # read-only use only — bots must not write
    sport: str
    as_of_utc: str
    games: list[dict]                 # this slot's slate (games table rows, as dicts)
    odds: dict[str, list[dict]]       # game_id -> list of normalized price rows
    features: dict[str, dict]         # game_id -> {feature_key: value}


class Bot(ABC):
    key: str
    display_name: str
    sports: tuple[str, ...] = ()

    @abstractmethod
    def generate(self, ctx: BotContext) -> list[Pick]:
        """Read from ctx; return Picks. Must never write to ctx.conn."""
        raise NotImplementedError
