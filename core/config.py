"""Tunable parameters (§7) and their persistence.

Every knob here is exposed as a UI control. Retuning must never require a code
change, so nothing in the selection path reads a literal -- it reads this dict.
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path("config.json")

# Standard sportsbook prices, used for the odds-floor control. American odds
# have no valid values strictly between -100 and +100, so a plain slider would
# offer impossible prices; the UI uses these ticks instead.
ODDS_TICKS = (
    [-300, -290, -280, -270, -260, -250, -240, -230, -220, -210, -200,
     -195, -190, -185, -180, -175, -170, -165, -160, -155, -150, -145,
     -140, -135, -130, -125, -120, -115, -110, -105, -100]
    + [100, 105, 110, 115, 120, 125, 130, 135, 140, 145, 150, 155, 160,
       165, 170, 175, 180, 185, 190, 195, 200, 210, 220, 230, 240, 250,
       260, 270, 280, 290, 300]
)

DEFAULTS = {
    # Thresholds
    "gap_threshold": 0.10,        # X: Tier-1 qualification for yardage props
    "ev_threshold": 0.20,         # Y: EV needed to steal a qualifying yardage slot
    "sanity_ceiling": 0.35,       # EV above this is flagged for review, never auto-picked
    "odds_floor": -110,           # worst acceptable price, any prop type
    # Volume / projection floors
    "yards_floor": 25.0,          # rec-yds and rush-yds
    "pass_yards_floor": 175.0,
    "rush_attempts_floor": 8.0,
    "receptions_floor": 1.0,
    # Toggles
    "ev_enabled": True,
    "starters_only": False,
    "exclude_questionable": False,
    "exclude_doubtful": True,
    # Misc
    "stake": 10.0,
    "season_year": 2026,
    "league_id": 1490739926,
    "fuzzy_threshold": 90,
    "markets": [
        "player_reception_yds", "player_rush_yds", "player_pass_yds",
        "player_rush_attempts", "player_receptions", "player_anytime_td",
        "player_pass_tds",
    ],
}


def load_config(path: str | Path = CONFIG_PATH) -> dict:
    """Defaults overlaid with anything previously saved.

    Unknown keys in the saved file are ignored, so an older config.json never
    breaks a newer build.
    """
    config = dict(DEFAULTS)
    path = Path(path)
    if path.exists():
        try:
            saved = json.loads(path.read_text() or "{}")
            for key, value in saved.items():
                if key in DEFAULTS:
                    config[key] = value
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_config(config: dict, path: str | Path = CONFIG_PATH) -> bool:
    """Persist the tunables. Returns False on a read-only filesystem.

    Hosted deployments often have an ephemeral or read-only working directory;
    failing to save settings must never break a run.
    """
    try:
        Path(path).write_text(
            json.dumps({k: v for k, v in config.items() if k in DEFAULTS},
                       indent=2, sort_keys=True)
        )
        return True
    except OSError:
        return False
