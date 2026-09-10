"""Name matching across the three sources (ESPN rosters, PFF CSV, Odds API).

Matching is deliberately conservative: exact normalized match first, then a
user-editable alias file, then fuzzy matching scoped to the same NFL team.
Below the fuzzy threshold a name is reported as unmatched rather than guessed.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

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
    """Manual name-match overrides, persisted as a flat JSON dict.

    Keys are normalized source names (from the CSV or the odds feed); values
    are the canonical ESPN name they should resolve to.
    """

    def __init__(self, path: str | Path = "data/aliases.json"):
        self.path = Path(path)
        self._aliases: dict[str, str] = {}
        self.load()

    def load(self) -> dict[str, str]:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text() or "{}")
                self._aliases = {
                    normalize_name(k): str(v) for k, v in raw.items() if v
                }
            except (json.JSONDecodeError, OSError):
                self._aliases = {}
        return self._aliases

    def save(self) -> bool:
        """Persist to disk. Returns False on a read-only filesystem.

        Hosted deployments often cannot write here; the alias still applies for
        the rest of the session, and the UI offers a download instead.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._aliases, indent=2, sort_keys=True))
            return True
        except OSError:
            return False

    def get(self, source_name: str) -> str | None:
        return self._aliases.get(normalize_name(source_name))

    def add(self, source_name: str, canonical_name: str) -> bool:
        self._aliases[normalize_name(source_name)] = canonical_name
        return self.save()

    def as_dict(self) -> dict[str, str]:
        return dict(self._aliases)


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
                if score >= self.threshold and len(cand_keys) == 1:
                    return MatchResult(cand_keys[0], "fuzzy", float(score))
                return MatchResult(None, "unmatched", 0.0,
                                   candidate=cand_keys[0], candidate_score=float(score))
        return MatchResult()
