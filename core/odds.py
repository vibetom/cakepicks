"""Odds ingestion, behind a provider interface so the vendor can be swapped.

Only the-odds-api.com (the hyphenated domain) is implemented. Scoring and
selection never import this module's concrete class -- they consume the plain
dicts produced by parse_event_props().
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

import requests

from .scoring import MARKET_LABELS
from .teams import normalize_team

API_BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "americanfootball_nfl"
BOOKMAKER = "fanduel"

# Main lines only. Never request alternate_* keys: those are a different
# market shape and would blow through the API quota.
DEFAULT_MARKETS = [
    "player_reception_yds",
    "player_rush_yds",
    "player_pass_yds",
    "player_rush_attempts",
    "player_receptions",
    "player_anytime_td",
    "player_pass_tds",
]


class OddsError(Exception):
    """Any failure talking to the odds vendor."""


class OddsProvider(ABC):
    """Minimal surface the rest of the app depends on."""

    @abstractmethod
    def get_week_events(self, start=None, end=None) -> list[dict]:
        ...

    @abstractmethod
    def get_event_props(self, event_id: str, markets: list[str]) -> dict:
        ...


class TheOddsAPI(OddsProvider):
    """v4 REST client for the-odds-api.com."""

    def __init__(self, api_key: str, timeout: int = 25):
        if not api_key:
            raise OddsError("No API key. Set ODDS_API_KEY (see the README).")
        self.api_key = api_key
        self.timeout = timeout
        # Populated from response headers so the UI can show remaining credits.
        self.quota = {"remaining": None, "used": None, "last_cost": None}

    def _get(self, url: str, params: dict) -> object:
        params = dict(params)
        params["apiKey"] = self.api_key
        try:
            response = requests.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise OddsError(f"Network error calling the odds API: {exc}") from exc

        for header, key in (
            ("x-requests-remaining", "remaining"),
            ("x-requests-used", "used"),
            ("x-requests-last", "last_cost"),
        ):
            if header in response.headers:
                try:
                    self.quota[key] = int(float(response.headers[header]))
                except ValueError:
                    pass

        if response.status_code == 401:
            raise OddsError("Odds API rejected the key (401). Check ODDS_API_KEY.")
        if response.status_code == 422:
            raise OddsError(f"Odds API rejected the request (422): {response.text[:300]}")
        if response.status_code == 429:
            raise OddsError("Odds API quota exhausted or rate limited (429).")
        if not response.ok:
            raise OddsError(f"Odds API error {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise OddsError(f"Odds API returned non-JSON: {response.text[:200]}") from exc

    def get_week_events(self, start=None, end=None) -> list[dict]:
        """This week's NFL games. The events endpoint costs 0 credits."""
        params = {}
        if start:
            params["commenceTimeFrom"] = _iso_z(start)
        if end:
            params["commenceTimeTo"] = _iso_z(end)
        data = self._get(f"{API_BASE}/sports/{SPORT_KEY}/events", params)
        if not isinstance(data, list):
            raise OddsError(f"Unexpected events payload: {str(data)[:200]}")
        return [_normalize_event(e) for e in data]

    def get_event_props(self, event_id: str, markets: list[str]) -> dict:
        """Raw FanDuel props payload for one event.

        Quota cost is roughly one credit per market per region, so this is the
        expensive call -- the caller caches the result.
        """
        params = {
            "regions": "us",
            "bookmakers": BOOKMAKER,
            "oddsFormat": "american",
            "includeLinks": "true",
            "includeSids": "true",
            "markets": ",".join(markets),
        }
        data = self._get(f"{API_BASE}/sports/{SPORT_KEY}/events/{event_id}/odds", params)
        if not isinstance(data, dict):
            raise OddsError(f"Unexpected odds payload for {event_id}: {str(data)[:200]}")
        return data


def _iso_z(value) -> str:
    """The API wants ISO8601 in UTC with a literal Z and no microseconds."""
    if isinstance(value, str):
        return value
    if isinstance(value, dt.datetime):
        moment = value.astimezone(dt.timezone.utc) if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
        return moment.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, dt.date):
        return f"{value.isoformat()}T00:00:00Z"
    raise ValueError(f"Cannot format {value!r} as an API timestamp")


def parse_commence_time(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_event(event: dict) -> dict:
    home = event.get("home_team")
    away = event.get("away_team")
    return {
        "id": event.get("id"),
        "commence_time": event.get("commence_time"),
        "home_team": home,
        "away_team": away,
        "home_code": normalize_team(home),
        "away_code": normalize_team(away),
    }


def event_team_codes(event: dict) -> list[str]:
    """The two canonical team codes playing in an event."""
    return [c for c in (event.get("home_code"), event.get("away_code")) if c]


def parse_event_props(payload: dict, event: dict | None = None) -> list[dict]:
    """Flatten one event-odds payload into a list of OVER-side prop dicts.

    Yardage / receptions / pass-TD markets return Over and Under outcomes with
    `description` = player name, `point` = line and `price` = American odds; we
    keep only the Over side. `player_anytime_td` returns a per-player outcome
    with no point, treated as "Yes at price". Both outcome shapes are handled
    defensively because the vendor is not perfectly consistent across markets.
    """
    props: list[dict] = []
    if not payload:
        return props

    event = event or {}
    event_id = payload.get("id") or event.get("id")
    home = payload.get("home_team") or event.get("home_team")
    away = payload.get("away_team") or event.get("away_team")
    commence = payload.get("commence_time") or event.get("commence_time")
    teams = [c for c in (normalize_team(home), normalize_team(away)) if c]

    for bookmaker in payload.get("bookmakers") or []:
        if bookmaker.get("key") != BOOKMAKER:
            continue
        book_link = bookmaker.get("link")
        for market in bookmaker.get("markets") or []:
            market_key = market.get("key")
            market_link = market.get("link")
            for outcome in market.get("outcomes") or []:
                parsed = _parse_outcome(outcome, market_key)
                if parsed is None:
                    continue
                parsed.update({
                    "event_id": event_id,
                    "home_team": home,
                    "away_team": away,
                    "event_teams": teams,
                    "commence_time": commence,
                    "market": market_key,
                    "market_label": MARKET_LABELS.get(market_key, market_key),
                    "last_update": market.get("last_update") or bookmaker.get("last_update"),
                    "link": parsed.get("link") or market_link or book_link,
                })
                props.append(parsed)
    return props


def _parse_outcome(outcome: dict, market_key: str) -> dict | None:
    """Return an Over-side prop dict, or None if this outcome is not one."""
    name = (outcome.get("name") or "").strip()
    description = (outcome.get("description") or "").strip()
    price = outcome.get("price")
    if price is None:
        return None

    if market_key == "player_anytime_td":
        # Usually name="Yes"/description=player; occasionally name=player.
        side = name.lower()
        if side in {"no", "under"}:
            return None
        player = description or name
        if not player or player.lower() in {"yes", "over"}:
            return None
        line = None
    else:
        # Over/Under markets: the player is in `description`.
        if name.lower() != "over":
            return None
        player = description
        if not player:
            return None
        line = outcome.get("point")
        if line is None:
            return None

    return {
        "player_name": player,
        "line": None if line is None else float(line),
        "price": float(price),
        "link": outcome.get("link"),
        "sid": outcome.get("sid"),
        "market_sid": outcome.get("market_sid"),
    }


def market_coverage(events: list[dict], raw_by_event: dict, markets: list[str]) -> dict:
    """Per-market count of events that actually posted that market.

    Books post some props late in the week, so the UI surfaces this as a note
    ("rush attempts: 9/14 games posted") rather than failing.
    """
    coverage = {m: 0 for m in markets}
    total = 0
    for event in events:
        payload = raw_by_event.get(event["id"])
        if not payload:
            continue
        total += 1
        posted = set()
        for bookmaker in payload.get("bookmakers") or []:
            if bookmaker.get("key") != BOOKMAKER:
                continue
            for market in bookmaker.get("markets") or []:
                posted.add(market.get("key"))
        for key in posted:
            if key in coverage:
                coverage[key] += 1
    return {"total_events": total, "by_market": coverage}


def default_window(now: dt.datetime | None = None) -> tuple[dt.datetime, dt.datetime]:
    """Default event window: now through the end of the current NFL week.

    An NFL week runs Thursday night through Monday night, so the window closes
    at the following Tuesday 12:00 UTC — late enough to include a Monday night
    game (which starts Tuesday in UTC) and early enough to exclude next
    Thursday's opener.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    days_ahead = (1 - now.weekday()) % 7          # Monday is 0, so Tuesday is 1
    end = (now + dt.timedelta(days=days_ahead)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    if end <= now:
        end += dt.timedelta(days=7)
    return now, end


def filter_events(events: list[dict], start: dt.datetime, end: dt.datetime) -> list[dict]:
    """Events kicking off inside [start, end].

    Games that have already started are excluded by the window's lower bound
    (§13), since the default start is "now".
    """
    out = []
    for event in events:
        moment = parse_commence_time(event.get("commence_time"))
        if moment is None:
            continue
        if start <= moment <= end:
            out.append(event)
    return out


def playing_team_codes(events: list[dict]) -> set[str]:
    """NFL teams with a game inside the window. Everyone else is on bye."""
    codes = set()
    for event in events:
        codes.update(event_team_codes(event))
    return codes
