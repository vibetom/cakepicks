"""Every alternate "over" line for one player's prop, as a FanDuel betslip link.

A standalone one-off, like dfs_gap.py: it touches neither app and needs nothing
beyond Python and this repo. Run it from the repo root:

    python tools/alt_ladder.py --player "Skyler Bell" --team Bills

It fetches FanDuel's standard and alternate lines for the player's game, lists
every Over with its price, and prints one link that loads them all onto a
betslip, plus a per-line link for each.

Cost: one request per market per region -- 2 credits with the defaults. The
actual figure is printed from the response headers.

Things a link cannot do, stated in the output as well:

* **It cannot set a stake.** FanDuel's addToBetslip URL carries only market
  and selection IDs. Enter the $5 on each line on the slip itself.
* **These go on as straight bets, not a parlay.** Several lines on the same
  player and stat are correlated, and FanDuel will not combine them; pick
  "singles" on the slip.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.betslip import build_parlay_url, leg_ids  # noqa: E402  (stdlib-only modules)
from core.ladder import ladder, to_win  # noqa: E402
from core.ladder import MARKETS as LADDER_MARKETS  # noqa: E402

API = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"

#: CLI slugs for the shared stat names.
MARKETS = {name.lower().replace(" ", "-"): keys for name, keys in LADDER_MARKETS.items()}


def get(path: str, params: dict) -> tuple[object, dict]:
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            quota = {k: response.headers.get(k) for k in (
                "x-requests-used", "x-requests-remaining", "x-requests-last")}
            return json.load(response), quota
    except urllib.error.HTTPError as exc:
        sys.exit(f"Odds API {exc.code}: {exc.read().decode()[:300]}")


def find_event(key: str, team: str) -> dict:
    events, _ = get(f"/sports/{SPORT}/events", {"apiKey": key})
    needle = team.lower()
    hits = sorted((e for e in events
                   if needle in e["home_team"].lower() or needle in e["away_team"].lower()),
                  key=lambda e: e["commence_time"])
    if not hits:
        sys.exit(f"No upcoming game matches {team!r}.")
    return hits[0]


def fmt(price) -> str:
    return f"{price:+d}" if isinstance(price, int) else f"{price:+.0f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--player", required=True)
    parser.add_argument("--team", help="Either team in the game, e.g. Bills")
    parser.add_argument("--stat", default="receiving-yards", choices=sorted(MARKETS))
    parser.add_argument("--stake", type=float, default=5.0)
    parser.add_argument("--key", help="Odds API key (or set ODDS_API_KEY)")
    parser.add_argument("--payload", help=argparse.SUPPRESS)   # offline testing
    args = parser.parse_args()
    markets = MARKETS[args.stat]

    if args.payload:
        payload, quota = json.loads(Path(args.payload).read_text()), {}
    else:
        key = args.key or os.environ.get("ODDS_API_KEY", "").strip()
        if not key or not args.team:
            sys.exit("Pass --team and an API key (--key or ODDS_API_KEY).")
        event = find_event(key, args.team)
        print(f"{event['away_team']} @ {event['home_team']}  ({event['commence_time']})")
        payload, quota = get(f"/sports/{SPORT}/events/{event['id']}/odds", {
            "apiKey": key, "bookmakers": "fanduel", "markets": ",".join(markets),
            "oddsFormat": "american", "includeLinks": "true", "includeSids": "true",
        })
        print(f"Credits: this call {quota.get('x-requests-last')}, "
              f"remaining {quota.get('x-requests-remaining')}\n")

    legs = ladder(payload, args.player, markets)
    if not legs:
        sys.exit(f"FanDuel has no {args.stat} Overs posted for {args.player} yet. "
                 "Props often go up only on game day -- try again closer to kickoff.")

    print(f"{legs[0]['player']} — {args.stat.replace('-', ' ')}, every Over FanDuel offers:\n")
    usable = [leg for leg in legs if leg_ids(leg)]
    for leg in legs:
        wins = to_win(args.stake, leg["price"])
        flag = "" if leg_ids(leg) else "   (no betslip ID — add by hand)"
        print(f"  Over {leg['line']:<6g} {fmt(leg['price']):>6}   "
              f"${args.stake:.0f} wins ${wins:,.2f}{flag}")

    print(f"\n{len(legs)} bets × ${args.stake:.0f} = ${len(legs) * args.stake:,.0f} total.\n")
    url = build_parlay_url(usable)
    if url:
        print("One link for all of them:\n")
        print(url + "\n")
    print("Or one at a time (each adds to the slip):")
    for leg in usable:
        print(f"  Over {leg['line']:g}: {leg['outcome_link']}")
    print(
        "\nOn the slip: choose singles, not a parlay -- lines on the same player\n"
        "and stat can't be combined -- and enter the stake on each. The link\n"
        "can't carry a stake. If the one-link version only loads some lines,\n"
        "use the per-line links."
    )


if __name__ == "__main__":
    main()
