"""Legs that pull against each other on the same NFL offence.

Every leg is chosen for its own fantasy team, in isolation, so nothing stopped
two of them landing on one NFL team and competing for the same football. Two
Bengals receivers need the same catches. A quarterback's passing yards and his
own running back's carries want the ball going in opposite directions. Each
leg looks fine alone and the pair is worse than either.

This module only *finds* those pairs. Replacing the weaker one happens in
selection.select_picks, which is the only place that knows what else a fantasy
team could have played instead.

Two things worth knowing about the rules below:

**Most pairs are same-kind, and one is not.** Yardage and rush attempts are gap
markets; receptions and touchdowns are EV markets. Every rule here stays inside
one of those groups except receiving yards against receptions, which spans
both. That distinction matters because "the weaker one gets replaced" needs the
two scores to mean the same thing, and a +40% gap is not a +40% EV -- this
codebase never ranks one against the other with a plain comparison. For the
crossing rule, selection._weaker defers to the tier logic that fills a slot in
the first place, so the clash rule and the slot rule cannot disagree. A test
lists which rules cross, so adding another is a deliberate act rather than an
accident.

**Position comes from the projections file, not the ESPN roster.** ESPN reports
a *default position id* that this league renders as "TQB" and "RB/WR", which
cannot tell a WR from a TE from a flex RB. The PFF file carries a plain
QB/RB/WR/TE, so that is what the touchdown exception reads.
"""

from __future__ import annotations

REC_YDS = "player_reception_yds"
RUSH_YDS = "player_rush_yds"
PASS_YDS = "player_pass_yds"
RUSH_ATT = "player_rush_attempts"
RECEPTIONS = "player_receptions"
ANYTIME_TD = "player_anytime_td"
PASS_TDS = "player_pass_tds"

TD_MARKETS = frozenset({ANYTIME_TD, PASS_TDS})

#: Market pairs that may not share an NFL team, and how to say so.
#:
#: Keyed by frozenset, so a rule about two legs in the *same* market is a
#: one-element set -- frozenset({"a", "a"}) is frozenset({"a"}).
MARKET_CONFLICTS = {
    frozenset({REC_YDS}): "two receiving-yards legs",
    frozenset({RUSH_YDS}): "two rushing-yards legs",
    frozenset({RECEPTIONS}): "two receptions legs",
    frozenset({RUSH_ATT}): "two rush-attempts legs",
    frozenset({PASS_YDS}): "two passing-yards legs",
    frozenset({PASS_YDS, RUSH_YDS}): "passing yards against rushing yards",
    frozenset({PASS_YDS, RUSH_ATT}): "passing yards against rush attempts",
    # The one rule that crosses scoring kinds: receiving yards are scored by
    # gap, receptions by EV. selection._weaker explains how the weaker leg is
    # picked when the two scores are not comparable quantities.
    frozenset({REC_YDS, RECEPTIONS}): "receiving yards against receptions",
}

#: The touchdown exception: a quarterback and a player he throws to are
#: *positively* correlated -- the pass TD and the receiving TD are often the
#: same play -- so that pair is left alone. Every other touchdown pairing on
#: one team competes for the same end-zone trips.
RECEIVER_POSITIONS = frozenset({"WR", "TE"})


def position(leg: dict) -> str:
    """The leg's position, preferring the projections file over ESPN's."""
    return str(leg.get("projection_position")
               or leg.get("position") or "").strip().upper()


def _qb_with_receiver(a: dict, b: dict) -> bool:
    left, right = position(a), position(b)
    return ((left == "QB" and right in RECEIVER_POSITIONS)
            or (right == "QB" and left in RECEIVER_POSITIONS))


def conflict(a: dict, b: dict) -> str | None:
    """Why these two legs may not sit on the same slip, or None.

    Returns the reason as a phrase, so a caller can drop it into a sentence.
    """
    team = a.get("nfl_team")
    if not team or team != b.get("nfl_team"):
        return None
    if a.get("player_key") is not None and a.get("player_key") == b.get("player_key"):
        return None                      # the same player twice is handled elsewhere

    markets = frozenset({a.get("market"), b.get("market")})
    if markets <= TD_MARKETS:
        return None if _qb_with_receiver(a, b) else "two touchdown legs"
    return MARKET_CONFLICTS.get(markets)


def find(legs: list[dict]) -> list[tuple[int, int, str]]:
    """Every conflicting pair, as (index, index, reason).

    Indices are into the list given, so the caller can map them back to
    whatever rows the legs came from.
    """
    found = []
    for i in range(len(legs)):
        for j in range(i + 1, len(legs)):
            reason = conflict(legs[i], legs[j])
            if reason:
                found.append((i, j, reason))
    return found


def describe(leg: dict) -> str:
    """A leg in the fewest words that still identify it."""
    from . import scoring

    name = leg.get("player_name_espn") or leg.get("player_name") or "?"
    market = scoring.MARKET_LABELS.get(leg.get("market"), leg.get("market") or "?")
    line = leg.get("line")
    tail = "" if line is None else f" o{line:g}"
    return f"{name} {market}{tail}"
