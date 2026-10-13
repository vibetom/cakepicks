"""The alt-line ladder: every Over for one player, as one FanDuel link."""

import datetime as dt
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import ladder

PROPS = str(Path(__file__).resolve().parents[1] / "props.py")
REC = ladder.MARKETS["Receiving yards"]


def over(player, line, price, market_id, selection_id, link=True):
    out = {"name": "Over", "description": player, "price": price, "point": line,
           "sid": selection_id}
    out["link"] = (f"https://sportsbook.fanduel.com/addToBetslip?marketId={market_id}"
                   f"&selectionId={selection_id}") if link else None
    if not link:
        out["sid"] = None
    return out


def payload():
    """FanDuel's real link shape: market sid absent, IDs carried in the link."""
    return {"bookmakers": [{"key": "fanduel", "markets": [
        {"key": "player_reception_yds", "sid": None, "outcomes": [
            over("Skyler Bell", 33.5, -113, "42.1", "901"),
            {"name": "Under", "description": "Skyler Bell", "price": -113,
             "point": 33.5, "sid": "902",
             "link": "https://sportsbook.fanduel.com/addToBetslip?marketId=42.1&selectionId=902"},
            over("Chris Bell", 40.5, -110, "42.9", "999"),
        ]},
        {"key": "player_reception_yds_alternate", "sid": None, "outcomes": [
            over("Skyler Bell", 14.5, -400, "42.2", "910"),
            over("Skyler Bell", 33.5, -125, "42.2", "911"),      # duplicate number
            over("Skyler Bell", 54.5, 210, "42.2", "912"),
            over("Skyler Bell", 79.5, 600, "42.2", "913", link=False),
        ]},
        {"key": "player_rush_yds", "sid": None, "outcomes": [
            over("Skyler Bell", 2.5, -110, "42.3", "920"),
        ]},
    ]}]}


class TestLadder:
    def test_every_over_for_the_player_lowest_first(self):
        lines = [leg["line"] for leg in ladder.ladder(payload(), "Skyler Bell", REC)]
        assert lines == [14.5, 33.5, 54.5, 79.5]

    def test_unders_are_left_out(self):
        assert all(leg["sid"] != "902"
                   for leg in ladder.ladder(payload(), "Skyler Bell", REC))

    def test_a_different_player_with_the_same_surname_is_not_matched(self):
        players = {leg["player"] for leg in ladder.ladder(payload(), "Skyler Bell", REC)}
        assert players == {"Skyler Bell"}

    def test_other_stats_are_left_out(self):
        assert all(leg["market"] in REC
                   for leg in ladder.ladder(payload(), "Skyler Bell", REC))

    def test_a_number_in_both_markets_keeps_the_standard_lines_ids(self):
        leg = next(l for l in ladder.ladder(payload(), "Skyler Bell", REC)
                   if l["line"] == 33.5)
        assert leg["sid"] == "901" and leg["price"] == -113

    def test_a_line_without_ids_is_kept_so_the_caller_can_flag_it(self):
        from core.betslip import leg_ids

        leg = next(l for l in ladder.ladder(payload(), "Skyler Bell", REC)
                   if l["line"] == 79.5)
        assert leg_ids(leg) is None

    def test_the_link_loads_every_line_that_has_ids(self):
        from core.betslip import build_parlay_url, leg_ids

        legs = [l for l in ladder.ladder(payload(), "Skyler Bell", REC) if leg_ids(l)]
        url = build_parlay_url(legs)
        for selection in ("910", "901", "912"):
            assert f"={selection}" in url
        assert "=911" not in url and "=902" not in url

    def test_an_empty_name_matches_nothing(self):
        assert ladder.ladder(payload(), "  ", REC) == []

    def test_players_lists_everyone_with_an_over(self):
        assert ladder.players(payload(), REC) == ["Chris Bell", "Skyler Bell"]

    def test_players_on_an_empty_payload(self):
        assert ladder.players({}, REC) == []
        assert ladder.players(None, REC) == []

    @pytest.mark.parametrize("price,expected", [(-400, 1.25), (-113, 4.42),
                                                (210, 10.50), (600, 30.0)])
    def test_to_win_on_five_dollars(self, price, expected):
        assert round(ladder.to_win(5, price), 2) == expected


# ---------------------------------------------------------------------------
# Through the Prop Finder, with a fake odds provider -- no network
# ---------------------------------------------------------------------------

class FakeOddsAPI:
    calls: list = []
    response: dict = {}

    def __init__(self, key, timeout=25):
        self.quota = {"remaining": 480, "used": 20, "last_cost": 2}

    def get_week_events(self, start=None, end=None):
        FakeOddsAPI.calls.append("events")
        kickoff = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=6))
        return [{"id": "buf-lar", "commence_time": kickoff.isoformat().replace("+00:00", "Z"),
                 "home_team": "Los Angeles Rams", "away_team": "Buffalo Bills",
                 "home_code": "LAR", "away_code": "BUF"}]

    def get_event_props(self, event_id, markets):
        FakeOddsAPI.calls.append(("props", event_id, tuple(markets)))
        return FakeOddsAPI.response


@pytest.fixture
def app(monkeypatch, tmp_path):
    import streamlit as st

    st.cache_data.clear()
    st.cache_resource.clear()
    monkeypatch.chdir(tmp_path)
    for name in ("GITHUB_TOKEN", "GITHUB_REPO", "APP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    monkeypatch.setattr("core.odds.TheOddsAPI", FakeOddsAPI)
    FakeOddsAPI.calls = []
    FakeOddsAPI.response = payload()
    application = AppTest.from_file(PROPS, default_timeout=60)
    application.run()
    yield application
    st.cache_data.clear()
    st.cache_resource.clear()


def open_ladder(app):
    next(t for t in app.toggle if "Alt-line ladder" in t.label).set_value(True).run()
    assert not app.exception, [str(e) for e in app.exception]


def fetch(app):
    next(b for b in app.button if "Get FanDuel lines" in b.label).click().run()
    assert not app.exception, [str(e) for e in app.exception]


def choose(app, player):
    next(s for s in app.selectbox if s.key == "ladder_player").set_value(player).run()
    assert not app.exception, [str(e) for e in app.exception]


def link(app):
    return next((c.value for c in app.code if "addToBetslip" in str(c.value)), "")


class TestTheLadderInTheApp:
    def test_closed_it_makes_no_api_calls(self, app):
        """It lives on a page people open all week; closed, it must cost nothing."""
        assert not app.exception
        assert FakeOddsAPI.calls == []

    def test_opening_it_lists_games_without_spending_credits(self, app):
        open_ladder(app)
        assert FakeOddsAPI.calls == ["events"]
        assert any(s.key == "ladder_game" for s in app.selectbox)

    def test_lines_are_fetched_only_on_the_button(self, app):
        open_ladder(app)
        assert not any(isinstance(c, tuple) for c in FakeOddsAPI.calls)
        fetch(app)
        assert ("props", "buf-lar", tuple(REC)) in FakeOddsAPI.calls

    def test_choosing_the_player_gives_one_link_for_every_line(self, app):
        open_ladder(app)
        fetch(app)
        choose(app, "Skyler Bell")
        url = link(app)
        for selection in ("910", "901", "912"):
            assert f"={selection}" in url
        assert "=999" not in url            # Chris Bell is not Skyler Bell

    def test_the_table_has_a_row_per_line_and_the_total(self, app):
        open_ladder(app)
        fetch(app)
        choose(app, "Skyler Bell")
        table = next(d.value for d in app.dataframe if "Over" in d.value.columns)
        assert list(table["Over"]) == [14.5, 33.5, 54.5, 79.5]
        totals = " ".join(m.value for m in app.markdown)
        assert "4 bets × \\$5 = \\$20 total" in totals   # escaped, or it renders as LaTeX

    def test_a_line_without_ids_is_called_out(self, app):
        open_ladder(app)
        fetch(app)
        choose(app, "Skyler Bell")
        assert any("without FanDuel's IDs" in w.value for w in app.warning)

    def test_nothing_posted_yet_says_so(self, app):
        FakeOddsAPI.response = {"bookmakers": [{"key": "fanduel", "markets": []}]}
        open_ladder(app)
        fetch(app)
        assert any("hasn't posted" in w.value for w in app.warning)

    def test_rerendering_does_not_refetch(self, app):
        """Picking a player or changing the stake must not spend more credits."""
        open_ladder(app)
        fetch(app)
        choose(app, "Skyler Bell")
        next(n for n in app.number_input if n.key == "ladder_stake").set_value(10.0).run()
        assert sum(isinstance(c, tuple) for c in FakeOddsAPI.calls) == 1
