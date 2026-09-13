"""The Prop Finder page, driven through Streamlit's headless harness."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import board as board_mod
from core.matching import AliasStore
from core.store import LocalStore

PROPS = str(Path(__file__).resolve().parents[1] / "props.py")
TIMEOUT = 60


def run(**state):
    app = AppTest.from_file(PROPS, default_timeout=TIMEOUT)
    for key, value in state.items():
        app.session_state[key] = value
    app.run()
    return app


def toggle_named(app, fragment):
    return next(t for t in app.toggle if fragment in t.label)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """No secrets, no shared cache, and nothing written into the repo."""
    import streamlit as st

    st.cache_resource.clear()
    st.cache_data.clear()
    monkeypatch.chdir(tmp_path)
    for name in ("GITHUB_TOKEN", "GITHUB_REPO", "APP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


@pytest.fixture
def loaded(events, raw_by_event, projections, tmp_path):
    return dict(
        props_config=dict(board_mod.PROPS_DEFAULTS),
        aliases=AliasStore(LocalStore(tmp_path / "state")),
        events=events,
        raw_by_event=raw_by_event,
        odds_fetched_at="2026-09-12T18:00:00+00:00",
        odds_markets=list(board_mod.PROPS_DEFAULTS["markets"]),
        projections=projections,
        projection_filename="week-2.csv",
        quota={},
        slip_keys=[],
        stake=10.0,
        restored=True,
    )


class TestTouchdownToggle:
    def test_the_toggle_is_on_the_page_not_the_sidebar(self, loaded):
        app = run(**loaded)
        assert not app.exception, [str(e) for e in app.exception]
        toggle = toggle_named(app, "Touchdowns only")
        assert not toggle.value          # off until asked for
        assert toggle.label not in [t.label for t in app.sidebar.toggle]

    def test_off_by_default_the_board_is_not_touchdowns_only(self, loaded):
        app = run(**loaded)
        markets = {row["market"] for row in board_mod.qualifying(
            board_mod.score_board(
                events=app.session_state.events,
                raw_by_event=app.session_state.raw_by_event,
                projections=app.session_state.projections,
                config=app.session_state.props_config,
                aliases=app.session_state.aliases)["rows"],
            app.session_state.props_config)}
        assert markets - set(board_mod.TD_MARKETS)

    def test_turning_it_on_leaves_only_touchdown_props(self, loaded):
        # A threshold the fixture's touchdown props can actually clear, so the
        # table renders and there is something to check.
        loaded["props_config"] = {**loaded["props_config"], "min_ev": 0.0}
        app = run(**loaded)
        before = app.dataframe[0].value["Prop"].tolist()
        assert any("Yds" in prop for prop in before)

        toggle_named(app, "Touchdowns only").set_value(True).run()
        assert not app.exception, [str(e) for e in app.exception]
        after = app.dataframe[0].value["Prop"].tolist()
        assert after
        assert all("TD" in prop for prop in after)

    def test_it_disables_the_market_filter_rather_than_fighting_it(self, loaded):
        """Two market controls that disagree would be a trap."""
        app = run(**loaded)
        before = next(m for m in app.multiselect if m.label == "Market")
        assert not before.disabled
        toggle_named(app, "Touchdowns only").set_value(True).run()
        after = next(m for m in app.multiselect if m.label == "Market")
        assert after.disabled

    def test_an_empty_touchdown_board_says_which_setting_hid_it(self, loaded):
        """A tight ceiling hides most touchdown props; that must be visible."""
        loaded["props_config"] = {**loaded["props_config"], "odds_ceiling": -300}
        app = run(**loaded)
        toggle_named(app, "Touchdowns only").set_value(True).run()
        assert not app.exception, [str(e) for e in app.exception]
        info = " ".join(i.value for i in app.info)
        assert "odds ceiling" in info

    def test_it_never_shows_a_raw_exclusion_code(self, loaded):
        loaded["props_config"] = {**loaded["props_config"], "odds_ceiling": -300}
        app = run(**loaded)
        toggle_named(app, "Touchdowns only").set_value(True).run()
        info = " ".join(i.value for i in app.info)
        assert "price_ceiling" not in info

    def test_a_short_list_says_what_trimmed_it(self, loaded):
        """One play out of hundreds looks broken without the reason."""
        loaded["props_config"] = {**loaded["props_config"], "min_ev": 0.0}
        app = run(**loaded)
        toggle_named(app, "Touchdowns only").set_value(True).run()
        assert not app.exception, [str(e) for e in app.exception]
        captions = " ".join(c.value for c in app.caption)
        assert "made the list" in captions
        assert "sidebar settings" in captions
