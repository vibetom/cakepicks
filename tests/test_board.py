"""The slate-wide prop board: scoring, thresholds and filters."""

import pytest

from core import board as board_mod
from core.matching import AliasStore
from core.store import LocalStore


@pytest.fixture
def config():
    return dict(board_mod.PROPS_DEFAULTS)


@pytest.fixture
def scored(events, raw_by_event, projections, config, tmp_path):
    return board_mod.score_board(
        events=events, raw_by_event=raw_by_event, projections=projections,
        config=config, aliases=AliasStore(LocalStore(tmp_path)))


class TestScoreBoard:
    def test_it_scores_props_without_any_roster(self, scored):
        """The whole point: no ESPN league is involved."""
        assert scored["rows"]
        assert all("player" in row and "market" in row for row in scored["rows"])

    def test_it_covers_players_no_fantasy_team_rosters(self, scored):
        """The fantasy app only sees rostered players; this sees everyone."""
        names = {row["player"] for row in scored["rows"]}
        assert "Josh Jacobs" in names
        assert "Joe Burrow" in names

    def test_rows_carry_the_game_and_team(self, scored):
        row = next(r for r in scored["rows"] if r["player"] == "Ja'Marr Chase")
        assert row["team"] == "CIN"
        assert row["opponent"] == "NE"
        assert "@" in row["matchup"]

    def test_book_probability_is_recorded(self, scored):
        row = next(r for r in scored["rows"] if r["kind"] == "ev")
        assert 0 < row["book_probability"] < 1

    def test_every_row_explains_itself(self, scored):
        for row in scored["rows"]:
            assert row["why"]

    def test_a_gap_explanation_names_the_line(self, scored):
        row = next(r for r in scored["rows"] if r["kind"] == "gap")
        assert "line of" in row["why"]

    def test_an_ev_explanation_contrasts_the_probabilities(self, scored):
        row = next(r for r in scored["rows"] if r["kind"] == "ev")
        assert "price implies" in row["why"]

    def test_unmatched_names_are_reported_not_guessed(self, events, raw_by_event,
                                                      projections, config, tmp_path):
        payload = raw_by_event["evt-cin-ne"]
        payload["bookmakers"][0]["markets"].append({
            "key": "player_rush_yds", "sid": "m",
            "outcomes": [{"name": "Over", "description": "Nobody Here",
                          "price": -110, "point": 30.5}]})
        result = board_mod.score_board(
            events=events, raw_by_event=raw_by_event, projections=projections,
            config=config, aliases=AliasStore(LocalStore(tmp_path)))
        assert any(u["name"] == "Nobody Here" for u in result["unmatched"])
        assert not any(r["player_name"] == "Nobody Here" for r in result["rows"])


class TestQualifying:
    def test_thresholds_are_applied_per_kind(self, scored, config):
        """A 10% gap and a 10% EV are not the same claim."""
        config = {**config, "min_gap": 0.15, "min_ev": 0.90}
        plays = board_mod.qualifying(scored["rows"], config)
        for row in plays:
            if row["kind"] == "gap":
                assert row["score"] >= 0.15
            else:
                assert row["score"] >= 0.90

    def test_results_are_sorted_best_first(self, scored, config):
        plays = board_mod.qualifying(scored["rows"], {**config, "min_gap": 0, "min_ev": 0})
        assert plays == sorted(plays, key=lambda r: -r["score"])

    def test_a_prop_failing_a_hard_filter_never_lists(self, scored, config):
        plays = board_mod.qualifying(scored["rows"], config)
        for row in plays:
            blocked = [c for c in row["exclusion_codes"] if c != "sanity_ceiling"]
            assert not blocked

    def test_the_price_ceiling_applies(self, events, raw_by_event, projections,
                                       config, tmp_path):
        tight = {**config, "odds_ceiling": 200, "min_ev": 0.0, "min_gap": 0.0}
        result = board_mod.score_board(
            events=events, raw_by_event=raw_by_event, projections=projections,
            config=tight, aliases=AliasStore(LocalStore(tmp_path)))
        for row in board_mod.qualifying(result["rows"], tight):
            assert scoring_price_ok(row["price"], 200)

    def test_review_flagged_plays_are_hidden_by_default(self, scored, config):
        loose = {**config, "min_gap": 0, "min_ev": 0}
        hidden = board_mod.qualifying(scored["rows"], {**loose, "hide_review": True})
        shown = board_mod.qualifying(scored["rows"], {**loose, "hide_review": False})
        assert len(shown) >= len(hidden)
        assert not any(r.get("needs_review") for r in hidden)


def scoring_price_ok(price, ceiling):
    from core import scoring

    return scoring.price_meets_ceiling(price, ceiling)


class TestFilters:
    def test_options_come_from_the_loaded_slate(self, scored):
        options = board_mod.filter_options(scored["rows"])
        assert "CIN" in options["teams"]
        assert any("@" in m for m in options["matchups"])
        assert "Ja'Marr Chase" in options["players"]
        assert set(options["kinds"]) <= {"gap", "ev"}

    def test_no_filter_means_everything(self, scored):
        rows = scored["rows"]
        assert board_mod.apply_filters(rows) == rows

    def test_filtering_by_team(self, scored):
        rows = board_mod.apply_filters(scored["rows"], teams=["CIN"])
        assert rows and all(r["team"] == "CIN" for r in rows)

    def test_filtering_by_game(self, scored):
        matchup = scored["rows"][0]["matchup"]
        rows = board_mod.apply_filters(scored["rows"], matchups=[matchup])
        assert rows and all(r["matchup"] == matchup for r in rows)

    def test_filtering_by_player(self, scored):
        rows = board_mod.apply_filters(scored["rows"], players=["Ja'Marr Chase"])
        assert rows and all(r["player"] == "Ja'Marr Chase" for r in rows)

    def test_filtering_by_kind(self, scored):
        rows = board_mod.apply_filters(scored["rows"], kinds=["ev"])
        assert rows and all(r["kind"] == "ev" for r in rows)

    def test_filters_combine(self, scored):
        rows = board_mod.apply_filters(scored["rows"], teams=["CIN"], kinds=["gap"])
        assert all(r["team"] == "CIN" and r["kind"] == "gap" for r in rows)

    def test_search_matches_a_partial_name(self, scored):
        rows = board_mod.apply_filters(scored["rows"], search="chase")
        assert rows and all("chase" in r["player"].lower() for r in rows)


class TestPropsConfig:
    def test_it_is_stored_separately_from_the_parlay_bot(self, tmp_path):
        """Changing one app's settings must not disturb the other's."""
        from core.config import CONFIG_FILE

        store = LocalStore(tmp_path)
        board_mod.save_props_config(
            {**board_mod.PROPS_DEFAULTS, "min_gap": 0.42}, store)
        assert board_mod.PROPS_CONFIG_FILE != CONFIG_FILE
        assert store.read_json(CONFIG_FILE) is None
        assert board_mod.load_props_config(store)["min_gap"] == 0.42

    def test_defaults_without_a_store(self):
        assert board_mod.load_props_config(None) == board_mod.PROPS_DEFAULTS

    def test_unknown_keys_are_ignored(self, tmp_path):
        store = LocalStore(tmp_path)
        store.write_json(board_mod.PROPS_CONFIG_FILE, {"nonsense": 1, "min_ev": 0.5}, "m")
        config = board_mod.load_props_config(store)
        assert "nonsense" not in config
        assert config["min_ev"] == 0.5


class TestPercentPresentation:
    """Scores are fractions; anything rendering them must scale first.

    Streamlit's NumberColumn runs printf against the raw value, so a 45.7% gap
    stored as 0.457 renders as "0.5%" unless it is multiplied out.
    """

    def test_scores_are_stored_as_fractions(self, scored, config):
        plays = board_mod.qualifying(scored["rows"], {**config, "min_gap": 0, "min_ev": 0})
        assert plays
        for row in plays:
            assert -2.0 < row["score"] < 20.0     # a fraction, not a percentage

    def test_probabilities_are_fractions_too(self, scored):
        for row in scored["rows"]:
            for key in ("probability", "book_probability"):
                value = row.get(key)
                if value is not None:
                    assert 0.0 <= value <= 1.0
