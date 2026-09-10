"""Parlay math, share text, betslip links, and run logs (§8, §10, §12)."""

import json

import pytest

from core import runlog
from core.betslip import leg_link, parlay_link
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
            events=[], raw_by_event={}, teams=[], diagnostics={},
            coverage={}, odds_fetched_at="2026-09-10T18:00:00+00:00")

    def test_record_is_json_serializable(self, config):
        record = self._record(config)
        assert json.loads(json.dumps(record))["picks"][0]["team_name"] == "A"

    def test_write_and_read_round_trip(self, tmp_path, config):
        path = runlog.write_run(self._record(config), tmp_path)
        assert path and path.exists()
        runs = runlog.list_runs(tmp_path)
        assert len(runs) == 1 and runs[0]["picks"][0]["team_name"] == "A"

    def test_grades_are_written_back(self, tmp_path, config):
        path = runlog.write_run(self._record(config), tmp_path)
        assert runlog.update_results(path, {"1": "Win"})
        assert runlog.list_runs(tmp_path)[0]["picks"][0]["result"] == "Win"

    def test_season_totals(self, tmp_path, config):
        runlog.write_run(self._record(config), tmp_path)
        path = runlog.list_runs(tmp_path)[0]["_path"]
        runlog.update_results(path, {"1": "Win"})
        totals = runlog.season_totals(runlog.list_runs(tmp_path))
        assert totals["legs_won"] == 1
        assert totals["leg_hit_rate"] == pytest.approx(1.0)
        assert totals["parlays_hit"] == 1

    def test_ungraded_runs_do_not_count_as_parlays(self, tmp_path, config):
        runlog.write_run(self._record(config), tmp_path)
        totals = runlog.season_totals(runlog.list_runs(tmp_path))
        assert totals["parlays_graded"] == 0
        assert totals["leg_hit_rate"] is None

    def test_corrupt_run_file_is_skipped(self, tmp_path):
        (tmp_path / "broken.json").write_text("{ nope")
        assert runlog.list_runs(tmp_path) == []
