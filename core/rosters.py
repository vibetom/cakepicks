"""ESPN fantasy roster ingestion.

The league is public, so no cookies are required. The primary path is a direct
call to ESPN's read API (no third-party release cadence to depend on); the
`espn_api` package is used as a fallback if that shape ever changes.
"""

from __future__ import annotations

import requests

from .teams import ESPN_PRO_TEAM_ID, normalize_team

LEAGUE_ID = 563635
DEFAULT_YEAR = 2026

READ_HOST = "https://lm-api-reads.fantasy.espn.com"
FALLBACK_HOST = "https://fantasy.espn.com"

POSITION_BY_ID = {
    0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE",
    7: "OP", 8: "DT", 9: "DE", 10: "LB", 11: "DL", 12: "CB", 13: "S",
    14: "DB", 15: "DP", 16: "D/ST", 17: "K", 18: "P", 19: "HC",
}
# Lineup slots that are not active starters.
BENCH_SLOTS = {20, 21, 24}  # 20 = Bench, 21 = IR, 24 = taxi/other
SLOT_LABELS = dict(POSITION_BY_ID)
SLOT_LABELS.update({20: "BE", 21: "IR", 23: "FLEX", 24: "TAXI"})

# Statuses that make a player ineligible outright (§3).
HARD_OUT_STATUSES = {"OUT", "INJURY_RESERVE", "SUSPENSION", "NOT_ACTIVE", "IR"}
DOUBTFUL_STATUSES = {"DOUBTFUL"}
QUESTIONABLE_STATUSES = {"QUESTIONABLE"}


class RosterError(Exception):
    """Raised when league data cannot be loaded; the run must abort (§3)."""


def league_url(league_id: int = LEAGUE_ID, year: int = DEFAULT_YEAR) -> str:
    return f"https://fantasy.espn.com/football/league?leagueId={league_id}&seasonId={year}"


def fetch_league(league_id: int = LEAGUE_ID, year: int = DEFAULT_YEAR,
                 timeout: int = 25) -> list[dict]:
    """Return one dict per fantasy team, each with its roster.

    Raises RosterError on any failure -- §3 forbids partial results from a
    missing roster.
    """
    try:
        return _fetch_via_http(league_id, year, timeout)
    except RosterError as primary_error:
        try:
            return _fetch_via_espn_api(league_id, year)
        except Exception:
            raise primary_error


def _fetch_via_http(league_id: int, year: int, timeout: int) -> list[dict]:
    params = {"view": ["mTeam", "mRoster", "mSettings"]}
    urls = [
        f"{READ_HOST}/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{league_id}",
        f"{FALLBACK_HOST}/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{league_id}",
    ]
    headers = {"User-Agent": "Mozilla/5.0 (compatible; league-parlay-bot)"}
    last_error = None
    for url in urls:
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = f"network error: {exc}"
            continue
        if response.status_code == 401:
            last_error = "ESPN returned 401 — the league is not public for this season."
            continue
        if not response.ok:
            last_error = f"ESPN returned HTTP {response.status_code}"
            continue
        try:
            data = response.json()
        except ValueError:
            last_error = "ESPN returned a non-JSON response"
            continue
        if isinstance(data, list):
            data = data[0] if data else {}
        teams = _parse_league_payload(data)
        if teams:
            return teams
        last_error = "ESPN returned no teams for this league/season"
    raise RosterError(
        f"Could not load ESPN league {league_id} for {year} ({last_error}). "
        f"Check the league is public and the season has started: {league_url(league_id, year)}"
    )


def _parse_league_payload(data: dict) -> list[dict]:
    teams = []
    members = {m.get("id"): m for m in (data.get("members") or [])}
    for team in data.get("teams") or []:
        owner_name = None
        owner_ids = team.get("owners") or []
        if owner_ids:
            member = members.get(owner_ids[0]) or {}
            first = (member.get("firstName") or "").strip()
            last = (member.get("lastName") or "").strip()
            owner_name = (f"{first} {last}".strip()
                          or member.get("displayName") or None)

        name = (team.get("name")
                or f"{(team.get('location') or '').strip()} {(team.get('nickname') or '').strip()}".strip()
                or team.get("abbrev")
                or f"Team {team.get('id')}")

        players = []
        entries = ((team.get("roster") or {}).get("entries")) or []
        for entry in entries:
            player = _parse_roster_entry(entry, team.get("id"))
            if player:
                players.append(player)
        teams.append({
            "team_id": team.get("id"),
            "team_name": name.strip(),
            "abbrev": team.get("abbrev"),
            "owner": owner_name,
            "players": players,
        })
    return teams


def _parse_roster_entry(entry: dict, fantasy_team_id) -> dict | None:
    pool = entry.get("playerPoolEntry") or {}
    player = pool.get("player") or entry.get("player") or {}
    if not player:
        return None
    slot_id = entry.get("lineupSlotId")
    pro_team_id = player.get("proTeamId")
    return {
        "player_id": player.get("id"),
        "name": player.get("fullName") or player.get("name") or "",
        "pro_team_id": pro_team_id,
        "nfl_team": normalize_team(pro_team_id),
        "position": POSITION_BY_ID.get(player.get("defaultPositionId"), "?"),
        "lineup_slot_id": slot_id,
        "lineup_slot": SLOT_LABELS.get(slot_id, str(slot_id)),
        "injury_status": (player.get("injuryStatus") or "ACTIVE").upper(),
        "injured": bool(player.get("injured")),
        "fantasy_team_id": fantasy_team_id,
    }


def _fetch_via_espn_api(league_id: int, year: int) -> list[dict]:
    """Fallback path using the third-party espn_api package."""
    from espn_api.football import League  # imported lazily; optional dependency

    league = League(league_id=league_id, year=year)
    teams = []
    for team in league.teams:
        players = []
        for player in team.roster:
            players.append({
                "player_id": getattr(player, "playerId", None),
                "name": getattr(player, "name", ""),
                "pro_team_id": None,
                "nfl_team": normalize_team(getattr(player, "proTeam", None)),
                "position": getattr(player, "position", "?"),
                "lineup_slot_id": None,
                "lineup_slot": getattr(player, "lineupSlot", ""),
                "injury_status": (getattr(player, "injuryStatus", None) or "ACTIVE").upper(),
                "injured": bool(getattr(player, "injured", False)),
                "fantasy_team_id": getattr(team, "team_id", None),
            })
        teams.append({
            "team_id": getattr(team, "team_id", None),
            "team_name": getattr(team, "team_name", "") or f"Team {getattr(team, 'team_id', '?')}",
            "abbrev": getattr(team, "team_abbrev", None),
            "owner": getattr(team, "owner", None),
            "players": players,
        })
    if not teams:
        raise RosterError("espn_api returned no teams")
    return teams


def filter_players(teams: list[dict], playing_teams: set[str], *,
                   starters_only: bool = False,
                   exclude_questionable: bool = False,
                   exclude_doubtful: bool = True) -> list[dict]:
    """Apply the §3 hard filters, annotating every player with a reason.

    `playing_teams` is the set of NFL team codes with a game in the selected
    window; anyone outside it is ineligible (bye week, or a game outside the
    window). Returns the same structure with `eligible` and `exclusion_reason`
    set on each player.
    """
    out = []
    for team in teams:
        players = []
        for player in team["players"]:
            reason = None
            status = player.get("injury_status", "ACTIVE")
            position = player.get("position", "?")
            slot_id = player.get("lineup_slot_id")

            if position in {"D/ST", "K", "HC", "P"}:
                reason = f"{position} has no player props"
            elif not player.get("nfl_team"):
                reason = "no NFL team (free agent)"
            elif starters_only and slot_id is not None and slot_id in BENCH_SLOTS:
                reason = f"bench/IR slot ({player.get('lineup_slot')}) and starters-only is on"
            elif status in HARD_OUT_STATUSES:
                reason = f"injury status {status}"
            elif exclude_doubtful and status in DOUBTFUL_STATUSES:
                reason = "injury status DOUBTFUL"
            elif exclude_questionable and status in QUESTIONABLE_STATUSES:
                reason = "injury status QUESTIONABLE (excluded by toggle)"
            elif player["nfl_team"] not in playing_teams:
                reason = f"{player['nfl_team']} has no game in the selected window (bye or outside window)"

            players.append({
                **player,
                "eligible": reason is None,
                "exclusion_reason": reason,
                "questionable": status in QUESTIONABLE_STATUSES,
            })
        out.append({**team, "players": players})
    return out


def roster_entries(teams: list[dict], eligible_only: bool = True) -> dict:
    """Matcher entries: (fantasy_team_id, player_id) -> (name, nfl_team)."""
    entries = {}
    for team in teams:
        for player in team["players"]:
            if eligible_only and not player.get("eligible", True):
                continue
            key = (team["team_id"], player["player_id"])
            entries[key] = (player["name"], player.get("nfl_team"))
    return entries


def player_lookup(teams: list[dict]) -> dict:
    """(fantasy_team_id, player_id) -> the player dict."""
    return {
        (team["team_id"], player["player_id"]): player
        for team in teams for player in team["players"]
    }
