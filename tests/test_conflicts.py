"""Legs that fight each other on the same NFL offence."""

import pytest

from core import conflicts, scoring


def leg(market, team="CIN", position="WR", score=0.20, player="A Player",
        line=50.5, key=None):
    return {"market": market, "nfl_team": team, "projection_position": position,
            "score": score, "player_name": player, "line": line,
            "player_key": key or player, "prop_id": f"{player}-{market}"}


#: Rules whose two markets are scored differently. Such a pair cannot be
#: resolved by comparing scores, so selection._weaker defers to the tier logic.
CROSSING = {frozenset({"player_reception_yds", "player_receptions"})}


class TestScoresStayComparable:
    """The rule "the weaker one is replaced" has to mean something.

    A +40% gap and a +40% EV are not the same claim, and this codebase never
    ranks one against the other with a plain comparison. Most rules stay inside
    one scoring kind, so their scores are directly comparable. The ones that do
    not are listed here deliberately: adding another should be a decision, not
    something that slips in because the test only checked a general property.
    """

    def test_exactly_the_known_rules_cross_between_gap_and_ev(self):
        crossing = {markets for markets in conflicts.MARKET_CONFLICTS
                    if not (markets <= scoring.GAP_MARKETS
                            or markets <= scoring.EV_MARKETS)}
        assert crossing == CROSSING

    def test_every_other_rule_is_directly_comparable(self):
        for markets in conflicts.MARKET_CONFLICTS:
            if markets in CROSSING:
                continue
            assert markets <= scoring.GAP_MARKETS or markets <= scoring.EV_MARKETS, markets

    def test_the_touchdown_rule_is_ev_only(self):
        assert conflicts.TD_MARKETS <= scoring.EV_MARKETS

    def test_every_market_named_in_a_rule_is_a_real_market(self):
        named = set(conflicts.TD_MARKETS)
        for markets in conflicts.MARKET_CONFLICTS:
            named |= markets
        assert named <= (scoring.GAP_MARKETS | scoring.EV_MARKETS)


class TestTheListedPairs:
    """Exactly the combinations that were asked for, and no others."""

    @pytest.mark.parametrize("market,reason", [
        ("player_reception_yds", "two receiving-yards legs"),
        ("player_rush_yds", "two rushing-yards legs"),
        ("player_receptions", "two receptions legs"),
        ("player_rush_attempts", "two rush-attempts legs"),
        ("player_pass_yds", "two passing-yards legs"),
    ])
    def test_the_same_market_twice_on_one_team(self, market, reason):
        assert conflicts.conflict(leg(market, player="A"),
                                  leg(market, player="B")) == reason

    def test_pass_yards_against_rushing_yards(self):
        assert conflicts.conflict(
            leg("player_pass_yds", position="QB", player="A"),
            leg("player_rush_yds", position="RB", player="B"),
        ) == "passing yards against rushing yards"

    def test_pass_yards_against_rush_attempts(self):
        assert conflicts.conflict(
            leg("player_pass_yds", position="QB", player="A"),
            leg("player_rush_attempts", position="RB", player="B"),
        ) == "passing yards against rush attempts"

    def test_two_touchdown_legs(self):
        assert conflicts.conflict(
            leg("player_anytime_td", position="RB", player="A"),
            leg("player_anytime_td", position="WR", player="B"),
        ) == "two touchdown legs"

    def test_a_pass_td_and_an_anytime_td_also_count(self):
        assert conflicts.conflict(
            leg("player_pass_tds", position="QB", player="A"),
            leg("player_anytime_td", position="RB", player="B"),
        ) == "two touchdown legs"

    @pytest.mark.parametrize("pair", [
        ("player_reception_yds", "player_rush_yds"),
        ("player_reception_yds", "player_anytime_td"),
        ("player_rush_yds", "player_rush_attempts"),
        ("player_rush_yds", "player_receptions"),
        ("player_pass_yds", "player_reception_yds"),
        ("player_pass_yds", "player_receptions"),
        ("player_pass_yds", "player_pass_tds"),
        ("player_rush_attempts", "player_receptions"),
    ])
    def test_combinations_that_were_not_asked_for_are_allowed(self, pair):
        assert conflicts.conflict(leg(pair[0], player="A"),
                                  leg(pair[1], player="B")) is None


class TestTheQuarterbackException:
    """A QB and the player he throws to help each other, so leave them be."""

    def test_a_quarterback_and_his_wide_receiver(self):
        assert conflicts.conflict(
            leg("player_pass_tds", position="QB", player="Burrow"),
            leg("player_anytime_td", position="WR", player="Chase"),
        ) is None

    def test_a_quarterback_and_his_tight_end(self):
        assert conflicts.conflict(
            leg("player_pass_tds", position="QB", player="Burrow"),
            leg("player_anytime_td", position="TE", player="Gesicki"),
        ) is None

    def test_the_exception_does_not_extend_to_a_running_back(self):
        """As specified: the carve-out names a WR or TE, not any team-mate."""
        assert conflicts.conflict(
            leg("player_pass_tds", position="QB", player="Burrow"),
            leg("player_anytime_td", position="RB", player="Brown"),
        ) == "two touchdown legs"

    def test_the_exception_is_touchdowns_only(self):
        """A QB's passing yards still fight his receiver's... nothing here."""
        assert conflicts.conflict(
            leg("player_pass_yds", position="QB", player="Burrow"),
            leg("player_rush_yds", position="WR", player="Chase"),
        ) == "passing yards against rushing yards"

    def test_two_receivers_are_not_covered_by_it(self):
        assert conflicts.conflict(
            leg("player_anytime_td", position="WR", player="A"),
            leg("player_anytime_td", position="TE", player="B"),
        ) == "two touchdown legs"

    def test_espn_position_is_only_a_fallback(self):
        """ESPN gives "TQB"/"RB/WR" in this league, so PFF's value wins."""
        qb = leg("player_pass_tds", position=None, player="Burrow")
        qb["position"] = "TQB"
        qb["projection_position"] = "QB"
        wr = leg("player_anytime_td", position="WR", player="Chase")
        assert conflicts.conflict(qb, wr) is None

    def test_an_unusable_espn_position_stays_conservative(self):
        """"RB/WR" could be anything, so it is not treated as a receiver."""
        qb = leg("player_pass_tds", position=None, player="Burrow")
        qb["position"] = "TQB"
        flex = leg("player_anytime_td", position=None, player="Someone")
        flex["position"] = "RB/WR"
        assert conflicts.conflict(qb, flex) == "two touchdown legs"


class TestScope:
    def test_different_nfl_teams_never_conflict(self):
        assert conflicts.conflict(
            leg("player_reception_yds", team="CIN", player="A"),
            leg("player_reception_yds", team="NE", player="B"),
        ) is None

    def test_a_missing_nfl_team_is_not_treated_as_a_match(self):
        assert conflicts.conflict(
            leg("player_reception_yds", team=None, player="A"),
            leg("player_reception_yds", team=None, player="B"),
        ) is None

    def test_the_same_player_twice_is_left_to_the_existing_guard(self):
        one = leg("player_reception_yds", player="Chase", key="chase")
        two = leg("player_reception_yds", player="Chase", key="chase")
        assert conflicts.conflict(one, two) is None


class TestFind:
    def test_it_reports_every_pair_with_indices(self):
        legs = [leg("player_reception_yds", player="A"),
                leg("player_reception_yds", player="B"),
                leg("player_rush_yds", team="NE", player="C")]
        found = conflicts.find(legs)
        assert found == [(0, 1, "two receiving-yards legs")]

    def test_a_clean_slate_reports_nothing(self):
        assert conflicts.find([
            leg("player_reception_yds", team="CIN", player="A"),
            leg("player_rush_yds", team="NE", player="B"),
        ]) == []

    def test_three_of_a_kind_yields_all_three_pairs(self):
        legs = [leg("player_receptions", player=name) for name in "ABC"]
        assert len(conflicts.find(legs)) == 3


class TestDescribe:
    def test_it_names_the_player_market_and_line(self):
        assert conflicts.describe(
            leg("player_reception_yds", player="Ja'Marr Chase", line=74.5)
        ) == "Ja'Marr Chase Rec Yds o74.5"

    def test_a_prop_with_no_line_omits_it(self):
        assert conflicts.describe(
            leg("player_anytime_td", player="Ja'Marr Chase", line=None)
        ) == "Ja'Marr Chase Anytime TD"


# ---------------------------------------------------------------------------
# End to end through select_picks: the clash is found AND something is done.
# ---------------------------------------------------------------------------

from core.config import DEFAULTS                                # noqa: E402
from core.selection import select_picks                         # noqa: E402


def prop(market, *, team="CIN", position="WR", score, player, line=50.5,
         eligible=True):
    return {
        "market": market, "market_label": scoring.MARKET_LABELS[market],
        "nfl_team": team, "projection_position": position, "position": position,
        "score": score, "kind": "gap" if market in scoring.GAP_MARKETS else "ev",
        "player_name": player, "player_name_espn": player, "player_key": player,
        "line": line, "event_id": "evt", "eligible": eligible,
        "needs_review": False, "exclusion_codes": [], "price": -110,
    }


def slate(*rosters):
    """(scored, teams) for one fantasy team per roster given."""
    teams, by_team = [], {}
    for index, props in enumerate(rosters, start=1):
        teams.append({"team_id": index, "team_name": f"Team {index}",
                      "owner": f"Owner {index}", "players": []})
        by_team[index] = list(props)
    return {"by_team": by_team}, teams


@pytest.fixture
def config():
    return {**DEFAULTS, "gap_threshold": 0.05, "ev_threshold": 0.10}


class TestResolutionThroughSelectPicks:
    def test_the_weaker_of_two_clashing_legs_is_replaced(self, config):
        """Two CIN receiving-yards legs; the +8% one gives way."""
        scored, teams = slate(
            [prop("player_reception_yds", score=0.20, player="Chase")],
            [prop("player_reception_yds", score=0.08, player="Higgins"),
             prop("player_rush_yds", team="NE", score=0.06, player="Gainwell")],
        )
        picks = select_picks(scored, teams, config)
        assert picks[0]["pick"]["player_name"] == "Chase"      # stronger, kept
        assert picks[1]["pick"]["player_name"] == "Gainwell"   # swapped away
        assert "two receiving-yards legs" in str(picks[1]["conflict_notes"])

    def test_the_stronger_leg_is_untouched_and_unannotated(self, config):
        scored, teams = slate(
            [prop("player_reception_yds", score=0.20, player="Chase")],
            [prop("player_reception_yds", score=0.08, player="Higgins"),
             prop("player_rush_yds", team="NE", score=0.06, player="Gainwell")],
        )
        picks = select_picks(scored, teams, config)
        assert picks[0]["conflict_notes"] == []

    def test_a_slot_with_no_clean_alternative_empties_out(self, config):
        scored, teams = slate(
            [prop("player_reception_yds", score=0.20, player="Chase")],
            [prop("player_reception_yds", score=0.08, player="Higgins")],
        )
        picks = select_picks(scored, teams, config)
        assert picks[1]["pick"] is None
        assert "two receiving-yards legs" in picks[1]["none_reason"]

    def test_a_hand_picked_leg_is_never_displaced(self, config):
        """Even when it is the weaker one: the user said what they wanted."""
        weak = prop("player_reception_yds", score=0.08, player="Higgins")
        scored, teams = slate(
            [prop("player_reception_yds", score=0.20, player="Chase"),
             prop("player_rush_yds", team="NE", score=0.06, player="Gainwell")],
            [weak],
        )
        from core.selection import prop_id

        picks = select_picks(scored, teams, config,
                             overrides={2: prop_id(weak)})
        assert picks[1]["pick"]["player_name"] == "Higgins"    # kept by hand
        assert picks[0]["pick"]["player_name"] == "Gainwell"   # the other moved

    def test_two_hand_picked_legs_are_left_alone_and_reported(self, config):
        from core.selection import prop_id

        one = prop("player_reception_yds", score=0.20, player="Chase")
        two = prop("player_reception_yds", score=0.08, player="Higgins")
        scored, teams = slate([one], [two])
        picks = select_picks(scored, teams, config,
                             overrides={1: prop_id(one), 2: prop_id(two)})
        assert picks[0]["pick"] and picks[1]["pick"]
        assert "both were chosen by hand" in str(picks[0]["conflict_notes"])
        assert "both were chosen by hand" in str(picks[1]["conflict_notes"])

    def test_a_replacement_that_clashes_again_is_resolved_too(self, config):
        """Three CIN receivers: the pass has to run more than once."""
        scored, teams = slate(
            [prop("player_reception_yds", score=0.30, player="Chase")],
            [prop("player_reception_yds", score=0.20, player="Higgins"),
             prop("player_reception_yds", score=0.19, player="Iosivas"),
             prop("player_rush_yds", team="NE", score=0.06, player="Gainwell")],
            [prop("player_reception_yds", score=0.10, player="Jones"),
             prop("player_rush_attempts", team="GB", score=0.07, player="Lloyd")],
        )
        picks = select_picks(scored, teams, config)
        markets = [(r["pick"]["nfl_team"], r["pick"]["market"]) for r in picks]
        assert markets.count(("CIN", "player_reception_yds")) == 1

    def test_the_qb_receiver_pair_survives_selection(self, config):
        scored, teams = slate(
            [prop("player_pass_tds", position="QB", score=0.30, player="Burrow",
                  line=1.5)],
            [prop("player_anytime_td", position="WR", score=0.20, player="Chase",
                  line=None)],
        )
        picks = select_picks(scored, teams, config)
        assert picks[0]["pick"]["player_name"] == "Burrow"
        assert picks[1]["pick"]["player_name"] == "Chase"
        assert all(not r["conflict_notes"] for r in picks)

    def test_the_toggle_turns_the_whole_pass_off(self, config):
        scored, teams = slate(
            [prop("player_reception_yds", score=0.20, player="Chase")],
            [prop("player_reception_yds", score=0.08, player="Higgins"),
             prop("player_rush_yds", team="NE", score=0.06, player="Gainwell")],
        )
        picks = select_picks(scored, teams, {**config, "avoid_team_conflicts": False})
        assert picks[1]["pick"]["player_name"] == "Higgins"
        assert all(not r["conflict_notes"] for r in picks)

    def test_legs_on_different_nfl_teams_are_never_disturbed(self, config):
        scored, teams = slate(
            [prop("player_reception_yds", team="CIN", score=0.20, player="Chase")],
            [prop("player_reception_yds", team="NE", score=0.08, player="Boutte")],
        )
        picks = select_picks(scored, teams, config)
        assert [r["pick"]["player_name"] for r in picks] == ["Chase", "Boutte"]
        assert all(not r["conflict_notes"] for r in picks)

    def test_a_resolved_slate_has_no_clashes_left(self, config):
        scored, teams = slate(
            [prop("player_receptions", score=0.30, player="Chase", line=4.5)],
            [prop("player_receptions", score=0.25, player="Higgins", line=3.5),
             prop("player_rush_yds", team="NE", score=0.06, player="Gainwell")],
            [prop("player_receptions", score=0.20, player="Iosivas", line=2.5),
             prop("player_pass_yds", team="GB", score=0.08, player="Love")],
        )
        picks = select_picks(scored, teams, config)
        legs = [r["pick"] for r in picks if r["pick"]]
        assert conflicts.find(legs) == []

    def test_ties_break_the_same_way_every_run(self, config):
        """Otherwise the parlay flaps between two equal legs on each rerun."""
        def build():
            return slate(
                [prop("player_reception_yds", score=0.20, player="Chase")],
                [prop("player_reception_yds", score=0.20, player="Higgins"),
                 prop("player_rush_yds", team="NE", score=0.06, player="Gainwell")],
            )
        first = [r["pick"]["player_name"] for r in select_picks(*build(), config)]
        second = [r["pick"]["player_name"] for r in select_picks(*build(), config)]
        assert first == second

    def test_a_slot_swapped_twice_keeps_both_notes(self, config):
        """Overwriting would hide the swap that caused the second one."""
        scored, teams = slate(
            [prop("player_reception_yds", score=0.40, player="Chase")],
            [prop("player_rush_yds", score=0.30, player="Mixon")],
            [prop("player_reception_yds", score=0.20, player="Iosivas"),
             prop("player_rush_yds", score=0.15, player="Brown"),
             prop("player_rush_yds", team="NE", score=0.06, player="Gainwell")],
        )
        picks = select_picks(scored, teams, config)
        third = picks[2]
        assert third["pick"]["player_name"] == "Gainwell"
        assert len(third["conflict_notes"]) == 2
        assert "two receiving-yards legs" in third["conflict_notes"][0]
        assert "two rushing-yards legs" in third["conflict_notes"][1]


class TestReceivingYardsAgainstReceptions:
    """The one rule that spans both scoring kinds.

    Receiving yards are scored by gap, receptions by EV, so the two legs of
    this clash carry numbers that do not mean the same thing. Resolution
    defers to the tier logic rather than comparing them.
    """

    def test_the_pair_clashes_on_one_team(self):
        assert conflicts.conflict(
            leg("player_reception_yds", player="Chase"),
            leg("player_receptions", player="Higgins"),
        ) == "receiving yards against receptions"

    def test_it_is_direction_agnostic(self):
        assert conflicts.conflict(
            leg("player_receptions", player="Higgins"),
            leg("player_reception_yds", player="Chase"),
        ) == "receiving yards against receptions"

    def test_different_teams_are_still_fine(self):
        assert conflicts.conflict(
            leg("player_reception_yds", team="CIN", player="Chase"),
            leg("player_receptions", team="NE", player="Boutte"),
        ) is None

    def test_the_same_player_is_still_left_to_the_other_guard(self):
        """Chase's yards and Chase's catches agree; they do not compete."""
        one = leg("player_reception_yds", player="Chase", key="chase")
        two = leg("player_receptions", player="Chase", key="chase")
        assert conflicts.conflict(one, two) is None


class TestCrossKindResolution:
    """Which leg gives way when the two scores are not comparable.

    Delegated to the tier logic, so the clash rule and the slot rule can never
    disagree: a qualifying EV prop beats a qualifying gap prop (the EV
    override), and below the thresholds the gap prop is preferred.
    """

    @staticmethod
    def two_team_slate(gap_score, ev_score, spare=True):
        spares = [prop("player_rush_yds", team="NE", score=0.02, player="Spare")]
        return slate(
            [prop("player_reception_yds", score=gap_score, player="Chase")]
            + (spares if spare else []),
            [prop("player_receptions", score=ev_score, player="Higgins", line=4.5)],
        )

    def test_a_qualifying_ev_leg_beats_a_qualifying_gap_leg(self, config):
        """Even though the gap number is the bigger of the two.

        +30% gap against +15% EV: the EV override says the EV prop takes the
        slot, so the same precedence decides the clash.
        """
        picks = select_picks(*self.two_team_slate(0.30, 0.15), config)
        assert picks[1]["pick"]["player_name"] == "Higgins"   # EV kept
        assert picks[0]["pick"]["player_name"] == "Spare"     # gap gave way

    def test_below_the_thresholds_the_gap_leg_is_preferred(self, config):
        """Mirror image: neither clears, and tier 2 favours the gap prop."""
        picks = select_picks(*self.two_team_slate(0.03, 0.04), config)
        assert picks[0]["pick"]["player_name"] == "Chase"     # gap kept
        assert picks[1]["pick"] is None                       # EV gave way

    def test_the_leg_that_clears_its_own_threshold_wins(self, config):
        """A +20% gap that qualifies beats a +6% EV that does not."""
        picks = select_picks(*self.two_team_slate(0.20, 0.06), config)
        assert picks[0]["pick"]["player_name"] == "Chase"
        assert picks[1]["pick"] is None

    def test_it_agrees_with_what_one_roster_would_have_chosen(self, config):
        """The real guarantee: same two props, same winner, either route.

        If these sat on one fantasy roster the tier logic would pick one. The
        clash rule must not pick the other.
        """
        from core.selection import _choose, prop_id

        for gap_score, ev_score in ((0.30, 0.15), (0.03, 0.04), (0.20, 0.06),
                                    (0.40, 0.90), (0.06, 0.11)):
            gap = prop("player_reception_yds", score=gap_score, player="Chase")
            ev = prop("player_receptions", score=ev_score, player="Higgins",
                      line=4.5)
            for candidate in (gap, ev):
                candidate["prop_id"] = prop_id(candidate)
            one_roster, _, _, _ = _choose([gap, ev], config)

            scored, teams = slate([gap, prop("player_rush_yds", team="NE",
                                             score=0.02, player="Spare")], [ev])
            picks = select_picks(scored, teams, config)
            survived = {r["pick"]["player_name"] for r in picks if r["pick"]}
            assert one_roster["player_name"] in survived, (gap_score, ev_score)

    def test_a_hand_picked_leg_still_wins_a_cross_kind_clash(self, config):
        from core.selection import prop_id

        gap = prop("player_reception_yds", score=0.03, player="Chase")
        scored, teams = slate(
            [gap],
            [prop("player_receptions", score=0.50, player="Higgins", line=4.5),
             prop("player_rush_yds", team="NE", score=0.02, player="Spare")],
        )
        picks = select_picks(scored, teams, config, overrides={1: prop_id(gap)})
        assert picks[0]["pick"]["player_name"] == "Chase"
        assert picks[1]["pick"]["player_name"] == "Spare"
