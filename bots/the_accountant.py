"""
the_accountant.py — pure predictive model, indifferent to market value.

Real implementation: a net-EPA/play matchup model. For each game, reads both
teams' offensive EPA/play and defensive EPA/play allowed (season-to-date,
blended with last season's full-season numbers early in the year — see
scripts/pull_tendencies_nfl.py) from the features table, combines them into
a net expected-points edge for the game, and converts that into a win
probability via a logistic curve. It bets its own number, moneyline only for
v1 — never compares its projection to a market price (that's Degen Darren's
job) and never touches spread/total markets yet (those need a margin model,
not just a win-probability model — a real v2, not this build).

Model:
    matchup_value(team, opponent) = team's off_epa + opponent's def_epa
        (opponent's def_epa is EPA/play ALLOWED — positive means the
        opponent's defense has been conceding value, which adds to how much
        the team in question is expected to score against them)
    net_edge = matchup_value(home, away) - matchup_value(away, home) + HOME_FIELD_EPA
    fair_prob_home = 1 / (1 + exp(-EPA_TO_LOGIT_SCALE * net_edge))

HOME_FIELD_EPA and EPA_TO_LOGIT_SCALE are reasonable starting constants, NOT
fit against any historical results — there's no graded history yet to
calibrate against. Revisit both once a real season's worth of graded picks
exists to check the model's actual calibration (are its 65%-confidence picks
really winning ~65% of the time?).

Dual-axis note: bots/config.py's tier_for() expects an edge_pct that's
independent of fair_prob (normally "model probability vs market-implied
probability"). The Accountant has no market comparison by design, so
edge_pct here is defined as the model's deviation from a coin flip
(fair_prob - 0.5) — a real, honest number, but not an independent second
axis from fair_prob for this bot specifically (same accepted tradeoff
bots/coach_bo.py documents for its own dual-axis departure).

If either team is missing off_epa/def_epa entirely (shouldn't happen once
the tendency pull has run, since it always has at least a prior-season
fallback), the game is skipped — the Accountant doesn't guess without a
number to bet on.
"""

import math

from .base import Bot, Pick, BotContext
from .config import tier_for
from . import registry

HOME_FIELD_EPA = 0.03      # a small, undedicated home-field bump in EPA/play terms
EPA_TO_LOGIT_SCALE = 4.0   # scales a net EPA/play edge into a logit


def _team_epa(features: dict, team: str) -> tuple[float, float] | None:
    """(off_epa, def_epa) for this team, or None if either is missing."""
    off_epa = features.get(f'tendency:{team}:off_epa')
    def_epa = features.get(f'tendency:{team}:def_epa')
    if off_epa is None or def_epa is None:
        return None
    return float(off_epa), float(def_epa)


def _fair_prob_home(home_off: float, home_def: float, away_off: float, away_def: float) -> float:
    matchup_home = home_off + away_def
    matchup_away = away_off + home_def
    net_edge = (matchup_home - matchup_away) + HOME_FIELD_EPA
    return 1.0 / (1.0 + math.exp(-EPA_TO_LOGIT_SCALE * net_edge))


def _american_odds(prob: float) -> int:
    prob = min(max(prob, 0.01), 0.99)
    if prob >= 0.5:
        return round(-100 * prob / (1 - prob))
    return round(100 * (1 - prob) / prob)


class TheAccountant(Bot):
    key = 'the_accountant'
    display_name = 'The Accountant'
    sports = ('nfl',)

    def generate(self, ctx: BotContext) -> list[Pick]:
        picks: list[Pick] = []

        for game in ctx.games:
            home, away = game['home_team'], game['away_team']
            features = ctx.features.get(game['game_id'], {})

            home_epa = _team_epa(features, home)
            away_epa = _team_epa(features, away)
            if home_epa is None or away_epa is None:
                continue  # no efficiency read on one side — nothing to bet

            home_off, home_def = home_epa
            away_off, away_def = away_epa
            prob_home = _fair_prob_home(home_off, home_def, away_off, away_def)

            side = 'home' if prob_home >= 0.5 else 'away'
            fair_prob = prob_home if side == 'home' else 1 - prob_home
            edge_pct = fair_prob - 0.5

            tier = tier_for(self.key, edge_pct, fair_prob)
            if tier is None:
                continue  # genuine statistical toss-up per this bot's own thresholds -> fade
            units, confidence = tier

            side_team = home if side == 'home' else away
            other_team = away if side == 'home' else home
            notes = (
                f'Net EPA/play model: {side_team} off {home_off if side == "home" else away_off:+.2f} '
                f'vs {other_team} def {away_def if side == "home" else home_def:+.2f} allowed '
                f'(plus opposite side) -> fair {fair_prob:.0%} on {side_team}.'
            )

            picks.append(Pick(
                sport=ctx.sport,
                game_id=game['game_id'],
                market='moneyline',
                side=side,
                line=None,
                fair_price_american=_american_odds(fair_prob),
                edge_pct=edge_pct,
                confidence=confidence,
                units=units,
                is_shadow=True,
                notes=notes,
            ))
        return picks


registry.register(TheAccountant())
