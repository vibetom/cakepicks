"""Odds conversion and prop scoring math.

All internal math uses decimal odds. Poisson is implemented by hand (no scipy)
because the lambdas involved are small and the dependency is not worth the
cold-start cost on a hosted deployment.
"""

from __future__ import annotations

import math

# Prop kinds. Yardage-style props are scored by gap; the rest by EV.
GAP_MARKETS = {
    "player_reception_yds",
    "player_rush_yds",
    "player_pass_yds",
    "player_rush_attempts",
}
EV_MARKETS = {
    "player_receptions",
    "player_anytime_td",
    "player_pass_tds",
}

MARKET_LABELS = {
    "player_reception_yds": "Rec Yds",
    "player_rush_yds": "Rush Yds",
    "player_pass_yds": "Pass Yds",
    "player_rush_attempts": "Rush Att",
    "player_receptions": "Receptions",
    "player_anytime_td": "Anytime TD",
    "player_pass_tds": "Pass TDs",
}

# Which projection column feeds each market.
MARKET_PROJECTION_FIELD = {
    "player_reception_yds": "recvYds",
    "player_rush_yds": "rushYds",
    "player_pass_yds": "passYds",
    "player_rush_attempts": "rushAtt",
    "player_receptions": "recvReceptions",
    "player_pass_tds": "passTd",
    # anytime TD is a sum of three fields, handled in anytime_td_lambda()
}


def american_to_decimal(american: float) -> float:
    """Convert American odds to decimal odds.

    -110 -> 1.9091, +150 -> 2.50. Raises on the impossible band (-100, 100).
    """
    a = float(american)
    if a <= -100:
        return 1.0 + 100.0 / abs(a)
    if a >= 100:
        return 1.0 + a / 100.0
    raise ValueError(f"{american} is not a valid American price (-100 < a < 100)")


def decimal_to_american(decimal_odds: float) -> float:
    """Convert decimal odds back to American."""
    d = float(decimal_odds)
    if d <= 1.0:
        raise ValueError(f"decimal odds must exceed 1.0, got {decimal_odds}")
    if d >= 2.0:
        return round((d - 1.0) * 100.0)
    return round(-100.0 / (d - 1.0))


def format_american(american: float) -> str:
    """Render an American price the way a sportsbook does: -110, +145."""
    a = int(round(float(american)))
    return f"+{a}" if a > 0 else str(a)


def implied_probability(american: float) -> float:
    """Book-implied probability of a price (includes the vig)."""
    return 1.0 / american_to_decimal(american)


def price_meets_floor(american: float, floor_american: float) -> bool:
    """True if `american` is at least as good a price as the floor.

    American odds do not order numerically across the sign change, so the
    comparison is done in decimal space: -105 passes a -110 floor, -120 fails.
    """
    return american_to_decimal(american) >= american_to_decimal(floor_american) - 1e-12


def poisson_pmf(k: int, lam: float) -> float:
    """P(X = k) for X ~ Poisson(lam)."""
    if k < 0:
        return 0.0
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    # Computed in log space so large k cannot overflow the factorial.
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def poisson_cdf(k: int, lam: float) -> float:
    """P(X <= k) for X ~ Poisson(lam)."""
    if k < 0:
        return 0.0
    total = 0.0
    for i in range(int(k) + 1):
        total += poisson_pmf(i, lam)
    return min(total, 1.0)


def poisson_over_probability(line: float, lam: float) -> float:
    """P(X > line) for X ~ Poisson(lam), for a half-point line.

    A line of 1.5 means the bet wins on 2 or more, i.e. 1 - CDF(1).
    Whole-number lines would push; they are not offered as main lines here,
    but floor() keeps the formula well defined if one appears.
    """
    return max(0.0, 1.0 - poisson_cdf(math.floor(line), lam))


def anytime_td_probability(lam: float) -> float:
    """P(at least one TD) = 1 - e^-lambda.

    Expected TDs is not the probability of scoring: multi-TD games collapse
    into a single "yes". This Poisson transform is the agreed correction.
    Known limitation (accepted for v1): TD scoring clusters, so the true
    probability runs slightly below this.
    """
    if lam <= 0:
        return 0.0
    return 1.0 - math.exp(-lam)


def anytime_td_lambda(projection: dict) -> float:
    """Lambda for anytime TD: rushing + receiving + return TDs.

    FanDuel's anytime TD market pays on rushing, receiving and return TDs.
    Passing TDs are deliberately excluded.
    """
    return (
        float(projection.get("rushTd") or 0.0)
        + float(projection.get("recvTd") or 0.0)
        + float(projection.get("returnTd") or 0.0)
    )


def gap(projection: float, line: float) -> float:
    """Relative edge of a projection over a line: (proj - line) / line.

    Returns 0.0 for a non-positive line rather than dividing by zero; such a
    prop carries no information and is filtered out upstream anyway.
    """
    if line is None or line <= 0:
        return 0.0
    return (float(projection) - float(line)) / float(line)


def expected_value(probability: float, american: float) -> float:
    """EV per unit staked: p * decimal - 1. +0.20 means +20% expected return."""
    return probability * american_to_decimal(american) - 1.0
