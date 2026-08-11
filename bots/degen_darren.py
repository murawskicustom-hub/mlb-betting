"""
degen_darren.py — value hunter.

Real implementation: follows line movement, open vs current, across all
three markets independently. The rulebook names line movement as Darren's
core signal (alongside public-vs-sharp splits and news/fandom — see the
scoping note below on why only movement made it into this build). The
mechanic: compare the market's line/price the first time it was pulled this
week (tuesday_research) against the most recent pull (whatever lock slot is
running now). A meaningful move implies real money has come in on a side
since the market opened; Darren follows the direction of the move — a
simple, low-rigor "steam chasing" heuristic that fits a value-hunter/degen
persona better than a carefully-fit model would.

Pull cadence matters here: scripts/run_slot.py writes a fresh snapshot_time_utc
every time pull_odds_nfl.py runs (tuesday_research, plus every lock slot), so
by the time a real lock slot fires there are at least two distinct pulls to
compare. Before that — e.g. this same week, testing, or the very first pull
ever made for a game — there's only one snapshot and no real movement to
measure yet, so that market is left alone rather than reporting a fabricated
zero-movement reading (see bots/market_lines.py's opening_and_current()).

Sizing follows the rulebook's "hates seeing red, takes smaller safer edges
rather than skip a game": the pick threshold is deliberately low (any real
move clears it) and sizing leans toward 1-2u for ordinary movement, only
reaching for bigger tiers on genuine steam. This bot is expected to have the
highest pick volume of the three.

Scoping note — public-vs-sharp splits and "unpriced injury/news" are also
named in the rulebook as things Darren leans on, but neither made it into
this build: bet-percentage/ticket splits aren't something The Odds API (or
any free source currently wired up) provides, and "unpriced news" needs a
real timestamp comparison between when an injury was first reported and
when the market last moved, which isn't tracked precisely enough yet (our
injury pull only has day-granularity, not the timing precision a "the market
hasn't caught up to this yet" story actually needs). Both are honest v2
candidates, not something to fake with weaker data now.

Dual-axis note: this bot is NOT in bots/config.py's BOT_TIERS, same
departure bots/coach_bo.py documents — sizing here is driven by movement
magnitude, not an edge_pct/fair_prob pair, so tier_for() doesn't fit.
"""

from .base import Bot, Pick, BotContext
from . import registry
from .market_lines import opening_and_current

# (units, minimum magnitude of movement to qualify for that tier). Points for
# spread/total, implied-probability delta for moneyline. Reasonable starting
# thresholds, NOT calibrated against real results — there's no graded history
# yet. Deliberately low floors so ordinary weekly movement clears tier 1,
# matching "takes smaller safer edges rather than skip a game."
POINT_MOVE_TIERS = [(1, 0.5), (2, 1.5), (3, 2.5), (5, 4.0)]
PROB_MOVE_TIERS = [(1, 0.02), (2, 0.04), (3, 0.07), (5, 0.11)]


def _implied_prob(american: float) -> float:
    american = float(american)
    if american < 0:
        return -american / (-american + 100)
    return 100 / (american + 100)


def _tier_for_magnitude(magnitude: float, tiers: list[tuple[int, float]]) -> int | None:
    best = None
    for units, min_mag in tiers:
        if magnitude >= min_mag:
            best = units
    return best


class DegenDarren(Bot):
    key = 'degen_darren'
    display_name = 'Degen Darren'
    sports = ('nfl',)

    def generate(self, ctx: BotContext) -> list[Pick]:
        picks: list[Pick] = []

        for game in ctx.games:
            game_id = game['game_id']
            home, away = game['home_team'], game['away_team']

            # ── spread: line moving more negative for home = steam toward home ──
            spread_move = opening_and_current(ctx, game_id, 'spread', 'home', 'line')
            if spread_move is not None:
                opening, current = spread_move
                delta = current - opening
                magnitude = abs(delta)
                tier = _tier_for_magnitude(magnitude, POINT_MOVE_TIERS)
                if tier is not None and delta != 0:
                    side = 'home' if delta < 0 else 'away'
                    picks.append(Pick(
                        sport=ctx.sport, game_id=game_id, market='spread', side=side,
                        line=current if side == 'home' else -current,
                        fair_price_american=None, edge_pct=None,
                        confidence=f'{tier}u', units=float(tier), is_shadow=True,
                        notes=(f'{home} spread moved {opening:+g} -> {current:+g} '
                               f'({magnitude:.1f} pt toward {home if side == "home" else away}) — following the steam.'),
                    ))

            # ── total: line moving up = steam toward the over ──
            total_move = opening_and_current(ctx, game_id, 'total', 'over', 'line')
            if total_move is not None:
                opening, current = total_move
                delta = current - opening
                magnitude = abs(delta)
                tier = _tier_for_magnitude(magnitude, POINT_MOVE_TIERS)
                if tier is not None and delta != 0:
                    side = 'over' if delta > 0 else 'under'
                    picks.append(Pick(
                        sport=ctx.sport, game_id=game_id, market='total', side=side,
                        line=current, fair_price_american=None, edge_pct=None,
                        confidence=f'{tier}u', units=float(tier), is_shadow=True,
                        notes=(f'Total moved {opening:g} -> {current:g} '
                               f'({magnitude:.1f} pt toward the {side}) — following the steam.'),
                    ))

            # ── moneyline: price moving toward home (higher implied prob) = steam toward home ──
            price_move = opening_and_current(ctx, game_id, 'moneyline', 'home', 'price_american')
            if price_move is not None:
                opening_price, current_price = price_move
                opening_prob = _implied_prob(opening_price)
                current_prob = _implied_prob(current_price)
                delta = current_prob - opening_prob
                magnitude = abs(delta)
                tier = _tier_for_magnitude(magnitude, PROB_MOVE_TIERS)
                if tier is not None and delta != 0:
                    side = 'home' if delta > 0 else 'away'
                    picks.append(Pick(
                        sport=ctx.sport, game_id=game_id, market='moneyline', side=side,
                        line=None, fair_price_american=None, edge_pct=None,
                        confidence=f'{tier}u', units=float(tier), is_shadow=True,
                        notes=(f'{home} ML moved {opening_price:+g} ({opening_prob:.0%}) -> '
                               f'{current_price:+g} ({current_prob:.0%}) — following the steam.'),
                    ))

        return picks


registry.register(DegenDarren())
