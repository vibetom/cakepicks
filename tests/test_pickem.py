"""Rendering a slip for a pick'em app, and being honest about what it can't say."""

import pytest

from core import pickem as pickem_mod


def leg(**kwargs):
    base = {
        "player": "Ja'Marr Chase", "market": "player_reception_yds",
        "market_label": "Rec Yds", "line": 74.5, "price": -114,
        "projection": 88.4, "probability": None, "kind": "gap",
    }
    base.update(kwargs)
    return base


class TestSelectionText:
    def test_an_over_reads_as_more(self):
        assert pickem_mod.selection(leg()) == "More 74.5"

    def test_a_prop_without_a_line_reads_as_yes(self):
        assert pickem_mod.selection(leg(market="player_anytime_td", line=None)) == "Yes"

    def test_whole_numbers_do_not_gain_a_decimal(self):
        assert pickem_mod.selection(leg(line=2.0)) == "More 2"

    def test_stat_names_use_the_apps_wording(self):
        assert pickem_mod.stat_name(leg()) == "Receiving Yards"
        assert pickem_mod.stat_name(leg(market="player_receptions")) == "Receptions"

    def test_an_unknown_market_falls_back_to_our_label(self):
        assert pickem_mod.stat_name(leg(market="mystery", market_label="Mystery")) == "Mystery"


class TestHitProbability:
    def test_a_modelled_prop_uses_the_model(self):
        value, source = pickem_mod.hit_probability(leg(probability=0.601))
        assert value == pytest.approx(0.601)
        assert source == "model"

    def test_a_yardage_prop_falls_back_to_the_price(self):
        """Yards are not Poisson, so there is no model probability to use."""
        value, source = pickem_mod.hit_probability(leg(price=-114))
        assert value == pytest.approx(0.5327, abs=1e-3)
        assert source == "price"

    def test_an_unusable_price_reports_unknown(self):
        value, source = pickem_mod.hit_probability(leg(price=0, probability=None))
        assert value is None and source == "unknown"


class TestCombined:
    def test_probabilities_multiply(self):
        legs = [leg(probability=0.6), leg(probability=0.5)]
        result = pickem_mod.combined(legs)
        assert result["probability"] == pytest.approx(0.30)
        assert result["breakeven_multiplier"] == pytest.approx(1 / 0.30)
        assert result["modelled"] == 2 and result["from_price"] == 0

    def test_it_counts_where_each_number_came_from(self):
        """The caller warns on this, so the split has to be reported."""
        result = pickem_mod.combined([leg(probability=0.6), leg(price=-114)])
        assert result["modelled"] == 1
        assert result["from_price"] == 1

    def test_more_legs_means_a_bigger_multiplier_to_break_even(self):
        two = pickem_mod.combined([leg(probability=0.6)] * 2)
        four = pickem_mod.combined([leg(probability=0.6)] * 4)
        assert four["breakeven_multiplier"] > two["breakeven_multiplier"]

    def test_an_empty_slip(self):
        result = pickem_mod.combined([])
        assert result["probability"] is None and result["legs"] == 0

    def test_an_unscoreable_leg_voids_the_figure_rather_than_guessing(self):
        result = pickem_mod.combined([leg(probability=0.6), leg(price=0)])
        assert result["probability"] is None


class TestSlipText:
    def test_each_leg_appears_in_pickem_terms(self):
        text = pickem_mod.slip_text([leg(), leg(player="Makai Lemon", line=24.5)])
        assert "Ja'Marr Chase — Receiving Yards — More 74.5" in text
        assert "Makai Lemon — Receiving Yards — More 24.5" in text

    def test_the_projection_travels_with_the_line(self):
        """It is the only way to judge a different line on the other app."""
        assert "projected 88.4" in pickem_mod.slip_text([leg()])

    def test_it_always_warns_that_lines_differ(self):
        assert "differ" in pickem_mod.slip_text([leg()])

    def test_it_flags_a_price_derived_figure_as_the_markets_view(self):
        """It is the bar the projections claim to beat, not our forecast."""
        text = pickem_mod.slip_text([leg(price=-114)])
        assert "market's view" in text
        assert "not a forecast" in text

    def test_an_empty_slip_says_so(self):
        assert "nothing selected" in pickem_mod.slip_text([])


class TestLineComparison:
    def test_room_is_the_projection_over_the_line(self):
        rows = pickem_mod.line_comparison([leg()])
        assert rows[0]["Room"] == pytest.approx(88.4 - 74.5)
        assert rows[0]["FanDuel line"] == 74.5

    def test_a_prop_without_a_line_has_no_room(self):
        rows = pickem_mod.line_comparison([leg(line=None)])
        assert rows[0]["Room"] is None

    def test_negative_room_is_shown_not_hidden(self):
        rows = pickem_mod.line_comparison([leg(projection=60.0)])
        assert rows[0]["Room"] < 0
