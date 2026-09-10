"""Name normalization, aliases, and team-scoped fuzzy matching (§9)."""

import pytest

from core.matching import (
    AliasStore, NameMatcher, first_names_compatible, normalize_name,
)
from core.store import LocalStore
from core.teams import ESPN_PRO_TEAM_ID, CANONICAL, normalize_team


class TestNormalize:
    @pytest.mark.parametrize("raw,expected", [
        ("D.J. Moore", "dj moore"),
        ("Marvin Harrison Jr.", "marvin harrison"),
        ("Kenneth Walker III", "kenneth walker"),
        ("Ja'Marr Chase", "jamarr chase"),
        ("Amon-Ra St. Brown", "amonra st brown"),
        ("  Travis   Kelce  ", "travis kelce"),
        ("José Rodríguez", "jose rodriguez"),
        ("", ""),
    ])
    def test_normalize(self, raw, expected):
        assert normalize_name(raw) == expected

    def test_suffix_only_name_survives(self):
        # "Vi" is a real name fragment, not the roman numeral V.
        assert normalize_name("V") == "v"


class TestTeamNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("ARZ", "ARI"), ("BLT", "BAL"), ("CLV", "CLE"), ("HST", "HOU"),
        ("LA", "LAR"), ("WAS", "WSH"), ("JAC", "JAX"), ("OAK", "LV"),
        ("SD", "LAC"), ("Kansas City Chiefs", "KC"), ("New York Jets", "NYJ"),
        (22, "ARI"), (34, "HOU"),
    ])
    def test_aliases(self, raw, expected):
        assert normalize_team(raw) == expected

    def test_unknown_is_none(self):
        assert normalize_team("XYZ") is None
        assert normalize_team("") is None
        assert normalize_team(None) is None

    def test_every_espn_id_maps_to_a_canonical_code(self):
        assert set(ESPN_PRO_TEAM_ID.values()) == set(CANONICAL)
        assert len(CANONICAL) == 32


@pytest.fixture
def matcher(tmp_path):
    entries = {
        "chase": ("Ja'Marr Chase", "CIN"),
        "boutte": ("Kayshon Boutte", "NE"),
        "moore": ("D.J. Moore", "CHI"),
        "harrison": ("Marvin Harrison Jr.", "ARI"),
        "thomas-no": ("Michael Thomas", "NO"),
        "thomas-sea": ("Michael Thomas", "SEA"),
    }
    return NameMatcher(entries, AliasStore(LocalStore(tmp_path)), threshold=90)


class TestMatching:
    def test_exact_after_normalization(self, matcher):
        assert matcher.match("DJ Moore", ["CHI"]).key == "moore"
        assert matcher.match("Marvin Harrison", ["ARI"]).key == "harrison"

    def test_match_is_scoped_by_team(self, matcher):
        """Same name on another team must not match — that is the collision guard."""
        assert not matcher.match("D.J. Moore", ["NE"]).matched

    def test_duplicate_name_resolved_by_team(self, matcher):
        assert matcher.match("Michael Thomas", ["NO"]).key == "thomas-no"
        assert matcher.match("Michael Thomas", ["SEA"]).key == "thomas-sea"

    def test_duplicate_name_unscoped_is_ambiguous(self, matcher):
        result = matcher.match("Michael Thomas", None)
        assert not result.matched
        assert result.method == "ambiguous"

    def test_below_threshold_is_unmatched_with_a_candidate(self, matcher):
        result = matcher.match("K. Boutte", ["NE"])
        assert not result.matched
        assert result.candidate == "boutte"
        assert 0 < result.candidate_score < 90

    def test_fuzzy_accepts_above_threshold(self, tmp_path):
        entries = {"chase": ("Ja'Marr Chase", "CIN")}
        matcher = NameMatcher(entries, AliasStore(LocalStore(tmp_path)), threshold=85)
        result = matcher.match("Jamar Chase", ["CIN"])
        assert result.key == "chase"
        assert result.method == "fuzzy"

    def test_empty_name(self, matcher):
        assert not matcher.match("", ["CIN"]).matched


class TestAliases:
    def test_alias_resolves_an_otherwise_unmatched_name(self, tmp_path):
        store = AliasStore(LocalStore(tmp_path))
        entries = {"boutte": ("Kayshon Boutte", "NE")}
        matcher = NameMatcher(entries, store, threshold=95)
        assert not matcher.match("K. Boutte", ["NE"]).matched

        store.add("K. Boutte", "Kayshon Boutte")
        matcher = NameMatcher(entries, store, threshold=95)
        result = matcher.match("K. Boutte", ["NE"])
        assert result.key == "boutte"
        assert result.method == "alias"

    def test_aliases_persist_across_instances(self, tmp_path):
        backend = LocalStore(tmp_path)
        AliasStore(backend).add("K. Boutte", "Kayshon Boutte")
        assert AliasStore(backend).get("k boutte") == "Kayshon Boutte"

    def test_corrupt_alias_file_is_ignored(self, tmp_path):
        (tmp_path / "aliases.json").write_text("{ not json")
        assert AliasStore(LocalStore(tmp_path)).as_dict() == {}

    def test_no_store_means_no_persistence_but_no_crash(self):
        store = AliasStore(None)
        assert store.add("K. Boutte", "Kayshon Boutte") is False
        assert store.get("K. Boutte") == "Kayshon Boutte"   # still applies in-session


class TestFirstNameGuard:
    """Two different people with the same surname must never fuzzy-match.

    Similarity scoring is dominated by the surname, so real NFL pairs clear a
    sensible cutoff while being different players. Both cases below were found
    in live data: Brian Robinson Jr. scored 93 against Bijan Robinson, and
    Malik Washington scored 90 against Mike Washington Jr.
    """

    @pytest.mark.parametrize("left,right", [
        ("brian robinson", "bijan robinson"),
        ("malik washington", "mike washington"),
        ("gabe davis", "gabriel davis"),
        ("kenny gainwell", "kenneth gainwell"),
    ])
    def test_incompatible_first_names_are_rejected(self, left, right):
        assert not first_names_compatible(left, right)

    @pytest.mark.parametrize("left,right", [
        ("k boutte", "kayshon boutte"),
        ("dj moore", "dj moore"),
        ("jamar chase", "jamarr chase"),
        ("cam ward", "cameron ward"),
        ("josh allen", "joshua allen"),
        ("chig okonkwo", "chigoziem okonkwo"),
    ])
    def test_abbreviations_still_match(self, left, right):
        assert first_names_compatible(left, right)

    def test_empty_names(self):
        assert not first_names_compatible("", "bijan robinson")
        assert not first_names_compatible("brian robinson", "")

    def test_the_bijan_case_end_to_end(self, tmp_path):
        """Brian Robinson Jr.'s odds must not attach to Bijan Robinson."""
        entries = {"bijan": ("Bijan Robinson", "ATL")}
        matcher = NameMatcher(entries, AliasStore(LocalStore(tmp_path)), threshold=90)
        result = matcher.match("Brian Robinson Jr.", ["ATL"])
        assert not result.matched
        # It still shows up in diagnostics so an alias can override it.
        assert result.candidate == "bijan"
        assert result.candidate_score >= 90

    def test_the_washington_case_end_to_end(self, tmp_path):
        entries = {"mike": ("Mike Washington Jr.", "LV")}
        matcher = NameMatcher(entries, AliasStore(LocalStore(tmp_path)), threshold=90)
        assert not matcher.match("Malik Washington", ["LV", "MIA"]).matched

    def test_an_alias_can_still_force_a_rejected_pair(self, tmp_path):
        """The guard refuses to guess; it must not override an explicit alias."""
        store = AliasStore(LocalStore(tmp_path))
        entries = {"kenneth": ("Kenneth Gainwell", "PIT")}
        store.add("Kenny Gainwell", "Kenneth Gainwell")
        matcher = NameMatcher(entries, store, threshold=90)
        result = matcher.match("Kenny Gainwell", ["PIT"])
        assert result.key == "kenneth"
        assert result.method == "alias"

    def test_a_lower_threshold_stays_safe(self, tmp_path):
        """The guard, not the cutoff, is what blocks a different player."""
        entries = {"bijan": ("Bijan Robinson", "ATL")}
        matcher = NameMatcher(entries, AliasStore(LocalStore(tmp_path)), threshold=70)
        assert not matcher.match("Brian Robinson Jr.", ["ATL"]).matched
