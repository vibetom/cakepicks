"""Parlay math, share text, betslip links, and run logs (§8, §10, §12)."""

import json

import pytest

from core import runlog
from core.betslip import (
    ids_from_link, leg_ids, leg_link, parlay_link, parlay_link_status,
)
from core.parlay import combine, describe_prop, score_text, share_text


def leg(price, sid="s1", market_sid="m1", **kwargs):
    base = {
        "player_name": "X", "player_name_espn": "Ja'Marr Chase",
        "market": "player_reception_yds", "market_label": "Rec Yds",
        "line": 61.5, "price": price, "score": 0.142, "kind": "gap",
        "sid": sid, "market_sid": market_sid, "questionable": False,
    }
    base.update(kwargs)
    return base


class TestCombine:
    def test_two_leg_math(self):
        picks = [{"team_id": 1, "team_name": "A", "pick": leg(-110)},
                 {"team_id": 2, "team_name": "B", "pick": leg(125)}]
        summary = combine(picks, 10.0)
        assert summary["decimal_odds"] == pytest.approx(1.9090909 * 2.25)
        assert summary["american_odds"] == pytest.approx(330)
        assert summary["payout"] == pytest.approx(42.95, abs=0.01)
        assert summary["profit"] == pytest.approx(32.95, abs=0.01)

    def test_none_slots_reduce_the_leg_count(self):
        """§8: the summary computes 9-leg (or fewer) math and labels it."""
        picks = [{"team_id": 1, "team_name": "A", "pick": leg(-110)},
                 {"team_id": 2, "team_name": "B", "pick": None,
                  "none_reason": "all on bye"}]
        summary = combine(picks, 10.0)
        assert summary["leg_count"] == 1
        assert summary["empty_count"] == 1
        assert summary["empty_teams"] == ["B"]

    def test_all_empty(self):
        picks = [{"team_id": 1, "team_name": "A", "pick": None}]
        summary = combine(picks, 10.0)
        assert summary["leg_count"] == 0
        assert summary["decimal_odds"] is None
        assert summary["payout"] == 0.0

    def test_implied_probability_shrinks_with_more_legs(self):
        one = combine([{"team_id": 1, "team_name": "A", "pick": leg(-110)}], 10.0)
        two = combine([{"team_id": 1, "team_name": "A", "pick": leg(-110)},
                       {"team_id": 2, "team_name": "B", "pick": leg(-110)}], 10.0)
        assert two["implied_probability"] < one["implied_probability"]


class TestLabels:
    def test_yardage_label(self):
        assert describe_prop(leg(-110)) == "Rec Yds o61.5"

    def test_anytime_td_label_has_no_line(self):
        prop = leg(110, market="player_anytime_td", market_label="Anytime TD", line=None)
        assert describe_prop(prop) == "Anytime TD"

    def test_score_text_by_kind(self):
        assert score_text(leg(-110)) == "Gap +14.2%"
        assert score_text(leg(-110, kind="ev", score=0.231)) == "EV +23.1%"
        assert score_text(leg(-110, score=None)) == "—"


class TestShareText:
    def test_one_line_per_leg(self):
        picks = [
            {"team_id": 1, "team_name": "Team A", "pick": leg(-110)},
            {"team_id": 2, "team_name": "Team B", "pick": leg(125, questionable=True)},
            {"team_id": 3, "team_name": "Team C", "pick": None,
             "none_reason": "all on bye"},
        ]
        text = share_text(picks, combine(picks, 10.0))
        assert "Team A — Ja'Marr Chase Rec Yds o61.5 (-110)" in text
        assert "[Q]" in text
        assert "Team C — NONE (all on bye)" in text
        assert "2-leg parlay: +330" in text
        assert "$10.00 returns $42.95" in text

    def test_marks_manual_picks(self):
        picks = [{"team_id": 1, "team_name": "A", "pick": leg(-110), "manual": True}]
        assert "[manual]" in share_text(picks, combine(picks, 10.0))

    def test_no_legs(self):
        picks = [{"team_id": 1, "team_name": "A", "pick": None}]
        assert "No legs qualified" in share_text(picks, combine(picks, 10.0))


class TestBetslip:
    def test_parlay_link_needs_every_id(self):
        """§12: build the URL only when every leg has both IDs."""
        picks = [{"team_id": 1, "team_name": "A", "pick": leg(-110, sid="s1", market_sid="m1")},
                 {"team_id": 2, "team_name": "B", "pick": leg(125, sid="s2", market_sid="m2")}]
        url = parlay_link(picks)
        assert "marketId[0]=m1" in url and "selectionId[1]=s2" in url

    def test_missing_id_degrades_to_none(self):
        picks = [{"team_id": 1, "team_name": "A", "pick": leg(-110, market_sid=None)}]
        assert parlay_link(picks) is None

    def test_no_legs_is_none(self):
        assert parlay_link([{"team_id": 1, "team_name": "A", "pick": None}]) is None

    def test_leg_link_requires_http(self):
        assert leg_link({"link": "https://fd/x"}) == "https://fd/x"
        assert leg_link({"link": "javascript:alert(1)"}) is None
        assert leg_link({}) is None
        assert leg_link(None) is None


class TestRunLog:
    def _record(self, config):
        picks = [{"team_id": 1, "team_name": "A", "pick": leg(-110), "tier": 1},
                 {"team_id": 2, "team_name": "B", "pick": None,
                  "none_reason": "bye"}]
        return runlog.build_run_record(
            config=config, picks=picks, summary=combine(picks, 10.0),
            events=[{"id": "e1"}], raw_by_event={"e1": {"big": "payload"}},
            teams=[{"team_id": 1}], diagnostics={"unmatched_props": [1, 2]},
            coverage={}, odds_fetched_at="2026-09-10T18:00:00+00:00")

    def test_record_is_json_serializable(self, config):
        record = self._record(config)
        assert json.loads(json.dumps(record))["picks"][0]["team_name"] == "A"

    def test_slim_record_drops_the_bulky_payloads(self, config):
        """History only needs picks and grades, not megabytes of raw odds."""
        slim = runlog.slim_record(self._record(config))
        assert "raw_odds" not in slim and "rosters" not in slim
        assert slim["picks"][0]["team_name"] == "A"
        assert slim["diagnostics"]["unmatched_props"] == 2
        assert slim["event_count"] == 1

    def test_save_and_read_round_trip(self, store, config):
        path = runlog.save_run(store, self._record(config))
        assert path and path.startswith("runs/")
        runs = runlog.list_runs(store)
        assert len(runs) == 1 and runs[0]["picks"][0]["team_name"] == "A"

    def test_saved_run_excludes_raw_odds(self, store, config):
        runlog.save_run(store, self._record(config))
        assert "raw_odds" not in runlog.list_runs(store)[0]

    def test_grades_are_written_back(self, store, config):
        path = runlog.save_run(store, self._record(config))
        assert runlog.update_results(store, path, {"1": "Win"})
        assert runlog.list_runs(store)[0]["picks"][0]["result"] == "Win"

    def test_grading_a_missing_run_fails_cleanly(self, store):
        assert runlog.update_results(store, "runs/nope.json", {"1": "Win"}) is False

    def test_season_totals(self, store, config):
        path = runlog.save_run(store, self._record(config))
        runlog.update_results(store, path, {"1": "Win"})
        totals = runlog.season_totals(runlog.list_runs(store))
        assert totals["legs_won"] == 1
        assert totals["leg_hit_rate"] == pytest.approx(1.0)
        assert totals["parlays_hit"] == 1

    def test_ungraded_runs_do_not_count_as_parlays(self, store, config):
        runlog.save_run(store, self._record(config))
        totals = runlog.season_totals(runlog.list_runs(store))
        assert totals["parlays_graded"] == 0
        assert totals["leg_hit_rate"] is None

    def test_a_lost_leg_sinks_the_parlay(self, store, config):
        path = runlog.save_run(store, self._record(config))
        runlog.update_results(store, path, {"1": "Loss"})
        totals = runlog.season_totals(runlog.list_runs(store))
        assert totals["parlays_hit"] == 0
        assert totals["parlays_graded"] == 1

    def test_corrupt_run_file_is_skipped(self, store, tmp_path):
        (tmp_path / "runs").mkdir()
        (tmp_path / "runs" / "broken.json").write_text("{ nope")
        assert runlog.list_runs(store) == []


class TestWholeParlayLink:
    """§12: one link that loads every leg, for whoever places the bet."""

    def _picks(self, count=3, overrides=None):
        """`overrides` maps a leg index to fields to change on that leg."""
        overrides = overrides or {}
        rows = []
        for i in range(count):
            prop = leg(-110, sid=f"sel{i}", market_sid=f"mkt{i}")
            prop.update(overrides.get(i, {}))
            rows.append({"team_id": i, "team_name": f"Team {i}", "pick": prop})
        return rows

    def test_builds_one_url_with_every_leg(self):
        status = parlay_link_status(self._picks(3))
        assert status["leg_count"] == 3
        url = status["url"]
        for i in range(3):
            assert f"marketId[{i}]=mkt{i}" in url
            assert f"selectionId[{i}]=sel{i}" in url
        assert url.startswith("https://sportsbook.fanduel.com/addToBetslip?")

    def test_ten_legs(self):
        """The real case: a full 10-team league in a single link."""
        status = parlay_link_status(self._picks(10))
        assert status["leg_count"] == 10
        assert status["url"].count("marketId[") == 10
        assert "marketId[9]=mkt9" in status["url"]

    def test_empty_slots_are_skipped_not_fatal(self):
        picks = self._picks(2) + [{"team_id": 9, "team_name": "Bye", "pick": None}]
        status = parlay_link_status(picks)
        assert status["leg_count"] == 2
        assert status["url"].count("marketId[") == 2

    def test_a_leg_without_ids_blocks_the_link_and_names_the_team(self):
        """A partial slip would under-report the bet, which is worse than none."""
        picks = self._picks(3, {1: {"market_sid": None}})
        status = parlay_link_status(picks)
        assert status["url"] is None
        assert status["missing"] == ["Team 1"]
        assert "1 of 3 legs" in status["reason"]
        assert "fetching fresh odds" in status["reason"].lower()

    def test_no_legs_at_all(self):
        status = parlay_link_status([{"team_id": 1, "team_name": "A", "pick": None}])
        assert status["url"] is None
        assert status["leg_count"] == 0

    def test_ids_are_url_encoded(self):
        picks = self._picks(1, {0: {"market_sid": "42.123/456", "sid": "a b&c"}})
        url = parlay_link_status(picks)["url"]
        assert "42.123%2F456" in url
        assert "a%20b%26c" in url

    def test_share_text_carries_the_link(self):
        """The share block is what reaches whoever places the bet."""
        picks = self._picks(2)
        summary = combine(picks, 10.0)
        url = parlay_link_status(picks)["url"]
        text = share_text(picks, summary, parlay_url=url)
        assert url in text
        assert "Tap to load the whole parlay" in text

    def test_share_text_without_a_link_says_nothing_about_it(self):
        picks = self._picks(2)
        text = share_text(picks, combine(picks, 10.0))
        assert "FanDuel" not in text


class TestIdsFromLinks:
    """Falling back to FanDuel's own per-leg links when the feed omits sids."""

    def _leg(self, **kwargs):
        prop = leg(-110, sid=None, market_sid=None)
        prop.update(kwargs)
        return prop

    @pytest.mark.parametrize("url,expected", [
        ("https://sportsbook.fanduel.com/addToBetslip?marketId[0]=42.1&selectionId[0]=99",
         ("42.1", "99")),
        ("https://sportsbook.fanduel.com/addToBetslip?marketId%5B0%5D=42.2&selectionId%5B0%5D=88",
         ("42.2", "88")),
        ("https://sportsbook.fanduel.com/addToBetslip?marketId=42.3&selectionId=77",
         ("42.3", "77")),
        ("https://sportsbook.fanduel.com/addToBetslip?selectionId[0]=66&marketId[0]=42.4",
         ("42.4", "66")),
    ])
    def test_parses_the_known_link_shapes(self, url, expected):
        assert ids_from_link(url) == expected

    @pytest.mark.parametrize("url", [
        "https://sportsbook.fanduel.com/football",
        "https://sportsbook.fanduel.com/addToBetslip?marketId[0]=42.1",   # no selection
        "not a url", "", None, 12345,
    ])
    def test_unusable_links_return_none(self, url):
        assert ids_from_link(url) is None

    def test_leg_ids_fall_back_to_the_link(self):
        prop = self._leg(outcome_link="https://sportsbook.fanduel.com/addToBetslip"
                                      "?marketId[0]=42.9&selectionId[0]=123")
        assert leg_ids(prop) == ("42.9", "123")

    def test_sids_win_when_both_are_present(self):
        prop = self._leg(sid="s", market_sid="m",
                         outcome_link="https://sportsbook.fanduel.com/addToBetslip"
                                      "?marketId[0]=other&selectionId[0]=other")
        assert leg_ids(prop) == ("m", "s")

    def test_a_shared_market_link_is_never_used_as_a_selection(self):
        """The display link can be a market-level URL shared by every player.

        Deriving ids from it would put the same selection on the slip for
        different players, so only the outcome's own link is trusted.
        """
        prop = self._leg(link="https://sportsbook.fanduel.com/addToBetslip"
                              "?marketId[0]=42.1&selectionId[0]=shared")
        assert prop.get("outcome_link") is None
        assert leg_ids(prop) is None

    def test_a_parlay_builds_entirely_from_links(self):
        picks = [
            {"team_id": i, "team_name": f"Team {i}",
             "pick": self._leg(outcome_link="https://sportsbook.fanduel.com/addToBetslip"
                                            f"?marketId[0]=42.{i}&selectionId[0]={i}00")}
            for i in range(3)
        ]
        status = parlay_link_status(picks)
        assert status["url"]
        assert "marketId[2]=42.2" in status["url"]
        assert status["has_sids"] is False
        assert status["has_links"] is True


class TestLinkDiagnostics:
    def test_no_ids_and_no_links_blames_the_feed_plan(self):
        picks = [{"team_id": 1, "team_name": "A",
                  "pick": leg(-110, sid=None, market_sid=None)}]
        status = parlay_link_status(picks)
        assert status["url"] is None
        assert "includeSids" in status["reason"]
        assert status["has_links"] is False

    def test_links_present_but_unparseable_says_so(self):
        prop = leg(-110, sid=None, market_sid=None)
        prop["outcome_link"] = "https://sportsbook.fanduel.com/football"
        status = parlay_link_status([{"team_id": 1, "team_name": "A", "pick": prop}])
        assert status["url"] is None
        assert "link format may have changed" in status["reason"]
        assert status["sample_link"] == "https://sportsbook.fanduel.com/football"

    def test_partial_failure_is_reported_differently(self):
        good = leg(-110, sid="s", market_sid="m")
        bad = leg(-110, sid=None, market_sid=None)
        status = parlay_link_status([
            {"team_id": 1, "team_name": "A", "pick": good},
            {"team_id": 2, "team_name": "B", "pick": bad},
        ])
        assert "1 of 2 legs" in status["reason"]
        assert "posted late" in status["reason"]
        assert status["missing"] == ["B"]
