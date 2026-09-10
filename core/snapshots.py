"""Saved working state, so a restart does not cost API credits.

The odds snapshot is the expensive thing in this app: a full slate costs around
100 credits out of a 500/month free tier. Streamlit restarts on every code
change, every secrets edit, and after a few hours idle, so without this the
user pays again just to see the same week.

Three pieces are saved, each independently restorable:

* **odds** -- the event list and FanDuel payloads. This is the one that costs
  money.
* **rosters** -- free to refetch, but saving it makes a restart instant.
* **projections** -- the parsed PFF file, so the CSV need not be re-uploaded.

Size matters here. GitHub's Contents API returns empty content for files over
1 MB, so a full week of raw payloads (~1 MB pretty-printed) would save fine and
then read back as nothing. Two measures keep it well clear: Under/No outcomes
are dropped, since selection is overs-only and never looks at them, and the
document is written without indentation. That takes a 16-game slate from about
1.08 MB to 0.38 MB.
"""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd

ODDS_PATH = "odds/latest.json"
ROSTERS_PATH = "rosters/latest.json"
PROJECTIONS_PATH = "projections/latest.json"

# Refuse to save anything close to the Contents API's 1 MB read ceiling.
MAX_SNAPSHOT_BYTES = 900_000

# Outcome sides the app never scores. Dropping them roughly halves the payload.
_UNUSED_SIDES = {"under", "no"}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def prune_odds(raw_by_event: dict) -> dict:
    """Drop the outcome sides selection never reads.

    Every leg is an OVER or an anytime-TD "Yes", so Under and No outcomes are
    dead weight in a stored snapshot. Structure is otherwise untouched, so a
    restored payload still goes through parse_event_props unchanged.
    """
    pruned = {}
    for event_id, payload in (raw_by_event or {}).items():
        if not isinstance(payload, dict):
            continue
        bookmakers = []
        for bookmaker in payload.get("bookmakers") or []:
            markets = []
            for market in bookmaker.get("markets") or []:
                outcomes = [
                    outcome for outcome in (market.get("outcomes") or [])
                    if str(outcome.get("name", "")).strip().lower() not in _UNUSED_SIDES
                ]
                markets.append({**market, "outcomes": outcomes})
            bookmakers.append({**bookmaker, "markets": markets})
        pruned[event_id] = {**payload, "bookmakers": bookmakers}
    return pruned


def _too_big(document: dict) -> int | None:
    """Serialized size if it exceeds the ceiling, else None."""
    size = len(json.dumps(document, separators=(",", ":")).encode("utf-8"))
    return size if size > MAX_SNAPSHOT_BYTES else None


def save_odds(store, *, events, raw_by_event, markets, fetched_at,
              window=None) -> tuple[bool, str | None]:
    """Persist the odds snapshot. Returns (saved, message).

    A message is returned whenever the user should know something, whether or
    not the save succeeded.
    """
    document = {
        "saved_at": _now(),
        "fetched_at": fetched_at,
        "markets": list(markets or []),
        "window": window,
        "events": events,
        "raw_by_event": prune_odds(raw_by_event),
        "pruned": True,
    }
    oversize = _too_big(document)
    if oversize:
        return False, (
            f"The odds snapshot is {oversize / 1e6:.1f} MB, too large to store "
            "reliably. It is still loaded for this session, but a restart will "
            "lose it. Fetching fewer markets would bring it under the limit."
        )
    try:
        saved = store.write_json(
            ODDS_PATH, document,
            f"Save odds snapshot ({len(events or [])} games)", compact=True,
        )
    except Exception as exc:
        return False, f"Could not save the odds snapshot: {exc}"
    return (saved, None) if saved else (False, "Could not save the odds snapshot.")


def load_odds(store) -> dict | None:
    """The stored odds snapshot, or None if there isn't a usable one."""
    try:
        document = store.read_json(ODDS_PATH)
    except Exception:
        return None
    if not isinstance(document, dict):
        return None
    if not document.get("raw_by_event"):
        return None
    return document


def save_rosters(store, *, teams, fetched_at) -> bool:
    document = {"saved_at": _now(), "fetched_at": fetched_at, "teams": teams}
    if _too_big(document):
        return False
    try:
        return bool(store.write_json(
            ROSTERS_PATH, document,
            f"Save roster snapshot ({len(teams or [])} teams)", compact=True))
    except Exception:
        return False


def load_rosters(store) -> dict | None:
    try:
        document = store.read_json(ROSTERS_PATH)
    except Exception:
        return None
    if isinstance(document, dict) and document.get("teams"):
        return document
    return None


def save_projections(store, *, frame, filename, warnings=None) -> bool:
    """Persist the parsed PFF file so the CSV need not be re-uploaded."""
    if frame is None or not len(frame):
        return False
    document = {
        "saved_at": _now(),
        "filename": filename,
        "warnings": list(warnings or []),
        "columns": list(frame.columns),
        "records": json.loads(frame.to_json(orient="records")),
    }
    if _too_big(document):
        return False
    try:
        return bool(store.write_json(
            PROJECTIONS_PATH, document,
            f"Save projections snapshot ({filename})", compact=True))
    except Exception:
        return False


def load_projections(store) -> tuple[pd.DataFrame | None, dict]:
    """Rebuild the projections frame from storage.

    Returns (frame, metadata). The frame is None when nothing usable is stored.
    """
    try:
        document = store.read_json(PROJECTIONS_PATH)
    except Exception:
        return None, {}
    if not isinstance(document, dict) or not document.get("records"):
        return None, {}
    try:
        frame = pd.DataFrame(document["records"])
    except Exception:
        return None, {}
    if not len(frame):
        return None, {}
    columns = document.get("columns")
    if columns:
        # Restore the original column order; a missing column would mean the
        # snapshot predates a schema change, so fall back to what is there.
        keep = [c for c in columns if c in frame.columns]
        if keep:
            frame = frame[keep]
    return frame, {
        "filename": document.get("filename"),
        "saved_at": document.get("saved_at"),
        "warnings": document.get("warnings") or [],
    }


def age_in_hours(iso: str | None) -> float | None:
    """Hours since an ISO timestamp, or None if it cannot be read."""
    if not iso:
        return None
    try:
        moment = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return max(0.0, (dt.datetime.now(dt.timezone.utc) - moment).total_seconds() / 3600)
