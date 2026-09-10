"""PFF weekly fantasy projections: parsing and validation.

There is no PFF API. The user downloads the weekly projections CSV from their
PFF+ subscription and uploads it. Columns are parsed by header name, never by
position, because PFF's column order shifts between releases.

Interpretation note (empirically established, do not "optimize" away):
these projections are per-game MEANS of right-skewed distributions, while
sportsbook lines sit near MEDIANS. Raw mean-vs-line comparison therefore
systematically flatters overs on low-volume players. That is precisely why the
volume floors exist -- they restrict gap scoring to props where mean is close
to median. Do not remove the floors or default them to zero.
"""

from __future__ import annotations

import io
import math

import pandas as pd

from .teams import normalize_team

# Canonical field -> accepted header spellings (compared case-insensitively
# after stripping non-alphanumerics).
FIELD_ALIASES = {
    "playerName": ["playername", "player", "name", "fullname"],
    "teamName": ["teamname", "team", "teamabbrev", "nflteam", "offteam"],
    "position": ["position", "pos"],
    "passYds": ["passyds", "passingyards", "passyards", "passyd"],
    "passTd": ["passtd", "passtds", "passingtds", "passingtouchdowns"],
    "rushAtt": ["rushatt", "rushatts", "rushingattempts", "carries", "rushattempts"],
    "rushYds": ["rushyds", "rushingyards", "rushyards"],
    "rushTd": ["rushtd", "rushtds", "rushingtds", "rushingtouchdowns"],
    "recvTargets": ["recvtargets", "targets", "receivingtargets", "tgts"],
    "recvReceptions": ["recvreceptions", "receptions", "recs", "rec", "catches"],
    "recvYds": ["recvyds", "receivingyards", "recyards", "recyds"],
    "recvTd": ["recvtd", "recvtds", "receivingtds", "receivingtouchdowns", "rectd"],
    "returnTd": ["returntd", "returntds", "returntouchdowns", "sttd"],
}

REQUIRED_FIELDS = ["playerName", "teamName", "position"]

# Every stat we score off. Missing ones default to 0 with a warning.
STAT_FIELDS = [
    "passYds", "passTd", "rushAtt", "rushYds", "rushTd",
    "recvTargets", "recvReceptions", "recvYds", "recvTd", "returnTd",
]

# A weekly file should never have a receiving-yards value this high. A season
# file will (hundreds to thousands), which is the failure we are guarding.
WEEKLY_RECV_YDS_CEILING = 200


class ProjectionError(Exception):
    """Raised when the uploaded file cannot be used as weekly projections."""


def _slug(header: str) -> str:
    return "".join(ch for ch in str(header).lower() if ch.isalnum())


def _resolve_columns(columns) -> dict[str, str]:
    """Map canonical field -> the actual header found in the file."""
    slug_to_actual = {}
    for col in columns:
        slug_to_actual.setdefault(_slug(col), col)
    resolved = {}
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in slug_to_actual:
                resolved[field] = slug_to_actual[alias]
                break
    return resolved


def load_projections(source, filename: str | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Parse a PFF weekly projections CSV.

    `source` may be a path, a file-like object, or raw bytes. Returns the
    normalized frame and a list of non-fatal warnings. Raises ProjectionError
    when the file is unusable (missing key columns, or season-long values).
    """
    warnings: list[str] = []

    if isinstance(source, bytes):
        source = io.BytesIO(source)
    try:
        frame = pd.read_csv(source)
    except Exception as exc:  # pandas raises a wide variety here
        raise ProjectionError(f"Could not read the CSV: {exc}") from exc

    if frame.empty:
        raise ProjectionError("The projections file has no rows.")

    resolved = _resolve_columns(frame.columns)
    missing_required = [f for f in REQUIRED_FIELDS if f not in resolved]
    if missing_required:
        raise ProjectionError(
            "The file is missing required column(s): "
            + ", ".join(missing_required)
            + ". Expected PFF weekly projections with headers like "
            "playerName, teamName, position, recvYds."
        )

    out = pd.DataFrame()
    out["playerName"] = frame[resolved["playerName"]].astype(str).str.strip()
    out["position"] = frame[resolved["position"]].astype(str).str.strip().str.upper()
    raw_team = frame[resolved["teamName"]]
    out["teamRaw"] = raw_team.astype(str).str.strip()
    out["team"] = [normalize_team(v) for v in raw_team]

    for field in STAT_FIELDS:
        if field in resolved:
            out[field] = pd.to_numeric(frame[resolved[field]], errors="coerce").fillna(0.0)
        else:
            out[field] = 0.0
            warnings.append(
                f"Column '{field}' not found in the CSV — treated as 0 for every player."
            )

    unknown_teams = sorted(
        {t for t, code in zip(out["teamRaw"], out["team"]) if code is None and t}
    )
    if unknown_teams:
        warnings.append(
            "Unrecognized team abbreviation(s), those rows can't be matched: "
            + ", ".join(unknown_teams[:10])
            + ("…" if len(unknown_teams) > 10 else "")
        )

    # Season-file guard.
    max_recv = float(out["recvYds"].max()) if len(out) else 0.0
    max_pass = float(out["passYds"].max()) if len(out) else 0.0
    if max_recv >= WEEKLY_RECV_YDS_CEILING or max_pass >= 1000:
        raise ProjectionError(
            "This looks like a SEASON projections file — the bot needs the WEEKLY "
            f"file. (Highest receiving yards in the file is {max_recv:.0f}; a weekly "
            f"file stays under {WEEKLY_RECV_YDS_CEILING}.)"
        )

    out = out[out["playerName"].str.len() > 0].reset_index(drop=True)

    dupes = out.duplicated(subset=["playerName", "team"], keep=False)
    if bool(dupes.any()):
        names = sorted(set(out.loc[dupes, "playerName"]))
        warnings.append(
            "Duplicate player+team rows in the CSV (treated as unmatched): "
            + ", ".join(names[:5])
        )

    if filename:
        warnings.extend(_sanity_notes(out, filename))
    return out, warnings


def _sanity_notes(frame: pd.DataFrame, filename: str) -> list[str]:
    notes = []
    if len(frame) < 100:
        notes.append(
            f"Only {len(frame)} rows in the projections file — a full weekly PFF "
            "file usually has several hundred."
        )
    return notes


def projection_index(frame: pd.DataFrame) -> dict:
    """Row index keyed by dataframe position, as (display name, team) pairs.

    Rows whose player+team pair is duplicated are dropped: §13 says an
    ambiguous duplicate is treated as unmatched rather than guessed.
    """
    counts: dict[tuple, int] = {}
    for name, team in zip(frame["playerName"], frame["team"]):
        counts[(name, team)] = counts.get((name, team), 0) + 1
    entries = {}
    for idx, (name, team) in enumerate(zip(frame["playerName"], frame["team"])):
        if counts[(name, team)] > 1:
            continue
        entries[idx] = (name, team)
    return entries


def row_to_projection(frame: pd.DataFrame, idx: int) -> dict:
    """Extract one row as a plain dict of the stat fields we score on."""
    row = frame.iloc[idx]
    out = {
        "playerName": row["playerName"],
        "team": row["team"],
        "position": row["position"],
    }
    for field in STAT_FIELDS:
        value = row[field]
        out[field] = 0.0 if (value is None or (isinstance(value, float) and math.isnan(value))) else float(value)
    return out
