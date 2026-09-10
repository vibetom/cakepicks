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

import re
from urllib.parse import parse_qs, quote, unquote, urlparse

ADD_TO_BETSLIP = "https://sportsbook.fanduel.com/addToBetslip"

# FanDuel's own per-leg links already carry the two ids the combined slip
# needs. When the odds feed omits the `sid` fields -- some plans do not return
# them -- these links are the only source, so they are parsed as a fallback.
_ID_PATTERN = re.compile(
    r"(marketId|selectionId)(?:\[\d+\])?=([^&#]+)", re.IGNORECASE)


def leg_link(prop: dict) -> str | None:
    """Outcome-level or market-level FanDuel link for one leg, if present."""
    if not prop:
        return None
    link = prop.get("link")
    if isinstance(link, str) and link.startswith("http"):
        return link
    return None


def ids_from_link(url: str | None) -> tuple[str, str] | None:
    """Pull (market id, selection id) out of a FanDuel betslip URL.

    Handles the bracketed form FanDuel uses (`marketId[0]=…`), the plain form,
    and percent-encoded brackets. Returns None for any URL that does not carry
    both ids.
    """
    if not url or not isinstance(url, str):
        return None
    found: dict[str, str] = {}
    query = urlparse(url).query or ""
    for key, value in parse_qs(query, keep_blank_values=False).items():
        name = re.sub(r"\[\d+\]$", "", key).lower()
        if name in ("marketid", "selectionid") and value:
            found.setdefault(name, value[0])
    if len(found) < 2:
        # Some links arrive without a parseable query string; fall back to a
        # direct scan of the raw text.
        for name, value in _ID_PATTERN.findall(unquote(url)):
            found.setdefault(name.lower(), value)
    market = found.get("marketid")
    selection = found.get("selectionid")
    return (market, selection) if market and selection else None


def leg_ids(prop: dict) -> tuple[str, str] | None:
    """(market id, selection id) for a leg, or None if neither source has them.

    The feed's `sid` fields are preferred; failing that the ids are recovered
    from the leg's own FanDuel link.
    """
    if not prop:
        return None
    market_sid = prop.get("market_sid")
    selection_sid = prop.get("sid")
    if market_sid and selection_sid:
        return str(market_sid), str(selection_sid)
    # Only the outcome's own link identifies this selection; the display link
    # may be a market- or bookmaker-level URL shared by every player.
    return ids_from_link(prop.get("outcome_link"))


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

    Distinguishes the two very different causes of a missing link: every leg
    failing (the feed is not supplying ids at all, usually a plan limitation)
    versus a few legs failing (a market posted late).

    Keys: url, leg_count, missing, reason, sample_link, has_sids, has_links.
    """
    filled = [(row, row["pick"]) for row in picks if row.get("pick")]
    missing = [row["team_name"] for row, prop in filled if leg_ids(prop) is None]

    if not filled:
        return {"url": None, "leg_count": 0, "missing": [], "sample_link": None,
                "has_sids": False, "has_links": False,
                "reason": "No legs have been selected yet."}

    has_sids = any(prop.get("sid") and prop.get("market_sid") for _, prop in filled)
    outcome_links = [prop.get("outcome_link") for _, prop in filled
                     if prop.get("outcome_link")]
    has_links = bool(outcome_links)

    if not missing:
        return {
            "url": build_parlay_url([prop for _, prop in filled]),
            "leg_count": len(filled), "missing": [], "reason": None,
            "sample_link": outcome_links[0] if outcome_links else None,
            "has_sids": has_sids, "has_links": has_links,
        }

    if len(missing) == len(filled):
        if not has_links:
            reason = (
                f"The odds feed returned no betslip IDs and no per-leg links for "
                f"any of the {len(filled)} legs, so a combined link can't be built. "
                "That is a property of the feed, not of this week's slate: "
                "`includeSids` and `includeLinks` are not available on every "
                "The Odds API plan. Check your plan, then fetch fresh odds."
            )
        else:
            reason = (
                f"All {len(filled)} legs have FanDuel links, but none of them "
                "carry the marketId/selectionId pair a combined slip needs — the "
                "link format may have changed. The per-leg links still work."
            )
    else:
        reason = (
            f"{len(missing)} of {len(filled)} legs are missing their betslip IDs, "
            "so a combined link would be incomplete. This normally means those "
            "markets were posted late; fetching fresh odds usually fixes it."
        )

    return {"url": None, "leg_count": len(filled), "missing": missing,
            "reason": reason, "sample_link": outcome_links[0] if outcome_links else None,
            "has_sids": has_sids, "has_links": has_links}
