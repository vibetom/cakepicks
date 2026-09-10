"""Per-run logs and the season history they feed (§10.7).

Two shapes of the same run:

* the **full record**, which includes the raw odds payloads and the roster
  snapshot, so a week can be reproduced exactly. It is offered as a download
  and can run to a few megabytes.
* the **slim record**, which keeps the config, the picks and their grades. That
  is all the History tab needs, and it is what gets persisted -- a few
  kilobytes a week rather than a few megabytes.
"""

from __future__ import annotations

import datetime as dt

RUNS_PREFIX = "runs"

# Keys dropped from the full record to make the slim one.
_BULK_KEYS = ("raw_odds", "rosters", "events")


def _sanitize(value):
    """Make a value JSON-serializable without dragging in numpy/pandas types."""
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(v) for v in value]
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if hasattr(value, "item"):          # numpy scalar
        try:
            return _sanitize(value.item())
        except Exception:
            pass
    return str(value)


def build_run_record(*, config, picks, summary, events, raw_by_event,
                     teams, diagnostics, coverage, odds_fetched_at) -> dict:
    """Everything needed to reproduce and later grade a run."""
    now = dt.datetime.now(dt.timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()
    return _sanitize({
        "run_id": now.strftime("%Y-%m-%dT%H%M%SZ"),
        "created_at": now.isoformat(),
        "nfl_week_label": f"{iso_year}-W{iso_week:02d}",
        "config": config,
        "odds_fetched_at": odds_fetched_at,
        "coverage": coverage,
        "events": events,
        "raw_odds": raw_by_event,
        "rosters": teams,
        "diagnostics": diagnostics,
        "picks": [
            {
                "team_id": row["team_id"],
                "team_name": row["team_name"],
                "tier": row.get("tier"),
                "manual": row.get("manual", False),
                "none_reason": row.get("none_reason"),
                "pick": row.get("pick"),
                "result": None,          # set later in the History tab
            }
            for row in picks
        ],
        "summary": summary,
    })


def slim_record(record: dict) -> dict:
    """The persisted shape: drop the raw odds and roster snapshots.

    Diagnostics are reduced to counts for the same reason -- the History tab
    only reports on picks and grades.
    """
    slim = {k: v for k, v in record.items() if k not in _BULK_KEYS}
    diagnostics = record.get("diagnostics") or {}
    slim["diagnostics"] = {
        key: len(value) if isinstance(value, list) else value
        for key, value in diagnostics.items()
    }
    slim["event_count"] = len(record.get("events") or [])
    return slim


def run_path(record: dict) -> str:
    return f"{RUNS_PREFIX}/{record['nfl_week_label']}-{record['run_id']}.json"


def save_run(store, record: dict) -> str | None:
    """Persist the slim record. Returns its path, or None if the write failed."""
    path = run_path(record)
    message = f"Save parlay run {record['nfl_week_label']} ({record['run_id']})"
    return path if store.write_json(path, slim_record(record), message) else None


def list_runs(store) -> list[dict]:
    """All stored runs, newest first. Unreadable entries are skipped."""
    runs = []
    for path in store.list_json(RUNS_PREFIX):
        record = store.read_json(path)
        if isinstance(record, dict):
            record["_path"] = path
            runs.append(record)
    return runs


def update_results(store, path: str, results: dict) -> bool:
    """Write Win/Loss/Push grades back into a stored run.

    `results` maps a stringified team_id to "Win"/"Loss"/"Push"/None.
    """
    record = store.read_json(path)
    if not isinstance(record, dict):
        return False
    for pick in record.get("picks", []):
        key = str(pick.get("team_id"))
        if key in results:
            pick["result"] = results[key]
    record.pop("_path", None)
    label = record.get("nfl_week_label", path)
    return store.write_json(path, record, f"Grade parlay legs for {label}")


def season_totals(runs: list[dict]) -> dict:
    """Leg hit rate and parlay hit count across graded runs."""
    legs_won = legs_lost = legs_push = legs_ungraded = 0
    parlays_hit = parlays_graded = 0

    for run in runs:
        picks = [p for p in run.get("picks", []) if p.get("pick")]
        if not picks:
            continue
        graded = [p for p in picks if p.get("result") in {"Win", "Loss", "Push"}]
        for pick in picks:
            result = pick.get("result")
            if result == "Win":
                legs_won += 1
            elif result == "Loss":
                legs_lost += 1
            elif result == "Push":
                legs_push += 1
            else:
                legs_ungraded += 1
        if graded and len(graded) == len(picks):
            parlays_graded += 1
            if all(p["result"] in {"Win", "Push"} for p in graded):
                parlays_hit += 1

    decided = legs_won + legs_lost
    return {
        "legs_won": legs_won,
        "legs_lost": legs_lost,
        "legs_push": legs_push,
        "legs_ungraded": legs_ungraded,
        "leg_hit_rate": (legs_won / decided) if decided else None,
        "parlays_hit": parlays_hit,
        "parlays_graded": parlays_graded,
        "runs": len(runs),
    }
