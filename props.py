"""Prop Finder — the best plays on the slate, with no fantasy league involved.

A separate app from the parlay bot (app.py), sharing everything below the UI:
the same scoring, the same name matching, the same price limits, and the same
stored odds snapshot. A slate fetched by either app costs the other nothing.

Deploy it as a second Streamlit app from this repository with
"Main file path" set to `props.py` and the same secrets.
"""

from __future__ import annotations

import datetime as dt
import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from core import board as board_mod
from core import gate as gate_mod
from core import odds as odds_mod
from core import pickem as pickem_mod
from core import scoring
from core import slip as slip_mod
from core import snapshots
from core.betslip import leg_link
from core.config import CEILING_TICKS, ODDS_TICKS
from core.matching import AliasStore
from core.projections import ProjectionError, load_projections
from core.store import build_store

load_dotenv()

st.set_page_config(page_title="Prop Finder", page_icon="🔎", layout="wide")


# --------------------------------------------------------------------------
# Secrets and storage — the same backend the parlay bot writes to
# --------------------------------------------------------------------------

def get_secret(name: str) -> str:
    try:
        value = st.secrets.get(name)
        if value:
            return str(value).strip()
    except Exception:
        pass
    return (os.environ.get(name) or "").strip()


@st.cache_resource(show_spinner=False)
def get_store(fingerprint: str, repo: str, branch: str):
    return build_store(
        {"GITHUB_TOKEN": get_secret("GITHUB_TOKEN"), "GITHUB_REPO": repo,
         "GITHUB_DATA_BRANCH": branch},
        root="data",
    )


# Gate before any work: a locked-out visitor should not trigger a GitHub read,
# and certainly not reach the button that spends API credits.
if not gate_mod.require_password(get_secret(gate_mod.SECRET_NAME)):
    st.stop()


token = get_secret("GITHUB_TOKEN")
store, store_warning = get_store(
    f"{len(token)}:{token[-4:] if token else ''}",
    get_secret("GITHUB_REPO"),
    get_secret("GITHUB_DATA_BRANCH") or "parlay-data",
)
api_key = get_secret("ODDS_API_KEY") or None


# --------------------------------------------------------------------------
# Session state, restored from the shared snapshot
# --------------------------------------------------------------------------

def init_state() -> None:
    if "props_config" in st.session_state:
        return
    odds = snapshots.load_odds(store)
    frame, meta = snapshots.load_projections(store)
    st.session_state.update({
        "props_config": board_mod.load_props_config(store),
        "aliases": AliasStore(store),
        "events": (odds or {}).get("events") or [],
        "raw_by_event": (odds or {}).get("raw_by_event") or {},
        "odds_fetched_at": (odds or {}).get("fetched_at") or (odds or {}).get("saved_at"),
        "odds_markets": (odds or {}).get("markets") or [],
        "projections": frame,
        "projection_filename": meta.get("filename"),
        "quota": {},
        "slip_keys": slip_mod.load_slip(store),
        "stake": 10.0,
        "restored": bool(odds),
    })


init_state()
config = st.session_state.props_config


# --------------------------------------------------------------------------
# Sidebar — the same knobs, stored separately from the parlay bot's
# --------------------------------------------------------------------------

def render_sidebar() -> dict:
    st.sidebar.title("🔎 Finder settings")
    st.sidebar.caption(
        "The same scoring the parlay bot uses. Settings are stored separately, "
        "so changing them here never affects that app."
    )
    new = dict(config)

    with st.sidebar.expander("Listing thresholds", expanded=True):
        st.caption(
            "Gap and EV are different quantities — a 10% gap is not a 10% EV — "
            "so each has its own minimum."
        )
        new["min_gap"] = st.slider(
            "Minimum gap (yardage props)", 0, 60,
            int(round(float(config["min_gap"]) * 100)), step=1, format="%d%%",
            help="How far the projection must beat the line.",
        ) / 100
        new["min_ev"] = st.slider(
            "Minimum EV (receptions, TDs)", 0, 100,
            int(round(float(config["min_ev"]) * 100)), step=1, format="%d%%",
            help="Expected return per dollar, from the Poisson model.",
        ) / 100
        new["sanity_ceiling"] = st.slider(
            "Sanity ceiling", 10, 200,
            int(round(float(config["sanity_ceiling"]) * 100)), step=5, format="%d%%",
            help="An edge above this usually means stale odds or a bad name "
                 "match rather than value.",
        ) / 100
        new["hide_review"] = st.toggle(
            "Hide plays above the sanity ceiling", value=bool(config["hide_review"]))

    with st.sidebar.expander("Price limits", expanded=True):
        floor_value = (config["odds_floor"] if config["odds_floor"] in ODDS_TICKS
                       else board_mod.PROPS_DEFAULTS["odds_floor"])
        new["odds_floor"] = st.select_slider(
            "Odds floor", options=ODDS_TICKS, value=floor_value,
            format_func=scoring.format_american,
            help="Shortest price worth taking.")
        ceiling_value = (config.get("odds_ceiling")
                         if config.get("odds_ceiling") in CEILING_TICKS
                         else board_mod.PROPS_DEFAULTS["odds_ceiling"])
        new["odds_ceiling"] = st.select_slider(
            "Odds ceiling", options=CEILING_TICKS, value=ceiling_value,
            format_func=lambda v: "No ceiling" if v is None else scoring.format_american(v),
            help="Longest price worth taking.")

    with st.sidebar.expander("Volume floors", expanded=False):
        st.caption(
            "PFF projections are means of right-skewed distributions while "
            "lines sit near medians, which flatters overs on low-volume "
            "players. These floors keep scoring where mean ≈ median."
        )
        new["yards_floor"] = st.slider(
            "Rec / rush yards", 0.0, 80.0, float(config["yards_floor"]), step=1.0)
        new["pass_yards_floor"] = st.slider(
            "Passing yards", 0.0, 320.0, float(config["pass_yards_floor"]), step=5.0)
        new["rush_attempts_floor"] = st.slider(
            "Rush attempts", 0.0, 25.0, float(config["rush_attempts_floor"]), step=1.0)
        new["receptions_floor"] = st.slider(
            "Receptions", 0.0, 6.0, float(config["receptions_floor"]), step=0.1)

    with st.sidebar.expander("Markets", expanded=False):
        selected = []
        for heading, keys in (
            ("Yardage — scored by gap", sorted(scoring.GAP_MARKETS)),
            ("Expected value — scored by EV", sorted(scoring.EV_MARKETS)),
        ):
            st.markdown(f"**{heading}**")
            for key in keys:
                if st.checkbox(scoring.MARKET_LABELS.get(key, key),
                               value=key in config.get("markets", []),
                               key=f"pf_market__{key}"):
                    selected.append(key)
        new["markets"] = [k for k in odds_mod.DEFAULT_MARKETS if k in selected]
        new["ev_enabled"] = st.toggle("EV props enabled", value=bool(config["ev_enabled"]))
        new["fuzzy_threshold"] = st.slider(
            "Fuzzy match threshold", 70, 100, int(config["fuzzy_threshold"]),
            help="Names with disagreeing first names are rejected at any score, "
                 "so a lower cutoff is safer than it looks.")

    save_col, reset_col = st.sidebar.columns(2)
    with save_col:
        if st.button("💾 Save", width="stretch"):
            st.toast("Settings saved." if board_mod.save_props_config(new, store)
                     else "Couldn't save settings.")
    with reset_col:
        if st.button("Reset", width="stretch"):
            st.session_state.props_config = dict(board_mod.PROPS_DEFAULTS)
            for key in list(st.session_state.keys()):
                if str(key).startswith("pf_market__"):
                    del st.session_state[key]
            st.rerun()

    st.sidebar.divider()
    healthy, message = (True, store.label)
    try:
        healthy, message = store.check()
    except Exception as exc:
        healthy, message = False, str(exc)
    (st.sidebar.success if healthy and store.persistent else st.sidebar.info)(message)
    return new


new_config = render_sidebar()
if new_config != config:
    st.session_state.props_config = new_config
    config = new_config


# --------------------------------------------------------------------------
# Header and data
# --------------------------------------------------------------------------

st.title("🔎 Prop Finder")
st.caption(
    "Every player prop on the slate, scored the same way the parlay bot scores "
    "its legs. No fantasy league involved."
)

setup, actions = st.columns([3, 2])
with setup:
    uploaded = st.file_uploader(
        "PFF **weekly** projections CSV", type=["csv"],
        help="The same file the parlay bot uses. Uploading here shares it with "
             "that app too.")
    if uploaded is not None and uploaded.name != st.session_state.projection_filename:
        try:
            frame, warnings = load_projections(uploaded, uploaded.name)
            st.session_state.projections = frame
            st.session_state.projection_filename = uploaded.name
            snapshots.save_projections(store, frame=frame, filename=uploaded.name,
                                       warnings=warnings)
        except ProjectionError as exc:
            st.error(str(exc))

with actions:
    window_start, window_end = odds_mod.default_window()
    dates = st.columns(2)
    start_date = dates[0].date_input("Games from", value=window_start.date())
    end_date = dates[1].date_input("Games through", value=window_end.date())
    start = max(dt.datetime.combine(start_date, dt.time.min, tzinfo=dt.timezone.utc),
                window_start)
    end = dt.datetime.combine(end_date, dt.time(12, 0), tzinfo=dt.timezone.utc)

    if st.button("🔄 Fetch fresh odds", type="primary", width="stretch",
                 disabled=not api_key,
                 help=f"About {len(config['markets'])} credits per game. The "
                      "result is shared with the parlay bot."):
        try:
            provider = odds_mod.TheOddsAPI(api_key)
            events = odds_mod.filter_events(
                provider.get_week_events(start, end), start, end)
            raw = {}
            progress = st.progress(0.0, text="Fetching FanDuel props…")
            for index, event in enumerate(events, start=1):
                raw[event["id"]] = provider.get_event_props(
                    event["id"], config["markets"])
                progress.progress(index / max(len(events), 1),
                                  text=f"Fetching… {index}/{len(events)} games")
            progress.empty()
            fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
            st.session_state.update({
                "events": events, "raw_by_event": raw,
                "odds_fetched_at": fetched_at,
                "odds_markets": list(config["markets"]),
                "quota": provider.quota, "restored": False,
            })
            snapshots.save_odds(store, events=events, raw_by_event=raw,
                                markets=config["markets"], fetched_at=fetched_at)
            st.rerun()
        except odds_mod.OddsError as exc:
            st.error(f"Odds API: {exc}")


def age(iso):
    if not iso:
        return "never"
    try:
        moment = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    minutes = max(0.0, (dt.datetime.now(dt.timezone.utc) - moment).total_seconds() / 60)
    if minutes < 60:
        return f"{minutes:.0f} min ago"
    if minutes < 1440:
        return f"{minutes / 60:.1f} hours ago"
    return f"{minutes / 1440:.1f} days ago"


status = st.columns(3)
status[0].caption(
    f"**Odds:** {age(st.session_state.odds_fetched_at)}"
    + (f" · {len(st.session_state.raw_by_event)} games" if st.session_state.raw_by_event else "")
    + (" · shared snapshot" if st.session_state.restored else ""))
status[1].caption(f"**Projections:** {st.session_state.projection_filename or 'not uploaded'}")
quota = st.session_state.quota or {}
status[2].caption(
    f"**API credits left:** {quota['remaining']}" if quota.get("remaining") is not None
    else "**Storage:** shared with the parlay bot")

if store_warning:
    st.warning(store_warning)
if not store.persistent:
    st.info(
        "Storage is local-only, so this app can't see the parlay bot's saved "
        "odds. Set `GITHUB_TOKEN` and `GITHUB_REPO` in this app's secrets — the "
        "same values — and both apps share one snapshot."
    )

if not st.session_state.raw_by_event or st.session_state.projections is None:
    missing = []
    if not st.session_state.raw_by_event:
        missing.append("fetch odds (or let the parlay bot's snapshot load)")
    if st.session_state.projections is None:
        missing.append("upload the weekly PFF CSV")
    st.info("To see plays: " + ", then ".join(missing) + ".")
    st.stop()


# --------------------------------------------------------------------------
# Score the whole slate
# --------------------------------------------------------------------------

events = odds_mod.filter_events(st.session_state.events, start, end)
if st.session_state.events and not events:
    st.error(
        "Every game in the loaded odds falls outside your date window. Widen "
        "the dates, or fetch fresh odds."
    )
    st.stop()

board = board_mod.score_board(
    events=events, raw_by_event=st.session_state.raw_by_event,
    projections=st.session_state.projections, config=config,
    aliases=st.session_state.aliases,
)
plays = board_mod.qualifying(board["rows"], config)
options = board_mod.filter_options(board["rows"])


# --------------------------------------------------------------------------
# Filters — on the page, driven by whatever slate is loaded
# --------------------------------------------------------------------------

st.subheader("Filters")
row1 = st.columns([2, 1, 1])
picked_games = row1[0].multiselect("Game", options["matchups"],
                                   placeholder="All games")
picked_teams = row1[1].multiselect("Team", options["teams"],
                                   placeholder="All teams")
picked_positions = row1[2].multiselect("Position", options["positions"],
                                       placeholder="All positions")

row2 = st.columns([2, 2, 1])
picked_players = row2[0].multiselect("Player", options["players"],
                                     placeholder="All players")
picked_markets = row2[1].multiselect(
    "Market", options["markets"], placeholder="All markets",
    format_func=lambda m: scoring.MARKET_LABELS.get(m, m))
picked_kinds = row2[2].multiselect(
    "Scored by", options["kinds"], placeholder="Gap and EV",
    format_func=lambda k: "Gap (yardage)" if k == "gap" else "EV (receptions, TDs)")

filtered = board_mod.apply_filters(
    plays, matchups=picked_games, teams=picked_teams, players=picked_players,
    positions=picked_positions, markets=picked_markets, kinds=picked_kinds,
)


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

st.subheader(f"Best plays ({len(filtered)})")
if not filtered:
    st.info(
        "Nothing passes those filters. The listing thresholds in the sidebar "
        "are usually the reason — try lowering the minimum gap or EV."
    )
else:
    st.caption(
        f"{len(board['rows'])} props scored across {len(events)} games. "
        "**Gap and EV are different measures** — a +40% gap and a +40% EV are "
        "not the same claim — so compare within a kind, not across."
    )
    # Percentages are scaled here, not in the format string: Streamlit runs
    # printf against the raw value, so 0.457 with "%.1f%%" renders as "0.5%".
    table = pd.DataFrame([{
        "Score": row["score"] * 100,
        "By": "Gap" if row["kind"] == "gap" else "EV",
        "Player": row["player"],
        "Pos": row.get("position") or "",
        "Team": row.get("team") or "",
        "Game": row.get("matchup") or "",
        "Prop": (row["market_label"] if row["line"] is None
                 else f"{row['market_label']} o{row['line']:g}"),
        "Price": scoring.format_american(row["price"]),
        "Projection": row.get("projection"),
        "Model %": None if row.get("probability") is None else row["probability"] * 100,
        "Book %": (None if row.get("book_probability") is None
                   else row["book_probability"] * 100),
        "Kickoff": row.get("kickoff") or "",
        "Bet": leg_link(row) or "",
    } for row in filtered])

    st.caption("Tick a row to add it to your slip.")
    event = st.dataframe(
        table, width="stretch", hide_index=True,
        on_select="rerun", selection_mode="multi-row", key="board_table",
        column_config={
            "Score": st.column_config.NumberColumn("Score", format="%.1f%%",
                                                   help="Gap or EV, per the By column"),
            "Projection": st.column_config.NumberColumn(format="%.2f"),
            "Model %": st.column_config.NumberColumn(format="%.1f%%"),
            "Book %": st.column_config.NumberColumn(format="%.1f%%"),
            "Bet": st.column_config.LinkColumn("Bet", display_text="FanDuel"),
        },
    )

    chosen = list(getattr(event, "selection", {}).get("rows", []) or [])
    add_col, download_col = st.columns([2, 1])
    with add_col:
        if st.button(f"➕ Add {len(chosen)} selected to slip" if chosen
                     else "➕ Add selected to slip",
                     type="primary", width="stretch", disabled=not chosen):
            keys = [slip_mod.leg_key(filtered[i]) for i in chosen
                    if 0 <= i < len(filtered)]
            before = len(st.session_state.slip_keys)
            st.session_state.slip_keys = slip_mod.add_many(
                st.session_state.slip_keys, keys)
            added = len(st.session_state.slip_keys) - before
            slip_mod.save_slip(store, st.session_state.slip_keys)
            st.toast(f"Added {added} leg(s)." if added
                     else "Those plays are already on the slip.")
            st.rerun()
    with download_col:
        st.download_button(
            "⬇️ Download plays (CSV)", table.to_csv(index=False),
            file_name="prop-finder.csv", mime="text/csv", width="stretch")

    st.subheader("Why these numbers")
    for row in filtered[:15]:
        line = "" if row["line"] is None else f" o{row['line']:g}"
        with st.expander(
            f"{row['score']:+.1%} · {row['player']} {row['market_label']}{line} "
            f"at {scoring.format_american(row['price'])}"
        ):
            st.write(row["why"])
            detail = st.columns(4)
            detail[0].caption(f"Game: {row.get('matchup')} · {row.get('kickoff')}")
            detail[1].caption(f"Projection ({row.get('projection_field')}): "
                              f"{row.get('projection', 0):.2f}")
            detail[2].caption(
                f"Match: {row.get('match_method')} ({row.get('match_score', 0):.0f})")
            detail[3].caption(f"Odds updated: {age(row.get('last_update'))}")
            if row.get("needs_review"):
                st.warning(
                    "This clears the sanity ceiling. An edge that big usually "
                    "means stale odds, un-priced news, or a bad name match."
                )

# --------------------------------------------------------------------------
# The slip — plays chosen by hand, and the link to hand on
# --------------------------------------------------------------------------

st.divider()
legs, missing_keys = slip_mod.resolve(st.session_state.slip_keys, board["rows"])
st.subheader(f"Your slip ({len(legs)} legs)")

if missing_keys:
    st.warning(
        f"{len(missing_keys)} leg(s) that were on the slip are no longer on the "
        "board — the market moved, was withdrawn, or is filtered out by your "
        "current thresholds. They are not included in the odds below."
    )

if not legs:
    st.info("Nothing on the slip yet. Tick rows in the table above and press "
            "**Add selected to slip**.")
else:
    for index, leg in enumerate(legs):
        line = "" if leg["line"] is None else f" o{leg['line']:g}"
        detail, remove_col = st.columns([9, 1])
        with detail:
            st.markdown(
                f"**{leg['player']}** {leg['market_label']}{line} at "
                f"**{scoring.format_american(leg['price'])}** · "
                f"{leg.get('matchup')} · "
                f"{'Gap' if leg['kind'] == 'gap' else 'EV'} {leg['score']:+.1%}"
            )
        with remove_col:
            if st.button("✕", key=f"drop-{index}", help="Remove this leg"):
                st.session_state.slip_keys = slip_mod.remove(
                    st.session_state.slip_keys, slip_mod.leg_key(leg))
                slip_mod.save_slip(store, st.session_state.slip_keys)
                st.rerun()

    st.session_state.stake = st.number_input(
        "Stake ($)", min_value=0.0, value=float(st.session_state.stake), step=1.0,
        help="Display only — this app never places a bet.")
    summary = slip_mod.summarize(legs, float(st.session_state.stake))

    metrics = st.columns(4)
    metrics[0].metric("Legs", summary["leg_count"])
    metrics[1].metric(
        "Combined odds",
        scoring.format_american(summary["american_odds"]) if summary["american_odds"]
        else "—",
        help=f"{summary['decimal_odds']:.2f} decimal" if summary["decimal_odds"] else None)
    metrics[2].metric("Payout", f"${summary['payout']:,.2f}")
    metrics[3].metric("Profit", f"${summary['profit']:,.2f}")
    if summary["implied_probability"]:
        st.caption(
            f"Book-implied probability of hitting all {summary['leg_count']} legs: "
            f"{summary['implied_probability']:.2%} (vig included)."
        )

    st.subheader("Where to place it")
    fanduel_tab, pickem_tab = st.tabs(["FanDuel", "PrizePicks / pick'em"])

    status = slip_mod.link_status(legs)
    with fanduel_tab:
        if status["url"]:
            st.success(
                f"One link that loads all {len(legs)} legs onto a FanDuel betslip."
            )
            st.link_button("🔗 Open this parlay on FanDuel", status["url"],
                           type="primary")
            st.caption("Or copy this and send it to whoever is placing the bet:")
            st.code(status["url"], language=None)
        else:
            st.warning(status["reason"])
            if status["missing"]:
                st.caption("Legs without IDs: " + ", ".join(status["missing"]))
        st.caption("Share block:")
        st.code(slip_mod.share_text(legs, summary, status["url"]), language=None)

    with pickem_tab:
        st.caption(
            "Pick'em apps have no price on a single selection — the payout is a "
            "fixed multiplier for the whole slip — so the odds don't travel. "
            "The player, stat and line do."
        )
        pickem = pickem_mod.combined(legs)
        if pickem["probability"] is not None:
            columns = st.columns(3)
            columns[0].metric("All legs land together",
                              f"{pickem['probability']:.1%}")
            columns[1].metric("Payout must beat",
                              f"{pickem['breakeven_multiplier']:.2f}x",
                              help="A pick'em multiplier above this is profitable "
                                   "if these hit rates hold.")
            columns[2].metric("Legs", pickem["legs"])
            if pickem["from_price"]:
                st.info(
                    f"**That is the market's view, not ours.** "
                    f"{pickem['from_price']} of {pickem['legs']} legs are yardage "
                    "props, which have no Poisson model, so their hit rate comes "
                    "from FanDuel's price. Your projections are claiming those "
                    "lines land *more* often than the price implies — that "
                    "disagreement is the bet. Read the figure as the bar to "
                    "clear, not as a forecast. "
                    + (f"{pickem['modelled']} leg(s) do use the model."
                       if pickem["modelled"] else "")
                )

        st.markdown("**Check the lines before you enter them**")
        st.caption(
            "Pick'em lines are usually close to a sportsbook's but not identical. "
            "*Room* is how far the projection sits above the line here — if the "
            "app's line is higher than that room, the play no longer stands."
        )
        comparison = pd.DataFrame(pickem_mod.line_comparison(legs))
        st.dataframe(
            comparison, width="stretch", hide_index=True,
            column_config={
                "FanDuel line": st.column_config.NumberColumn(format="%.1f"),
                "Our projection": st.column_config.NumberColumn(format="%.1f"),
                "Room": st.column_config.NumberColumn(
                    format="%+.1f", help="Projection minus the FanDuel line"),
            },
        )

        st.markdown("**Quick entry**")
        st.caption(
            "No deep link is possible here — a pick'em app's slip URL needs its "
            "own projection IDs, and the odds feed carries FanDuel's only. These "
            "copy buttons are the next best thing: paste each name into the app's "
            "search, then take the pick shown beside it."
        )
        for index, leg in enumerate(legs):
            name_col, pick_col = st.columns([1, 1])
            with name_col:
                st.code(leg.get("player") or "", language=None)
            with pick_col:
                st.markdown(
                    f"{pickem_mod.stat_name(leg)} — **{pickem_mod.selection(leg)}**"
                    + (f"  ·  we project {leg['projection']:.1f}"
                       if leg.get("projection") is not None else "")
                )

        st.caption("Or copy the whole slip:")
        st.code(pickem_mod.slip_text(legs), language=None)

    if st.button("🗑️ Clear the slip"):
        st.session_state.slip_keys = []
        slip_mod.save_slip(store, [])
        st.rerun()

with st.expander(f"Names that couldn't be matched ({len(board['unmatched'])})"):
    st.caption(
        "Props whose player isn't in the projections file. Most are simply "
        "players PFF didn't project; they are ignored rather than guessed at."
    )
    if board["unmatched"]:
        st.dataframe(
            pd.DataFrame([{
                "Name": item["name"],
                "Game teams": ", ".join(t for t in (item.get("teams") or []) if t),
                "Closest in CSV": item.get("closest") or "—",
                "Score": round(item.get("score") or 0),
            } for item in board["unmatched"]]),
            width="stretch", hide_index=True)
    else:
        st.success("Everything matched.")
