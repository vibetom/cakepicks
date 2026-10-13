"""Every "over" line FanDuel offers for one player's stat, as a betslip.

Used by the Prop Finder's alt-line ladder and by tools/alt_ladder.py. Pure
functions over a raw Odds API payload, so both share one implementation and
the logic is testable without a network.

Two things no FanDuel link can do, which every caller has to say:

* **Carry a stake.** addToBetslip URLs hold market and selection IDs only.
* **Make these a parlay.** Several lines on one player and stat are
  correlated, and FanDuel will not combine them, so they go on as singles.
"""

from __future__ import annotations

#: Stat -> markets, standard line first so it wins when both post the same
#: number. The alternate market is where "every level" lives.
MARKETS = {
    "Receiving yards": ["player_reception_yds", "player_reception_yds_alternate"],
    "Rushing yards": ["player_rush_yds", "player_rush_yds_alternate"],
    "Passing yards": ["player_pass_yds", "player_pass_yds_alternate"],
    "Receptions": ["player_receptions", "player_receptions_alternate"],
}


def _overs(payload: dict, markets: list[str]):
    for book in (payload or {}).get("bookmakers") or []:
        for market in book.get("markets") or []:
            if market.get("key") not in markets:
                continue
            for outcome in market.get("outcomes") or []:
                if outcome.get("name") == "Over" and outcome.get("description"):
                    yield market, outcome


def players(payload: dict, markets: list[str]) -> list[str]:
    """Everyone with at least one Over posted, alphabetically."""
    return sorted({outcome["description"] for _, outcome in _overs(payload, markets)})


def ladder(payload: dict, player: str, markets: list[str]) -> list[dict]:
    """Every Over for `player` across `markets`, one per line, lowest first.

    `player` matches when every word of it appears in the feed's name, so
    "Skyler Bell" finds "Skyler Bell" but not "Chris Bell".
    """
    wanted = player.lower().split()
    by_line: dict = {}
    for market, outcome in _overs(payload, markets):
        name = outcome["description"].lower()
        if not wanted or not all(token in name for token in wanted):
            continue
        leg = {
            "line": outcome.get("point"),
            "price": outcome.get("price"),
            "market": market["key"],
            "market_sid": market.get("sid"),
            "sid": outcome.get("sid"),
            "outcome_link": outcome.get("link"),
            "player": outcome["description"],
        }
        previous = by_line.get(leg["line"])
        # The same number in both markets: keep the standard line's IDs.
        if previous is None or markets.index(leg["market"]) < markets.index(previous["market"]):
            by_line[leg["line"]] = leg
    return sorted(by_line.values(), key=lambda leg: leg["line"] or 0)


def to_win(stake: float, price: float) -> float:
    """Profit on a winning straight bet at American odds."""
    return stake * (price / 100 if price > 0 else 100 / abs(price))
