"""A hand-built parlay slip for the Prop Finder.

The parlay bot fills every slot itself, one leg per fantasy team. Here the user
picks plays off the board, so the slip is an explicit list they add to and
remove from.

The maths is not reimplemented: combined odds, payout and the share block come
from core.parlay, and the FanDuel deep link from core.betslip, exactly as the
parlay bot builds them. Only the bookkeeping -- which plays are on the slip,
in what order, and surviving a restart -- lives here.
"""

from __future__ import annotations

from . import parlay as parlay_mod
from .betslip import build_parlay_url, leg_ids
from .selection import prop_id

SLIP_FILE = "slip.json"


def leg_key(row: dict) -> str:
    """Stable identifier for a play, shared with the parlay bot's prop ids."""
    return prop_id(row)


def add(keys: list[str], key: str) -> list[str]:
    """Append a play if it isn't already on the slip. Order is preserved."""
    if key in keys:
        return list(keys)
    return [*keys, key]


def add_many(keys: list[str], new_keys) -> list[str]:
    out = list(keys)
    for key in new_keys:
        out = add(out, key)
    return out


def remove(keys: list[str], key: str) -> list[str]:
    return [k for k in keys if k != key]


def resolve(keys: list[str], rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Match slip keys against the current board.

    Returns (legs in slip order, keys no longer on the board). A key can go
    missing when the odds are refetched and a market moves or is withdrawn --
    that must be reported, never silently dropped from a bet.
    """
    by_key = {leg_key(row): row for row in rows}
    legs, missing = [], []
    for key in keys:
        row = by_key.get(key)
        if row is None:
            missing.append(key)
        else:
            legs.append(row)
    return legs, missing


def _label(leg: dict) -> str:
    """What the share block calls this leg's line: the game it comes from."""
    return leg.get("matchup") or leg.get("team") or "Play"


def _as_picks(legs: list[dict]) -> list[dict]:
    """Adapt legs to the shape core.parlay works in."""
    return [{"team_id": index, "team_name": _label(leg), "pick": leg}
            for index, leg in enumerate(legs)]


def summarize(legs: list[dict], stake: float) -> dict:
    """Combined odds, payout and profit for the slip."""
    return parlay_mod.combine(_as_picks(legs), stake)


def link_status(legs: list[dict]) -> dict:
    """The whole-slip FanDuel URL, or why it could not be built.

    All-or-nothing, as in the parlay bot: a partial slip that quietly drops
    legs is worse than no link.
    """
    if not legs:
        return {"url": None, "missing": [], "reason": "The slip is empty."}
    missing = [f"{leg.get('player')} {leg.get('market_label')}"
               for leg in legs if leg_ids(leg) is None]
    if missing:
        return {
            "url": None, "missing": missing,
            "reason": (
                f"{len(missing)} of {len(legs)} legs are missing FanDuel's "
                "betslip IDs, so a combined link would be incomplete. Fetching "
                "fresh odds usually supplies them."
            ),
        }
    return {"url": build_parlay_url(legs), "missing": [], "reason": None}


def share_text(legs: list[dict], summary: dict, url: str | None = None) -> str:
    """Plain-text block for sending the slip to whoever places the bet."""
    return parlay_mod.share_text(
        _as_picks(legs), summary, title="Prop Finder parlay", parlay_url=url)


def save_slip(store, keys: list[str]) -> bool:
    """Persist the slip so a restart does not lose a half-built bet."""
    try:
        return bool(store.write_json(
            SLIP_FILE, {"keys": list(keys)}, f"Update prop slip ({len(keys)} legs)"))
    except Exception:
        return False


def load_slip(store) -> list[str]:
    try:
        document = store.read_json(SLIP_FILE)
    except Exception:
        return []
    if isinstance(document, dict) and isinstance(document.get("keys"), list):
        return [str(k) for k in document["keys"]]
    return []
