"""Shared fixtures: a miniature league, projections file, and odds payload."""

import io
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import DEFAULTS  # noqa: E402
from core.matching import AliasStore  # noqa: E402
from core.projections import load_projections  # noqa: E402
from core.store import LocalStore  # noqa: E402


@pytest.fixture
def config():
    return dict(DEFAULTS)


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path)


@pytest.fixture
def aliases(store):
    return AliasStore(store)


@pytest.fixture
def events():
    return [
        {"id": "evt-cin-ne", "commence_time": "2026-09-13T17:00:00Z",
         "home_team": "Cincinnati Bengals", "away_team": "New England Patriots",
         "home_code": "CIN", "away_code": "NE"},
        {"id": "evt-gb-chi", "commence_time": "2026-09-13T20:25:00Z",
         "home_team": "Green Bay Packers", "away_team": "Chicago Bears",
         "home_code": "GB", "away_code": "CHI"},
    ]


def _outcome(name, description, price, point=None, sid=None):
    outcome = {"name": name, "description": description, "price": price}
    if point is not None:
        outcome["point"] = point
    if sid:
        outcome["sid"] = sid
    return outcome


@pytest.fixture
def raw_by_event():
    """FanDuel payloads for two games, covering every market shape."""
    cin_ne = {
        "id": "evt-cin-ne", "commence_time": "2026-09-13T17:00:00Z",
        "home_team": "Cincinnati Bengals", "away_team": "New England Patriots",
        "bookmakers": [{
            "key": "fanduel", "title": "FanDuel", "last_update": "2026-09-12T12:00:00Z",
            "markets": [
                {"key": "player_reception_yds", "sid": "mkt-player_reception_yds", "outcomes": [
                    _outcome("Over", "Ja'Marr Chase", -110, 74.5, "sid-chase-recyds"),
                    _outcome("Under", "Ja'Marr Chase", -110, 74.5),
                    _outcome("Over", "Kayshon Boutte", -114, 38.5, "sid-boutte-recyds"),
                ]},
                {"key": "player_pass_yds", "sid": "mkt-player_pass_yds", "outcomes": [
                    _outcome("Over", "Joe Burrow", -108, 248.5, "sid-burrow-passyds"),
                ]},
                {"key": "player_receptions", "sid": "mkt-player_receptions", "outcomes": [
                    # Priced below a -110 floor on purpose (acceptance check 4).
                    _outcome("Over", "Ja'Marr Chase", -155, 4.5, "sid-chase-recs"),
                    _outcome("Over", "Kayshon Boutte", -105, 1.5, "sid-boutte-recs"),
                ]},
                {"key": "player_anytime_td", "sid": "mkt-player_anytime_td", "outcomes": [
                    _outcome("Yes", "Ja'Marr Chase", 110, None, "sid-chase-td"),
                    _outcome("No", "Ja'Marr Chase", -140),
                ]},
                {"key": "player_pass_tds", "sid": "mkt-player_pass_tds", "outcomes": [
                    _outcome("Over", "Joe Burrow", 100, 1.5, "sid-burrow-passtds"),
                ]},
            ],
        }],
    }
    gb_chi = {
        "id": "evt-gb-chi", "commence_time": "2026-09-13T20:25:00Z",
        "home_team": "Green Bay Packers", "away_team": "Chicago Bears",
        "bookmakers": [{
            "key": "fanduel", "title": "FanDuel", "last_update": "2026-09-12T12:00:00Z",
            "markets": [
                {"key": "player_rush_yds", "sid": "mkt-player_rush_yds", "outcomes": [
                    _outcome("Over", "Josh Jacobs", -108, 62.5, "sid-jacobs-rushyds"),
                ]},
                {"key": "player_rush_attempts", "sid": "mkt-player_rush_attempts", "outcomes": [
                    _outcome("Over", "Josh Jacobs", -120, 15.5, "sid-jacobs-rushatt"),
                ]},
                {"key": "player_anytime_td", "sid": "mkt-player_anytime_td", "outcomes": [
                    # Absurd price for a strong projection -> trips the ceiling.
                    _outcome("Yes", "Josh Jacobs", 250, None, "sid-jacobs-td"),
                ]},
            ],
        }],
    }
    return {"evt-cin-ne": cin_ne, "evt-gb-chi": gb_chi}


@pytest.fixture
def projections():
    csv = (
        "playerName,teamName,position,passYds,passTd,rushAtt,rushYds,rushTd,"
        "recvTargets,recvReceptions,recvYds,recvTd,returnTd\n"
        # 88.4 vs a 74.5 line = +18.7% gap, clears the 10% threshold.
        "Ja'Marr Chase,CIN,WR,0,0,0.2,1.0,0.0,10.1,6.90,88.4,0.62,0\n"
        # 31.8 vs 38.5 = negative gap; receptions lambda 2.27 over 1.5.
        "Kayshon Boutte,NE,WR,0,0,0,0,0,4.2,2.27,31.8,0.19,0\n"
        "Joe Burrow,CIN,QB,271.3,1.80,3.1,11.0,0.10,0,0,0,0,0\n"
        # Big TD lambda so the +250 anytime price trips the sanity ceiling.
        "Josh Jacobs,GB,RB,0,0,17.4,71.2,0.96,3.1,2.40,18.6,0.10,0\n"
    )
    frame, _ = load_projections(io.BytesIO(csv.encode()))
    return frame


def _player(pid, name, team, position, slot_id=4, status="ACTIVE"):
    return {
        "player_id": pid, "name": name, "nfl_team": team, "position": position,
        "lineup_slot_id": slot_id, "lineup_slot": "WR", "injury_status": status,
        "injured": False, "pro_team_id": None, "fantasy_team_id": None,
    }


@pytest.fixture
def league():
    """Three fantasy teams: one Bengals-heavy, one Packers, one all on bye."""
    teams = [
        {"team_id": 1, "team_name": "Chase Lounge", "abbrev": "CL", "owner": "A",
         "players": [_player(101, "Ja'Marr Chase", "CIN", "WR"),
                     _player(102, "Joe Burrow", "CIN", "QB", slot_id=0)]},
        {"team_id": 2, "team_name": "Jacobs Ladder", "abbrev": "JL", "owner": "B",
         "players": [_player(201, "Josh Jacobs", "GB", "RB", slot_id=2),
                     _player(202, "Kayshon Boutte", "NE", "WR")]},
        {"team_id": 3, "team_name": "Bye Week Blues", "abbrev": "BW", "owner": "C",
         "players": [_player(301, "Patrick Mahomes", "KC", "QB", slot_id=0),
                     _player(302, "Rashee Rice", "KC", "WR")]},
    ]
    for team in teams:
        for player in team["players"]:
            player["fantasy_team_id"] = team["team_id"]
    return teams
