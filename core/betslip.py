"""FanDuel betslip deep links.

The whole-parlay link is the one that matters: it opens FanDuel with every leg
already on the slip, so it can be handed to whoever is actually placing the
bet. Per-leg links are only a fallback for when the IDs are incomplete.

Nothing here scrapes FanDuel, calls its endpoints, or automates a login (§12).
These are URLs handed to a person's browser; that person places the bet.

The multi-selection URL format is **unofficial**. It is built from the source
IDs The Odds API returns under `includeSids`, and FanDuel can change it without
notice, so every failure degrades to the plain table rather than breaking.
"""

from __future__ import annotations

from urllib.parse import quote

ADD_TO_BETSLIP = "https://sportsbook.fanduel.com/addToBetslip"


def leg_link(prop: dict) -> str | None:
    """Outcome-level or market-level FanDuel link for one leg, if present."""
    if not prop:
        return None
    link = prop.get("link")
    if isinstance(link, str) and link.startswith("http"):
        return link
    return None


def leg_ids(prop: dict) -> tuple[str, str] | None:
    """(market id, selection id) for a leg, or None if either is missing."""
    if not prop:
        return None
    market_sid = prop.get("market_sid")
    selection_sid = prop.get("sid")
    if not market_sid or not selection_sid:
        return None
    return str(market_sid), str(selection_sid)


def build_parlay_url(legs: list[dict]) -> str | None:
    """Multi-selection betslip URL for the given legs, or None.

    Returns None unless every leg has both IDs: a partial slip would quietly
    under-report the bet, which is worse than no link at all.
    """
    if not legs:
        return None
    params = []
    for index, leg in enumerate(legs):
        ids = leg_ids(leg)
        if ids is None:
            return None
        market_sid, selection_sid = ids
        params.append(f"marketId[{index}]={quote(market_sid, safe='')}")
        params.append(f"selectionId[{index}]={quote(selection_sid, safe='')}")
    return f"{ADD_TO_BETSLIP}?" + "&".join(params)


def parlay_link(picks: list[dict]) -> str | None:
    """Whole-parlay betslip URL built from the filled legs of `picks`."""
    try:
        return build_parlay_url([row["pick"] for row in picks if row.get("pick")])
    except Exception:
        return None


def parlay_link_status(picks: list[dict]) -> dict:
    """The parlay URL plus enough detail for the UI to explain a failure.

    Keys: url, leg_count, missing (team names whose leg lacks IDs), reason.
    """
    filled = [(row, row["pick"]) for row in picks if row.get("pick")]
    missing = [row["team_name"] for row, prop in filled if leg_ids(prop) is None]

    if not filled:
        return {"url": None, "leg_count": 0, "missing": [],
                "reason": "No legs have been selected yet."}
    if missing:
        return {
            "url": None,
            "leg_count": len(filled),
            "missing": missing,
            "reason": (
                f"{len(missing)} of {len(filled)} legs are missing FanDuel's "
                "selection IDs, so a full-parlay link would be incomplete. "
                "Fetching fresh odds usually fixes this — the IDs come from the "
                "odds feed and are sometimes absent for a market posted late."
            ),
        }
    return {
        "url": build_parlay_url([prop for _, prop in filled]),
        "leg_count": len(filled),
        "missing": [],
        "reason": None,
    }
