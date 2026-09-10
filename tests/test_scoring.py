"""Odds conversion and Poisson math (§6, acceptance check 6)."""

import math

import pytest

from core import scoring


class TestOddsConversion:
    @pytest.mark.parametrize("american,decimal", [
        (-110, 1.909090909), (-200, 1.5), (100, 2.0), (150, 2.5), (250, 3.5),
    ])
    def test_american_to_decimal(self, american, decimal):
        assert scoring.american_to_decimal(american) == pytest.approx(decimal)

    @pytest.mark.parametrize("american", [-110, -250, 100, 175, 300])
    def test_round_trip(self, american):
        decimal = scoring.american_to_decimal(american)
        assert scoring.decimal_to_american(decimal) == pytest.approx(american)

    def test_impossible_prices_rejected(self):
        # American odds do not exist strictly between -100 and +100.
        for value in (-99, 0, 50, 99):
            with pytest.raises(ValueError):
                scoring.american_to_decimal(value)

    def test_format(self):
        assert scoring.format_american(-110) == "-110"
        assert scoring.format_american(145) == "+145"

    def test_implied_probability(self):
        assert scoring.implied_probability(-110) == pytest.approx(0.5238, abs=1e-4)


class TestPriceFloor:
    """§8: '-105 passes a -110 floor; -120 fails'."""

    def test_better_price_passes(self):
        assert scoring.price_meets_floor(-105, -110)

    def test_worse_price_fails(self):
        assert not scoring.price_meets_floor(-120, -110)

    def test_equal_price_passes(self):
        assert scoring.price_meets_floor(-110, -110)

    def test_plus_money_passes_a_minus_floor(self):
        # The comparison must not be numeric on the American scale.
        assert scoring.price_meets_floor(200, -110)

    def test_minus_money_fails_a_plus_floor(self):
        assert not scoring.price_meets_floor(-110, 100)


class TestPoisson:
    def test_pmf_matches_closed_form(self):
        for lam in (0.5, 2.27, 6.0):
            for k in (0, 1, 3, 7):
                expected = math.exp(-lam) * lam ** k / math.factorial(k)
                assert scoring.poisson_pmf(k, lam) == pytest.approx(expected)

    def test_cdf_is_monotonic_and_bounded(self):
        values = [scoring.poisson_cdf(k, 2.27) for k in range(30)]
        assert values == sorted(values)
        assert values[11] < 1.0
        assert values[-1] == pytest.approx(1.0, abs=1e-9)

    def test_anytime_td_spot_check(self):
        """§14.6: lambda 0.96 -> P ~= 61.7%."""
        assert scoring.anytime_td_probability(0.96) == pytest.approx(0.6171, abs=1e-4)

    def test_receptions_spot_check(self):
        """lambda 2.27, over 1.5 -> P(X>=2) = 66.2%.

        The design doc's §14.6 quotes 68.5% here, but that is the value for
        lambda 2.37; 1 - e^-2.27 * (1 + 2.27) = 0.6622. The implementation
        follows the formula, not the quoted number.
        """
        assert scoring.poisson_over_probability(1.5, 2.27) == pytest.approx(0.6622, abs=1e-4)
        assert scoring.poisson_over_probability(1.5, 2.37) == pytest.approx(0.6850, abs=1e-4)

    def test_over_probability_uses_the_right_tail(self):
        # A 1.5 line wins on 2+, so it must equal 1 - CDF(1), not 1 - CDF(2).
        lam = 3.0
        assert scoring.poisson_over_probability(1.5, lam) == pytest.approx(
            1 - scoring.poisson_cdf(1, lam))

    def test_zero_lambda(self):
        assert scoring.anytime_td_probability(0.0) == 0.0
        assert scoring.poisson_over_probability(0.5, 0.0) == pytest.approx(0.0)

    def test_anytime_lambda_excludes_passing_tds(self):
        projection = {"rushTd": 0.4, "recvTd": 0.5, "returnTd": 0.06, "passTd": 2.5}
        assert scoring.anytime_td_lambda(projection) == pytest.approx(0.96)


class TestGapAndEv:
    def test_gap(self):
        assert scoring.gap(88.4, 74.5) == pytest.approx(0.18657, abs=1e-5)

    def test_negative_gap(self):
        assert scoring.gap(31.8, 38.5) < 0

    def test_gap_guards_zero_line(self):
        assert scoring.gap(50, 0) == 0.0

    def test_expected_value(self):
        # A 60% shot at +110 returns 0.6 * 2.1 - 1 = +26%.
        assert scoring.expected_value(0.6, 110) == pytest.approx(0.26)

    def test_fair_bet_has_zero_ev(self):
        assert scoring.expected_value(0.5, 100) == pytest.approx(0.0)


class TestPriceCeiling:
    """The mirror of the floor: reject prices that are too long."""

    @pytest.mark.parametrize("price,ceiling,expected", [
        (700, 300, False),      # the case that prompted it
        (460, 300, False),
        (301, 300, False),
        (300, 300, True),       # equal passes, as the floor does
        (250, 300, True),
        (-155, 300, True),      # favourites are never too long
        (-114, 300, True),
        (100, 300, True),
    ])
    def test_ceiling(self, price, ceiling, expected):
        assert scoring.price_meets_ceiling(price, ceiling) is expected

    def test_none_disables_it(self):
        assert scoring.price_meets_ceiling(5000, None)

    def test_it_does_not_order_numerically(self):
        """-155 is a shorter price than +200 despite the larger absolute value."""
        assert scoring.price_meets_ceiling(-155, 200)
        assert not scoring.price_meets_ceiling(250, 200)

    def test_floor_and_ceiling_together_bracket_a_range(self):
        floor, ceiling = -120, 300
        inside = [-120, -110, 100, 250, 300]
        outside = [-200, -130, 320, 700]
        for price in inside:
            assert scoring.price_meets_floor(price, floor)
            assert scoring.price_meets_ceiling(price, ceiling)
        for price in outside:
            assert not (scoring.price_meets_floor(price, floor)
                        and scoring.price_meets_ceiling(price, ceiling))
