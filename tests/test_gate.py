"""The password gate, and that it actually gates."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import gate as gate_mod

PROPS = str(Path(__file__).resolve().parents[1] / "props.py")
TIMEOUT = 60


class TestComparison:
    def test_correct_password(self):
        assert gate_mod._matches("hunter2", "hunter2")

    def test_wrong_password(self):
        assert not gate_mod._matches("hunter3", "hunter2")

    def test_empty_supplied(self):
        assert not gate_mod._matches("", "hunter2")

    def test_near_miss(self):
        assert not gate_mod._matches("hunter2 ", "hunter2")


def run(**state):
    app = AppTest.from_file(PROPS, default_timeout=TIMEOUT)
    for key, value in state.items():
        app.session_state[key] = value
    app.run()
    return app


class TestGateBehaviour:
    @pytest.fixture(autouse=True)
    def _isolated(self, monkeypatch, tmp_path):
        import streamlit as st

        st.cache_resource.clear()
        st.cache_data.clear()
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_REPO", raising=False)
        monkeypatch.setenv("ODDS_API_KEY", "test-key")
        yield
        st.cache_resource.clear()
        st.cache_data.clear()

    def test_no_password_configured_means_no_gate(self, monkeypatch):
        """An app that is already private must be unaffected."""
        monkeypatch.delenv("APP_PASSWORD", raising=False)
        app = run()
        assert not app.exception
        titles = " ".join(t.value for t in app.title)
        assert "Prop Finder" in titles
        assert "password protected" not in titles

    def test_a_configured_password_hides_the_app(self, monkeypatch):
        monkeypatch.setenv("APP_PASSWORD", "hunter2")
        app = run()
        assert not app.exception
        titles = " ".join(t.value for t in app.title)
        assert "password protected" in titles
        assert "Prop Finder" not in titles

    def test_the_gate_stops_before_the_fetch_button_exists(self, monkeypatch):
        """The thing being protected is the button that spends credits."""
        monkeypatch.setenv("APP_PASSWORD", "hunter2")
        app = run()
        assert not any("Fetch fresh odds" in b.label for b in app.button)

    def test_a_wrong_password_stays_locked(self, monkeypatch):
        monkeypatch.setenv("APP_PASSWORD", "hunter2")
        app = run()
        app.text_input[0].set_value("wrong")
        app.button[0].click().run()
        assert not app.exception
        assert any("isn't right" in e.value for e in app.error)
        assert "password protected" in " ".join(t.value for t in app.title)

    def test_the_right_password_unlocks_it(self, monkeypatch):
        monkeypatch.setenv("APP_PASSWORD", "hunter2")
        app = run()
        app.text_input[0].set_value("hunter2")
        app.button[0].click().run()
        assert not app.exception
        assert "Prop Finder" in " ".join(t.value for t in app.title)

    def test_unlocking_persists_across_reruns(self, monkeypatch):
        monkeypatch.setenv("APP_PASSWORD", "hunter2")
        app = run(unlocked=True)
        assert not app.exception
        assert "Prop Finder" in " ".join(t.value for t in app.title)

    def test_a_blank_configured_password_does_not_lock_anyone_out(self, monkeypatch):
        monkeypatch.setenv("APP_PASSWORD", "")
        app = run()
        assert "Prop Finder" in " ".join(t.value for t in app.title)
