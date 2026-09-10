"""Odds payload parsing, windows, and coverage (§5, §13)."""

import datetime as dt

import pytest

from core import odds as odds_mod


class TestParseEventProps:
    def test_keeps_only_the_over_side(self, raw_by_event):
        props = odds_mod.parse_event_props(raw_by_event["evt-cin-ne"])
        yardage = [p for p in props if p["market"] == "player_reception_yds"]
        assert {p["player_name"] for p in yardage} == {"Ja'Marr Chase", "Kayshon Boutte"}
        # The Under outcome in the fixture must not survive.
        assert len(yardage) == 2

    def test_anytime_td_drops_the_no_side(self, raw_by_event):
        props = odds_mod.parse_event_props(raw_by_event["evt-cin-ne"])
        td = [p for p in props if p["market"] == "player_anytime_td"]
        assert len(td) == 1
        assert td[0]["player_name"] == "Ja'Marr Chase"
        assert td[0]["line"] is None
        assert td[0]["price"] == 110

    def test_anytime_td_accepts_the_name_only_shape(self):
        """Some responses put the player in `name` with no `description`."""
        payload = {"id": "e", "home_team": "Green Bay Packers",
                   "away_team": "Chicago Bears",
                   "bookmakers": [{"key": "fanduel", "markets": [
                       {"key": "player_anytime_td",
                        "outcomes": [{"name": "Chase Brown", "price": 135}]}]}]}
        props = odds_mod.parse_event_props(payload)
        assert props[0]["player_name"] == "Chase Brown"

    def test_ignores_other_bookmakers(self):
        payload = {"id": "e", "home_team": "Green Bay Packers",
                   "away_team": "Chicago Bears",
                   "bookmakers": [{"key": "draftkings", "markets": [
                       {"key": "player_rush_yds", "outcomes": [
                           {"name": "Over", "description": "Josh Jacobs",
                            "price": -110, "point": 60.5}]}]}]}
        assert odds_mod.parse_event_props(payload) == []

    def test_over_without_a_line_is_dropped(self):
        payload = {"id": "e", "home_team": "Green Bay Packers",
                   "away_team": "Chicago Bears",
                   "bookmakers": [{"key": "fanduel", "markets": [
                       {"key": "player_rush_yds", "outcomes": [
                           {"name": "Over", "description": "Josh Jacobs",
                            "price": -110}]}]}]}
        assert odds_mod.parse_event_props(payload) == []

    def test_carries_event_teams_for_scoping(self, raw_by_event):
        props = odds_mod.parse_event_props(raw_by_event["evt-cin-ne"])
        assert all(set(p["event_teams"]) == {"CIN", "NE"} for p in props)

    def test_link_falls_back_from_outcome_to_market_to_book(self):
        payload = {"id": "e", "home_team": "Green Bay Packers",
                   "away_team": "Chicago Bears",
                   "bookmakers": [{"key": "fanduel", "link": "https://fd/book",
                                   "markets": [
                       {"key": "player_rush_yds", "link": "https://fd/market",
                        "outcomes": [
                            {"name": "Over", "description": "A", "price": -110,
                             "point": 1.5, "link": "https://fd/outcome"},
                            {"name": "Over", "description": "B", "price": -110,
                             "point": 1.5}]}]}]}
        props = odds_mod.parse_event_props(payload)
        assert props[0]["link"] == "https://fd/outcome"
        assert props[1]["link"] == "https://fd/market"

    def test_empty_payload(self):
        assert odds_mod.parse_event_props({}) == []
        assert odds_mod.parse_event_props(None) == []


class TestWindow:
    def test_window_ends_after_monday_night(self):
        """A Thursday run must include Monday night, which is Tuesday in UTC."""
        thursday = dt.datetime(2026, 9, 10, 18, 0, tzinfo=dt.timezone.utc)
        _, end = odds_mod.default_window(thursday)
        assert end == dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc)

    def test_window_rolls_forward_once_the_week_is_over(self):
        tuesday_afternoon = dt.datetime(2026, 9, 15, 18, 0, tzinfo=dt.timezone.utc)
        _, end = odds_mod.default_window(tuesday_afternoon)
        assert end == dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.timezone.utc)

    def test_started_games_are_excluded(self):
        """§13: players whose games already kicked off are out."""
        now = dt.datetime(2026, 9, 13, 20, 0, tzinfo=dt.timezone.utc)
        events = [
            {"id": "early", "commence_time": "2026-09-13T17:00:00Z"},
            {"id": "late", "commence_time": "2026-09-13T20:25:00Z"},
        ]
        kept = odds_mod.filter_events(events, now, now + dt.timedelta(days=2))
        assert [e["id"] for e in kept] == ["late"]

    def test_unparseable_commence_time_is_skipped(self):
        now = dt.datetime(2026, 9, 13, tzinfo=dt.timezone.utc)
        events = [{"id": "bad", "commence_time": "not-a-date"}]
        assert odds_mod.filter_events(events, now, now + dt.timedelta(days=2)) == []

    def test_playing_team_codes(self, events):
        assert odds_mod.playing_team_codes(events) == {"CIN", "NE", "GB", "CHI"}


class TestCoverage:
    def test_reports_partial_market_coverage(self, events, raw_by_event):
        """§5: books post some props late; that is a note, not a failure."""
        coverage = odds_mod.market_coverage(
            events, raw_by_event, ["player_reception_yds", "player_rush_attempts"])
        assert coverage["total_events"] == 2
        assert coverage["by_market"]["player_reception_yds"] == 1
        assert coverage["by_market"]["player_rush_attempts"] == 1

    def test_missing_event_payloads_are_not_counted(self, events):
        coverage = odds_mod.market_coverage(events, {}, ["player_rush_yds"])
        assert coverage["total_events"] == 0


class TestClient:
    def test_missing_key_raises(self):
        with pytest.raises(odds_mod.OddsError):
            odds_mod.TheOddsAPI("")

    def test_alternate_markets_are_never_requested(self):
        """§5: main lines only — alternate_* keys would blow the quota."""
        assert not any(m.startswith("alternate") for m in odds_mod.DEFAULT_MARKETS)
        assert len(odds_mod.DEFAULT_MARKETS) == 7
