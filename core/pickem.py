"""Taking a slip to a pick'em app instead of a sportsbook.

PrizePicks and similar products have no price on an individual selection: you
choose players over/under a projection and the payout is a fixed multiplier for
the whole slip. So a slip built here can be placed there, but the odds cannot
travel with it -- only the player, the stat and the line.

That is the whole job of this module: render a slip in the terms a pick'em app
uses, and say plainly what does not carry over.

Two honest limits, stated wherever this output is shown:

* **Lines differ between operators.** A projection that beats FanDuel's 28.5
  may not beat a pick'em app's 30.5. The projection is shown next to the line
  so the difference can be judged rather than assumed away.
* **Two different things get called a probability here.** Receptions and
  touchdown props have a model probability (Poisson). Yardage props do not --
  yards are not Poisson -- so their figure comes from FanDuel's price, which is
  *the market's* view, not ours. Since the whole premise of this app is that
  the projections beat the market, a combined number built from prices is the
  bar to clear, not a forecast of how often these land. It is labelled that way
  rather than presented as our own estimate.
"""

from __future__ import annotations

from . import scoring

#: Our market keys in the words a pick'em app uses.
STAT_NAMES = {
    "player_reception_yds": "Receiving Yards",
    "player_rush_yds": "Rushing Yards",
    "player_pass_yds": "Passing Yards",
    "player_rush_attempts": "Rush Attempts",
    "player_receptions": "Receptions",
    "player_pass_tds": "Pass TDs",
    "player_anytime_td": "Anytime TD",
}


def stat_name(leg: dict) -> str:
    return STAT_NAMES.get(leg.get("market"), leg.get("market_label", "?"))


def selection(leg: dict) -> str:
    """How the pick reads on a pick'em app: "More 28.5", or "Yes"."""
    line = leg.get("line")
    if line is None:
        return "Yes"
    return f"More {line:g}"


def hit_probability(leg: dict) -> tuple[float | None, str]:
    """(probability this leg hits, where the number came from).

    "model" is our Poisson estimate. "price" is FanDuel's implied probability --
    the market's opinion, which our projection is claiming to beat, so it must
    never be presented as our own forecast.
    """
    if leg.get("probability") is not None:
        return leg["probability"], "model"
    try:
        return scoring.implied_probability(leg["price"]), "price"
    except (ValueError, KeyError, TypeError):
        return None, "unknown"


def combined(legs: list[dict]) -> dict:
    """Chance of every leg hitting, and the multiplier needed to break even.

    A pick'em slip pays a fixed multiplier, so the number that decides whether
    it is worth taking is not odds but "how often do all N land". Break-even is
    simply 1 / that. Where legs fall back to FanDuel's price this is the
    market's answer, which is exactly the bar a projection-based bet is trying
    to clear.
    """
    if not legs:
        return {"probability": None, "breakeven_multiplier": None,
                "legs": 0, "modelled": 0, "from_price": 0}

    probability = 1.0
    modelled = from_price = 0
    for leg in legs:
        value, source = hit_probability(leg)
        if value is None:
            return {"probability": None, "breakeven_multiplier": None,
                    "legs": len(legs), "modelled": modelled, "from_price": from_price}
        probability *= value
        if source == "model":
            modelled += 1
        else:
            from_price += 1

    return {
        "probability": probability,
        "breakeven_multiplier": (1.0 / probability) if probability > 0 else None,
        "legs": len(legs),
        "modelled": modelled,
        "from_price": from_price,
    }


def slip_text(legs: list[dict], *, title: str = "Pick'em slip") -> str:
    """The slip in pick'em terms, with the projection alongside each line.

    The projection is included deliberately: if the pick'em app's line differs
    from FanDuel's, it is the only way to tell whether the play still stands.
    """
    if not legs:
        return f"{title}\n\n(nothing selected)"

    lines = [title, ""]
    for leg in legs:
        projection = leg.get("projection")
        note = "" if projection is None else f"   [projected {projection:.1f}]"
        lines.append(
            f"{leg.get('player')} — {stat_name(leg)} — {selection(leg)}{note}"
        )

    summary = combined(legs)
    lines.append("")
    if summary["probability"] is not None:
        lines.append(
            f"All {summary['legs']} land together {summary['probability']:.1%} of "
            f"the time — a payout above {summary['breakeven_multiplier']:.2f}x "
            "beats that."
        )
        if summary["from_price"]:
            lines.append(
                f"({summary['from_price']} of {summary['legs']} legs use FanDuel's "
                "price rather than a model, so that figure is the market's view — "
                "the bar these projections claim to beat, not a forecast.)"
            )
    lines.append("Check each line on the app before entering — they differ.")
    return "\n".join(lines)


def line_comparison(legs: list[dict]) -> list[dict]:
    """Rows for eyeballing our number against whatever line the app shows."""
    return [{
        "Player": leg.get("player"),
        "Stat": stat_name(leg),
        "FanDuel line": leg.get("line"),
        "Pick": selection(leg),
        "Our projection": leg.get("projection"),
        "Room": (None if leg.get("line") in (None, 0)
                 else leg["projection"] - leg["line"]),
    } for leg in legs]
