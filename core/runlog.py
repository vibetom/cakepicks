"""Per-run JSON logs and the season history they feed (§10.7).

On a hosted deployment the working directory is usually ephemeral, so writes
here are best-effort and the UI also offers download/import of the same JSON.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

RUNS_DIR = Path("data/runs")


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


def write_run(record: dict, runs_dir: Path = RUNS_DIR) -> Path | None:
    """Persist a run. Returns None if the filesystem is not writable."""
    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
        path = runs_dir / f"{record['nfl_week_label']}-{record['run_id']}.json"
        path.write_text(json.dumps(record, indent=2))
        return path
    except OSError:
        return None


def list_runs(runs_dir: Path = RUNS_DIR) -> list[dict]:
    """All stored runs, newest first. Unreadable files are skipped."""
    runs = []
    if not runs_dir.exists():
        return runs
    for path in sorted(runs_dir.glob("*.json"), reverse=True):
        try:
            record = json.loads(path.read_text())
            record["_path"] = str(path)
            runs.append(record)
        except (json.JSONDecodeError, OSError):
            continue
    return runs


def update_results(path: str | Path, results: dict) -> bool:
    """Write Win/Loss/Push grades back into a stored run."""
    try:
        path = Path(path)
        record = json.loads(path.read_text())
        for pick in record.get("picks", []):
            key = str(pick.get("team_id"))
            if key in results:
                pick["result"] = results[key]
        path.write_text(json.dumps(record, indent=2))
        return True
    except (json.JSONDecodeError, OSError):
        return False


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
