"""Name matching across the three sources (ESPN rosters, PFF CSV, Odds API).

Matching is deliberately conservative: exact normalized match first, then a
user-editable alias file, then fuzzy matching scoped to the same NFL team.
Below the fuzzy threshold a name is reported as unmatched rather than guessed.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz, process

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_PUNCT = re.compile(r"[.'`’\-,]")
_NONWORD = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Lowercase, strip accents/punctuation/suffixes, collapse whitespace.

    "D.J. Moore" -> "dj moore"; "Marvin Harrison Jr." -> "marvin harrison".
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = _PUNCT.sub("", text)
    text = _NONWORD.sub(" ", text)
    tokens = [t for t in _WS.sub(" ", text).strip().split(" ") if t]
    while len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


class AliasStore:
    """Manual name-match overrides, persisted as a flat JSON document.

    Keys are normalized source names (from the CSV or the odds feed); values
    are the canonical ESPN name they should resolve to.
    """

    FILE = "aliases.json"

    def __init__(self, store=None):
        self.store = store
        self._aliases: dict[str, str] = {}
        self.load()

    def load(self) -> dict[str, str]:
        self._aliases = {}
        if self.store is None:
            return self._aliases
        try:
            raw = self.store.read_json(self.FILE)
        except Exception:
            return self._aliases
        if isinstance(raw, dict):
            self._aliases = {normalize_name(k): str(v) for k, v in raw.items() if v}
        return self._aliases

    def save(self) -> bool:
        """Persist to the store. Returns False when the write was refused.

        A failed save is never fatal: the alias still applies for the rest of
        the session and the UI offers a download instead.
        """
        if self.store is None:
            return False
        try:
            return self.store.write_json(
                self.FILE, self._aliases, "Update player name aliases"
            )
        except Exception:
            return False

    def get(self, source_name: str) -> str | None:
        return self._aliases.get(normalize_name(source_name))

    def add(self, source_name: str, canonical_name: str) -> bool:
        self._aliases[normalize_name(source_name)] = canonical_name
        return self.save()

    def as_dict(self) -> dict[str, str]:
        return dict(self._aliases)


def _token_compatible(a: str, b: str) -> bool:
    """True when two name tokens can be the same person's.

    Real variation between these sources is abbreviation and truncation --
    "K." for "Kayshon", "Cam" for "Cameron", "Jamar" for "Ja'Marr" -- all of
    which leave one token a prefix of the other. Two different spellings of
    different names ("Brian" vs "Bijan", "Malik" vs "Mike") are not.
    """
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a)


def first_names_compatible(left: str, right: str) -> bool:
    """Guard against fuzzy-matching two different people with the same surname.

    Similarity scoring is dominated by the surname, so "Brian Robinson Jr." and
    "Bijan Robinson" score 93 and "Malik Washington" and "Mike Washington Jr."
    score 90 -- both above a sensible cutoff, and both wrong. Requiring the
    first names to be compatible rejects those while leaving abbreviations
    intact. A rejected pair is reported as unmatched, where an alias can
    confirm it if it really is the same player.
    """
    left_tokens = left.split()
    right_tokens = right.split()
    if not left_tokens or not right_tokens:
        return False
    return _token_compatible(left_tokens[0], right_tokens[0])


class RejectionStore:
    """Name pairs a person has confirmed are two different players.

    The diagnostics panel surfaces near-misses so a genuine spelling variant
    can be aliased. Most near-misses are not variants at all -- they are other
    NFL players who simply are not on anyone's roster, and there is nothing to
    fix. Recording that judgement keeps the panel down to entries that might
    still need action.

    This affects only what is shown. Matching never consults it: a rejected
    pair is already one the matcher refused, and an exact match must keep
    working if that player is later rostered.
    """

    FILE = "not_matches.json"

    def __init__(self, store=None):
        self.store = store
        self._pairs: set[tuple[str, str]] = set()
        self.load()

    @staticmethod
    def _key(name: str, candidate: str) -> tuple[str, str]:
        return normalize_name(name), normalize_name(candidate or "")

    def load(self) -> set:
        self._pairs = set()
        if self.store is None:
            return self._pairs
        try:
            raw = self.store.read_json(self.FILE)
        except Exception:
            return self._pairs
        for entry in (raw or {}).get("pairs", []) if isinstance(raw, dict) else []:
            if isinstance(entry, dict) and entry.get("name"):
                self._pairs.add(self._key(entry["name"], entry.get("candidate", "")))
        return self._pairs

    def save(self) -> bool:
        if self.store is None:
            return False
        payload = {"pairs": [{"name": name, "candidate": candidate}
                             for name, candidate in sorted(self._pairs)]}
        try:
            return bool(self.store.write_json(
                self.FILE, payload, "Record names that are different players"))
        except Exception:
            return False

    def add(self, name: str, candidate: str | None) -> bool:
        self._pairs.add(self._key(name, candidate or ""))
        return self.save()

    def contains(self, name: str, candidate: str | None) -> bool:
        return self._key(name, candidate or "") in self._pairs

    def as_list(self) -> list[dict]:
        return [{"name": n, "candidate": c} for n, c in sorted(self._pairs)]


class MatchResult:
    """Outcome of one name lookup."""

    __slots__ = ("key", "method", "score", "candidate", "candidate_score")

    def __init__(self, key=None, method="unmatched", score=0.0,
                 candidate=None, candidate_score=0.0):
        self.key = key                        # matched entry's key, or None
        self.method = method                  # exact | alias | fuzzy | unmatched
        self.score = score                    # 100 for exact/alias
        self.candidate = candidate            # nearest miss, for diagnostics
        self.candidate_score = candidate_score

    @property
    def matched(self) -> bool:
        return self.key is not None

    def __repr__(self) -> str:
        return (f"MatchResult(key={self.key!r}, method={self.method!r}, "
                f"score={self.score:.0f})")


class NameMatcher:
    """Matches source names against a fixed set of roster entries.

    `entries` maps an arbitrary key -> (display_name, nfl_team_code). Lookups
    are scoped by NFL team: only entries whose team is in the caller's allowed
    set are considered, which is what prevents same-name collisions.
    """

    def __init__(self, entries: dict, aliases: AliasStore | None = None,
                 threshold: float = 90.0):
        self.threshold = threshold
        self.aliases = aliases or AliasStore()
        self._by_team: dict[str, dict[str, list]] = {}
        self._by_norm: dict[str, list] = {}
        self._display_to_key: dict[str, object] = {}
        for key, (display, team) in entries.items():
            norm = normalize_name(display)
            team = team or "?"
            self._by_team.setdefault(team, {}).setdefault(norm, []).append(key)
            self._by_norm.setdefault(norm, []).append(key)
            self._display_to_key.setdefault(normalize_name(display), key)

    def _pool(self, teams) -> dict[str, list]:
        """Normalized-name -> keys, restricted to the given teams."""
        if not teams:
            return self._by_norm
        pool: dict[str, list] = {}
        for team in teams:
            for norm, keys in self._by_team.get(team, {}).items():
                pool.setdefault(norm, []).extend(keys)
        return pool

    def match(self, source_name: str, teams=None) -> MatchResult:
        """Resolve `source_name` to a roster key, scoped to `teams`.

        `teams` is an iterable of canonical NFL team codes; for a prop this is
        the two teams playing in that event. None means search everyone.
        """
        norm = normalize_name(source_name)
        if not norm:
            return MatchResult()
        pool = self._pool(teams)

        # 1. Exact normalized match within scope.
        keys = pool.get(norm)
        if keys and len(keys) == 1:
            return MatchResult(keys[0], "exact", 100.0)
        if keys and len(keys) > 1:
            # Ambiguous inside the scope: refuse rather than guess.
            return MatchResult(None, "ambiguous", 0.0)

        # 2. Alias override.
        alias_target = self.aliases.get(source_name)
        if alias_target:
            alias_norm = normalize_name(alias_target)
            keys = pool.get(alias_norm) or self._by_norm.get(alias_norm)
            if keys and len(keys) == 1:
                return MatchResult(keys[0], "alias", 100.0)

        # 3. Fuzzy within scope.
        if pool:
            best = process.extractOne(norm, list(pool.keys()), scorer=fuzz.WRatio)
            if best:
                cand_norm, score, _ = best
                cand_keys = pool[cand_norm]
                if (score >= self.threshold and len(cand_keys) == 1
                        and first_names_compatible(norm, cand_norm)):
                    return MatchResult(cand_keys[0], "fuzzy", float(score))
                return MatchResult(None, "unmatched", 0.0,
                                   candidate=cand_keys[0], candidate_score=float(score))
        return MatchResult()
