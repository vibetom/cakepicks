"""Where is PrizePicks off the sportsbook market for one game?

A standalone one-off — it imports nothing from this repo and touches neither
app. Run it with your Odds API key and a team name:

    python tools/dfs_gap.py --team Detroit

Two different things get reported, and conflating them is the usual mistake:

**Line gaps (middles).** PrizePicks posts 45.5 while the books post 52.5. Take
More on PrizePicks and Under at the book: both win if the result lands in
between, and one refunds the other if it doesn't. This is the opportunity that
actually shows up, and it is a *middle*, not an arb — you can lose the vig on
both sides if the result misses the window.

**Same-line arbitrage.** PrizePicks and a book on the same number, with prices
whose implied probabilities sum below 100%. Reported separately because it is
the only one that's risk-free, and on a fixed-multiplier product it is close to
nonexistent (see the warning the script prints).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"

#: Markets with a genuine book equivalent. PrizePicks stat types with no book
#: counterpart (Fantasy Score, Pass+Rush Yards) are skipped rather than guessed.
MARKETS = [
    "player_pass_yds", "player_pass_tds", "player_rush_yds",
    "player_reception_yds", "player_receptions", "player_rush_attempts",
]

DFS_BOOKS = {"prizepicks", "underdog", "sleeper", "betr_picks", "dabble_au"}


def american_to_decimal(price: float) -> float:
    return 1 + (price / 100 if price > 0 else 100 / abs(price))


def implied(price: float) -> float:
    return 1 / american_to_decimal(price)


def get(path: str, params: dict) -> tuple[object, dict]:
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            body = json.load(response)
            quota = {k: response.headers.get(k) for k in (
                "x-requests-used", "x-requests-remaining", "x-requests-last")}
            return body, quota
    except urllib.error.HTTPError as exc:
        sys.exit(f"Odds API {exc.code}: {exc.read().decode()[:300]}")


def find_event(key: str, team: str) -> dict:
    events, _ = get(f"/sports/{SPORT}/events", {"apiKey": key})
    needle = team.lower()
    hits = [e for e in events
            if needle in e["home_team"].lower() or needle in e["away_team"].lower()]
    if not hits:
        upcoming = "\n".join(
            f"  {e['commence_time']}  {e['away_team']} @ {e['home_team']}"
            for e in events[:12])
        sys.exit(f"No upcoming game matches {team!r}. Next up:\n{upcoming}")
    return sorted(hits, key=lambda e: e["commence_time"])[0]


def collect(payload: dict) -> dict:
    """{(player, market, side): [{book, line, price}, ...]}"""
    quotes: dict = {}
    for book in payload.get("bookmakers", []):
        for market in book.get("markets", []):
            if market["key"] not in MARKETS:
                continue
            for outcome in market.get("outcomes", []):
                player = outcome.get("description")
                side = outcome.get("name")
                if not player or side not in ("Over", "Under"):
                    continue
                quotes.setdefault((player, market["key"], side), []).append({
                    "book": book["key"],
                    "line": outcome.get("point"),
                    "price": outcome.get("price"),
                })
    return quotes


def split(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    return ([e for e in entries if e["book"] in DFS_BOOKS],
            [e for e in entries if e["book"] not in DFS_BOOKS])


def line_gaps(quotes: dict, minimum: float) -> list[dict]:
    """PrizePicks' number against the books' consensus number."""
    out = []
    for (player, market, side), entries in quotes.items():
        if side != "Over":              # the line is the same on both sides
            continue
        dfs, books = split(entries)
        pp = next((e for e in dfs if e["book"] == "prizepicks"), None)
        if pp is None or pp["line"] is None:
            continue
        book_lines = sorted(e["line"] for e in books if e["line"] is not None)
        if not book_lines:
            continue
        middle = book_lines[len(book_lines) // 2]
        gap = middle - pp["line"]
        if abs(gap) < minimum:
            continue
        out.append({
            "player": player, "market": market,
            "pp_line": pp["line"], "book_line": middle, "gap": gap,
            "books": len(book_lines),
            "spread": f"{book_lines[0]:g}–{book_lines[-1]:g}",
            # A PrizePicks line BELOW the books favours More on PrizePicks.
            "take": "More on PrizePicks / Under at the book" if gap > 0
                    else "Less on PrizePicks / Over at the book",
        })
    return sorted(out, key=lambda r: -abs(r["gap"]))


def arbs(quotes: dict) -> list[dict]:
    """Genuine same-line, two-sided arbitrage. Rare to the point of absent."""
    out = []
    for (player, market, side), entries in quotes.items():
        dfs, _ = split(entries)
        pp = next((e for e in dfs if e["book"] == "prizepicks"), None)
        if pp is None or pp["price"] is None or pp["line"] is None:
            continue
        other = "Under" if side == "Over" else "Over"
        _, books = split(quotes.get((player, market, other), []))
        same = [e for e in books
                if e["line"] == pp["line"] and e["price"] is not None]
        if not same:
            continue
        best = max(same, key=lambda e: american_to_decimal(e["price"]))
        total = implied(pp["price"]) + implied(best["price"])
        if total >= 1.0:
            continue
        out.append({
            "player": player, "market": market, "line": pp["line"],
            "pp_side": side, "pp_price": pp["price"],
            "book": best["book"], "book_price": best["price"],
            "margin": (1 - total),
        })
    return sorted(out, key=lambda r: -r["margin"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", help="Odds API key (or set ODDS_API_KEY)")
    parser.add_argument("--team", required=True, help="Either team, e.g. Detroit")
    parser.add_argument("--min-gap", type=float, default=1.5,
                        help="Smallest line difference worth printing")
    parser.add_argument("--markets", default=",".join(MARKETS))
    args = parser.parse_args()

    import os
    key = args.key or os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        sys.exit("No API key. Pass --key or set ODDS_API_KEY.")

    event = find_event(key, args.team)
    print(f"{event['away_team']} @ {event['home_team']}  ({event['commence_time']})\n")

    payload, quota = get(f"/sports/{SPORT}/events/{event['id']}/odds", {
        "apiKey": key,
        "regions": "us,us_dfs",
        "markets": args.markets,
        "oddsFormat": "american",
    })

    books = [b["key"] for b in payload.get("bookmakers", [])]
    print(f"Books returned ({len(books)}): {', '.join(books) or 'none'}")
    print(f"Credits — used {quota.get('x-requests-used')}, "
          f"this call {quota.get('x-requests-last')}, "
          f"remaining {quota.get('x-requests-remaining')}\n")

    if "prizepicks" not in books:
        sys.exit(
            "PrizePicks was not in the response. Either your plan doesn't "
            "include the us_dfs region, or they haven't posted this game yet."
        )

    quotes = collect(payload)

    found = arbs(quotes)
    print("== Same-line arbitrage ==")
    if not found:
        print("  None — which is the expected result. See the note below.\n")
    for row in found:
        print(f"  {row['margin']:.2%}  {row['player']} {row['market']} "
              f"{row['line']:g}: {row['pp_side']} {row['pp_price']:+.0f} on "
              f"PrizePicks vs {row['book']} {row['book_price']:+.0f}")
    print()

    gaps = line_gaps(quotes, args.min_gap)
    print(f"== Line gaps of {args.min_gap:g}+ (middles) ==")
    if not gaps:
        print("  PrizePicks is within a rounding error of the books on every "
              "prop here.")
    for row in gaps:
        print(f"  {row['gap']:+5.1f}  {row['player']:<24} {row['market']:<22} "
              f"PP {row['pp_line']:<6g} books {row['book_line']:g} "
              f"(range {row['spread']}, n={row['books']})")
        print(f"         → {row['take']}")

    print(
        "\nBefore acting on any of this:\n"
        "  * The Odds API states DFS odds are INDICATIVE ONLY and vary with the\n"
        "    user's own selections. A gap here can be a stale or mis-keyed feed\n"
        "    rather than a real number. Confirm it in the PrizePicks app.\n"
        "  * PrizePicks needs 2+ picks on a standard entry, so a single leg\n"
        "    cannot be hedged 1:1 against a book. Sizing a genuine arb around a\n"
        "    multi-pick fixed multiplier is a different exercise from the\n"
        "    two-way arb this script checks for.\n"
        "  * A gap usually means the two sides know different things — an\n"
        "    injury or a snap-count report one of them hasn't priced. Check the\n"
        "    news before assuming the books are the ones who are wrong."
    )


if __name__ == "__main__":
    main()
