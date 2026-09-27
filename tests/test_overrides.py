"""A manual override goes on the slip no matter what.

Two separate failures led here:

* The override list only offered props that passed every filter, so a leg the
  odds ceiling, a volume floor or the Doubtful toggle had rejected could not be
  chosen at all.
* The override dropdown wrote its choice after the Parlay tab -- table and
  FanDuel link -- had already rendered, so every override landed one click
  late. The link showed the previous state while the dropdown showed the new.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import selection
from core.config import DEFAULTS
from core.matching import AliasStore
from core.odds import playing_team_codes
from core.rosters import filter_players
from core.selection import prop_id, score_props, select_picks
from core.store import LocalStore

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def slate(events, raw_by_event, projections, league, tmp_path):
    """Score and select with any config, the way the app does."""
    def run(config=None, overrides=None, league_edit=None, **toggles):
        config = {**DEFAULTS, **(config or {})}
        roster = league_edit(league) if league_edit else league
        teams = filter_players(roster, playing_team_codes(events), **toggles)
        scored = score_props(events=events, raw_by_event=raw_by_event, teams=teams,
                             projections=projections, config=config,
                             aliases=AliasStore(LocalStore(tmp_path)))
        picks = select_picks(scored, teams, config, overrides=overrides or {})
        return {r["team_name"]: r for r in picks}, scored
    return run


def find(scored, player, market):
    return next(p for props in scored["by_team"].values() for p in props
                if p["player_name"] == player and p["market"] == market)


def override_to(slate, team, player, market, **kw):
    _, scored = slate(**kw)
    target = find(scored, player, market)
    team_id = target["fantasy_team_id"]
    picks, _ = slate(overrides={team_id: prop_id(target)}, **kw)
    return picks[team], target


# ---------------------------------------------------------------------------
# Every filter is ignored for a hand-picked leg
# ---------------------------------------------------------------------------

class TestAnOverrideIgnoresEveryFilter:
    def test_the_odds_floor(self, slate):
        """Chase's receptions at -155 fail the -110 floor."""
        row, target = override_to(slate, "Chase Lounge", "Ja'Marr Chase",
                                  "player_receptions")
        assert "price_floor" in target["exclusion_codes"]
        assert row["pick"]["prop_id"] == target["prop_id"]
        assert row["manual"]

    def test_the_odds_ceiling(self, slate):
        row, target = override_to(slate, "Jacobs Ladder", "Josh Jacobs",
                                  "player_anytime_td", config={"odds_ceiling": 200})
        assert "price_ceiling" in target["exclusion_codes"]
        assert row["pick"]["prop_id"] == target["prop_id"]

    def test_the_sanity_ceiling_without_any_approval(self, slate):
        row, target = override_to(slate, "Jacobs Ladder", "Josh Jacobs",
                                  "player_anytime_td")
        assert target["needs_review"]
        assert row["pick"]["prop_id"] == target["prop_id"]

    def test_a_volume_floor(self, slate):
        config = {"yards_floor": 500.0}
        row, target = override_to(slate, "Chase Lounge", "Ja'Marr Chase",
                                  "player_reception_yds", config=config)
        assert "volume_floor" in target["exclusion_codes"]
        assert row["pick"]["prop_id"] == target["prop_id"]

    def test_an_unticked_market(self, slate):
        markets = [m for m in DEFAULTS["markets"] if m != "player_receptions"]
        row, target = override_to(slate, "Chase Lounge", "Ja'Marr Chase",
                                  "player_receptions", config={"markets": markets})
        assert "market_off" in target["exclusion_codes"]
        assert row["pick"]["prop_id"] == target["prop_id"]

    def test_ev_props_turned_off(self, slate):
        row, target = override_to(slate, "Chase Lounge", "Ja'Marr Chase",
                                  "player_anytime_td", config={"ev_enabled": False})
        assert "ev_off" in target["exclusion_codes"]
        assert row["pick"]["prop_id"] == target["prop_id"]


class TestRosterToggles:
    """Doubtful / Questionable / Starters-only are preferences, not facts."""

    @staticmethod
    def burrow_doubtful(league):
        for team in league:
            for player in team["players"]:
                if player["name"] == "Joe Burrow":
                    player["injury_status"] = "DOUBTFUL"
        return league

    def test_a_doubtful_players_props_are_offered(self, slate):
        picks, _ = slate(league_edit=self.burrow_doubtful)
        offered = {(o["player_name"], o["market"])
                   for o in picks["Chase Lounge"]["override_options"]}
        assert ("Joe Burrow", "player_pass_yds") in offered

    def test_they_are_labelled_with_the_toggle(self, slate):
        picks, _ = slate(league_edit=self.burrow_doubtful)
        burrow = next(o for o in picks["Chase Lounge"]["override_options"]
                      if o["player_name"] == "Joe Burrow")
        assert "DOUBTFUL" in selection.override_note(burrow)

    def test_they_are_never_auto_picked(self, slate):
        picks, scored = slate(league_edit=self.burrow_doubtful)
        for row in picks.values():
            if row["pick"]:
                assert row["pick"]["player_name"] != "Joe Burrow"
        for p in scored["by_team"][1]:
            if p["player_name"] == "Joe Burrow":
                assert not p["eligible"]
                assert "roster_toggle" in p["exclusion_codes"]

    def test_they_never_become_sanity_review_cards(self, slate):
        """Approving one could never work, so the card would be a dead end."""
        picks, _ = slate(league_edit=self.burrow_doubtful)
        for row in picks.values():
            assert all(c["player_name"] != "Joe Burrow" for c in row["review_cards"])

    def test_an_override_can_use_one(self, slate):
        row, target = override_to(slate, "Chase Lounge", "Joe Burrow",
                                  "player_pass_yds", league_edit=self.burrow_doubtful)
        assert row["pick"]["prop_id"] == target["prop_id"]


class TestWhatAnOverrideStillCannotReach:
    """Facts, not preferences: nothing to bet on."""

    def test_a_player_on_bye_is_never_offered(self, slate):
        picks, _ = slate()
        assert picks["Bye Week Blues"]["override_options"] == []

    def test_a_doubtful_player_on_bye_is_not_overridable(self, league, events):
        """filter_players labels him DOUBTFUL -- the first reason in its chain
        -- so reading the label would let an override reach a game outside the
        window. Overridability is checked independently of that label."""
        for team in league:
            for player in team["players"]:
                if player["name"] == "Patrick Mahomes":
                    player["injury_status"] = "DOUBTFUL"
        teams = filter_players(league, playing_team_codes(events))
        mahomes = next(p for t in teams for p in t["players"]
                       if p["name"] == "Patrick Mahomes")
        assert "DOUBTFUL" in mahomes["exclusion_reason"]
        assert not mahomes["overridable"]

    @pytest.mark.parametrize("status", ["OUT", "INJURY_RESERVE", "SUSPENSION"])
    def test_a_player_ruled_out_is_not_overridable(self, league, events, status):
        for team in league:
            for player in team["players"]:
                if player["name"] == "Joe Burrow":
                    player["injury_status"] = status
        teams = filter_players(league, playing_team_codes(events))
        burrow = next(p for t in teams for p in t["players"]
                      if p["name"] == "Joe Burrow")
        assert not burrow["overridable"]


class TestTheOverrideList:
    def test_it_includes_the_blocked_props(self, slate):
        picks, _ = slate()
        offered = {(o["player_name"], o["market"])
                   for o in picks["Chase Lounge"]["override_options"]}
        assert ("Ja'Marr Chase", "player_receptions") in offered

    def test_props_that_passed_come_first(self, slate):
        picks, _ = slate()
        options = picks["Jacobs Ladder"]["override_options"]
        flags = [o["eligible"] for o in options]
        assert flags == sorted(flags, reverse=True)      # all True before any False

    def test_it_never_lists_the_current_pick(self, slate):
        picks, _ = slate()
        for row in picks.values():
            if row["pick"]:
                ids = [o["prop_id"] for o in row["override_options"]]
                assert row["pick"]["prop_id"] not in ids

    def test_a_passing_prop_has_no_note(self, slate):
        picks, _ = slate()
        clean = next(o for o in picks["Chase Lounge"]["override_options"] if o["eligible"])
        assert selection.override_note(clean) == ""

    def test_a_blocked_prop_names_what_blocked_it(self, slate):
        picks, _ = slate()
        recs = next(o for o in picks["Chase Lounge"]["override_options"]
                    if o["market"] == "player_receptions")
        note = selection.override_note(recs)
        assert note.startswith("⛔") and "-155" in note and "floor" in note

    def test_a_clash_drop_is_named_as_such(self):
        leg = {"prop_id": "x", "eligible": True, "exclusion_codes": []}
        assert "same-team" in selection.override_note(leg, dropped={"x"})


# ---------------------------------------------------------------------------
# Through the real app: the override reaches the table AND the link at once
# ---------------------------------------------------------------------------

class TestTheOverrideReachesTheLinkOnTheSameClick:
    @pytest.fixture
    def app(self, monkeypatch, tmp_path, events, raw_by_event, league, projections):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        for name in ("GITHUB_TOKEN", "GITHUB_REPO"):
            monkeypatch.delenv(name, raising=False)
        app = AppTest.from_file(APP, default_timeout=60)
        for key, value in dict(
            config=dict(DEFAULTS), events=events, raw_by_event=raw_by_event,
            teams_raw=league, projections=projections,
            projection_filename="week-2.csv", projection_warnings=[],
            odds_fetched_at="2026-09-10T18:00:00+00:00",
            roster_fetched_at="2026-09-10T18:00:00+00:00",
            overrides={}, approved_reviews=set(), quota={},
        ).items():
            app.session_state[key] = value
        app.run()
        assert not app.exception, [str(e) for e in app.exception]
        return app

    @staticmethod
    def chase_receptions(raw_by_event, events):
        from core.odds import parse_event_props

        event = next(e for e in events if e["id"] == "evt-cin-ne")
        prop = next(p for p in parse_event_props(raw_by_event["evt-cin-ne"], event)
                    if p["market"] == "player_receptions"
                    and p["player_name"] == "Ja'Marr Chase")
        return prop_id(prop)

    @staticmethod
    def parlay_row(app, team):
        table = next(d.value for d in app.dataframe if "Team" in d.value.columns)
        return table[table["Team"] == team].iloc[0]

    @staticmethod
    def link(app):
        return next((c.value for c in app.code
                     if "marketId[" in str(c.value)), "")

    def test_the_table_shows_it_on_the_same_click(self, app, raw_by_event, events):
        target = self.chase_receptions(raw_by_event, events)
        box = next(s for s in app.selectbox if s.key == "override-pick-1")
        box.set_value(target).run()
        assert not app.exception, [str(e) for e in app.exception]
        row = self.parlay_row(app, "Chase Lounge")
        assert "Receptions" in row["Prop"], "the override landed a click late"

    def test_the_fanduel_link_carries_the_override(self, app, raw_by_event, events):
        before = self.link(app)
        assert "sid-chase-recyds" in before          # the automatic pick
        target = self.chase_receptions(raw_by_event, events)
        next(s for s in app.selectbox if s.key == "override-pick-1") \
            .set_value(target).run()
        after = self.link(app)
        assert "sid-chase-recs" in after, "the link still holds the old leg"
        assert "sid-chase-recyds" not in after

    def test_back_to_automatic_also_lands_at_once(self, app, raw_by_event, events):
        target = self.chase_receptions(raw_by_event, events)
        box = next(s for s in app.selectbox if s.key == "override-pick-1")
        box.set_value(target).run()
        next(s for s in app.selectbox if s.key == "override-pick-1") \
            .set_value("__auto__").run()
        assert "sid-chase-recyds" in self.link(app)
        assert "sid-chase-recs" not in self.link(app)

    def test_the_table_flags_a_leg_past_the_filters(self, app, raw_by_event, events):
        target = self.chase_receptions(raw_by_event, events)
        next(s for s in app.selectbox if s.key == "override-pick-1") \
            .set_value(target).run()
        row = self.parlay_row(app, "Chase Lounge")
        assert "manual (past filters)" in row["Flags / reason"]


class TestNoReviewCardForALegAlreadyChosen:
    def test_overriding_to_a_flagged_leg_clears_its_card(self, slate):
        """Otherwise the Review tab offers "use it anyway" for a leg on the slip."""
        row, target = override_to(slate, "Jacobs Ladder", "Josh Jacobs",
                                  "player_anytime_td")
        assert row["pick"]["prop_id"] == target["prop_id"]
        assert all(c["prop_id"] != target["prop_id"] for c in row["review_cards"])

    def test_without_the_override_the_card_is_still_there(self, slate):
        picks, _ = slate()
        assert any(c["player_name"] == "Josh Jacobs"
                   for c in picks["Jacobs Ladder"]["review_cards"])
