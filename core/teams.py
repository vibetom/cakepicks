"""Canonical NFL team codes and normalization across ESPN / PFF / The Odds API.

The three data sources spell teams differently:
  * ESPN uses numeric proTeamId and abbreviations like WSH, LAR, JAX.
  * PFF uses ARZ, BLT, CLV, HST, LA, and a few other oddities.
  * The Odds API uses full display names ("Kansas City Chiefs").

Everything in the app is normalized to the ESPN-style code in CANONICAL.
"""

from __future__ import annotations

CANONICAL = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
    "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
    "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WSH",
]

# ESPN's proTeamId -> canonical code. 0 is free agent; 31/32 are unused.
ESPN_PRO_TEAM_ID = {
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR",
    15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI",
    22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WSH",
    29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}

# Full display names, as returned by The Odds API.
FULL_NAME = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys", "DEN": "Denver Broncos",
    "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars", "KC": "Kansas City Chiefs",
    "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings", "NE": "New England Patriots",
    "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers", "SEA": "Seattle Seahawks",
    "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WSH": "Washington Commanders",
}

# Every alternate spelling we have seen, lowercased, -> canonical.
# Covers PFF abbreviations, ESPN variants, Odds API full names and common aliases.
_ALIASES: dict[str, str] = {}


def _register(code: str, *spellings: str) -> None:
    _ALIASES[code.lower()] = code
    for s in spellings:
        _ALIASES[s.lower()] = code


for _code in CANONICAL:
    _register(_code, FULL_NAME[_code], FULL_NAME[_code].split()[-1])

# PFF-specific and other historical/alternate abbreviations.
_register("ARI", "ARZ")
_register("BAL", "BLT")
_register("CLE", "CLV")
_register("HOU", "HST")
_register("LAR", "LA", "STL", "RAM", "LA Rams", "Rams")
_register("LAC", "SD", "SDG", "LA Chargers", "Chargers")
_register("LV", "OAK", "LVR", "Raiders", "Las Vegas")
_register("JAX", "JAC")
_register("WSH", "WAS", "WFT", "Washington Football Team", "Washington")
_register("GB", "GNB", "Green Bay")
_register("KC", "KAN", "Kansas City")
_register("NE", "NWE", "New England")
_register("NO", "NOR", "New Orleans")
_register("SF", "SFO", "San Francisco", "49ers", "Niners")
_register("TB", "TAM", "Tampa Bay")
_register("NYG", "New York Giants", "Giants")
_register("NYJ", "New York Jets", "Jets")
# "Giants"/"Jets" nickname collisions are resolved above by explicit registration.


def normalize_team(value: object) -> str | None:
    """Return the canonical code for any team spelling, or None if unknown.

    Accepts abbreviations, full names, nicknames, and ESPN numeric proTeamIds.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return ESPN_PRO_TEAM_ID.get(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return ESPN_PRO_TEAM_ID.get(int(text))
    return _ALIASES.get(text.lower())


def full_name(code: str) -> str:
    """Display name for a canonical code, falling back to the code itself."""
    return FULL_NAME.get(code, code)
