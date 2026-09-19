"""Tunable parameters (§7) and their persistence.

Every knob here is exposed as a UI control. Retuning must never require a code
change, so nothing in the selection path reads a literal -- it reads this dict.
"""

from __future__ import annotations

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

# Ceiling ticks run from even money upward: the control answers "don't take
# anything longer than this". None means no ceiling at all.
CEILING_TICKS = (
    [t for t in ODDS_TICKS if t >= 100]
    + [320, 340, 360, 380, 400, 450, 500, 550, 600, 700, 800, 900, 1000]
    + [None]
)

DEFAULTS = {
    # Thresholds
    "gap_threshold": 0.10,        # X: Tier-1 qualification for yardage props
    "ev_threshold": 0.20,         # Y: EV needed to steal a qualifying yardage slot
    "sanity_ceiling": 0.35,       # EV above this is flagged for review, never auto-picked
    "odds_floor": -110,           # worst acceptable price, any prop type
    "odds_ceiling": 300,          # longest acceptable price; None disables it
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
    # Stops two legs on one NFL offence competing for the same football; see
    # core.conflicts for the pairs it rules out.
    "avoid_team_conflicts": True,
    # Misc
    "stake": 10.0,
    "season_year": 2026,
    "league_id": 563635,
    "fuzzy_threshold": 90,
    "markets": [
        "player_reception_yds", "player_rush_yds", "player_pass_yds",
        "player_rush_attempts", "player_receptions", "player_anytime_td",
        "player_pass_tds",
    ],
}


CONFIG_FILE = "config.json"


def load_config(store=None) -> dict:
    """Defaults overlaid with anything previously saved.

    Unknown keys in the saved document are ignored, so an older saved config
    never breaks a newer build.
    """
    config = dict(DEFAULTS)
    if store is None:
        return config
    try:
        saved = store.read_json(CONFIG_FILE)
    except Exception:
        return config
    if isinstance(saved, dict):
        for key, value in saved.items():
            if key in DEFAULTS:
                config[key] = value
    return config


def save_config(config: dict, store) -> bool:
    """Persist the tunables. Returns False when the store could not write.

    Only called from an explicit "Save settings" action -- autosaving on every
    slider drag would spam the history with commits.
    """
    payload = {k: v for k, v in config.items() if k in DEFAULTS}
    try:
        return store.write_json(CONFIG_FILE, payload, "Update parlay bot settings")
    except Exception:
        return False
