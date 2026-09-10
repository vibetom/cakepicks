"""PFF CSV parsing and validation (§4, §13)."""

import io

import pytest

from core.projections import (
    ProjectionError, load_projections, projection_index, row_to_projection,
)

HEADER = ("playerName,teamName,position,passYds,passTd,rushAtt,rushYds,rushTd,"
          "recvTargets,recvReceptions,recvYds,recvTd,returnTd\n")
ROW = "Ja'Marr Chase,CIN,WR,0,0,0.2,1.0,0.0,10.1,6.9,88.4,0.62,0\n"


def parse(text):
    return load_projections(io.BytesIO(text.encode()))


class TestParsing:
    def test_basic_parse(self):
        frame, _ = parse(HEADER + ROW)
        assert len(frame) == 1
        assert frame.iloc[0]["playerName"] == "Ja'Marr Chase"
        assert frame.iloc[0]["team"] == "CIN"
        assert frame.iloc[0]["recvYds"] == pytest.approx(88.4)

    def test_columns_are_matched_by_name_not_position(self):
        """PFF reorders columns between releases, so order must not matter."""
        reordered = ("recvYds,position,playerName,teamName,recvReceptions\n"
                     "88.4,WR,Ja'Marr Chase,CIN,6.9\n")
        frame, _ = parse(reordered)
        assert frame.iloc[0]["recvYds"] == pytest.approx(88.4)
        assert frame.iloc[0]["playerName"] == "Ja'Marr Chase"

    def test_alternate_header_spellings(self):
        frame, _ = parse("Player,Team,Pos,Receiving Yards,Receptions\n"
                         "DJ Moore,CHI,WR,64.2,5.1\n")
        assert frame.iloc[0]["recvYds"] == pytest.approx(64.2)
        assert frame.iloc[0]["recvReceptions"] == pytest.approx(5.1)

    def test_pff_team_abbreviations_are_translated(self):
        frame, _ = parse(HEADER + "Marvin Harrison,ARZ,WR,0,0,0,0,0,8,5.1,74.2,0.4,0\n")
        assert frame.iloc[0]["team"] == "ARI"

    def test_missing_stat_columns_default_to_zero_with_a_warning(self):
        frame, warnings = parse("playerName,teamName,position,recvYds\n"
                                "DJ Moore,CHI,WR,64.2\n")
        assert frame.iloc[0]["rushYds"] == 0.0
        assert any("rushYds" in w for w in warnings)

    def test_non_numeric_values_become_zero(self):
        frame, _ = parse(HEADER + "Broken Guy,CIN,WR,-,-,-,-,-,-,-,-,-,-\n")
        assert frame.iloc[0]["recvYds"] == 0.0

    def test_unknown_team_is_flagged(self):
        _, warnings = parse(HEADER + ROW.replace("CIN", "ZZZ"))
        assert any("Unrecognized team" in w for w in warnings)


class TestValidation:
    def test_season_file_is_rejected(self):
        """§4: a season-long file must be blocked, not silently scored."""
        season = HEADER + ROW.replace("88.4", "1402.5")
        with pytest.raises(ProjectionError, match="SEASON"):
            parse(season)

    def test_season_length_passing_yards_rejected(self):
        season = HEADER + "Joe Burrow,CIN,QB,4610,38,45,180,2,0,0,0,0,0\n"
        with pytest.raises(ProjectionError, match="SEASON"):
            parse(season)

    def test_weekly_file_at_the_boundary_passes(self):
        frame, _ = parse(HEADER + ROW.replace("88.4", "199"))
        assert len(frame) == 1

    def test_missing_required_columns(self):
        with pytest.raises(ProjectionError, match="missing required column"):
            parse("foo,bar\n1,2\n")

    def test_empty_file(self):
        with pytest.raises(ProjectionError):
            parse("playerName,teamName,position\n")

    def test_unreadable_input(self):
        with pytest.raises(ProjectionError):
            load_projections(io.BytesIO(b"\x00\x01\x02binary"))


class TestIndexing:
    def test_duplicate_player_team_rows_are_dropped(self):
        """§13: an ambiguous duplicate is treated as unmatched, never guessed."""
        frame, warnings = parse(HEADER + ROW + ROW)
        assert any("Duplicate" in w for w in warnings)
        assert projection_index(frame) == {}

    def test_same_name_different_teams_both_indexed(self):
        frame, _ = parse(HEADER + ROW + ROW.replace("CIN", "GB"))
        assert len(projection_index(frame)) == 2

    def test_row_to_projection_returns_floats(self):
        frame, _ = parse(HEADER + ROW)
        projection = row_to_projection(frame, 0)
        assert projection["recvYds"] == pytest.approx(88.4)
        assert isinstance(projection["recvTd"], float)
        assert projection["team"] == "CIN"
