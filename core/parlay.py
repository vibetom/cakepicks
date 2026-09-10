"""Combined parlay odds, payout, and the share block for the group chat."""

from __future__ import annotations

from . import scoring


def describe_prop(prop: dict) -> str:
    """Human label for a leg: "Rec Yds o61.5" or "Anytime TD"."""
    label = prop.get("market_label") or prop.get("market", "")
    line = prop.get("line")
    if prop.get("market") == "player_anytime_td" or line is None:
        return label
    return f"{label} o{line:g}"


def score_text(prop: dict) -> str:
    """Score rendered per its kind: "Gap +14.2%" or "EV +23.1%"."""
    score = prop.get("score")
    if score is None:
        return "—"
    prefix = "Gap" if prop.get("kind") == "gap" else "EV"
    return f"{prefix} {score:+.1%}"


def combine(picks: list[dict], stake: float) -> dict:
    """Parlay math over the filled legs only.

    NONE slots are simply absent, so the summary reports the reduced leg count
    rather than pretending the parlay is 10 legs (§8).
    """
    legs = [row["pick"] for row in picks if row.get("pick")]
    empty = [row for row in picks if not row.get("pick")]

    decimal = 1.0
    for leg in legs:
        decimal *= scoring.american_to_decimal(leg["price"])

    payout = stake * decimal if legs else 0.0
    return {
        "leg_count": len(legs),
        "empty_count": len(empty),
        "empty_teams": [row["team_name"] for row in empty],
        "decimal_odds": decimal if legs else None,
        "american_odds": scoring.decimal_to_american(decimal) if legs and decimal > 1 else None,
        "implied_probability": (1.0 / decimal) if legs and decimal > 1 else None,
        "stake": stake,
        "payout": payout,
        "profit": payout - stake if legs else 0.0,
    }


def share_text(picks: list[dict], summary: dict, *,
               title: str = "This week's league parlay",
               parlay_url: str | None = None) -> str:
    """Plain-text block for the group chat, one line per leg.

    The betslip URL goes in here deliberately: whoever places the bet is often
    not the person running the app, and this block is what reaches them.
    """
    lines = [title, ""]
    for row in picks:
        prop = row.get("pick")
        if not prop:
            lines.append(f"{row['team_name']} — NONE ({row.get('none_reason') or 'no qualifying prop'})")
            continue
        flags = ""
        if row.get("manual"):
            flags += " [manual]"
        if prop.get("questionable"):
            flags += " [Q]"
        lines.append(
            f"{row['team_name']} — {prop.get('player_name_espn') or prop['player_name']} "
            f"{describe_prop(prop)} ({scoring.format_american(prop['price'])}){flags}"
        )

    lines.append("")
    if summary["leg_count"]:
        american = summary["american_odds"]
        lines.append(
            f"{summary['leg_count']}-leg parlay: {scoring.format_american(american)} "
            f"({summary['decimal_odds']:.2f} decimal)"
        )
        lines.append(
            f"${summary['stake']:,.2f} returns ${summary['payout']:,.2f} "
            f"(${summary['profit']:,.2f} profit)"
        )
    else:
        lines.append("No legs qualified this week.")
    if summary["empty_count"]:
        lines.append(f"Empty slots: {', '.join(summary['empty_teams'])}")
    if parlay_url:
        lines.append("")
        lines.append("Tap to load the whole parlay on FanDuel:")
        lines.append(parlay_url)
    return "\n".join(lines)
