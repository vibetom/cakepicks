"""End-to-end smoke tests: the Streamlit script must actually execute.

These drive app.py through Streamlit's headless AppTest harness, which runs the
real script and records any exception it raises.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")
TIMEOUT = 60


def run_app(**state):
    app = AppTest.from_file(APP, default_timeout=TIMEOUT)
    for key, value in state.items():
        app.session_state[key] = value
    app.run()
    return app


def assert_no_exceptions(app):
    assert not app.exception, [str(e) for e in app.exception]


class TestColdStart:
    def test_starts_with_nothing_loaded(self, monkeypatch):
        """Acceptance check 1: it starts and asks for what it needs."""
        monkeypatch.delenv("ODDS_API_KEY", raising=False)
        app = run_app()
        assert_no_exceptions(app)
        messages = " ".join(e.value for e in app.error)
        assert "No Odds API key found" in messages

    def test_prompts_for_the_missing_inputs(self, monkeypatch):
        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        app = run_app()
        assert_no_exceptions(app)
        info = " ".join(e.value for e in app.info)
        assert "fetch odds" in info and "PFF CSV" in info

    def test_no_key_error_disappears_once_set(self, monkeypatch):
        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        app = run_app()
        assert not any("No Odds API key" in e.value for e in app.error)


class TestFullRender:
    @pytest.fixture
    def loaded(self, monkeypatch, events, raw_by_event, league, projections, config):
        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        return dict(
            config=config,
            events=events,
            raw_by_event=raw_by_event,
            teams_raw=league,
            projections=projections,
            projection_filename="week-2.csv",
            projection_warnings=[],
            odds_fetched_at="2026-09-10T18:00:00+00:00",
            roster_fetched_at="2026-09-10T18:00:00+00:00",
            overrides={},
            approved_reviews=set(),
            quota={"remaining": 430, "used": 70, "last_cost": 14},
        )

    def test_renders_every_tab_without_error(self, loaded):
        """Acceptance check 2: a full run renders the results table."""
        app = run_app(**loaded)
        assert_no_exceptions(app)
        assert len(app.tabs) == 4

    def test_results_table_has_a_row_per_fantasy_team(self, loaded):
        app = run_app(**loaded)
        assert_no_exceptions(app)
        frame = app.dataframe[0].value
        assert len(frame) == 3
        assert set(frame["Team"]) == {"Chase Lounge", "Jacobs Ladder", "Bye Week Blues"}

    def test_empty_slot_is_labelled_with_its_reason(self, loaded):
        app = run_app(**loaded)
        frame = app.dataframe[0].value
        bye_row = frame[frame["Team"] == "Bye Week Blues"].iloc[0]
        assert bye_row["Prop"] == "NONE"
        assert "bye" in bye_row["Flags / reason"]

    def test_share_block_is_rendered(self, loaded):
        app = run_app(**loaded)
        share = "\n".join(block.value for block in app.code)
        assert "Chase Lounge" in share
        assert "parlay:" in share

    def test_moving_a_slider_re_runs_without_an_api_call(self, loaded, monkeypatch):
        """Acceptance check 3: sliders re-score from cache, never refetch.

        Any call into the odds client here would be a bug, so the client is
        replaced with one that raises.
        """
        import core.odds as odds_mod

        def explode(*args, **kwargs):
            raise AssertionError("slider interaction must not call the odds API")

        monkeypatch.setattr(odds_mod.TheOddsAPI, "get_week_events", explode)
        monkeypatch.setattr(odds_mod.TheOddsAPI, "get_event_props", explode)

        app = run_app(**loaded)
        before = app.dataframe[0].value
        chase_before = before[before["Team"] == "Chase Lounge"].iloc[0]
        assert chase_before["Tier"] == "1", "Chase's +18.7% gap clears the 10% default"

        # 45% is above every gap in the fixture, so Tier 1 must become Tier 2.
        slider = next(s for s in app.slider if "gap threshold" in s.label.lower())
        slider.set_value(45).run()
        assert_no_exceptions(app)

        after = app.dataframe[0].value
        chase_after = after[after["Team"] == "Chase Lounge"].iloc[0]
        assert chase_after["Tier"] == "2"
        assert chase_after["Player"] == chase_before["Player"]

    def test_slider_labels_show_real_percentages(self, loaded):
        """The threshold sliders read as percentages, not as 0."""
        app = run_app(**loaded)
        gap = next(s for s in app.slider if "gap threshold" in s.label.lower())
        assert gap.value == 10
        ceiling = next(s for s in app.slider if "sanity ceiling" in s.label.lower())
        assert ceiling.value == 35

    def test_ev_toggle_off_removes_ev_legs(self, loaded):
        """Acceptance check 4, through the UI rather than the engine."""
        app = run_app(**loaded)
        toggle = next(t for t in app.toggle if t.label == "EV props enabled")
        toggle.set_value(False).run()
        assert_no_exceptions(app)
        frame = app.dataframe[0].value
        assert not any(p in {"Anytime TD", "Receptions"} for p in frame["Prop"])

    def test_review_tab_shows_the_flagged_prop(self, loaded):
        """§6.5: the outsized EV appears as a review card, not as a pick."""
        app = run_app(**loaded)
        assert_no_exceptions(app)
        approve_buttons = [b for b in app.button if "Approve" in b.label]
        assert approve_buttons

    def test_diagnostics_tab_renders(self, loaded):
        app = run_app(**loaded)
        assert_no_exceptions(app)
        headers = " ".join(s.value for s in app.subheader)
        assert "Unmatched names" in headers
        assert "Roster exclusions" in headers

    def test_history_tab_offers_a_download(self, loaded):
        app = run_app(**loaded)
        assert_no_exceptions(app)
        assert any("run log" in b.label for b in list(app.button) + list(app.download_button))


class TestBadInputs:
    def test_survives_an_empty_odds_payload(self, monkeypatch, events, league,
                                            projections, config):
        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        app = run_app(
            config=config, events=events,
            raw_by_event={e["id"]: {"id": e["id"], "bookmakers": []} for e in events},
            teams_raw=league, projections=projections,
            projection_filename="week-2.csv", odds_fetched_at="2026-09-10T18:00:00+00:00",
            roster_fetched_at="2026-09-10T18:00:00+00:00",
        )
        assert_no_exceptions(app)
        frame = app.dataframe[0].value
        assert all(prop == "NONE" for prop in frame["Prop"])

    def test_survives_a_roster_with_no_players(self, monkeypatch, events,
                                               raw_by_event, projections, config):
        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        app = run_app(
            config=config, events=events, raw_by_event=raw_by_event,
            teams_raw=[{"team_id": 9, "team_name": "Empty", "abbrev": "E",
                        "owner": None, "players": []}],
            projections=projections, projection_filename="w.csv",
            odds_fetched_at="2026-09-10T18:00:00+00:00",
            roster_fetched_at="2026-09-10T18:00:00+00:00",
        )
        assert_no_exceptions(app)
