"""
devig.py — core vig-removal and edge math.

Pure functions, no database calls. All probabilities are floats in [0, 1].
"""


def american_to_decimal(american: int) -> float:
    """Convert American odds to decimal (stake-inclusive return per $1 bet)."""
    if american >= 0:
        return round((american / 100) + 1, 6)
    else:
        return round((100 / abs(american)) + 1, 6)


def american_to_probability(american: int) -> float:
    """Raw implied probability from American odds (includes vig)."""
    if american <= 0:
        n = abs(american)
        return round(n / (n + 100), 6)
    else:
        return round(100 / (american + 100), 6)


def probability_to_american(prob: float) -> int:
    """
    Convert a true probability to American odds.
    Clips to ±10000 to avoid infinities near 0 or 1.
    """
    prob = max(0.0001, min(0.9999, prob))   # guard against 0/1
    if prob >= 0.5:
        return round(-100 * prob / (1 - prob))
    else:
        return round(100 * (1 - prob) / prob)


def devig_two_way(prob_a: float, prob_b: float) -> tuple[float, float]:
    """
    Remove vig from a two-outcome market using the proportional (multiplicative) method.
    The two devigged probabilities sum to exactly 1.0.

    Example: implied probs 0.60 + 0.55 = 1.15 overround
    Devigged: (0.60/1.15, 0.55/1.15) ≈ (0.5217, 0.4783)
    """
    total = prob_a + prob_b
    if total <= 0:
        raise ValueError("Sum of probabilities must be positive")
    return (prob_a / total, prob_b / total)


def stake_units(recommended_stake_pct: float) -> float:
    """Convert stake_pct to units: 0.02→2u, 0.01→1u, 0.0→0.5u (red/shadow floor)."""
    if recommended_stake_pct >= 0.02:
        return 2.0
    if recommended_stake_pct >= 0.01:
        return 1.0
    return 0.5


def unit_payout(result: str, price_american: int, stake_units_val: float) -> float:
    """
    Unit profit for a graded recommendation.
    Win:       stake_units × (decimal_odds − 1)
    Loss:      −stake_units
    Push/Void: 0.0
    """
    if result in ('push', 'void'):
        return 0.0
    if result == 'loss':
        return round(-stake_units_val, 4)
    dec = american_to_decimal(price_american)
    return round(stake_units_val * (dec - 1), 4)


def edge_percent(offered_price_american: int, fair_price_american: int) -> float:
    """
    Expected value as a percentage of stake.

    Formula: (fair_probability × offered_decimal − 1) × 100

    Positive = offered price beats fair value (good for bettor).
    Negative = offered price is worse than fair value (house edge is on this side).

    Example: fair = -150 (prob=0.60), offered = +130 (dec=2.30)
             edge = (0.60 × 2.30 − 1) × 100 = +38.0%   (would never see this in practice)
    Example: fair = EV (prob=0.50), offered = -150 (dec=1.667)
             edge = (0.50 × 1.667 − 1) × 100 = −16.65%
    """
    fair_prob     = american_to_probability(fair_price_american)
    offered_dec   = american_to_decimal(offered_price_american)
    return round((fair_prob * offered_dec - 1) * 100, 4)
