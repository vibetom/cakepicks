"""Saved working state: pruning, size limits, and round trips."""

import json

import pandas as pd
import pytest

from core import snapshots
from core.odds import parse_event_props
from core.store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path)


class TestPruning:
    def test_under_and_no_outcomes_are_dropped(self, raw_by_event):
        """Selection is overs-only, so the other side is dead weight."""
        pruned = snapshots.prune_odds(raw_by_event)
        for payload in pruned.values():
            for bookmaker in payload["bookmakers"]:
                for market in bookmaker["markets"]:
                    sides = {o["name"].lower() for o in market["outcomes"]}
                    assert not sides & {"under", "no"}

    def test_pruning_does_not_change_what_gets_parsed(self, raw_by_event):
        """The whole point: a restored payload must score identically."""
        pruned = snapshots.prune_odds(raw_by_event)
        for event_id, payload in raw_by_event.items():
            before = parse_event_props(payload)
            after = parse_event_props(pruned[event_id])
            assert before == after

    def test_pruning_is_substantial(self, raw_by_event):
        before = len(json.dumps(raw_by_event))
        after = len(json.dumps(snapshots.prune_odds(raw_by_event)))
        assert after < before

    def test_malformed_entries_are_skipped(self):
        assert snapshots.prune_odds({"a": "not a dict"}) == {}
        assert snapshots.prune_odds({}) == {}
        assert snapshots.prune_odds(None) == {}

    def test_payload_without_bookmakers(self):
        pruned = snapshots.prune_odds({"e": {"id": "e"}})
        assert pruned["e"]["bookmakers"] == []


class TestOddsSnapshot:
    def test_round_trip(self, store, events, raw_by_event):
        saved, note = snapshots.save_odds(
            store, events=events, raw_by_event=raw_by_event,
            markets=["player_reception_yds"], fetched_at="2026-09-10T18:00:00+00:00")
        assert saved and note is None

        loaded = snapshots.load_odds(store)
        assert loaded["fetched_at"] == "2026-09-10T18:00:00+00:00"
        assert loaded["markets"] == ["player_reception_yds"]
        assert set(loaded["raw_by_event"]) == set(raw_by_event)
        assert len(loaded["events"]) == len(events)

    def test_restored_payload_still_scores(self, store, events, raw_by_event):
        snapshots.save_odds(store, events=events, raw_by_event=raw_by_event,
                            markets=[], fetched_at=None)
        loaded = snapshots.load_odds(store)
        for event_id, payload in loaded["raw_by_event"].items():
            assert parse_event_props(payload) == parse_event_props(raw_by_event[event_id])

    def test_written_compactly(self, store, tmp_path, events, raw_by_event):
        """Indentation is about a third of the size, and the file has a ceiling."""
        snapshots.save_odds(store, events=events, raw_by_event=raw_by_event,
                            markets=[], fetched_at=None)
        body = (tmp_path / "odds" / "latest.json").read_text()
        assert "\\n  " not in body

    def test_nothing_saved_reads_as_none(self, store):
        assert snapshots.load_odds(store) is None

    def test_empty_payload_is_not_a_usable_snapshot(self, store):
        store.write_json(snapshots.ODDS_PATH, {"raw_by_event": {}}, "m")
        assert snapshots.load_odds(store) is None

    def test_corrupt_snapshot_reads_as_none(self, store, tmp_path):
        (tmp_path / "odds").mkdir()
        (tmp_path / "odds" / "latest.json").write_text("{ not json")
        assert snapshots.load_odds(store) is None

    def test_oversize_snapshot_is_refused_with_an_explanation(self, store, events):
        """GitHub returns empty content for files over 1 MB, so guard the write."""
        bloated = {
            f"evt{i}": {
                "id": f"evt{i}",
                "bookmakers": [{"key": "fanduel", "markets": [{
                    "key": "player_reception_yds",
                    "outcomes": [{"name": "Over", "description": "x" * 400,
                                  "price": -110, "point": 1.5}
                                 for _ in range(120)],
                }]}],
            }
            for i in range(30)
        }
        saved, note = snapshots.save_odds(
            store, events=events, raw_by_event=bloated, markets=[], fetched_at=None)
        assert not saved
        assert "too large" in note
        assert "fewer markets" in note
        assert snapshots.load_odds(store) is None

    def test_a_store_that_raises_is_reported_not_crashed(self, events, raw_by_event):
        class Broken(LocalStore):
            def write_json(self, *args, **kwargs):
                raise RuntimeError("github is down")

        saved, note = snapshots.save_odds(
            Broken("/tmp"), events=events, raw_by_event=raw_by_event,
            markets=[], fetched_at=None)
        assert not saved and "github is down" in note


class TestRosterSnapshot:
    def test_round_trip(self, store, league):
        assert snapshots.save_rosters(store, teams=league, fetched_at="2026-09-10T18:00:00Z")
        loaded = snapshots.load_rosters(store)
        assert len(loaded["teams"]) == len(league)
        assert loaded["teams"][0]["team_name"] == league[0]["team_name"]

    def test_nothing_saved(self, store):
        assert snapshots.load_rosters(store) is None

    def test_empty_roster_is_not_saved_as_usable(self, store):
        snapshots.save_rosters(store, teams=[], fetched_at=None)
        assert snapshots.load_rosters(store) is None


class TestProjectionSnapshot:
    def test_round_trip_preserves_values(self, store, projections):
        assert snapshots.save_projections(
            store, frame=projections, filename="week2.csv", warnings=["note"])
        frame, meta = snapshots.load_projections(store)
        assert meta["filename"] == "week2.csv"
        assert meta["warnings"] == ["note"]
        assert list(frame.columns) == list(projections.columns)
        assert len(frame) == len(projections)
        assert frame.iloc[0]["recvYds"] == pytest.approx(projections.iloc[0]["recvYds"])
        assert frame.iloc[0]["playerName"] == projections.iloc[0]["playerName"]

    def test_restored_frame_still_scores(self, store, projections):
        from core.projections import projection_index, row_to_projection

        snapshots.save_projections(store, frame=projections, filename="w.csv")
        frame, _ = snapshots.load_projections(store)
        assert projection_index(frame) == projection_index(projections)
        assert row_to_projection(frame, 0) == row_to_projection(projections, 0)

    def test_none_values_survive(self, store):
        frame = pd.DataFrame([{"playerName": "A", "team": None, "recvYds": 1.0}])
        snapshots.save_projections(store, frame=frame, filename="w.csv")
        restored, _ = snapshots.load_projections(store)
        assert restored.iloc[0]["team"] is None

    def test_empty_frame_is_not_saved(self, store):
        assert not snapshots.save_projections(
            store, frame=pd.DataFrame(), filename="w.csv")
        assert snapshots.load_projections(store) == (None, {})

    def test_nothing_saved(self, store):
        assert snapshots.load_projections(store) == (None, {})


class TestAge:
    def test_hours(self):
        import datetime as dt

        past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).isoformat()
        assert snapshots.age_in_hours(past) == pytest.approx(5, abs=0.1)

    def test_z_suffix(self):
        assert snapshots.age_in_hours("2026-09-10T18:00:00Z") is not None

    def test_future_is_clamped_to_zero(self):
        import datetime as dt

        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)).isoformat()
        assert snapshots.age_in_hours(future) == 0.0

    def test_unparseable(self):
        assert snapshots.age_in_hours("not a date") is None
        assert snapshots.age_in_hours(None) is None


class TestManualBackup:
    """The escape hatch for users who have not set up durable storage."""

    def test_downloaded_document_matches_what_the_store_would_write(
        self, store, events, raw_by_event
    ):
        manual = snapshots.build_odds_document(
            events=events, raw_by_event=raw_by_event,
            markets=["player_rush_yds"], fetched_at="2026-09-10T18:00:00+00:00")
        snapshots.save_odds(store, events=events, raw_by_event=raw_by_event,
                            markets=["player_rush_yds"],
                            fetched_at="2026-09-10T18:00:00+00:00")
        stored = snapshots.load_odds(store)
        # saved_at differs by construction time; everything else must match.
        for key in ("fetched_at", "markets", "raw_by_event", "pruned"):
            assert manual[key] == stored[key]

    def test_uploaded_document_is_validated(self):
        assert snapshots.read_odds_document({"raw_by_event": {"e": {}}})
        assert snapshots.read_odds_document({"raw_by_event": {}}) is None
        assert snapshots.read_odds_document({"nope": 1}) is None
        assert snapshots.read_odds_document([1, 2]) is None
        assert snapshots.read_odds_document(None) is None
        assert snapshots.read_odds_document({"raw_by_event": "not a dict"}) is None

    def test_a_downloaded_snapshot_round_trips_through_upload(
        self, events, raw_by_event
    ):
        import json as _json

        document = snapshots.build_odds_document(
            events=events, raw_by_event=raw_by_event, markets=[], fetched_at=None)
        # Exactly what the download button produces and the uploader receives.
        reloaded = snapshots.read_odds_document(_json.loads(_json.dumps(document)))
        assert reloaded is not None
        assert set(reloaded["raw_by_event"]) == set(raw_by_event)
        assert parse_event_props(reloaded["raw_by_event"]["evt-cin-ne"]) == \
            parse_event_props(raw_by_event["evt-cin-ne"])
