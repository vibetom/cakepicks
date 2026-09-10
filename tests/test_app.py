"""End-to-end smoke tests: the Streamlit script must actually execute.

These drive app.py through Streamlit's headless AppTest harness, which runs the
real script and records any exception it raises.
"""

import json
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core.odds import DEFAULT_MARKETS

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
        assert [b for b in app.button if b.label.startswith("Use this leg for")]

    def test_review_card_contrasts_the_two_probabilities(self, loaded):
        """The card must state the disagreement, not just dump the inputs.

        The flagged fixture prop is Josh Jacobs anytime TD at +250: the price
        implies 28.6%, while lambda = rushTd 0.96 + recvTd 0.10 gives 65.4%.
        """
        app = run_app(**loaded)
        assert_no_exceptions(app)
        labels = [m.label for m in app.metric]
        assert "FanDuel's price implies" in labels
        assert "Your projection implies" in labels

        book = next(m for m in app.metric if m.label == "FanDuel's price implies")
        model = next(m for m in app.metric if m.label == "Your projection implies")
        assert book.value == "28.6%"
        assert model.value == "65.4%"
        assert model.delta.startswith("+")

    def test_review_card_flags_the_touchdown_clustering_caveat(self, loaded):
        app = run_app(**loaded)
        body = " ".join(m.value for m in app.markdown)
        assert "Touchdowns cluster" in body
        assert "News the projection can't see" in body

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


class TestStorageWiring:
    """The app must render correctly under every persistence configuration."""

    @pytest.fixture(autouse=True)
    def _clear_caches(self):
        import streamlit as st
        st.cache_resource.clear()
        st.cache_data.clear()
        yield
        st.cache_resource.clear()
        st.cache_data.clear()

    def test_local_storage_is_reported_as_ephemeral(self, monkeypatch):
        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_REPO", raising=False)
        app = run_app()
        assert_no_exceptions(app)
        notes = " ".join(w.value for w in app.sidebar.caption) + \
                " ".join(getattr(e, "value", "") for e in app.sidebar.info)
        assert "GITHUB_TOKEN" in notes

    def test_token_without_repo_warns_and_still_runs(self, monkeypatch):
        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "tok_abc")
        monkeypatch.delenv("GITHUB_REPO", raising=False)
        app = run_app()
        assert_no_exceptions(app)

    def test_unreachable_github_does_not_break_the_app(self, monkeypatch):
        """A storage outage must degrade to a message, never a stack trace."""
        import requests

        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "tok_abc")
        monkeypatch.setenv("GITHUB_REPO", "owner/repo")

        def boom(*args, **kwargs):
            raise requests.ConnectionError("no network")

        monkeypatch.setattr(requests.Session, "request", boom)
        app = run_app()
        assert_no_exceptions(app)

    def test_configured_github_store_is_used(self, monkeypatch):
        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "tok_abc")
        monkeypatch.setenv("GITHUB_REPO", "owner/repo")

        from tests.test_store import FakeGitHub
        fake = FakeGitHub()
        monkeypatch.setattr("requests.Session.request",
                            lambda self, *a, **k: fake.request(*a, **k))
        app = run_app()
        assert_no_exceptions(app)
        text = " ".join(s.value for s in app.sidebar.success)
        assert "owner/repo" in text


class TestSaveThroughTheUI:
    """Clicking Save must actually commit, and grading must write back."""

    @pytest.fixture(autouse=True)
    def _clear_caches(self):
        import streamlit as st
        st.cache_resource.clear()
        st.cache_data.clear()
        yield
        st.cache_resource.clear()
        st.cache_data.clear()

    @pytest.fixture
    def wired(self, monkeypatch, events, raw_by_event, league, projections, config):
        from tests.test_store import FakeGitHub

        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "tok_abc")
        monkeypatch.setenv("GITHUB_REPO", "owner/repo")
        fake = FakeGitHub()
        monkeypatch.setattr("requests.Session.request",
                            lambda self, *a, **k: fake.request(*a, **k))
        state = dict(
            config=config, events=events, raw_by_event=raw_by_event,
            teams_raw=league, projections=projections,
            projection_filename="week-2.csv", projection_warnings=[],
            odds_fetched_at="2026-09-10T18:00:00+00:00",
            roster_fetched_at="2026-09-10T18:00:00+00:00",
            overrides={}, approved_reviews=set(), quota={},
        )
        return fake, state

    def test_saving_a_run_commits_it(self, wired):
        fake, state = wired
        app = run_app(**state)
        save = next(b for b in app.button if "Save this run" in b.label)
        save.click().run()
        assert_no_exceptions(app)

        saved = [p for p in fake.files["parlay-data"] if p.startswith("runs/")]
        assert len(saved) == 1, fake.files["parlay-data"].keys()
        assert any("Save parlay run" in m for m in fake.commit_messages)

    def test_the_committed_record_is_slim(self, wired):
        """The raw odds blob must not be committed — only picks and config."""
        import json as _json

        fake, state = wired
        app = run_app(**state)
        next(b for b in app.button if "Save this run" in b.label).click().run()

        path = next(p for p in fake.files["parlay-data"] if p.startswith("runs/"))
        record = _json.loads(fake.files["parlay-data"][path])
        assert "raw_odds" not in record
        assert "rosters" not in record
        assert len(record["picks"]) == 3
        assert record["config"]["gap_threshold"] == 0.10

    def test_a_saved_run_appears_in_history_on_the_next_render(self, wired):
        fake, state = wired
        app = run_app(**state)
        next(b for b in app.button if "Save this run" in b.label).click().run()
        assert_no_exceptions(app)
        # The metric row only renders once at least one run is stored.
        labels = [m.label for m in app.metric]
        assert "Leg hit rate" in labels

    def test_saving_settings_commits_the_config(self, wired):
        fake, state = wired
        app = run_app(**state)
        next(b for b in app.sidebar.button if b.label == "💾 Save").click().run()
        assert_no_exceptions(app)
        assert "config.json" in fake.files["parlay-data"]

    def test_adding_an_alias_commits_it(self, wired):
        """An abbreviated name shows in diagnostics; one click stores the fix."""
        fake, state = wired
        # "K. Boutte" scores below the fuzzy cutoff against rostered
        # "Kayshon Boutte", so it must surface as an unmatched near-miss.
        state["raw_by_event"] = dict(state["raw_by_event"])
        payload = json.loads(json.dumps(state["raw_by_event"]["evt-cin-ne"]))
        payload["bookmakers"][0]["markets"].append({
            "key": "player_rush_yds",
            "outcomes": [{"name": "Over", "description": "K. Boutte",
                          "price": -110, "point": 12.5}],
        })
        state["raw_by_event"]["evt-cin-ne"] = payload

        app = run_app(**state)
        assert_no_exceptions(app)
        alias_buttons = [b for b in app.button if b.label.startswith("Alias →")]
        assert alias_buttons, "the unmatched name should offer an alias fix"

        alias_buttons[0].click().run()
        assert_no_exceptions(app)
        assert "aliases.json" in fake.files["parlay-data"]
        stored = json.loads(fake.files["parlay-data"]["aliases.json"])
        assert stored == {"k boutte": "Kayshon Boutte"}


class TestReviewCardRendering:
    """Regressions in the sanity card's copy, both found by looking at it."""

    @pytest.fixture
    def loaded(self, monkeypatch, events, raw_by_event, league, projections, config):
        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        return dict(
            config=config, events=events, raw_by_event=raw_by_event,
            teams_raw=league, projections=projections,
            projection_filename="week-2.csv", projection_warnings=[],
            odds_fetched_at="2026-09-10T18:00:00+00:00",
            roster_fetched_at="2026-09-10T18:00:00+00:00",
            overrides={}, approved_reviews=set(), quota={},
        )

    def test_currency_is_escaped_for_streamlit_markdown(self, loaded):
        """A bare $ opens LaTeX math and eats the rest of the sentence."""
        app = run_app(**loaded)
        assert_no_exceptions(app)
        sentence = next(m.value for m in app.markdown if "too cheap" in m.value)
        assert "\\$" in sentence
        assert "bet returns" in sentence
        # No unescaped dollar sign may survive anywhere in the sentence.
        assert not re.search(r"(?<!\\)\$", sentence)

    def test_a_future_odds_timestamp_is_not_negative(self, loaded):
        """Feed clocks can run ahead of ours; that is skew, not negative time."""
        app = run_app(**loaded)
        body = " ".join(m.value for m in app.markdown)
        assert "Odds age" in body
        assert "-" not in body.split("Odds age")[1].split(".")[0]


class TestMarketCheckboxes:
    """The markets control, driven through the real UI."""

    @pytest.fixture
    def loaded(self, monkeypatch, events, raw_by_event, league, projections, config):
        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        return dict(
            config=config, events=events, raw_by_event=raw_by_event,
            teams_raw=league, projections=projections,
            projection_filename="week-2.csv", projection_warnings=[],
            odds_fetched_at="2026-09-10T18:00:00+00:00",
            roster_fetched_at="2026-09-10T18:00:00+00:00",
            overrides={}, approved_reviews=set(), quota={},
        )

    def test_one_checkbox_per_market(self, loaded):
        app = run_app(**loaded)
        assert_no_exceptions(app)
        labels = {c.label for c in app.sidebar.checkbox}
        for expected in ("Rec Yds", "Rush Yds", "Pass Yds", "Rush Att",
                         "Receptions", "Anytime TD", "Pass TDs"):
            assert expected in labels

    def test_all_markets_start_checked(self, loaded):
        app = run_app(**loaded)
        market_boxes = [c for c in app.sidebar.checkbox
                        if c.label in {"Rec Yds", "Rush Yds", "Pass Yds", "Rush Att",
                                       "Receptions", "Anytime TD", "Pass TDs"}]
        assert len(market_boxes) == 7
        assert all(c.value for c in market_boxes)

    def test_unchecking_changes_the_table_without_an_api_call(self, loaded, monkeypatch):
        """The bug this replaced: the control did nothing until a refetch."""
        import core.odds as odds_mod

        def explode(*args, **kwargs):
            raise AssertionError("changing markets must not call the odds API")

        monkeypatch.setattr(odds_mod.TheOddsAPI, "get_week_events", explode)
        monkeypatch.setattr(odds_mod.TheOddsAPI, "get_event_props", explode)

        app = run_app(**loaded)
        before = app.dataframe[0].value
        chase_before = before[before["Team"] == "Chase Lounge"].iloc[0]
        assert chase_before["Prop"].startswith("Rec Yds")

        next(c for c in app.sidebar.checkbox if c.label == "Rec Yds").set_value(False).run()
        assert_no_exceptions(app)

        after = app.dataframe[0].value
        chase_after = after[after["Team"] == "Chase Lounge"].iloc[0]
        assert not chase_after["Prop"].startswith("Rec Yds")

    def test_unchecking_everything_warns(self, loaded):
        app = run_app(**loaded)
        for label in ("Rec Yds", "Rush Yds", "Pass Yds", "Rush Att",
                      "Receptions", "Anytime TD", "Pass TDs"):
            next(c for c in app.sidebar.checkbox if c.label == label).set_value(False)
        app.run()
        assert_no_exceptions(app)
        assert any("No markets selected" in w.value for w in app.sidebar.warning)
        frame = app.dataframe[0].value
        assert all(prop == "NONE" for prop in frame["Prop"])


class TestRestoreOnRestart:
    """A restart must not cost API credits."""

    @pytest.fixture(autouse=True)
    def _clear_caches(self):
        import streamlit as st
        st.cache_resource.clear()
        st.cache_data.clear()
        yield
        st.cache_resource.clear()
        st.cache_data.clear()

    @pytest.fixture
    def data_dir(self, tmp_path, monkeypatch):
        """Point the app's local store at a scratch directory."""
        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_REPO", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        return tmp_path / "data"

    def _seed(self, data_dir, events, raw_by_event, league, projections):
        from core import snapshots
        from core.store import LocalStore

        store = LocalStore(data_dir)
        snapshots.save_odds(store, events=events, raw_by_event=raw_by_event,
                            markets=list(DEFAULT_MARKETS),
                            fetched_at="2026-09-10T18:00:00+00:00")
        snapshots.save_rosters(store, teams=league,
                               fetched_at="2026-09-10T18:00:00+00:00")
        snapshots.save_projections(store, frame=projections, filename="week-2.csv")

    def test_cold_start_restores_everything(self, data_dir, events, raw_by_event,
                                            league, projections, monkeypatch):
        """The bug this fixes: a restart used to demand a fresh paid fetch."""
        import core.odds as odds_mod

        self._seed(data_dir, events, raw_by_event, league, projections)

        def explode(*args, **kwargs):
            raise AssertionError("restoring must not call the odds API")

        monkeypatch.setattr(odds_mod.TheOddsAPI, "get_week_events", explode)
        monkeypatch.setattr(odds_mod.TheOddsAPI, "get_event_props", explode)

        # No pre-seeded session state: this is a genuine cold boot.
        app = AppTest.from_file(APP, default_timeout=TIMEOUT)
        app.run()
        assert_no_exceptions(app)

        frame = app.dataframe[0].value
        assert len(frame) == 3
        assert "Chase Lounge" in set(frame["Team"])

    def test_cold_start_says_it_spent_nothing(self, data_dir, events, raw_by_event,
                                              league, projections):
        self._seed(data_dir, events, raw_by_event, league, projections)
        app = AppTest.from_file(APP, default_timeout=TIMEOUT)
        app.run()
        assert_no_exceptions(app)
        captions = " ".join(c.value for c in app.caption)
        assert "no credits spent" in captions
        assert "restored" in captions

    def test_cold_start_without_a_snapshot_still_asks_for_input(self, data_dir):
        app = AppTest.from_file(APP, default_timeout=TIMEOUT)
        app.run()
        assert_no_exceptions(app)
        info = " ".join(e.value for e in app.info)
        assert "fetch odds" in info

    def test_stale_odds_are_called_out(self, data_dir, events, raw_by_event,
                                       league, projections):
        from core import snapshots
        from core.store import LocalStore

        old = "2026-09-01T18:00:00+00:00"          # well over a day before "now"
        store = LocalStore(data_dir)
        snapshots.save_odds(store, events=events, raw_by_event=raw_by_event,
                            markets=list(DEFAULT_MARKETS), fetched_at=old)
        snapshots.save_rosters(store, teams=league, fetched_at=old)
        snapshots.save_projections(store, frame=projections, filename="week-2.csv")

        app = AppTest.from_file(APP, default_timeout=TIMEOUT)
        app.run()
        assert_no_exceptions(app)
        warnings = " ".join(w.value for w in app.warning)
        assert "days old" in warnings
        assert "fetch fresh odds" in warnings.lower()

    def test_events_outside_the_window_are_rejected_clearly(
        self, data_dir, raw_by_event, league, projections
    ):
        """A snapshot from last week must say so, not silently produce NONE."""
        from core import snapshots
        from core.store import LocalStore

        stale_events = [
            {"id": "evt-cin-ne", "commence_time": "2020-09-13T17:00:00Z",
             "home_team": "Cincinnati Bengals", "away_team": "New England Patriots",
             "home_code": "CIN", "away_code": "NE"},
            {"id": "evt-gb-chi", "commence_time": "2020-09-13T20:25:00Z",
             "home_team": "Green Bay Packers", "away_team": "Chicago Bears",
             "home_code": "GB", "away_code": "CHI"},
        ]
        store = LocalStore(data_dir)
        snapshots.save_odds(store, events=stale_events, raw_by_event=raw_by_event,
                            markets=list(DEFAULT_MARKETS), fetched_at=None)
        snapshots.save_rosters(store, teams=league, fetched_at=None)
        snapshots.save_projections(store, frame=projections, filename="week-2.csv")

        app = AppTest.from_file(APP, default_timeout=TIMEOUT)
        app.run()
        assert_no_exceptions(app)
        errors = " ".join(e.value for e in app.error)
        assert "outside your date window" in errors

    def test_availability_is_settled_once_not_polled(self, data_dir, events,
                                                     raw_by_event, league,
                                                     projections, monkeypatch):
        """Re-reading the snapshot each rerun would pull ~400 KB from GitHub."""
        from core import store as store_mod

        self._seed(data_dir, events, raw_by_event, league, projections)

        reads = []
        original = store_mod.LocalStore.read_json

        def counting_read(self, path):
            reads.append(path)
            return original(self, path)

        monkeypatch.setattr(store_mod.LocalStore, "read_json", counting_read)

        app = AppTest.from_file(APP, default_timeout=TIMEOUT)
        app.run()
        after_boot = reads.count("odds/latest.json")
        assert after_boot == 1

        next(s for s in app.slider if "gap threshold" in s.label.lower()).set_value(30).run()
        assert_no_exceptions(app)
        assert reads.count("odds/latest.json") == after_boot, \
            "interacting with a control must not re-read the odds snapshot"

    def test_reload_button_is_offered_when_a_snapshot_exists(
        self, data_dir, events, raw_by_event, league, projections
    ):
        self._seed(data_dir, events, raw_by_event, league, projections)
        app = AppTest.from_file(APP, default_timeout=TIMEOUT)
        app.run()
        assert_no_exceptions(app)
        assert any("Reload saved odds" in b.label for b in app.button)

    def test_reload_button_is_absent_without_a_snapshot(self, data_dir):
        app = AppTest.from_file(APP, default_timeout=TIMEOUT)
        app.run()
        assert_no_exceptions(app)
        assert not any("Reload saved odds" in b.label for b in app.button)
