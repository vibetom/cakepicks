"""Best-effort FanDuel deep links.

This is an enhancement, never a hard dependency (§12). Nothing here scrapes
FanDuel, calls its endpoints, or automates a login -- links are handed to the
user's browser and the user places the bet. Any failure degrades silently to
the plain table.
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


def parlay_link(picks: list[dict]) -> str | None:
    """Multi-selection betslip URL, or None if any leg lacks its source IDs.

    The multi-selection URL format is unofficial and may change without
    notice, hence the "beta" label in the UI and the all-or-nothing guard.
    """
    try:
        legs = [row["pick"] for row in picks if row.get("pick")]
        if not legs:
            return None
        params = []
        for index, leg in enumerate(legs):
            market_sid = leg.get("market_sid")
            selection_sid = leg.get("sid")
            if not market_sid or not selection_sid:
                return None
            params.append(f"marketId[{index}]={quote(str(market_sid))}")
            params.append(f"selectionId[{index}]={quote(str(selection_sid))}")
        return f"{ADD_TO_BETSLIP}?" + "&".join(params)
    except Exception:
        return None
