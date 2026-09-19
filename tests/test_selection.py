"""Filters and the three-tier pick logic (§8, acceptance checks 4 and 5)."""

import pytest

from core.odds import playing_team_codes
from core.parlay import combine
from core.rosters import filter_players
from core import scoring
from core import selection
from core.selection import prop_id, score_props, select_picks


@pytest.fixture
def run(events, raw_by_event, projections, league, aliases):
    """Score + select with a given config, the way the app does it."""
    def _run(config, **overrides):
        merged = dict(config)
        merged.update(overrides)
        playing = playing_team_codes(events)
        teams = filter_players(
            league, playing,
            starters_only=bool(merged["starters_only"]),
            exclude_questionable=bool(merged["exclude_questionable"]),
            exclude_doubtful=bool(merged["exclude_doubtful"]),
        )
        scored = score_props(events=events, raw_by_event=raw_by_event, teams=teams,
                             projections=projections, config=merged, aliases=aliases)
        picks = select_picks(scored, teams, merged)
        return {"scored": scored, "picks": picks, "teams": teams, "config": merged}
    return _run


def pick_for(result, team_name):
    return next(r for r in result["picks"] if r["team_name"] == team_name)


def props_for(result, team_id):
    return result["scored"]["by_team"][team_id]


class TestOddsFloor:
    """Acceptance check 4: with floor -110, a -155 receptions over is never picked."""

    def test_juiced_prop_is_excluded(self, run, config):
        result = run(config)
        chase_receptions = [
            p for p in props_for(result, 1)
            if p["market"] == "player_receptions" and p["price"] == -155
        ]
        assert chase_receptions, "fixture should contain the -155 receptions prop"
        assert not chase_receptions[0]["eligible"]
        assert "price_floor" in chase_receptions[0]["exclusion_codes"]

    def test_juiced_prop_is_never_the_pick(self, run, config):
        for floor in (-110, -105, -100):
            result = run(config, odds_floor=floor)
            for row in result["picks"]:
                if row["pick"]:
                    assert row["pick"]["price"] != -155

    def test_loosening_the_floor_admits_it(self, run, config):
        result = run(config, odds_floor=-200)
        chase_receptions = [
            p for p in props_for(result, 1)
            if p["market"] == "player_receptions" and p["price"] == -155
        ]
        assert "price_floor" not in chase_receptions[0]["exclusion_codes"]


class TestEvToggle:
    """Acceptance check 4: EV off means no TD/receptions/pass-TD leg appears."""

    EV_MARKETS = {"player_anytime_td", "player_receptions", "player_pass_tds"}

    def test_ev_off_excludes_every_ev_market(self, run, config):
        result = run(config, ev_enabled=False)
        for row in result["picks"]:
            if row["pick"]:
                assert row["pick"]["market"] not in self.EV_MARKETS

    def test_ev_off_marks_the_reason(self, run, config):
        result = run(config, ev_enabled=False)
        ev_props = [p for team in result["scored"]["by_team"].values()
                    for p in team if p["market"] in self.EV_MARKETS]
        assert ev_props
        assert all("ev_off" in p["exclusion_codes"] for p in ev_props)

    def test_ev_on_allows_them(self, run, config):
        result = run(config, ev_enabled=True, sanity_ceiling=5.0)
        markets = {row["pick"]["market"] for row in result["picks"] if row["pick"]}
        assert markets & self.EV_MARKETS


class TestVolumeFloors:
    def test_low_volume_receiver_is_floored_out(self, run, config):
        # Boutte projects 31.8 receiving yards; a 40-yard floor excludes him.
        result = run(config, yards_floor=40.0)
        boutte = [p for p in props_for(result, 2)
                  if p["market"] == "player_reception_yds"]
        assert boutte and "volume_floor" in boutte[0]["exclusion_codes"]

    def test_floor_below_the_projection_admits_it(self, run, config):
        result = run(config, yards_floor=25.0)
        boutte = [p for p in props_for(result, 2)
                  if p["market"] == "player_reception_yds"]
        assert "volume_floor" not in boutte[0]["exclusion_codes"]

    def test_passing_floor_applies_to_quarterbacks(self, run, config):
        # Burrow projects 271.3 passing yards.
        result = run(config, pass_yards_floor=300.0)
        burrow = [p for p in props_for(result, 1) if p["market"] == "player_pass_yds"]
        assert burrow and "volume_floor" in burrow[0]["exclusion_codes"]


class TestTiers:
    def test_tier_one_when_gap_clears_the_threshold(self, run, config):
        # Chase: 88.4 projected vs a 74.5 line = +18.7%, over the 10% default.
        result = run(config, ev_enabled=False)
        row = pick_for(result, "Chase Lounge")
        assert row["tier"] == 1
        assert row["pick"]["market"] == "player_reception_yds"
        assert row["pick"]["score"] == pytest.approx(0.18657, abs=1e-4)

    def test_tier_two_when_nothing_clears(self, run, config):
        # An unreachable gap threshold leaves only "best available".
        result = run(config, gap_threshold=0.95, ev_threshold=5.0, ev_enabled=False)
        row = pick_for(result, "Chase Lounge")
        assert row["tier"] == 2
        assert row["pick"] is not None

    def test_ev_override_steals_a_qualifying_slot(self, run, config):
        # Chase's yardage prop qualifies, but a low EV bar hands the slot over.
        result = run(config, ev_threshold=0.01, sanity_ceiling=5.0)
        row = pick_for(result, "Chase Lounge")
        assert row["pick"]["kind"] == "ev"
        assert row["tier"] == 1

    def test_high_ev_bar_leaves_the_yardage_pick(self, run, config):
        result = run(config, ev_threshold=0.90, sanity_ceiling=5.0)
        row = pick_for(result, "Chase Lounge")
        assert row["pick"]["kind"] == "gap"


class TestNoneSlots:
    """Acceptance check 5: an all-bye team returns NONE with a reason."""

    def test_bye_team_returns_none(self, run, config):
        result = run(config)
        row = pick_for(result, "Bye Week Blues")
        assert row["pick"] is None
        assert "bye" in row["none_reason"]

    def test_parlay_math_uses_the_reduced_leg_count(self, run, config):
        result = run(config)
        summary = combine(result["picks"], 10.0)
        assert summary["leg_count"] == 2
        assert summary["empty_count"] == 1
        assert summary["empty_teams"] == ["Bye Week Blues"]

    def test_no_props_at_all_reports_it(self, run, config, events, raw_by_event,
                                        projections, league, aliases):
        # Strip every market: each team should report an empty slot, not crash.
        empty = {k: {**v, "bookmakers": []} for k, v in raw_by_event.items()}
        teams = filter_players(league, playing_team_codes(events))
        scored = score_props(events=events, raw_by_event=empty, teams=teams,
                             projections=projections, config=config, aliases=aliases)
        picks = select_picks(scored, teams, config)
        assert all(row["pick"] is None for row in picks)
        assert all(row["none_reason"] for row in picks)


class TestSanityCeiling:
    """§6.5: an outsized EV is flagged, never auto-picked."""

    def test_absurd_ev_is_flagged_not_picked(self, run, config):
        result = run(config)
        row = pick_for(result, "Jacobs Ladder")
        flagged = [c for c in row["review_cards"] if c["market"] == "player_anytime_td"]
        assert flagged, "the +250 anytime TD should trip the 35% ceiling"
        assert row["pick"] is None or row["pick"]["market"] != "player_anytime_td"

    def test_approving_a_flag_inserts_it(self, run, config, events, raw_by_event,
                                         projections, league, aliases):
        teams = filter_players(league, playing_team_codes(events))
        scored = score_props(events=events, raw_by_event=raw_by_event, teams=teams,
                             projections=projections, config=config, aliases=aliases)
        picks = select_picks(scored, teams, config)
        row = next(r for r in picks if r["team_name"] == "Jacobs Ladder")
        flagged = row["review_cards"][0]
        approved = select_picks(scored, teams, config,
                                approved_reviews={flagged["prop_id"]})
        row_after = next(r for r in approved if r["team_name"] == "Jacobs Ladder")
        assert row_after["pick"]["prop_id"] == flagged["prop_id"]

    def test_approval_cannot_bypass_other_filters(self, config, events,
                                                  raw_by_event, projections,
                                                  league, aliases):
        """A prop blocked by the price floor stays blocked even when approved.

        The floor is the guard against taking a juiced price, and unlike the
        odds ceiling no override clears it.

        The floor is +300 rather than +200 on purpose: the fixture's only
        flagged prop is priced at +250, so a +200 floor left this test passing
        without ever exercising its own claim.
        """
        tight = dict(config)
        tight["odds_floor"] = 300          # the flagged +250 prop is below it
        teams = filter_players(league, playing_team_codes(events))
        scored = score_props(events=events, raw_by_event=raw_by_event, teams=teams,
                             projections=projections, config=tight, aliases=aliases)
        flagged = {prop_id(p) for props in scored["by_team"].values()
                   for p in props if p.get("needs_review")}
        assert flagged, "fixture no longer produces a flagged prop below the floor"
        picks = select_picks(scored, teams, tight, approved_reviews=flagged)
        for row in picks:
            if row["pick"]:
                assert scoring.price_meets_floor(row["pick"]["price"], 300)

    def test_approving_clears_the_odds_ceiling(self, config, events, raw_by_event,
                                               projections, league, aliases):
        """The reported bug: a longshot trips the ceiling and the sanity flag.

        Approving it used to do nothing, because the gate only re-admitted
        props the sanity flag alone had blocked.
        """
        tight = {**config, "odds_ceiling": 200}      # the flagged prop is +250
        teams = filter_players(league, playing_team_codes(events))
        scored = score_props(events=events, raw_by_event=raw_by_event, teams=teams,
                             projections=projections, config=tight, aliases=aliases)
        row = next(r for r in select_picks(scored, teams, tight)
                   if r["review_cards"])
        card = row["review_cards"][0]
        assert set(card["exclusion_codes"]) == {"price_ceiling", "sanity_ceiling"}

        after = next(r for r in select_picks(scored, teams, tight,
                                             approved_reviews={card["prop_id"]})
                     if r["team_id"] == row["team_id"])
        assert after["pick"]["prop_id"] == card["prop_id"]
        assert after["approved_past"] == ["price_ceiling"]

    def test_an_ordinary_pick_is_not_marked_as_approved_past_anything(
            self, run, config):
        for row in run(config)["picks"]:
            if row["pick"]:
                assert row["approved_past"] == []


class TestPlayerUniqueness:
    def test_a_player_fills_only_one_slot(self, run, config):
        result = run(config)
        used = [row["pick"]["player_key"] for row in result["picks"] if row["pick"]]
        assert len(used) == len(set(used))


class TestOverrides:
    def test_override_replaces_the_automatic_pick(self, run, config, events,
                                                  raw_by_event, projections,
                                                  league, aliases):
        teams = filter_players(league, playing_team_codes(events))
        scored = score_props(events=events, raw_by_event=raw_by_event, teams=teams,
                             projections=projections, config=config, aliases=aliases)
        picks = select_picks(scored, teams, config)
        row = next(r for r in picks if r["team_name"] == "Chase Lounge")
        alternative = row["alternatives"][0]
        overridden = select_picks(scored, teams, config,
                                  overrides={1: alternative["prop_id"]})
        row_after = next(r for r in overridden if r["team_name"] == "Chase Lounge")
        assert row_after["pick"]["prop_id"] == alternative["prop_id"]
        assert row_after["manual"] is True
        assert row_after["tier"] == "manual"


class TestLeagueSize:
    """The parlay has one leg per fantasy team, whatever the league size."""

    @pytest.mark.parametrize("team_count", [2, 3, 8, 12])
    def test_one_row_per_team(self, team_count, config, events, raw_by_event,
                              projections, league, aliases):
        # Clone the fixture league up to the requested size; extra teams have
        # no matching props, so they exercise the empty-slot path too.
        teams_in = []
        for index in range(team_count):
            source = league[index % len(league)]
            clone = {**source, "team_id": 100 + index,
                     "team_name": f"Team {index}",
                     "players": [{**p, "fantasy_team_id": 100 + index}
                                 for p in source["players"]]}
            teams_in.append(clone)

        teams = filter_players(teams_in, playing_team_codes(events))
        scored = score_props(events=events, raw_by_event=raw_by_event, teams=teams,
                             projections=projections, config=config, aliases=aliases)
        picks = select_picks(scored, teams, config)

        assert len(picks) == team_count
        assert [row["team_name"] for row in picks] == [f"Team {i}" for i in range(team_count)]
        # No player may fill two slots, even when rosters are duplicated.
        used = [row["pick"]["player_key"] for row in picks if row["pick"]]
        assert len(used) == len(set(used))
        summary = combine(picks, 10.0)
        assert summary["leg_count"] + summary["empty_count"] == team_count


class TestMarketFilter:
    """Unchecking a market must change the picks, not just the next fetch.

    The cached odds snapshot holds every market that was fetched, so this
    filter has to be applied at scoring time or the control does nothing until
    the user spends credits refetching.
    """

    def test_unchecked_market_is_excluded_with_a_reason(self, run, config):
        result = run(config, markets=["player_reception_yds"])
        rush = [p for p in props_for(result, 2) if p["market"] == "player_rush_yds"]
        assert rush
        assert not rush[0]["eligible"]
        assert "market_off" in rush[0]["exclusion_codes"]
        assert "Rush Yds" in rush[0]["exclusion_reason"]

    def test_picks_change_without_refetching(self, run, config):
        """Same cached payload, different markets, different answer."""
        everything = run(config, sanity_ceiling=5.0)
        narrowed = run(config, sanity_ceiling=5.0, markets=["player_reception_yds"])

        jacobs_before = pick_for(everything, "Jacobs Ladder")
        jacobs_after = pick_for(narrowed, "Jacobs Ladder")
        assert jacobs_before["pick"] is not None
        assert jacobs_before["pick"]["market"] != "player_reception_yds"
        # Jacobs has no receiving-yards prop, so his slot must empty out.
        assert jacobs_after["pick"] is None

    def test_only_selected_markets_can_be_picked(self, run, config):
        allowed = ["player_rush_yds", "player_pass_yds"]
        result = run(config, markets=allowed, sanity_ceiling=5.0)
        for row in result["picks"]:
            if row["pick"]:
                assert row["pick"]["market"] in allowed

    def test_empty_selection_empties_the_parlay(self, run, config):
        result = run(config, markets=[])
        assert all(row["pick"] is None for row in result["picks"])
        assert all(row["none_reason"] for row in result["picks"])

    def test_none_reason_names_the_unchecked_markets(self, run, config):
        result = run(config, markets=[])
        reason = pick_for(result, "Chase Lounge")["none_reason"]
        assert "unchecked" in reason

    def test_missing_markets_key_means_no_filtering(self, run, config):
        """An older saved config without the key must not silently drop props."""
        legacy = {k: v for k, v in config.items() if k != "markets"}
        result = run(legacy, sanity_ceiling=5.0)
        assert any(row["pick"] for row in result["picks"])


class TestFuzzyMatchReporting:
    """Accepted-but-inexact matches must be visible, not silent."""

    def test_accepted_fuzzy_matches_are_reported(self, config, events, raw_by_event,
                                                 league, projections, aliases):
        # Rename the roster entry so the odds feed name only matches loosely,
        # while keeping the first name compatible so it is accepted.
        teams_in = [{**t, "players": [
            {**p, "name": "Ja'Marr Chase Jr." if p["name"] == "Ja'Marr Chase" else p["name"]}
            for p in t["players"]]} for t in league]
        teams = filter_players(teams_in, playing_team_codes(events))
        scored = score_props(events=events, raw_by_event=raw_by_event, teams=teams,
                             projections=projections, config=config, aliases=aliases)
        assert "fuzzy_matches" in scored

    def test_no_fuzzy_matches_on_clean_data(self, config, events, raw_by_event,
                                            league, projections, aliases):
        teams = filter_players(league, playing_team_codes(events))
        scored = score_props(events=events, raw_by_event=raw_by_event, teams=teams,
                             projections=projections, config=config, aliases=aliases)
        assert scored["fuzzy_matches"] == []

    def test_a_different_player_never_attaches(self, config, events, raw_by_event,
                                               league, projections, aliases):
        """The live bug: another player's props scored against your player."""
        teams = filter_players(league, playing_team_codes(events))
        # Add a prop for a same-surname stranger in the same game.
        payload = raw_by_event["evt-cin-ne"]
        payload["bookmakers"][0]["markets"].append({
            "key": "player_anytime_td", "sid": "mkt-x",
            "outcomes": [{"name": "Yes", "description": "Brian Chase", "price": 900}],
        })
        scored = score_props(events=events, raw_by_event=raw_by_event, teams=teams,
                             projections=projections, config=config, aliases=aliases)
        attached = [p for props in scored["by_team"].values() for p in props
                    if p["player_name"] == "Brian Chase"]
        assert attached == []


class TestOddsCeiling:
    """Longshot legs turn a parlay into a lottery ticket."""

    def test_a_longshot_is_excluded_with_a_reason(self, run, config):
        # The fixture's Josh Jacobs anytime TD is +250.
        result = run(config, odds_ceiling=200, sanity_ceiling=5.0)
        td = [p for p in props_for(result, 2) if p["market"] == "player_anytime_td"]
        assert td
        assert not td[0]["eligible"]
        assert "price_ceiling" in td[0]["exclusion_codes"]
        assert "longer than" in td[0]["exclusion_reason"]

    def test_a_price_inside_the_ceiling_passes(self, run, config):
        result = run(config, odds_ceiling=300, sanity_ceiling=5.0)
        td = [p for p in props_for(result, 2) if p["market"] == "player_anytime_td"]
        assert "price_ceiling" not in td[0]["exclusion_codes"]

    def test_no_pick_ever_exceeds_the_ceiling(self, run, config):
        for ceiling in (200, 300, 400):
            result = run(config, odds_ceiling=ceiling, sanity_ceiling=5.0)
            for row in result["picks"]:
                if row["pick"]:
                    assert scoring.price_meets_ceiling(row["pick"]["price"], ceiling)

    def test_none_disables_the_ceiling(self, run, config):
        result = run(config, odds_ceiling=None, sanity_ceiling=5.0)
        td = [p for p in props_for(result, 2) if p["market"] == "player_anytime_td"]
        assert "price_ceiling" not in td[0]["exclusion_codes"]

    def test_a_missing_key_behaves_as_no_ceiling(self, run, config):
        """An older saved config must not silently drop every longshot."""
        legacy = {k: v for k, v in config.items() if k != "odds_ceiling"}
        result = run(legacy, sanity_ceiling=5.0)
        td = [p for p in props_for(result, 2) if p["market"] == "player_anytime_td"]
        assert "price_ceiling" not in td[0]["exclusion_codes"]

    def test_the_slot_falls_back_rather_than_emptying(self, run, config):
        """Excluding a longshot should promote the next-best prop, not blank it."""
        loose = run(config, odds_ceiling=None, sanity_ceiling=5.0)
        tight = run(config, odds_ceiling=200, sanity_ceiling=5.0)
        jacobs_loose = pick_for(loose, "Jacobs Ladder")
        jacobs_tight = pick_for(tight, "Jacobs Ladder")
        assert jacobs_loose["pick"]["market"] == "player_anytime_td"
        assert jacobs_tight["pick"] is not None
        assert jacobs_tight["pick"]["market"] != "player_anytime_td"

    def test_none_reason_names_the_ceiling(self, run, config):
        result = run(config, odds_ceiling=-300, odds_floor=-300)
        reasons = [r["none_reason"] for r in result["picks"] if r["none_reason"]]
        assert any("ceiling" in r for r in reasons)


class TestPickRationale:
    """Every pick must be able to say why it won its slot."""

    def test_a_gap_pick_says_so(self, run, config):
        result = run(config, ev_enabled=False)
        row = pick_for(result, "Chase Lounge")
        assert row["why"] == "Gap"
        assert "cleared the 10% threshold" in row["why_detail"]

    def test_an_ev_override_names_what_it_displaced(self, run, config):
        """The confusing case: a qualifying yardage prop loses its slot."""
        result = run(config, ev_threshold=0.01, sanity_ceiling=5.0)
        row = pick_for(result, "Chase Lounge")
        assert row["why"] == "EV override"
        assert "qualified on gap" in row["why_detail"]
        assert "took the slot" in row["why_detail"]

    def test_tier_two_says_nothing_qualified(self, run, config):
        result = run(config, gap_threshold=0.95, ev_threshold=5.0, ev_enabled=False)
        row = pick_for(result, "Chase Lounge")
        assert row["why"] == "Best available"
        assert "Nothing on this roster cleared a threshold" in row["why_detail"]

    def test_an_empty_slot_has_no_rationale(self, run, config):
        row = pick_for(run(config), "Bye Week Blues")
        assert row["why"] == "—"
        assert row["why_detail"] == ""

    def test_a_manual_override_says_so(self, config, events, raw_by_event,
                                       league, projections, aliases):
        teams = filter_players(league, playing_team_codes(events))
        scored = score_props(events=events, raw_by_event=raw_by_event, teams=teams,
                             projections=projections, config=config, aliases=aliases)
        picks = select_picks(scored, teams, config)
        row = next(r for r in picks if r["team_name"] == "Chase Lounge")
        alternative = row["alternatives"][0]
        overridden = select_picks(scored, teams, config,
                                  overrides={1: alternative["prop_id"]})
        row_after = next(r for r in overridden if r["team_name"] == "Chase Lounge")
        assert row_after["why"] == "Manual"

    def test_every_filled_pick_gets_a_rationale(self, run, config):
        for ceiling in (200, 300, None):
            result = run(config, odds_ceiling=ceiling, sanity_ceiling=5.0)
            for row in result["picks"]:
                if row["pick"]:
                    assert row["why"] and row["why"] != "—"
                    assert row["why_detail"]


class TestApprovingAReviewCard:
    """The "use this leg" button, and the reason it used to do nothing.

    A long price with a big modelled edge trips the odds ceiling *and* the
    sanity ceiling. The old gate only cleared props blocked by the sanity flag
    alone, so on exactly the props most likely to be flagged, approving them
    changed nothing and said nothing.
    """

    @staticmethod
    def longshot_td(price=450, ceiling=300):
        from core.config import DEFAULTS

        config = {**DEFAULTS, "odds_ceiling": ceiling}
        prop = {"market": "player_anytime_td", "price": price, "line": None,
                "player_name": "DeVaughn Vele", "market_label": "Anytime TD",
                "event_id": "e1"}
        projection = {"rushTd": 0.02, "recvTd": 0.55, "returnTd": 0.0,
                      "playerName": "DeVaughn Vele", "team": "NO"}
        return selection.score_prop(prop, projection, config), config

    def test_the_flagged_longshot_trips_both_filters(self):
        scored, _ = self.longshot_td()
        assert scored["needs_review"]
        assert set(scored["exclusion_codes"]) == {"price_ceiling", "sanity_ceiling"}
        assert not scored["review_only"]        # why the old gate refused it

    def test_approving_it_is_now_possible(self):
        scored, _ = self.longshot_td()
        assert selection.can_approve(scored)

    def test_approval_names_what_else_it_overrides(self):
        scored, _ = self.longshot_td()
        assert selection.approval_clears(scored) == ["price_ceiling"]

    def test_a_card_blocked_only_by_the_sanity_flag_overrides_nothing_extra(self):
        scored, _ = self.longshot_td(price=250, ceiling=300)
        assert scored["needs_review"]
        assert selection.approval_clears(scored) == []
        assert selection.can_approve(scored)

    def test_the_human_text_for_each_block_survives(self):
        scored, _ = self.longshot_td()
        detail = scored["exclusion_details"]["price_ceiling"]
        assert "+450" in detail and "+300" in detail

    def test_an_unscorable_prop_cannot_be_approved(self):
        """Nothing to overrule: it has no score to rank."""
        assert not selection.can_approve(
            {"exclusion_codes": ["no_line", "unscored"], "score": None})

    def test_an_eligible_prop_needs_no_approval(self):
        assert not selection.can_approve({"exclusion_codes": [], "score": 0.2})

    def test_approvable_codes_are_all_user_settings(self):
        """Anything here must be a knob the user can reasonably overrule."""
        from core.board import BLOCKING_LABELS

        for code in selection.APPROVABLE_CODES:
            assert code in BLOCKING_LABELS
