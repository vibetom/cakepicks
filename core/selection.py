"""Prop scoring, hard filters, and the three-tier pick logic (§8).

Two stages:
  1. score_props()  -- joins odds to projections and rosters, scores every
     prop, and records an exclusion reason for each one that fails a filter.
  2. select_picks() -- picks one leg per fantasy team from the scored props.

Stage 1 is the expensive join; stage 2 is pure and instant, which is what lets
the sliders re-run selection without touching the API.
"""

from __future__ import annotations

from . import scoring
from .matching import NameMatcher
from .odds import parse_event_props
from .projections import projection_index, row_to_projection
from .rosters import player_lookup, roster_entries


def _floor_for(market: str, config: dict) -> tuple[float, str]:
    """The volume/projection floor guarding this market, and its label."""
    if market in ("player_reception_yds", "player_rush_yds"):
        return float(config["yards_floor"]), "yards"
    if market == "player_pass_yds":
        return float(config["pass_yards_floor"]), "passing yards"
    if market == "player_rush_attempts":
        return float(config["rush_attempts_floor"]), "rush attempts"
    if market == "player_receptions":
        return float(config["receptions_floor"]), "receptions"
    return 0.0, ""


def score_prop(prop: dict, projection: dict, config: dict) -> dict:
    """Score one prop against one player's projections.

    Adds: kind (gap|ev), projection value, score, probability, and structured
    exclusion reasons if it fails a hard filter. A failing prop is kept in the
    data (with its reasons) so diagnostics and the override panel can explain
    themselves (§13). Reasons carry a code so callers never string-match.
    """
    market = prop["market"]
    out = dict(prop)
    out["projection_source"] = projection.get("playerName")

    floor, floor_label = _floor_for(market, config)
    reasons: list[tuple[str, str]] = []

    # Markets the user has unchecked. Applied here rather than only at fetch
    # time so that unchecking one re-runs selection instantly from the cached
    # snapshot, without spending credits on a refetch.
    enabled_markets = config.get("markets")
    if enabled_markets is not None and market not in enabled_markets:
        label = scoring.MARKET_LABELS.get(market, market)
        reasons.append(("market_off", f"the {label} market is turned off"))   # (code, human-readable text)

    # Price limits apply to every prop type: the floor rejects prices too
    # short to be worth taking, the ceiling rejects longshots.
    try:
        if not scoring.price_meets_floor(prop["price"], config["odds_floor"]):
            reasons.append((
                "price_floor",
                f"price {scoring.format_american(prop['price'])} is worse than the "
                f"{scoring.format_american(config['odds_floor'])} floor",
            ))
        ceiling = config.get("odds_ceiling")
        if not scoring.price_meets_ceiling(prop["price"], ceiling):
            reasons.append((
                "price_ceiling",
                f"price {scoring.format_american(prop['price'])} is longer than the "
                f"{scoring.format_american(ceiling)} ceiling",
            ))
    except ValueError:
        out.update({
            "kind": "invalid", "score": None, "needs_review": False,
            "eligible": False, "review_only": False,
            "exclusion_codes": ["bad_price"],
            "exclusion_reason": f"invalid price {prop['price']}",
        })
        return out

    if market in scoring.GAP_MARKETS:
        field = scoring.MARKET_PROJECTION_FIELD[market]
        value = float(projection.get(field) or 0.0)
        line = prop.get("line")
        out.update({"kind": "gap", "projection": value, "projection_field": field,
                    "lambda": None, "probability": None})
        if line is None or line <= 0:
            reasons.append(("no_line", "no line posted"))
            out["score"] = None
        else:
            out["score"] = scoring.gap(value, line)
        if value < floor:
            reasons.append((
                "volume_floor",
                f"projection {value:.1f} is below the {floor:g} {floor_label} floor",
            ))
    elif market in scoring.EV_MARKETS:
        out["kind"] = "ev"
        if not config.get("ev_enabled", True):
            reasons.append(("ev_off", "EV props are turned off"))
        if market == "player_anytime_td":
            lam = scoring.anytime_td_lambda(projection)
            probability = scoring.anytime_td_probability(lam)
            out["projection_field"] = "rushTd+recvTd+returnTd"
        else:
            field = scoring.MARKET_PROJECTION_FIELD[market]
            lam = float(projection.get(field) or 0.0)
            line = prop.get("line")
            out["projection_field"] = field
            if line is None:
                reasons.append(("no_line", "no line posted"))
                probability = None
            else:
                probability = scoring.poisson_over_probability(line, lam)
            if market == "player_receptions" and lam < floor:
                reasons.append((
                    "volume_floor",
                    f"projection {lam:.2f} is below the {floor:g} {floor_label} floor",
                ))
        out.update({"projection": lam, "lambda": lam, "probability": probability})
        out["score"] = (None if probability is None
                        else scoring.expected_value(probability, prop["price"]))
        if out["score"] is not None and out["score"] > float(config["sanity_ceiling"]):
            out["needs_review"] = True
            reasons.append((
                "sanity_ceiling",
                f"EV {out['score']:+.1%} exceeds the {float(config['sanity_ceiling']):+.0%} "
                "sanity ceiling — needs manual review",
            ))
    else:
        out.update({"kind": "unknown", "score": None})
        reasons.append(("unsupported", f"unsupported market {market}"))

    codes = [code for code, _ in reasons]
    if out.get("score") is None and "no_line" not in codes:
        reasons.append(("unscored", "could not be scored"))
        codes.append("unscored")

    out.setdefault("needs_review", False)
    out["eligible"] = not reasons
    # True when the sanity flag is the ONLY thing blocking it, so that
    # approving a review card cannot smuggle in a prop that also failed the
    # price floor or a volume floor.
    out["review_only"] = codes == ["sanity_ceiling"]
    out["exclusion_codes"] = codes
    out["exclusion_reason"] = "; ".join(text for _, text in reasons) or None
    return out


def score_props(*, events, raw_by_event, teams, projections, config,
                aliases=None) -> dict:
    """Join odds -> rosters -> projections and score everything.

    Returns a dict with the scored props grouped by fantasy team, plus
    diagnostics for names that could not be matched.
    """
    roster_matcher = NameMatcher(
        roster_entries(teams, eligible_only=True),
        aliases=aliases,
        threshold=float(config["fuzzy_threshold"]),
    )
    projection_matcher = NameMatcher(
        projection_index(projections),
        aliases=aliases,
        threshold=float(config["fuzzy_threshold"]),
    )
    players = player_lookup(teams)
    all_roster_entries = roster_entries(teams, eligible_only=True)

    scored_by_team: dict[object, list] = {team["team_id"]: [] for team in teams}
    unmatched_props: list[dict] = []
    missing_projections: list[dict] = []
    fuzzy_matches: list[dict] = []
    matched_player_keys: set = set()

    events_by_id = {e["id"]: e for e in events}

    for event_id, payload in raw_by_event.items():
        event = events_by_id.get(event_id, {})
        for prop in parse_event_props(payload, event):
            teams_in_play = prop.get("event_teams") or None

            roster_hit = roster_matcher.match(prop["player_name"], teams_in_play)
            if not roster_hit.matched:
                # Not on any fantasy roster is the common case and not
                # interesting; only report near-misses against rostered names.
                if roster_hit.candidate is not None and roster_hit.candidate_score >= 70:
                    candidate_name = all_roster_entries.get(roster_hit.candidate, ("?", "?"))[0]
                    unmatched_props.append({
                        "source": "odds",
                        "name": prop["player_name"],
                        "teams": teams_in_play,
                        "closest": candidate_name,
                        "score": roster_hit.candidate_score,
                    })
                continue

            player = players[roster_hit.key]
            matched_player_keys.add(roster_hit.key)
            if roster_hit.method == "fuzzy":
                # Accepted, but not identical -- worth a human glance, since a
                # wrong match imports another player's odds silently.
                fuzzy_matches.append({
                    "source": "odds", "name": prop["player_name"],
                    "matched": player["name"], "score": roster_hit.score,
                    "teams": [player.get("nfl_team")],
                })

            projection_hit = projection_matcher.match(
                player["name"], [player["nfl_team"]] if player.get("nfl_team") else None
            )
            if not projection_hit.matched:
                closest = None
                if projection_hit.candidate is not None:
                    closest = projections.iloc[projection_hit.candidate]["playerName"]
                missing_projections.append({
                    "source": "csv",
                    "name": player["name"],
                    "teams": [player.get("nfl_team")],
                    "closest": closest,
                    "score": projection_hit.candidate_score,
                })
                continue

            if projection_hit.method == "fuzzy":
                fuzzy_matches.append({
                    "source": "csv", "name": player["name"],
                    "matched": projections.iloc[projection_hit.key]["playerName"],
                    "score": projection_hit.score,
                    "teams": [player.get("nfl_team")],
                })
            projection = row_to_projection(projections, projection_hit.key)
            scored = score_prop(prop, projection, config)
            scored.update({
                "fantasy_team_id": player["fantasy_team_id"],
                "player_key": roster_hit.key,
                "player_name_espn": player["name"],
                "player_id": player["player_id"],
                "nfl_team": player.get("nfl_team"),
                "position": player.get("position"),
                "questionable": player.get("questionable", False),
                "match_method": roster_hit.method,
                "match_score": roster_hit.score,
                "projection_match_method": projection_hit.method,
            })
            scored_by_team.setdefault(player["fantasy_team_id"], []).append(scored)

    return {
        "by_team": scored_by_team,
        "unmatched_props": _dedupe(unmatched_props),
        "missing_projections": _dedupe(missing_projections),
        "fuzzy_matches": _dedupe_fuzzy(fuzzy_matches),
    }


def _dedupe(rows: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for row in rows:
        key = (row["source"], row["name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return sorted(out, key=lambda r: -r.get("score", 0))


def _dedupe_fuzzy(rows: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for row in rows:
        key = (row["source"], row["name"], row["matched"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return sorted(out, key=lambda r: r["score"])


def _sort_key(prop: dict):
    """Rank by score, breaking ties on better price, then alphabetically (§8)."""
    try:
        decimal = scoring.american_to_decimal(prop["price"])
    except ValueError:
        decimal = 0.0
    return (prop["score"], decimal, _neg_alpha(prop.get("player_name_espn", "")))


class _neg_alpha:
    """Sort helper: reverses string order inside a descending max() key."""

    __slots__ = ("value",)

    def __init__(self, value: str):
        self.value = value or ""

    def __lt__(self, other):
        return self.value > other.value

    def __eq__(self, other):
        return self.value == other.value


def select_picks(scored: dict, teams: list[dict], config: dict,
                 approved_reviews=None, overrides=None) -> list[dict]:
    """Choose one leg per fantasy team using the three-tier logic (§8).

    `approved_reviews` is a set of prop ids the user has approved past the
    sanity ceiling; `overrides` maps fantasy_team_id -> prop id chosen by hand.
    """
    approved_reviews = approved_reviews or set()
    overrides = overrides or {}
    results = []
    used_players: set = set()

    for team in teams:
        team_id = team["team_id"]
        candidates = list(scored["by_team"].get(team_id, []))
        for prop in candidates:
            prop["prop_id"] = prop_id(prop)

        eligible = [
            p for p in candidates
            if (p["eligible"] or (p.get("review_only") and p["prop_id"] in approved_reviews))
            and p.get("score") is not None
            and p["player_key"] not in used_players
        ]
        review_cards = [
            p for p in candidates
            if p.get("needs_review") and p["prop_id"] not in approved_reviews
        ]

        pick = None
        tier = None
        manual = False
        best_gap = best_ev = None

        override_id = overrides.get(team_id)
        if override_id:
            pick = next((p for p in candidates if p["prop_id"] == override_id), None)
            if pick is not None:
                tier, manual = "manual", True

        if pick is None:
            gap_props = [p for p in eligible if p["kind"] == "gap"]
            ev_props = [p for p in eligible if p["kind"] == "ev"]
            best_gap = max(gap_props, key=_sort_key) if gap_props else None
            best_ev = max(ev_props, key=_sort_key) if ev_props else None

            gap_threshold = float(config["gap_threshold"])
            ev_threshold = float(config["ev_threshold"])

            if best_gap is not None and best_gap["score"] >= gap_threshold:
                pick, tier = best_gap, 1
                if best_ev is not None and best_ev["score"] >= ev_threshold:
                    pick = best_ev            # EV override steals the slot
            elif best_ev is not None and best_ev["score"] >= ev_threshold:
                pick, tier = best_ev, 1
            elif best_gap is not None:
                pick, tier = best_gap, 2      # best available under the circumstances
            elif best_ev is not None:
                pick, tier = best_ev, 2

        if pick is not None:
            used_players.add(pick["player_key"])

        why_short, why_detail = explain_pick(
            pick, tier, manual, best_gap, best_ev, config)

        alternatives = sorted(
            (p for p in eligible if pick is None or p["prop_id"] != pick["prop_id"]),
            key=_sort_key, reverse=True,
        )[:5]

        results.append({
            "team_id": team_id,
            "team_name": team["team_name"],
            "owner": team.get("owner"),
            "pick": pick,
            "tier": tier,
            "manual": manual,
            "alternatives": alternatives,
            "why": why_short,
            "why_detail": why_detail,
            "review_cards": review_cards,
            "none_reason": None if pick else _none_reason(team, candidates, config),
            "candidate_count": len(candidates),
        })
    return results


def _label(prop: dict) -> str:
    """Short description of a prop, for use inside an explanation."""
    market = scoring.MARKET_LABELS.get(prop.get("market"), prop.get("market", ""))
    line = prop.get("line")
    name = prop.get("player_name_espn") or prop.get("player_name") or "?"
    if line is None:
        return f"{name} {market}"
    return f"{name} {market} o{line:g}"


def explain_pick(pick, tier, manual, best_gap, best_ev, config) -> tuple[str, str]:
    """(short label, full sentence) saying why this leg won its slot.

    Without this the table shows a pick changing when a slider moves and gives
    no way to tell whether that is the rule working or a bug.
    """
    if pick is None:
        return "—", ""
    if manual:
        return "Manual", "You chose this leg by hand; thresholds were not applied."

    gap_threshold = float(config["gap_threshold"])
    ev_threshold = float(config["ev_threshold"])
    ev_won = best_ev is not None and pick is best_ev

    if tier == 2:
        return "Best available", (
            f"Nothing on this roster cleared a threshold, so the best remaining "
            f"prop was taken anyway: {_label(pick)} at {pick['score']:+.1%}."
        )
    if ev_won and best_gap is not None and best_gap["score"] >= gap_threshold:
        return "EV override", (
            f"{_label(best_gap)} qualified on gap ({best_gap['score']:+.1%}), but "
            f"this prop's EV of {pick['score']:+.1%} cleared the "
            f"{ev_threshold:.0%} override threshold and took the slot."
        )
    if ev_won:
        return "EV", (
            f"No yardage prop cleared the {gap_threshold:.0%} gap threshold, and "
            f"this prop's EV of {pick['score']:+.1%} cleared the "
            f"{ev_threshold:.0%} EV threshold."
        )
    return "Gap", (
        f"Best yardage prop on this roster, and its gap of {pick['score']:+.1%} "
        f"cleared the {gap_threshold:.0%} threshold."
        + (f" The best EV prop ({_label(best_ev)}, {best_ev['score']:+.1%}) did not "
           f"reach the {ev_threshold:.0%} override threshold."
           if best_ev is not None else "")
    )


def prop_id(prop: dict) -> str:
    """Stable identifier for a prop, used by overrides and review approvals."""
    line = "" if prop.get("line") is None else f"{prop['line']:g}"
    return f"{prop.get('event_id')}|{prop.get('market')}|{prop.get('player_name')}|{line}"


def _none_reason(team: dict, candidates: list[dict], config: dict) -> str:
    """Explain an empty slot (§8 Tier 3) in the user's terms."""
    roster = team.get("players", [])
    eligible_players = [p for p in roster if p.get("eligible")]

    if not roster:
        return "no players on this roster"
    if not eligible_players:
        reasons = {p.get("exclusion_reason") for p in roster if p.get("exclusion_reason")}
        reasons.discard(None)
        if reasons and all("no game in the selected window" in r for r in reasons):
            return "every rostered player is on bye or has no game in the window"
        return ("every rostered player was filtered out: "
                + "; ".join(sorted(reasons)[:3]))
    if not candidates:
        return "props not yet posted for these players — refetch closer to game day"

    counts: dict[str, int] = {}
    for candidate in candidates:
        for code in candidate.get("exclusion_codes") or []:
            counts[code] = counts.get(code, 0) + 1

    parts = []
    if counts.get("price_floor"):
        parts.append(f"{counts['price_floor']} priced worse than the odds floor")
    if counts.get("price_ceiling"):
        parts.append(f"{counts['price_ceiling']} longer than the odds ceiling")
    if counts.get("volume_floor"):
        parts.append(f"{counts['volume_floor']} below a volume floor")
    if counts.get("sanity_ceiling"):
        parts.append(f"{counts['sanity_ceiling']} awaiting sanity review")
    if counts.get("no_line"):
        parts.append(f"{counts['no_line']} with no line posted")
    if counts.get("ev_off"):
        parts.append("EV props are off")
    if counts.get("market_off"):
        parts.append(f"{counts['market_off']} in markets you've unchecked")
    if not parts:
        return "no prop passed the filters"
    return "no prop passed the filters (" + ", ".join(parts) + ")"
