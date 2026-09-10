"""Scoring every prop on the slate, with no fantasy league involved.

The parlay bot answers "one leg per fantasy team". This answers "what are the
best plays available", over the whole slate, using exactly the same scoring:
`score_prop` from core.selection, which takes a prop, a projection and a
config, and knows nothing about rosters.

Everything else is shared too -- the odds snapshot, the parsed PFF file, name
matching, the price floor and ceiling. In particular both apps read the same
stored snapshot, so a slate fetched by one costs the other nothing.

Two deliberate carry-overs from the parlay bot:

* **Overs only.** Every play is an OVER or an anytime-TD "Yes". The stored
  snapshot has the Under side pruned out, so unders are not available here
  even in principle without a separate fetch.
* **Gap and EV are not comparable.** A +40% gap and a +40% EV mean different
  things, so they are never merged into one ranking without saying so; the
  caller filters by kind or reads the kind column.
"""

from __future__ import annotations

import datetime as dt

from . import scoring
from .config import DEFAULTS
from .matching import NameMatcher
from .odds import parse_event_props
from .projections import projection_index, row_to_projection
from .selection import score_prop

PROPS_CONFIG_FILE = "props_config.json"

#: The prop finder's own tunables, stored separately so the two apps do not
#: overwrite each other's settings while sharing their data.
PROPS_DEFAULTS = {
    # Listing thresholds. Gap and EV are separate because they are not
    # comparable quantities.
    "min_gap": 0.10,
    "min_ev": 0.20,
    "sanity_ceiling": 0.35,
    "hide_review": True,
    # Price limits. -120 rather than the parlay bot's -110: FanDuel routinely
    # prices yardage props at -114/-115, and a -110 floor rejects most of them.
    "odds_floor": -120,
    "odds_ceiling": 300,
    # Volume floors, unchanged -- they exist because PFF projections are means
    # of right-skewed distributions while lines sit near medians.
    "yards_floor": DEFAULTS["yards_floor"],
    "pass_yards_floor": DEFAULTS["pass_yards_floor"],
    "rush_attempts_floor": DEFAULTS["rush_attempts_floor"],
    "receptions_floor": DEFAULTS["receptions_floor"],
    "ev_enabled": True,
    "fuzzy_threshold": DEFAULTS["fuzzy_threshold"],
    "markets": list(DEFAULTS["markets"]),
}


def load_props_config(store=None) -> dict:
    """Defaults overlaid with anything saved for the prop finder."""
    config = dict(PROPS_DEFAULTS)
    if store is None:
        return config
    try:
        saved = store.read_json(PROPS_CONFIG_FILE)
    except Exception:
        return config
    if isinstance(saved, dict):
        for key, value in saved.items():
            if key in PROPS_DEFAULTS:
                config[key] = value
    return config


def save_props_config(config: dict, store) -> bool:
    payload = {k: v for k, v in config.items() if k in PROPS_DEFAULTS}
    try:
        return bool(store.write_json(
            PROPS_CONFIG_FILE, payload, "Update prop finder settings"))
    except Exception:
        return False


def _scoring_config(config: dict) -> dict:
    """Translate the finder's config into what score_prop expects.

    score_prop reads gap/EV thresholds only for the sanity ceiling; the tier
    logic that uses them lives in the parlay bot. Passing the finder's minima
    through keeps one scoring implementation rather than two.
    """
    return {
        "gap_threshold": config.get("min_gap", 0.0),
        "ev_threshold": config.get("min_ev", 0.0),
        "sanity_ceiling": config["sanity_ceiling"],
        "odds_floor": config["odds_floor"],
        "odds_ceiling": config.get("odds_ceiling"),
        "yards_floor": config["yards_floor"],
        "pass_yards_floor": config["pass_yards_floor"],
        "rush_attempts_floor": config["rush_attempts_floor"],
        "receptions_floor": config["receptions_floor"],
        "ev_enabled": config.get("ev_enabled", True),
        "markets": config.get("markets"),
    }


def _matchup(event: dict) -> str:
    away = event.get("away_code") or event.get("away_team") or "?"
    home = event.get("home_code") or event.get("home_team") or "?"
    return f"{away} @ {home}"


def _kickoff(event: dict) -> str:
    value = event.get("commence_time")
    if not value:
        return ""
    try:
        moment = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return moment.strftime("%a %d %b %H:%M UTC")


def explain(row: dict) -> str:
    """One sentence saying where this play's number comes from."""
    if row["kind"] == "gap":
        return (
            f"PFF projects {row['projection']:.1f} against a line of "
            f"{row['line']:g} — {row['score']:+.1%} relative to the line."
        )
    book = row.get("book_probability")
    model = row.get("probability")
    if model is None or book is None:
        return "Not enough information to score this play."
    return (
        f"The projection implies a {model:.1%} chance; the price implies "
        f"{book:.1%}. If the projection is right, a bet returns "
        f"{row['score']:+.1%} per dollar."
    )


def score_board(*, events, raw_by_event, projections, config,
                aliases=None) -> dict:
    """Score every prop in the snapshot against the projections.

    Returns {"rows": [...], "unmatched": [...]}. Rows include the ones that
    failed a filter, each carrying its reason, so the UI can show or hide them
    without rescoring.
    """
    scoring_config = _scoring_config(config)
    entries = projection_index(projections)
    matcher = NameMatcher(entries, aliases=aliases,
                          threshold=float(config["fuzzy_threshold"]))
    events_by_id = {e["id"]: e for e in events}

    rows: list[dict] = []
    unmatched: list[dict] = []
    seen_unmatched: set = set()

    for event_id, payload in (raw_by_event or {}).items():
        event = events_by_id.get(event_id, {})
        teams_in_play = [c for c in (event.get("home_code"), event.get("away_code")) if c]

        for prop in parse_event_props(payload, event):
            hit = matcher.match(prop["player_name"], prop.get("event_teams") or None)
            if not hit.matched:
                key = prop["player_name"]
                if key not in seen_unmatched:
                    seen_unmatched.add(key)
                    closest = None
                    if hit.candidate is not None:
                        closest = projections.iloc[hit.candidate]["playerName"]
                    unmatched.append({
                        "name": key, "teams": teams_in_play,
                        "closest": closest, "score": hit.candidate_score,
                    })
                continue

            projection = row_to_projection(projections, hit.key)
            scored = score_prop(prop, projection, scoring_config)

            team = projection.get("team")
            opponent = next((c for c in teams_in_play if c and c != team), None)
            book_probability = None
            try:
                book_probability = scoring.implied_probability(prop["price"])
            except ValueError:
                pass

            row = {
                **scored,
                "player": projection.get("playerName") or prop["player_name"],
                "team": team,
                "opponent": opponent,
                "position": projection.get("position"),
                "matchup": _matchup(event) if event else "",
                "kickoff": _kickoff(event) if event else "",
                "book_probability": book_probability,
                "match_method": hit.method,
                "match_score": hit.score,
            }
            row["edge"] = (
                None if (row.get("probability") is None or book_probability is None)
                else row["probability"] - book_probability
            )
            row["why"] = explain(row)
            rows.append(row)

    return {"rows": rows, "unmatched": sorted(unmatched, key=lambda r: -r["score"])}


def qualifying(rows: list[dict], config: dict) -> list[dict]:
    """Rows that pass every hard filter and clear their listing threshold.

    Gap and EV are held to separate minima because they are separate
    quantities: a 10% gap and a 10% EV are not the same claim.
    """
    min_gap = float(config.get("min_gap", 0.0))
    min_ev = float(config.get("min_ev", 0.0))
    hide_review = bool(config.get("hide_review", True))

    out = []
    for row in rows:
        if row.get("score") is None:
            continue
        if row.get("needs_review") and hide_review:
            continue
        # A prop flagged only by the sanity ceiling is eligible once shown.
        blocked = [c for c in (row.get("exclusion_codes") or [])
                   if c != "sanity_ceiling"]
        if blocked:
            continue
        if row["kind"] == "gap" and row["score"] < min_gap:
            continue
        if row["kind"] == "ev" and row["score"] < min_ev:
            continue
        out.append(row)
    return sorted(out, key=lambda r: -r["score"])


def filter_options(rows: list[dict]) -> dict:
    """Distinct values available for each filter, from the loaded slate.

    Driven by the data rather than a fixed list, so the filters always match
    whatever slate is loaded.
    """
    def distinct(key):
        return sorted({r[key] for r in rows if r.get(key)})

    return {
        "matchups": distinct("matchup"),
        "teams": distinct("team"),
        "players": distinct("player"),
        "positions": distinct("position"),
        "markets": sorted({r["market"] for r in rows if r.get("market")}),
        "kinds": sorted({r["kind"] for r in rows if r.get("kind")}),
    }


def apply_filters(rows: list[dict], *, matchups=None, teams=None, players=None,
                  positions=None, markets=None, kinds=None,
                  search: str | None = None) -> list[dict]:
    """Narrow the board. An empty or absent filter means "no restriction"."""
    def keep(row):
        if matchups and row.get("matchup") not in matchups:
            return False
        if teams and row.get("team") not in teams:
            return False
        if players and row.get("player") not in players:
            return False
        if positions and row.get("position") not in positions:
            return False
        if markets and row.get("market") not in markets:
            return False
        if kinds and row.get("kind") not in kinds:
            return False
        if search:
            needle = search.strip().lower()
            if needle and needle not in str(row.get("player", "")).lower():
                return False
        return True

    return [row for row in rows if keep(row)]
