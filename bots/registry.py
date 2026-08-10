"""
registry.py — bot registration and lookup.

Each bot module (coach_bo.py, the_accountant.py, degen_darren.py) calls
register(SomeBot()) at import time. The orchestrator imports every bots.<name>
module purely for that side effect, then calls bots_for_sport() to get the
live roster. Adding or replacing a bot is "add a file + register()" — no
change here.
"""

from .base import Bot

_REGISTRY: dict[str, Bot] = {}


def register(bot: Bot) -> None:
    _REGISTRY[bot.key] = bot


def all_bots() -> list[Bot]:
    return list(_REGISTRY.values())


def bots_for_sport(sport: str) -> list[Bot]:
    return [b for b in _REGISTRY.values() if sport in b.sports]


def get_bot(key: str) -> Bot | None:
    return _REGISTRY.get(key)
