"""League Parlay Bot — Streamlit UI.

Builds a weekly NFL parlay: one FanDuel player prop per fantasy team, overs
only, hard filters, best available per team. The parlay has as many legs as the
league has teams. The user places the bet.
"""

from __future__ import annotations

import datetime as dt
import json
import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from core import odds as odds_mod
from core import parlay as parlay_mod
from core import runlog
from core import snapshots
from core import scoring
from core.betslip import leg_link, parlay_link_status
from core.config import DEFAULTS, ODDS_TICKS, load_config, save_config
from core.matching import AliasStore, RejectionStore
from core.store import build_store
from core.projections import ProjectionError, load_projections
from core.rosters import RosterError, fetch_league, filter_players, league_url
from core.selection import score_props, select_picks

load_dotenv()

st.set_page_config(page_title="League Parlay Bot", page_icon="🏈", layout="wide")


# --------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------

def get_secret(name: str) -> str:
    """A secret from Streamlit's vault, falling back to the environment."""
    try:
        value = st.secrets.get(name)
        if value:
            return str(value).strip()
    except Exception:
        pass  # no secrets.toml present; fall through to the environment
    return (os.environ.get(name) or "").strip()


def get_api_key() -> str | None:
    return get_secret("ODDS_API_KEY") or None


@st.cache_resource(show_spinner=False)
def get_store(token_fingerprint: str, repo: str, branch: str):
    """The persistence backend, built once per configuration.

    Cached on the *configuration* rather than the token itself so that a
    changed secret rebuilds the store, while ordinary reruns reuse the same
    HTTP session.
    """
    return build_store(
        {"GITHUB_TOKEN": get_secret("GITHUB_TOKEN"),
         "GITHUB_REPO": repo,
         "GITHUB_DATA_BRANCH": branch},
        root="data",
    )


@st.cache_data(ttl=300, show_spinner=False)
def _store_status(label: str) -> tuple[bool, str]:
    """Cached because check() costs an HTTP round trip on the GitHub backend."""
    return store.check()


def store_status() -> tuple[bool, str]:
    try:
        return _store_status(store.label)
    except Exception as exc:            # never let a status probe break the app
        return False, f"Storage check failed: {exc}"


def current_store():
    token = get_secret("GITHUB_TOKEN")
    repo = get_secret("GITHUB_REPO")
    branch = get_secret("GITHUB_DATA_BRANCH") or "parlay-data"
    fingerprint = f"{len(token)}:{token[-4:] if token else ''}"
    return get_store(fingerprint, repo, branch)


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

def restore_saved_state() -> dict:
    """Rebuild the last session from storage, so a restart costs nothing.

    Streamlit restarts on every code change, secrets edit, and idle timeout.
    Refetching odds after each one would burn ~100 of the 500 monthly credits,
    so the last snapshot is loaded automatically and clearly labelled with its
    age. Fetching fresh odds is always one click away.
    """
    restored = {"events": [], "raw_by_event": {}, "odds_fetched_at": None,
                "teams_raw": None, "roster_fetched_at": None,
                "projections": None, "projection_filename": None,
                "projection_warnings": [], "restored": []}

    odds = snapshots.load_odds(store)
    if odds:
        restored["events"] = odds.get("events") or []
        restored["raw_by_event"] = odds.get("raw_by_event") or {}
        restored["odds_fetched_at"] = odds.get("fetched_at") or odds.get("saved_at")
        restored["odds_saved_at"] = odds.get("saved_at")
        restored["odds_markets"] = odds.get("markets") or []
        restored["restored"].append("odds")

    rosters = snapshots.load_rosters(store)
    if rosters:
        restored["teams_raw"] = rosters.get("teams")
        restored["roster_fetched_at"] = rosters.get("fetched_at") or rosters.get("saved_at")
        restored["restored"].append("rosters")

    frame, meta = snapshots.load_projections(store)
    if frame is not None:
        restored["projections"] = frame
        restored["projection_filename"] = meta.get("filename")
        restored["projection_warnings"] = meta.get("warnings") or []
        restored["projections_saved_at"] = meta.get("saved_at")
        restored["restored"].append("projections")

    return restored


def init_state() -> None:
    """Seed session state, restoring the last snapshot for anything absent.

    Every key is filled with setdefault so a caller (or a test) can pre-seed
    state and have it survive. The store is only read when nothing is seeded,
    which keeps a rerun from re-reading GitHub on every interaction.
    """
    seeded = any(key in st.session_state
                 for key in ("raw_by_event", "teams_raw", "projections"))
    saved = ({"events": [], "raw_by_event": {}, "odds_fetched_at": None,
              "teams_raw": None, "roster_fetched_at": None, "projections": None,
              "projection_filename": None, "projection_warnings": [],
              "restored": []}
             if seeded else restore_saved_state())

    defaults = {
        "config": load_config(store),
        "aliases": AliasStore(store),
        "rejections": RejectionStore(store),
        "events": saved["events"],
        "raw_by_event": saved["raw_by_event"],
        "odds_fetched_at": saved["odds_fetched_at"],
        "odds_saved_at": saved.get("odds_saved_at"),
        "odds_markets": saved.get("odds_markets") or [],
        "quota": {},
        "teams_raw": saved["teams_raw"],
        "roster_fetched_at": saved["roster_fetched_at"],
        "projections": saved["projections"],
        "projection_warnings": saved["projection_warnings"],
        "projection_filename": saved["projection_filename"],
        "projections_saved_at": saved.get("projections_saved_at"),
        "overrides": {},
        "approved_reviews": set(),
        "last_run_path": None,
        "last_error": None,
        "restored_from_storage": saved["restored"],
        "saved_odds_available": "odds" in saved["restored"],
        "snapshot_note": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


store, store_warning = current_store()
init_state()
config = st.session_state.config
aliases = st.session_state.aliases
rejections = st.session_state.rejections


# --------------------------------------------------------------------------
# Sidebar — every tunable from §7
# --------------------------------------------------------------------------

def render_sidebar() -> dict:
    st.sidebar.title("🏈 Parlay settings")
    st.sidebar.caption(
        "Every knob lives here. Moving one re-runs selection instantly from "
        "cached odds — it never spends API credits."
    )
    new = dict(config)

    with st.sidebar.expander("Thresholds", expanded=True):
        # These read as percentages in the UI but are stored as fractions,
        # because Streamlit's `format` runs printf against the raw value and
        # would render 0.10 as "0%".
        new["gap_threshold"] = st.slider(
            "Yardage gap threshold (X)", 0, 50,
            int(round(float(config["gap_threshold"]) * 100)), step=1, format="%d%%",
            help="A yardage prop qualifies for Tier 1 when its projection beats "
                 "the line by at least this much.",
        ) / 100
        new["ev_threshold"] = st.slider(
            "EV override threshold (Y)", 0, 100,
            int(round(float(config["ev_threshold"]) * 100)), step=1, format="%d%%",
            help="An EV prop must beat this to steal the slot from a qualifying "
                 "yardage prop.",
        ) / 100
        new["sanity_ceiling"] = st.slider(
            "Sanity ceiling", 10, 150,
            int(round(float(config["sanity_ceiling"]) * 100)), step=5, format="%d%%",
            help="EV above this is never auto-picked — it usually means stale "
                 "odds, un-priced injury news, or a name-match bug. Flagged for "
                 "manual review instead.",
        ) / 100
        floor_index = (ODDS_TICKS.index(config["odds_floor"])
                       if config["odds_floor"] in ODDS_TICKS
                       else ODDS_TICKS.index(-110))
        new["odds_floor"] = st.select_slider(
            "Odds floor", options=ODDS_TICKS, value=ODDS_TICKS[floor_index],
            format_func=scoring.format_american,
            help="Worst price you'll accept, on any prop type. -105 passes a "
                 "-110 floor; -120 fails.",
        )
        if new["odds_floor"] >= -110:
            st.caption(
                "⚠️ FanDuel usually prices yardage props at -114 or -115, so a "
                "-110 floor rejects most of them and you'll see a lot of NONE "
                "slots. -120 admits standard juice. This is a deliberate hard "
                "filter — the bot never loosens it on its own."
            )

    with st.sidebar.expander("Volume floors", expanded=False):
        st.caption(
            "PFF projections are means of right-skewed distributions while "
            "lines sit near medians, which flatters overs on low-volume "
            "players. These floors keep scoring where mean ≈ median. Don't "
            "zero them out."
        )
        new["yards_floor"] = st.slider(
            "Rec / rush yards floor", 0.0, 80.0, float(config["yards_floor"]), step=1.0)
        new["pass_yards_floor"] = st.slider(
            "Passing yards floor", 0.0, 320.0, float(config["pass_yards_floor"]), step=5.0)
        new["rush_attempts_floor"] = st.slider(
            "Rush attempts floor", 0.0, 25.0, float(config["rush_attempts_floor"]), step=1.0)
        new["receptions_floor"] = st.slider(
            "Receptions projection floor", 0.0, 6.0, float(config["receptions_floor"]), step=0.1)

    with st.sidebar.expander("Markets", expanded=False):
        st.caption(
            "Unchecking a market removes it from the picks straight away, and "
            "from the next fetch. Fewer markets cost fewer API credits."
        )
        selected = []
        for heading, keys in (
            ("Yardage — scored by gap", sorted(scoring.GAP_MARKETS)),
            ("Expected value — scored by EV", sorted(scoring.EV_MARKETS)),
        ):
            st.markdown(f"**{heading}**")
            for key in keys:
                if st.checkbox(
                    scoring.MARKET_LABELS.get(key, key),
                    value=key in config.get("markets", odds_mod.DEFAULT_MARKETS),
                    key=f"market__{key}",
                ):
                    selected.append(key)
        new["markets"] = [k for k in odds_mod.DEFAULT_MARKETS if k in selected]

        if not selected:
            st.warning("No markets selected — every slot will come back NONE.")
        else:
            st.caption(
                f"{len(selected)} of {len(odds_mod.DEFAULT_MARKETS)} selected · "
                f"about {len(selected)} credits per game when you fetch."
            )

    with st.sidebar.expander("Toggles", expanded=False):
        new["ev_enabled"] = st.toggle(
            "EV props enabled", value=bool(config["ev_enabled"]),
            help="Off = anytime TD, receptions and pass-TD props are excluded "
                 "entirely; yardage props always win.")
        new["starters_only"] = st.toggle(
            "Starters only", value=bool(config["starters_only"]),
            help="On = ignore bench and IR slots.")
        new["exclude_questionable"] = st.toggle(
            "Exclude Questionable", value=bool(config["exclude_questionable"]),
            help="Off = questionable players stay eligible but their legs are "
                 "flagged ⚠️.")
        new["exclude_doubtful"] = st.toggle(
            "Exclude Doubtful", value=bool(config["exclude_doubtful"]))

    with st.sidebar.expander("Misc", expanded=False):
        new["stake"] = st.number_input(
            "Stake ($)", min_value=0.0, value=float(config["stake"]), step=1.0,
            help="Display only — the app never places a bet.")
        new["season_year"] = int(st.number_input(
            "Season year", min_value=2015, max_value=2100,
            value=int(config["season_year"]), step=1))
        new["league_id"] = int(st.number_input(
            "ESPN league ID", min_value=1, value=int(config["league_id"]), step=1))
        new["fuzzy_threshold"] = st.slider(
            "Fuzzy match threshold", 70, 100, int(config["fuzzy_threshold"]),
            help="rapidfuzz cutoff. Below this a name is reported as unmatched "
                 "rather than guessed. Names whose first names disagree "
                 "(Brian/Bijan, Malik/Mike) are always rejected regardless of "
                 "score, so a lower cutoff here is safer than it looks — it "
                 "mainly admits abbreviations like Chig/Chigoziem.")

    save_col, reset_col = st.sidebar.columns(2)
    with save_col:
        if st.button("💾 Save", width="stretch",
                     help="Store these values so they come back next time."):
            if save_config(new, store):
                st.toast("Settings saved.")
            else:
                st.toast("Couldn't save settings — see the Storage note below.")
    with reset_col:
        if st.button("Reset", width="stretch"):
            st.session_state.config = dict(DEFAULTS)
            # Checkbox widgets keep their own state, so clearing the config
            # alone would leave the old market selection on screen.
            for key in list(st.session_state.keys()):
                if str(key).startswith("market__"):
                    del st.session_state[key]
            st.rerun()

    st.sidebar.divider()
    with st.sidebar.expander("Storage", expanded=not store.persistent):
        if store_warning:
            st.warning(store_warning)
        healthy, message = store_status()
        if not healthy:
            st.error(message)
        elif store.persistent:
            st.success(message)
        else:
            st.info(message)
        if not store.persistent:
            st.caption(
                "Set GITHUB_TOKEN and GITHUB_REPO in your app's secrets to keep "
                "history between restarts. See the README."
            )

    quota = st.session_state.quota or {}
    if quota.get("remaining") is not None:
        st.sidebar.divider()
        st.sidebar.metric("API credits remaining", quota["remaining"])
        if quota.get("last_cost") is not None:
            st.sidebar.caption(f"Last fetch cost {quota['last_cost']} credits.")
    return new


new_config = render_sidebar()
if new_config != config:
    st.session_state.config = new_config
    config = new_config


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------

def fetch_odds(window_start: dt.datetime, window_end: dt.datetime) -> None:
    """Spend credits: pull the event list and every event's FanDuel props."""
    api_key = get_api_key()
    provider = odds_mod.TheOddsAPI(api_key)
    events = provider.get_week_events(window_start, window_end)
    events = odds_mod.filter_events(events, window_start, window_end)
    if not events:
        st.session_state.last_error = (
            "No NFL games found in that window. Widen the dates, or check that "
            "the season is under way."
        )
        return

    raw = {}
    progress = st.progress(0.0, text="Fetching FanDuel props…")
    failures = []
    for index, event in enumerate(events, start=1):
        try:
            raw[event["id"]] = provider.get_event_props(event["id"], config["markets"])
        except odds_mod.OddsError as exc:
            failures.append(f"{event.get('away_team')} @ {event.get('home_team')}: {exc}")
        progress.progress(index / len(events),
                          text=f"Fetching FanDuel props… {index}/{len(events)} games")
    progress.empty()

    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
    st.session_state.events = events
    st.session_state.raw_by_event = raw
    st.session_state.odds_fetched_at = fetched_at
    st.session_state.odds_markets = list(config["markets"])
    st.session_state.quota = provider.quota
    st.session_state.last_error = "; ".join(failures) if failures else None

    # Save immediately: this snapshot is the expensive artefact, and a restart
    # without it means paying for the same week twice.
    saved, note = snapshots.save_odds(
        store, events=events, raw_by_event=raw, markets=config["markets"],
        fetched_at=fetched_at,
        window={"start": window_start.isoformat(), "end": window_end.isoformat()},
    )
    st.session_state.odds_saved_at = fetched_at if saved else None
    st.session_state.saved_odds_available = bool(saved)
    if saved and not store.persistent:
        note = (
            "That fetch spent credits, and the snapshot was written to local disk "
            "only — your next redeploy will erase it. Set up GitHub storage "
            "(README Step 6), or download the snapshot before redeploying."
        )
    st.session_state.snapshot_note = note
    # A fresh fetch is live data, not a restore.
    st.session_state.restored_from_storage = []


def fetch_rosters() -> None:
    """ESPN rosters. Free, so this runs on every fetch and on demand."""
    st.session_state.teams_raw = fetch_league(
        league_id=int(config["league_id"]), year=int(config["season_year"])
    )
    st.session_state.roster_fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
    snapshots.save_rosters(store, teams=st.session_state.teams_raw,
                           fetched_at=st.session_state.roster_fetched_at)


# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------

st.title("🏈 League Parlay Bot")
api_key = get_api_key()
if not api_key:
    st.error(
        "**No Odds API key found.** The app can't fetch odds until one is set.\n\n"
        "- **Running locally:** copy `.env.example` to `.env` and put your key in it "
        "(`ODDS_API_KEY=...`), then restart.\n"
        "- **Running on Streamlit Cloud:** open the app menu → *Settings* → *Secrets* "
        "and add `ODDS_API_KEY = \"your_key\"`.\n\n"
        "Get a free key at https://the-odds-api.com."
    )

window_start_default, window_end_default = odds_mod.default_window()

setup, actions = st.columns([3, 2])
with setup:
    uploaded = st.file_uploader(
        "PFF **weekly** projections CSV", type=["csv"],
        help="PFF+ → Fantasy → Weekly Projections → export CSV. The season-long "
             "file will be rejected.",
    )
    if uploaded is not None and uploaded.name != st.session_state.projection_filename:
        try:
            frame, warnings = load_projections(uploaded, uploaded.name)
            st.session_state.projections = frame
            st.session_state.projection_warnings = warnings
            st.session_state.projection_filename = uploaded.name
            st.session_state.projections_saved_at = dt.datetime.now(
                dt.timezone.utc).isoformat()
            snapshots.save_projections(store, frame=frame, filename=uploaded.name,
                                       warnings=warnings)
        except ProjectionError as exc:
            st.session_state.projections = None
            st.session_state.projection_filename = None
            st.error(str(exc))

with actions:
    date_start, date_end = st.columns(2)
    with date_start:
        start_date = st.date_input("Games from", value=window_start_default.date())
    with date_end:
        end_date = st.date_input("Games through", value=window_end_default.date())

    window_start = dt.datetime.combine(start_date, dt.time.min, tzinfo=dt.timezone.utc)
    if window_start < window_start_default:
        # Never look backwards past "now": games already kicked off are out (§13).
        window_start = window_start_default
    window_end = dt.datetime.combine(end_date, dt.time(12, 0), tzinfo=dt.timezone.utc)

    estimate = len(config["markets"])
    fetch_clicked = st.button(
        "🔄 Fetch fresh odds", type="primary", width="stretch",
        disabled=not api_key,
        help=f"Spends API credits: about {estimate} per game "
             f"({estimate} markets × 1 region).",
    )
    reload_clicked = st.button("👥 Reload rosters only (free)", width="stretch")
    with st.expander("Back up / restore odds snapshot", expanded=False):
        st.caption(
            "A manual copy of the fetched odds. Download it before a redeploy "
            "and upload it afterwards to avoid paying for the same week twice. "
            "Setting up GitHub storage (README Step 6) does this automatically."
        )
        if st.session_state.raw_by_event:
            document = snapshots.build_odds_document(
                events=st.session_state.events,
                raw_by_event=st.session_state.raw_by_event,
                markets=config["markets"],
                fetched_at=st.session_state.odds_fetched_at,
            )
            st.download_button(
                "⬇️ Download odds snapshot", json.dumps(document),
                file_name="odds-snapshot.json", mime="application/json",
                width="stretch",
            )
        else:
            st.caption("Nothing loaded to back up yet.")

        incoming = st.file_uploader("Upload a snapshot", type=["json"],
                                    key="restore-odds-file")
        if incoming is not None and not st.session_state.get("_restored_upload"):
            try:
                document = snapshots.read_odds_document(
                    json.loads(incoming.getvalue()))
            except json.JSONDecodeError:
                document = None
            if document is None:
                st.error("That file isn't an odds snapshot.")
            else:
                st.session_state.events = document.get("events") or []
                st.session_state.raw_by_event = document["raw_by_event"]
                st.session_state.odds_fetched_at = (
                    document.get("fetched_at") or document.get("saved_at"))
                st.session_state.restored_from_storage = ["odds"]
                st.session_state._restored_upload = True
                # Persist it so the store has it too, where the store can keep it.
                snapshots.save_odds(
                    store, events=st.session_state.events,
                    raw_by_event=st.session_state.raw_by_event,
                    markets=document.get("markets") or config["markets"],
                    fetched_at=st.session_state.odds_fetched_at,
                )
                st.success(
                    f"Restored {len(document['raw_by_event'])} games — no credits spent."
                )
                st.rerun()

    if st.session_state.get("saved_odds_available"):
        if st.button("↩️ Reload saved odds (free)", width="stretch",
                     help="Go back to the last saved snapshot without spending "
                          "credits — useful if a fetch returned less than you "
                          "expected."):
            restored = restore_saved_state()
            for key in ("events", "raw_by_event", "odds_fetched_at", "teams_raw",
                        "roster_fetched_at", "projections", "projection_filename",
                        "projection_warnings"):
                st.session_state[key] = restored[key]
            st.session_state.restored_from_storage = restored["restored"]
            st.rerun()

if fetch_clicked:
    with st.spinner("Loading ESPN rosters…"):
        try:
            fetch_rosters()
        except RosterError as exc:
            st.error(f"{exc}")
            st.stop()
    try:
        fetch_odds(window_start, window_end)
    except odds_mod.OddsError as exc:
        st.error(f"Odds API: {exc}")
        if st.session_state.raw_by_event:
            st.warning(
                "Falling back to the cached odds snapshot below — selection still "
                "runs, but the prices may be stale."
            )

if reload_clicked:
    with st.spinner("Loading ESPN rosters…"):
        try:
            fetch_rosters()
            st.success("Rosters reloaded.")
        except RosterError as exc:
            st.error(str(exc))

if st.session_state.last_error:
    st.warning(st.session_state.last_error)

for warning in st.session_state.projection_warnings:
    st.info(warning)


# --------------------------------------------------------------------------
# Scoring and selection — pure, re-runs on every interaction, no API calls
# --------------------------------------------------------------------------

def snapshot_age(iso: str | None) -> str:
    """Human-readable age of a timestamp.

    Clamped at zero: a feed timestamp slightly ahead of our clock is ordinary
    skew, and "-2446 min ago" would be nonsense.
    """
    if not iso:
        return "never"
    try:
        moment = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    minutes = (dt.datetime.now(dt.timezone.utc) - moment).total_seconds() / 60
    if minutes <= 1:
        return "just now"
    if minutes < 60:
        return f"{minutes:.0f} min ago"
    if minutes < 60 * 24:
        return f"{minutes / 60:.1f} hours ago"
    return f"{minutes / 1440:.1f} days ago"


def render_review_card(row: dict, card: dict) -> None:
    """A sanity-flagged prop, written as a conclusion rather than a data dump.

    The point of the card is a disagreement: the book's price implies one
    probability, the projection implies another. Showing both side by side is
    what makes the flag legible, so that comparison leads.
    """
    player = card.get("player_name_espn") or card["player_name"]
    book_probability = scoring.implied_probability(card["price"])
    model_probability = card.get("probability") or 0.0
    stake = float(config["stake"])

    with st.container(border=True):
        st.markdown(
            f"#### {player} — {parlay_mod.describe_prop(card)} at "
            f"{scoring.format_american(card['price'])}"
        )
        st.caption(f"{row['team_name']}'s slot · {card.get('nfl_team') or '?'}")

        left, right = st.columns(2)
        left.metric(
            "FanDuel's price implies", f"{book_probability:.1%}",
            help="What the odds say the chance is, bookmaker's margin included. "
                 "The book's own estimate is a little lower than this.",
        )
        right.metric(
            "Your projection implies", f"{model_probability:.1%}",
            delta=f"{model_probability - book_probability:+.1%}",
            help=f"From the PFF projection of {card.get('projection', 0):.2f} "
                 f"({card.get('projection_field')}).",
        )
        # Dollar signs must be escaped: Streamlit's markdown reads a bare $ as
        # the start of a LaTeX expression and swallows the rest of the sentence.
        expected_return = stake * model_probability * scoring.american_to_decimal(card["price"])
        st.markdown(
            f"You'd be betting that FanDuel has this **too cheap**. If the "
            f"projection is right, a **\\${stake:,.0f}** bet returns "
            f"**\\${expected_return:,.2f}** on average — an edge of "
            f"**{card['score']:+.1%}**."
        )

        st.markdown("**Before you use it, rule these out:**")

        method = card.get("match_method")
        if method in ("exact", "alias"):
            st.markdown(
                f"- ✅ **Right player.** The name matched exactly across ESPN, the "
                f"PFF file and FanDuel (*{card.get('projection_source')}* / "
                f"*{card.get('player_name')}*)."
            )
        else:
            st.markdown(
                f"- ⚠️ **Check the player.** Matched by {method} at "
                f"{card.get('match_score', 0):.0f}/100, not exactly: PFF row "
                f"*{card.get('projection_source')}* ↔ odds feed "
                f"*{card.get('player_name')}*. A wrong match here would explain "
                f"the whole edge."
            )

        age = snapshot_age(card.get("last_update")) if card.get("last_update") else None
        if age and age != "never":
            st.markdown(
                f"- {'⚠️' if 'day' in age else '✅'} **Odds age.** FanDuel last moved "
                f"this price **{age}**. A stale price is a fake edge."
            )
        else:
            st.markdown("- ⚠️ **Odds age unknown** — refetch to be sure the price is current.")

        st.markdown(
            "- ⚠️ **News the projection can't see.** A long price on a player your "
            "projection likes usually means the book knows something about his "
            "role, health or snap count that last week's file doesn't. This is "
            "the most common cause — check his status before trusting it."
        )
        if card.get("market") == "player_anytime_td":
            st.markdown(
                "- ℹ️ **Touchdowns cluster.** Players who score often score twice, "
                "so this model slightly overstates the chance of scoring *at "
                "least* once. The real edge is a bit smaller than shown."
            )

        if st.button(f"Use this leg for {row['team_name']}",
                     key=f"approve-{card['prop_id']}"):
            st.session_state.approved_reviews.add(card["prop_id"])
            st.rerun()



ready = bool(
    st.session_state.raw_by_event
    and st.session_state.teams_raw
    and st.session_state.projections is not None
)

restored_kinds = set(st.session_state.get("restored_from_storage") or [])
status = st.columns(3)

odds_note = f"**Odds snapshot:** {snapshot_age(st.session_state.odds_fetched_at)}"
if st.session_state.raw_by_event:
    odds_note += f" · {len(st.session_state.raw_by_event)} games"
if "odds" in restored_kinds:
    odds_note += " · restored, no credits spent"
status[0].caption(odds_note)

roster_note = f"**Rosters:** {snapshot_age(st.session_state.roster_fetched_at)}"
if st.session_state.teams_raw:
    roster_note += f" · {len(st.session_state.teams_raw)} teams"
if "rosters" in restored_kinds:
    roster_note += " · restored"
status[1].caption(roster_note)

projection_note = f"**Projections:** {st.session_state.projection_filename or 'not uploaded'}"
if "projections" in restored_kinds:
    projection_note += " · restored"
status[2].caption(projection_note)

if st.session_state.get("snapshot_note"):
    st.warning(st.session_state.snapshot_note)

if not store.persistent:
    st.error(
        "**Nothing here will survive your next redeploy.** Storage is set to local "
        "disk, and Streamlit wipes that every time the app restarts — so each "
        "redeploy costs you another full fetch (~100 API credits).\n\n"
        "**To fix it permanently:** add `GITHUB_TOKEN` and `GITHUB_REPO` to "
        "**⋮ → Settings → Secrets** (README Step 6). The app then keeps its odds "
        "snapshot on the `parlay-data` branch and reloads it on every start.\n\n"
        "**Until then**, use *Back up / restore* below to download the snapshot "
        "before you redeploy and re-upload it afterwards."
    )

# Odds go stale as books move lines, and the projections file is weekly.
odds_hours = snapshots.age_in_hours(st.session_state.odds_fetched_at)
if odds_hours is not None and odds_hours > 24:
    st.warning(
        f"These odds are {odds_hours / 24:.1f} days old. Lines move — fetch fresh "
        "odds before you actually place the bet."
    )
projection_hours = snapshots.age_in_hours(st.session_state.get("projections_saved_at"))
if projection_hours is not None and projection_hours > 24 * 6:
    st.warning(
        f"The projections file was uploaded {projection_hours / 24:.0f} days ago, so "
        "it is probably last week's. Re-export this week's PFF file."
    )

if not ready:
    missing = []
    if not st.session_state.raw_by_event:
        missing.append("fetch odds")
    if not st.session_state.teams_raw:
        missing.append("load rosters")
    if st.session_state.projections is None:
        missing.append("upload the weekly PFF CSV")
    st.info("To build a parlay: " + ", then ".join(missing) + ".")
    st.stop()

# Re-filter every render, not just at fetch time: a restored snapshot may hold
# games that have since kicked off (§13) or fall outside a window the user has
# just changed.
all_events = st.session_state.events
events = odds_mod.filter_events(all_events, window_start, window_end)
if all_events and not events:
    st.error(
        f"All {len(all_events)} games in the loaded odds fall outside your date "
        "window — they have already kicked off, or the snapshot is from another "
        "week. Widen the dates above, or fetch fresh odds."
    )
    st.stop()
if len(events) < len(all_events):
    st.info(
        f"{len(all_events) - len(events)} of {len(all_events)} games in this snapshot "
        "are outside the window (already started, or a different week) and are "
        "being ignored."
    )
playing = odds_mod.playing_team_codes(events)
teams = filter_players(
    st.session_state.teams_raw, playing,
    starters_only=bool(config["starters_only"]),
    exclude_questionable=bool(config["exclude_questionable"]),
    exclude_doubtful=bool(config["exclude_doubtful"]),
)
scored = score_props(
    events=events, raw_by_event=st.session_state.raw_by_event, teams=teams,
    projections=st.session_state.projections, config=config, aliases=aliases,
)
picks = select_picks(
    scored, teams, config,
    approved_reviews=st.session_state.approved_reviews,
    overrides=st.session_state.overrides,
)
summary = parlay_mod.combine(picks, float(config["stake"]))
coverage = odds_mod.market_coverage(events, st.session_state.raw_by_event, config["markets"])

# A market can be ticked in the sidebar and still be absent from the cached
# snapshot, because the selection only ever affected the next fetch. Silence
# there reads as "the bot ignores this market".
present_markets = odds_mod.markets_in_snapshot(st.session_state.raw_by_event)
fetched_markets = set(st.session_state.get("odds_markets") or [])
absent = [m for m in config["markets"] if m not in present_markets]
never_fetched = [m for m in absent if fetched_markets and m not in fetched_markets]
if absent:
    names = ", ".join(scoring.MARKET_LABELS.get(m, m) for m in absent)
    if never_fetched:
        st.warning(
            f"**{names} can't appear in any pick.** These are ticked under "
            "**Markets**, but the odds snapshot you're looking at was fetched "
            "with a narrower selection, so it holds no prices for them. "
            f"Fetching fresh odds would include them — about "
            f"{len(absent) * max(len(events), 1)} extra credits."
        )
    else:
        st.info(
            f"**{names}**: selected and fetched, but FanDuel hasn't posted any "
            "of these props for this slate yet. Refetch closer to game day."
        )

tab_parlay, tab_review, tab_diag, tab_history = st.tabs(
    ["Parlay", "Review & overrides", "Diagnostics", "History"]
)


# --------------------------------------------------------------------------
# Parlay tab
# --------------------------------------------------------------------------

with tab_parlay:
    rows = []
    for row in picks:
        prop = row.get("pick")
        flags = []
        if row.get("manual"):
            flags.append("manual")
        if prop and prop.get("questionable"):
            flags.append("⚠️ Q")
        if row.get("review_cards"):
            flags.append(f"🚩 {len(row['review_cards'])}")
        rows.append({
            "Team": row["team_name"],
            "Player": (prop.get("player_name_espn") or prop.get("player_name")) if prop else "—",
            "Prop": parlay_mod.describe_prop(prop) if prop else "NONE",
            "FanDuel": scoring.format_american(prop["price"]) if prop else "—",
            "Score": parlay_mod.score_text(prop) if prop else "—",
            "Tier": str(row.get("tier") or "—"),
            "Flags / reason": ", ".join(flags) if prop else (row.get("none_reason") or ""),
            "Betslip": (leg_link(prop) or "") if prop else "",
        })
    st.dataframe(
        pd.DataFrame(rows), width="stretch", hide_index=True,
        column_config={"Betslip": st.column_config.LinkColumn("Betslip", display_text="Add to slip")},
    )

    st.subheader("Parlay summary")
    metrics = st.columns(4)
    metrics[0].metric("Legs", f"{summary['leg_count']}"
                      + (f" (+{summary['empty_count']} empty)" if summary["empty_count"] else ""))
    metrics[1].metric(
        "Combined odds",
        scoring.format_american(summary["american_odds"]) if summary["american_odds"] else "—",
        help=f"{summary['decimal_odds']:.2f} decimal" if summary["decimal_odds"] else None,
    )
    metrics[2].metric("Payout", f"${summary['payout']:,.2f}",
                      help=f"On a ${summary['stake']:,.2f} stake")
    metrics[3].metric("Profit", f"${summary['profit']:,.2f}")
    if summary["implied_probability"]:
        st.caption(
            f"Book-implied probability of hitting all {summary['leg_count']} legs: "
            f"{summary['implied_probability']:.2%} (vig included)."
        )
    if summary["empty_count"]:
        st.warning(
            f"{summary['empty_count']} slot(s) returned NONE: "
            f"{', '.join(summary['empty_teams'])}. The math above is for "
            f"{summary['leg_count']} legs. Fill them by hand if you want."
        )

    st.subheader("Send it to whoever is placing the bet")
    betslip = parlay_link_status(picks)
    if betslip["url"]:
        st.success(
            f"One link that loads all {betslip['leg_count']} legs onto a FanDuel "
            "betslip. Whoever opens it gets the whole parlay — they don't add "
            "legs one at a time."
        )
        st.link_button("🔗 Open the full parlay on FanDuel", betslip["url"],
                       type="primary")
        st.caption("Or copy this and send it to them:")
        st.code(betslip["url"], language=None)
        st.caption(
            "The recipient needs their own FanDuel account, signed in, in a "
            "state where FanDuel operates. This URL format is unofficial, so "
            "check the slip matches the table before anyone places money on it."
        )
    else:
        st.warning(betslip["reason"])
        if betslip["missing"]:
            st.caption("Legs without IDs: " + ", ".join(betslip["missing"])
                       + ". The per-leg links in the table still work as a fallback.")
        with st.expander("What the odds feed actually returned"):
            st.caption(
                "The combined link needs a marketId and a selectionId for every "
                "leg. They come either from the feed's `sid` fields or from each "
                "leg's own FanDuel link."
            )
            st.write(f"- Legs carrying both `sid` fields: "
                     f"**{'yes' if betslip['has_sids'] else 'no'}**")
            st.write(f"- Legs carrying a per-selection FanDuel link: "
                     f"**{'yes' if betslip['has_links'] else 'no'}**")
            if betslip["sample_link"]:
                st.caption("Example of one leg's link:")
                st.code(betslip["sample_link"], language=None)
            else:
                st.caption("No per-selection links were returned either.")
            sample = next((row["pick"] for row in picks if row.get("pick")), None)
            if sample:
                st.caption("Raw ID fields on one leg:")
                st.json({k: sample.get(k) for k in
                         ("player_name", "market", "sid", "market_sid",
                          "outcome_link", "link")})

    st.subheader("Share block")
    st.code(parlay_mod.share_text(picks, summary, parlay_url=betslip["url"]),
            language=None)

    with st.expander("Market coverage this week"):
        total = coverage["total_events"]
        for market, count in coverage["by_market"].items():
            label = scoring.MARKET_LABELS.get(market, market)
            if market in never_fetched:
                st.write(f"- **{label}**: not in this snapshot — it wasn't fetched")
            else:
                st.write(f"- **{label}**: {count}/{total} games posted")
        if any(c < total for c in coverage["by_market"].values()):
            st.caption(
                "A market showing fewer games than the total was fetched but not "
                "fully posted — books add props through the week. One marked "
                "\"wasn't fetched\" needs a fresh fetch instead."
            )


# --------------------------------------------------------------------------
# Review & overrides tab
# --------------------------------------------------------------------------

with tab_review:
    review_rows = [(row, card) for row in picks for card in row.get("review_cards", [])]
    if review_rows:
        st.subheader(f"🚩 Too good to trust ({len(review_rows)})")
        st.caption(
            f"These beat the {float(config['sanity_ceiling']):.0%} sanity ceiling you set. "
            "An edge that big against a real sportsbook is usually a mistake "
            "somewhere rather than free money, so the bot leaves them out and "
            "asks you. Nothing here is used in the parlay until you approve it."
        )
        for row, card in review_rows:
            render_review_card(row, card)
    else:
        st.caption("No props tripped the sanity ceiling.")

    st.subheader("Override a pick")
    st.caption("Each team's top 5 alternatives from the props that passed every filter.")
    for row in picks:
        current = row.get("pick")
        header = (f"{row['team_name']} — "
                  + (f"{current.get('player_name_espn')} {parlay_mod.describe_prop(current)}"
                     if current else "NONE"))
        with st.expander(header):
            if row.get("none_reason"):
                st.warning(row["none_reason"])
            options = [("__auto__", "Automatic pick")]
            for alt in row["alternatives"]:
                options.append((
                    alt["prop_id"],
                    f"{alt.get('player_name_espn')} · {parlay_mod.describe_prop(alt)} · "
                    f"{scoring.format_american(alt['price'])} · {parlay_mod.score_text(alt)}",
                ))
            if current and row.get("manual"):
                options.insert(1, (current["prop_id"],
                                   f"(current) {current.get('player_name_espn')} · "
                                   f"{parlay_mod.describe_prop(current)}"))
            selected = st.session_state.overrides.get(row["team_id"], "__auto__")
            keys = [key for key, _ in options]
            index = keys.index(selected) if selected in keys else 0
            choice = st.radio(
                "Leg", options=keys, index=index, key=f"override-{row['team_id']}",
                format_func=lambda key, opts=dict(options): opts[key],
                label_visibility="collapsed",
            )
            if choice == "__auto__":
                st.session_state.overrides.pop(row["team_id"], None)
            else:
                st.session_state.overrides[row["team_id"]] = choice
            if len(row["alternatives"]) == 0:
                st.caption(f"No alternatives passed the filters "
                           f"({row['candidate_count']} props scored for this roster).")


# --------------------------------------------------------------------------
# Diagnostics tab
# --------------------------------------------------------------------------

with tab_diag:
    st.subheader("Names the matcher wouldn't guess")
    st.caption(
        "These looked similar to one of your players but weren't close enough "
        "to match on their own. **Most are simply different people** — an NFL "
        "player who isn't on anyone's roster in your league. That needs no "
        "action: the bot has already ignored them, which is what you want. "
        "Only alias one if it really is your player under a different spelling."
    )
    candidates = [
        item for item in (scored["unmatched_props"] + scored["missing_projections"])
        if not rejections.contains(item["name"], item.get("closest"))
    ]
    hidden = (len(scored["unmatched_props"]) + len(scored["missing_projections"])
              - len(candidates))

    if not candidates:
        st.success("Nothing needs your attention."
                   + (f" ({hidden} marked as different players.)" if hidden else ""))
    for index, item in enumerate(candidates):
        with st.container(border=True):
            teams_label = ", ".join(t for t in (item.get("teams") or []) if t)
            # The two sources mean opposite things, so they are described
            # separately: an odds name is someone the feed priced, while a CSV
            # entry is one of your players missing from the projections file.
            if item["source"] == "odds":
                st.markdown(
                    f"**{item['name']}** — priced by FanDuel in the "
                    f"{teams_label or 'this week'} game, but not matched to "
                    "anyone on your rosters."
                )
                nearest = "Nearest player on your rosters"
            else:
                st.markdown(
                    f"**{item['name']}**"
                    + (f" ({teams_label})" if teams_label else "")
                    + " — on your roster, but no row in the PFF file matches, "
                    "so none of their props can be scored."
                )
                nearest = "Nearest row in the PFF file"
            if item.get("closest"):
                st.caption(
                    f"{nearest}: *{item['closest']}* ({item['score']:.0f}/100 "
                    "similar). Same person, or two different players?"
                )
                same, different = st.columns(2)
                with same:
                    if st.button(f"✓ Same player — link to {item['closest']}",
                                 key=f"alias-{index}", width="stretch"):
                        if not aliases.add(item["name"], item["closest"]):
                            st.toast("Alias applied for this session but not saved.")
                        st.rerun()
                with different:
                    if st.button("✗ Different player — hide this",
                                 key=f"reject-{index}", width="stretch"):
                        rejections.add(item["name"], item["closest"])
                        st.rerun()
            else:
                st.caption("No close match anywhere — nothing to do.")

    if hidden:
        with st.expander(f"Marked as different players ({hidden})"):
            st.caption("Cleared if you ever want them back in the list above.")
            st.json(rejections.as_list())
            if st.button("Clear these"):
                st.session_state.rejections = RejectionStore(store)
                st.session_state.rejections._pairs.clear()
                st.session_state.rejections.save()
                st.rerun()

    fuzzy = scored.get("fuzzy_matches") or []
    if fuzzy:
        st.subheader(f"Approximate name matches ({len(fuzzy)})")
        st.caption(
            "These were accepted without being identical. A wrong one silently "
            "scores another player's odds, so it is worth a glance."
        )
        st.dataframe(
            pd.DataFrame([
                {"From": item["name"],
                 "Source": "odds feed" if item["source"] == "odds" else "PFF CSV",
                 "Matched to": item["matched"],
                 "Score": round(item["score"]),
                 "NFL": ", ".join(t for t in (item.get("teams") or []) if t)}
                for item in fuzzy
            ]),
            width="stretch", hide_index=True,
        )

    st.subheader("Roster exclusions")
    excluded = [
        {"Team": team["team_name"], "Player": player["name"],
         "NFL": player.get("nfl_team"), "Pos": player.get("position"),
         "Reason": player["exclusion_reason"]}
        for team in teams for player in team["players"]
        if not player.get("eligible")
    ]
    if excluded:
        st.dataframe(pd.DataFrame(excluded), width="stretch", hide_index=True)
    else:
        st.caption("No players were excluded.")

    st.subheader("Projection coverage")
    skill_players = [p for team in teams for p in team["players"]
                     if p.get("eligible") and p.get("position") in {"QB", "RB", "WR", "TE"}]
    missing_names = {item["name"] for item in scored["missing_projections"]}
    if skill_players:
        missing_share = len([p for p in skill_players if p["name"] in missing_names]) / len(skill_players)
        if missing_share > 0.15:
            st.warning(
                f"{missing_share:.0%} of eligible skill players are missing from the "
                "projections CSV. That usually means the file is from a different "
                "week or an older season — re-download this week's file."
            )
        else:
            st.caption(f"{1 - missing_share:.0%} of eligible skill players found in the CSV.")

    with st.expander("Aliases in use"):
        st.json(aliases.as_dict() or {"(none yet)": ""})
        st.download_button(
            "Download aliases.json", json.dumps(aliases.as_dict(), indent=2),
            file_name="aliases.json", mime="application/json",
        )


# --------------------------------------------------------------------------
# History tab
# --------------------------------------------------------------------------

with tab_history:
    healthy, message = store_status()
    (st.success if healthy and store.persistent else st.info)(message)

    st.subheader("Save this run")
    st.caption(
        "Saving keeps the config, the picks and (later) their Win/Loss grades. "
        "The download is the complete record, raw odds included, for reproducing "
        "a week exactly."
    )
    record = runlog.build_run_record(
        config=config, picks=picks, summary=summary, events=events,
        raw_by_event=st.session_state.raw_by_event, teams=teams,
        diagnostics={"unmatched_props": scored["unmatched_props"],
                     "missing_projections": scored["missing_projections"]},
        coverage=coverage, odds_fetched_at=st.session_state.odds_fetched_at,
    )
    save_col, download_col = st.columns(2)
    with save_col:
        if st.button("💾 Save this run", width="stretch", type="primary"):
            try:
                path = runlog.save_run(store, record)
            except Exception as exc:
                path = None
                st.error(str(exc))
            if path:
                st.success(f"Saved to {store.label} → `{path}`")
                st.cache_data.clear()
            else:
                st.warning("Couldn't save. Use the download button instead.")
    with download_col:
        st.download_button(
            "⬇️ Download full run log (JSON)", json.dumps(record, indent=2),
            file_name=f"{record['nfl_week_label']}-{record['run_id']}.json",
            mime="application/json", width="stretch",
        )

    st.subheader("Past runs")
    try:
        runs = runlog.list_runs(store)
    except Exception as exc:
        runs = []
        st.error(f"Couldn't read saved runs: {exc}")

    imported = st.file_uploader("Import a downloaded run log", type=["json"],
                                key="import-run")
    if imported is not None:
        try:
            runs.insert(0, json.loads(imported.getvalue()))
        except json.JSONDecodeError:
            st.error("That file isn't a valid run log.")

    if not runs:
        st.caption("No saved runs yet.")
    else:
        totals = runlog.season_totals(runs)
        cols = st.columns(4)
        cols[0].metric("Runs", totals["runs"])
        cols[1].metric("Legs won", totals["legs_won"])
        cols[2].metric(
            "Leg hit rate",
            f"{totals['leg_hit_rate']:.1%}" if totals["leg_hit_rate"] is not None else "—",
        )
        cols[3].metric("Parlays hit", f"{totals['parlays_hit']}/{totals['parlays_graded']}")
        if totals["legs_ungraded"]:
            st.caption(f"{totals['legs_ungraded']} legs still ungraded.")

        for run in runs[:10]:
            label = f"{run.get('nfl_week_label')} · {run.get('created_at', '')[:16]}"
            with st.expander(label):
                grades = {}
                for pick_row in run.get("picks", []):
                    prop = pick_row.get("pick")
                    leg_label = (f"{pick_row['team_name']} — "
                                 + (f"{prop.get('player_name_espn') or prop.get('player_name')} "
                                    f"{parlay_mod.describe_prop(prop)} "
                                    f"({scoring.format_american(prop['price'])})"
                                    if prop else "NONE"))
                    if not prop:
                        st.write(leg_label)
                        continue
                    current = pick_row.get("result") or "Ungraded"
                    choices = ["Ungraded", "Win", "Loss", "Push"]
                    grade = st.selectbox(
                        leg_label, choices,
                        index=choices.index(current) if current in choices else 0,
                        key=f"grade-{run.get('run_id')}-{pick_row['team_id']}",
                    )
                    grades[str(pick_row["team_id"])] = None if grade == "Ungraded" else grade

                if not run.get("_path"):
                    st.caption("Imported run — save it to grade it.")
                elif st.button("Save grades", key=f"save-grades-{run.get('run_id')}"):
                    try:
                        saved = runlog.update_results(store, run["_path"], grades)
                    except Exception as exc:
                        saved = False
                        st.error(str(exc))
                    if saved:
                        st.success("Grades saved.")
                        st.cache_data.clear()
                        st.rerun()
