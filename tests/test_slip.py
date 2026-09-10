"""The hand-built slip: bookkeeping, odds, and the export link."""

import pytest

from core import board as board_mod
from core import slip as slip_mod
from core.matching import AliasStore
from core.store import LocalStore


@pytest.fixture
def config():
    return {**board_mod.PROPS_DEFAULTS, "min_gap": 0.0, "min_ev": 0.0}


@pytest.fixture
def rows(events, raw_by_event, projections, config, tmp_path):
    board = board_mod.score_board(
        events=events, raw_by_event=raw_by_event, projections=projections,
        config=config, aliases=AliasStore(LocalStore(tmp_path)))
    return board_mod.qualifying(board["rows"], config)


class TestBookkeeping:
    def test_adding_and_removing(self, rows):
        first, second = slip_mod.leg_key(rows[0]), slip_mod.leg_key(rows[1])
        keys = slip_mod.add(slip_mod.add([], first), second)
        assert keys == [first, second]
        assert slip_mod.remove(keys, first) == [second]

    def test_adding_the_same_play_twice_is_a_no_op(self, rows):
        """A leg must never appear twice on one slip."""
        key = slip_mod.leg_key(rows[0])
        assert slip_mod.add(slip_mod.add([], key), key) == [key]

    def test_add_many_preserves_order_and_dedupes(self, rows):
        keys = [slip_mod.leg_key(r) for r in rows[:3]]
        out = slip_mod.add_many([keys[0]], keys)
        assert out == keys

    def test_removing_something_absent_is_harmless(self):
        assert slip_mod.remove(["a"], "b") == ["a"]

    def test_resolve_returns_legs_in_slip_order(self, rows):
        keys = [slip_mod.leg_key(rows[2]), slip_mod.leg_key(rows[0])]
        legs, missing = slip_mod.resolve(keys, rows)
        assert not missing
        assert [leg["player"] for leg in legs] == [rows[2]["player"], rows[0]["player"]]

    def test_a_vanished_leg_is_reported_not_dropped(self, rows):
        """A market can be withdrawn between fetches; never silently omit it."""
        keys = [slip_mod.leg_key(rows[0]), "gone|player_rush_yds|Nobody|1.5"]
        legs, missing = slip_mod.resolve(keys, rows)
        assert len(legs) == 1
        assert missing == ["gone|player_rush_yds|Nobody|1.5"]


class TestOdds:
    def test_combined_odds_match_the_parlay_maths(self, rows):
        from core import parlay, scoring

        legs = rows[:3]
        summary = slip_mod.summarize(legs, 10.0)
        expected = 1.0
        for leg in legs:
            expected *= scoring.american_to_decimal(leg["price"])
        assert summary["decimal_odds"] == pytest.approx(expected)
        assert summary["leg_count"] == 3
        assert summary["payout"] == pytest.approx(10.0 * expected)

    def test_an_empty_slip_has_no_odds(self):
        summary = slip_mod.summarize([], 10.0)
        assert summary["leg_count"] == 0
        assert summary["decimal_odds"] is None

    def test_stake_scales_the_payout(self, rows):
        one = slip_mod.summarize(rows[:2], 10.0)
        two = slip_mod.summarize(rows[:2], 20.0)
        assert two["payout"] == pytest.approx(one["payout"] * 2)


class TestExport:
    def test_the_link_covers_every_leg(self, rows):
        legs = rows[:3]
        status = slip_mod.link_status(legs)
        assert status["url"]
        assert status["url"].count("marketId[") == 3

    def test_a_leg_without_ids_blocks_the_link(self, rows):
        legs = [dict(rows[0]), dict(rows[1])]
        legs[1] = {**legs[1], "sid": None, "market_sid": None, "outcome_link": None}
        status = slip_mod.link_status(legs)
        assert status["url"] is None
        assert "1 of 2 legs" in status["reason"]
        assert status["missing"]

    def test_an_empty_slip_says_so(self):
        assert slip_mod.link_status([])["reason"] == "The slip is empty."

    def test_share_text_lists_each_leg_and_the_link(self, rows):
        legs = rows[:2]
        summary = slip_mod.summarize(legs, 10.0)
        url = slip_mod.link_status(legs)["url"]
        text = slip_mod.share_text(legs, summary, url)
        assert "Prop Finder parlay" in text
        for leg in legs:
            assert leg["player"] in text
        assert url in text
        assert "2-leg parlay" in text

    def test_share_text_labels_legs_by_game(self, rows):
        legs = rows[:1]
        text = slip_mod.share_text(legs, slip_mod.summarize(legs, 10.0))
        assert legs[0]["matchup"] in text


class TestPersistence:
    def test_a_slip_survives_a_restart(self, tmp_path, rows):
        store = LocalStore(tmp_path)
        keys = [slip_mod.leg_key(r) for r in rows[:2]]
        assert slip_mod.save_slip(store, keys)
        assert slip_mod.load_slip(store) == keys

    def test_nothing_saved_is_an_empty_slip(self, tmp_path):
        assert slip_mod.load_slip(LocalStore(tmp_path)) == []

    def test_a_corrupt_slip_file_is_ignored(self, tmp_path):
        (tmp_path / "slip.json").write_text("{ not json")
        assert slip_mod.load_slip(LocalStore(tmp_path)) == []

    def test_a_store_that_raises_does_not_crash(self, rows):
        class Broken(LocalStore):
            def write_json(self, *args, **kwargs):
                raise RuntimeError("down")

        assert slip_mod.save_slip(Broken("/tmp"), ["a"]) is False
