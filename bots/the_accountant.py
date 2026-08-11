"""
the_accountant.py — pure predictive model, indifferent to market value.

Real implementation: a net-EPA/play matchup model. For each game, reads both
teams' offensive EPA/play and defensive EPA/play allowed (season-to-date,
blended with last season's full-season numbers early in the year — see
scripts/pull_tendencies_nfl.py), and evaluates all three markets
independently every week: moneyline, spread, and total. It bets its own
projection on each — never compares its projection to a market price looking
for a mispriced number (that's Degen Darren's job) — but spread/total bets
are inherently graded against a real market line, so it does read the
market's own spread/total number as the target to project against, the same
way a moneyline pick already gets priced against a target_price_american in
scripts/run_slot.py. That's not market-value-hunting, it's just "what number
am I betting on."

Model — everything derives from one matchup value per side:
    matchup_value(team, opponent) = team's off_epa + opponent's def_epa
        (opponent's def_epa is EPA/play ALLOWED — positive means the
        opponent's defense has been conceding value, which adds to how much
        the team in question is expected to score against them)
    projected_points(team) = LEAGUE_AVG_TEAM_POINTS + matchup_value(team, opponent) * PLAYS_PER_GAME
    projected_margin = projected_home_points - projected_away_points + HOME_FIELD_POINTS
    projected_total  = projected_home_points + projected_away_points

Moneyline: fair_prob_home = sigmoid(MARGIN_TO_LOGIT_SCALE * projected_margin)
Spread: home_cover_margin = projected_margin - (-market_home_spread_line)
        (market_home_spread_line is already signed, e.g. -3.5 if home's favored
        by 3.5, so this simplifies to projected_margin + market_home_spread_line)
        fair_prob_home_covers = sigmoid(MARGIN_TO_LOGIT_SCALE * home_cover_margin)
Total: total_diff = projected_total - market_total_line
       fair_prob_over = sigmoid(TOTAL_TO_LOGIT_SCALE * total_diff)

LEAGUE_AVG_TEAM_POINTS, PLAYS_PER_GAME, and HOME_FIELD_POINTS are reasonable
starting constants, NOT fit against any historical results. MARGIN_TO_LOGIT_SCALE
has slightly more grounding: NFL final-score margins have a commonly-cited
standard deviation around 13.5 points, and a logistic distribution with scale
s approximates a normal with stdev sigma when s = pi / (sigma * sqrt(3)) — that
works out to s ~= 0.134 for sigma=13.5, which is what's used below.
TOTAL_TO_LOGIT_SCALE has no equivalent public anchor and is a plainer guess.
None of this is fit against OUR results yet — there's no graded history to
calibrate against. Revisit all of it once a real season's worth of graded
picks exists to check the model's actual calibration (are its 65%-confidence
picks really winning ~65% of the time?).

Dual-axis note: bots/config.py's tier_for() expects an edge_pct that's
independent of fair_prob (normally "model probability vs market-implied
probability"). The Accountant has no such independent second axis for any of
its three markets by design, so edge_pct is always just fair_prob - 0.5 (a
real, honest number, but not truly independent of fair_prob for this bot) —
same accepted tradeoff bots/coach_bo.py documents for its own dual-axis
departure. Each market is gated through tier_for() independently, so a game
can produce a moneyline pick, a spread pick, a total pick, all three, or none.

If a team is missing off_epa/def_epa entirely (shouldn't happen once the
tendency pull has run — it always has at least a prior-season fallback), the
whole game is skipped. If a specific market has no market line available
(spread/total not yet posted by any book), just that market is skipped.
"""

import math

from .base import Bot, Pick, BotContext
from .config import tier_for
from .market_lines import consensus_line
from . import registry

HOME_FIELD_EPA = 0.03          # kept for backward-compatible framing; folded into HOME_FIELD_POINTS below
LEAGUE_AVG_TEAM_POINTS = 22.0   # rough modern-NFL per-team-per-game scoring average
PLAYS_PER_GAME = 64             # rough average offensive snaps per team per game
HOME_FIELD_POINTS = 1.5         # rough modern-NFL home-field scoring edge
MARGIN_TO_LOGIT_SCALE = 0.134   # pi / (13.5 * sqrt(3)) — see module docstring
TOTAL_TO_LOGIT_SCALE = 0.12     # scales a projected total-points diff into a logit


def _team_epa(features: dict, team: str) -> tuple[float, float] | None:
    """(off_epa, def_epa) for this team, or None if either is missing."""
    off_epa = features.get(f'tendency:{team}:off_epa')
    def_epa = features.get(f'tendency:{team}:def_epa')
    if off_epa is None or def_epa is None:
        return None
    return float(off_epa), float(def_epa)


def _projected_points(home_off: float, home_def: float, away_off: float, away_def: float) -> tuple[float, float]:
    """(projected_home_points, projected_away_points), home-field-adjusted."""
    matchup_home = home_off + away_def
    matchup_away = away_off + home_def
    home_pts = LEAGUE_AVG_TEAM_POINTS + matchup_home * PLAYS_PER_GAME + HOME_FIELD_POINTS / 2
    away_pts = LEAGUE_AVG_TEAM_POINTS + matchup_away * PLAYS_PER_GAME - HOME_FIELD_POINTS / 2
    return home_pts, away_pts


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


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
            game_id = game['game_id']
            features = ctx.features.get(game_id, {})

            home_epa = _team_epa(features, home)
            away_epa = _team_epa(features, away)
            if home_epa is None or away_epa is None:
                continue  # no efficiency read on one side — nothing to bet

            home_off, home_def = home_epa
            away_off, away_def = away_epa
            home_pts, away_pts = _projected_points(home_off, home_def, away_off, away_def)
            projected_margin = home_pts - away_pts
            projected_total = home_pts + away_pts

            # ── moneyline ──
            fair_prob_home = _sigmoid(MARGIN_TO_LOGIT_SCALE * projected_margin)
            self._maybe_pick(
                picks, ctx, game_id, market='moneyline',
                side='home' if fair_prob_home >= 0.5 else 'away',
                fair_prob=max(fair_prob_home, 1 - fair_prob_home),
                line=None,
                note=f'Projected {home} {home_pts:.1f} - {away} {away_pts:.1f}.',
            )

            # ── spread ──
            home_spread = consensus_line(ctx, game_id, 'spread', 'home')
            if home_spread is not None:
                home_cover_margin = projected_margin + home_spread
                fair_prob_home_covers = _sigmoid(MARGIN_TO_LOGIT_SCALE * home_cover_margin)
                side = 'home' if fair_prob_home_covers >= 0.5 else 'away'
                self._maybe_pick(
                    picks, ctx, game_id, market='spread', side=side,
                    fair_prob=max(fair_prob_home_covers, 1 - fair_prob_home_covers),
                    line=home_spread if side == 'home' else -home_spread,
                    note=f'Projected margin {projected_margin:+.1f} vs market {home} {home_spread:+.1f}.',
                )

            # ── total ──
            total_line = consensus_line(ctx, game_id, 'total', 'over')
            if total_line is not None:
                total_diff = projected_total - total_line
                fair_prob_over = _sigmoid(TOTAL_TO_LOGIT_SCALE * total_diff)
                side = 'over' if fair_prob_over >= 0.5 else 'under'
                self._maybe_pick(
                    picks, ctx, game_id, market='total', side=side,
                    fair_prob=max(fair_prob_over, 1 - fair_prob_over),
                    line=total_line,
                    note=f'Projected total {projected_total:.1f} vs market {total_line:.1f}.',
                )

        return picks

    def _maybe_pick(self, picks, ctx, game_id, market, side, fair_prob, line, note):
        edge_pct = fair_prob - 0.5
        tier = tier_for(self.key, edge_pct, fair_prob)
        if tier is None:
            return  # genuine statistical toss-up per this bot's own thresholds -> no pick this market
        units, confidence = tier
        picks.append(Pick(
            sport=ctx.sport,
            game_id=game_id,
            market=market,
            side=side,
            line=line,
            fair_price_american=_american_odds(fair_prob),
            edge_pct=edge_pct,
            confidence=confidence,
            units=units,
            is_shadow=True,
            notes=f'Net EPA/play model: {note} -> fair {fair_prob:.0%} on {side}.',
        ))


registry.register(TheAccountant())
