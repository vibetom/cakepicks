# League Parlay Bot

Builds a weekly NFL parlay for a fantasy league — one leg per fantasy team, so
a 10-team league gives a 10-leg parlay. Each team contributes the best-value
FanDuel player prop belonging to a player on its roster. The app picks the legs and shows its reasoning;
**you place the bet yourself at FanDuel.**

## Ground rules the bot follows

- **Overs only.** Every leg is an OVER, or an anytime-TD "Yes". Unders are never considered.
- **Hard filters, never relaxed.** If nothing on a team passes every filter, that slot
  returns `NONE` with the reason. The bot will not loosen a threshold to force a pick.
- **Best available, not necessarily great.** Among props that pass the filters, each team
  gets its best one even if it doesn't clear the value thresholds.
- **Every parameter is a control.** Retuning never requires a code change.

---

## Part 1 — Publish it on the web (start here)

You'll host it on **Streamlit Community Cloud**, which is free and connects
straight to GitHub. Total time: about ten minutes. You need two things you
probably already have: a GitHub account, and a key from
[the-odds-api.com](https://the-odds-api.com).

### Step 1 — Get the code onto GitHub

The code is already pushed to this repository on the branch
`claude/wizardly-newton-28zsh1`. You can deploy directly from that branch, or
merge it into `main` first. To merge it (recommended, so the URL is stable):

1. Open the repository on GitHub.
2. If GitHub offers a **"Compare & pull request"** button, click it, then
   **Create pull request**, then **Merge pull request**.
3. If the repo has no `main` branch yet, open **Settings → General → Default branch**
   and set the default to `claude/wizardly-newton-28zsh1` — that works just as well.

### Step 2 — Create the Streamlit app

1. Go to **[share.streamlit.io](https://share.streamlit.io)** and click **Sign in with GitHub**.
2. Authorize Streamlit when GitHub asks. If this repository is **private**, you must
   grant Streamlit access to it — on the authorization screen choose
   **"All repositories"** or explicitly tick `cakepicks`. Without this the repo
   won't appear in the next step.
3. Click **Create app**, then **"Deploy a public app from GitHub"**.
4. Fill the form in:
   - **Repository:** `vibetom/cakepicks`
   - **Branch:** `main` (or `claude/wizardly-newton-28zsh1` if you skipped the merge)
   - **Main file path:** `app.py`
   - **App URL:** pick whatever you like — this becomes `your-name.streamlit.app`

### Step 3 — Add your API key (do this before you hit Deploy)

Your key must never go in the repository. Streamlit has a secrets vault for it.

1. On that same form, click **Advanced settings…**
2. Set **Python version** to **3.11**.
3. In the **Secrets** box, paste exactly this, with your real key:

   ```toml
   ODDS_API_KEY = "paste_your_key_here"
   ```

4. Click **Save**, then **Deploy**.

The first build takes 2–5 minutes while it installs dependencies. When it's
done you'll land on the running app.

> **You can change secrets later** without redeploying: from the app, use the
> **⋮ menu → Settings → Secrets**, edit, and save. The app restarts itself.

### Step 4 — Lock it down (important)

A public Streamlit URL is usable by anyone who finds it, **and every fetch they
trigger spends your API credits.** Restrict it:

1. In the app, click **⋮ → Settings → Sharing**.
2. Set viewing to **"Only specific people can view this app"** and add the email
   addresses of whoever should have access (your league, or just you).

The free Community Cloud tier includes one private app, which is exactly what
you need here.

### Step 5 — Rotate your key

If your key has ever been pasted into a chat, an email, or a screenshot, treat
it as compromised. Log in at [the-odds-api.com](https://the-odds-api.com),
regenerate the key, and update it in **⋮ → Settings → Secrets**. Nothing else
needs to change.

### Step 6 — Make your history permanent (recommended)

Streamlit's disk is wiped every time the app restarts, so saved runs would
vanish. To keep them, let the app commit its history back to this repository.
It writes to a separate branch called `parlay-data` that holds **no code**, so
saving data never redeploys your app.

You need a GitHub token that can write to this one repo, and nothing else:

1. On GitHub, click your avatar → **Settings** (your account settings, not the
   repository's).
2. Scroll to the bottom of the left sidebar → **Developer settings**.
3. **Personal access tokens → Fine-grained tokens → Generate new token**.
4. Fill it in:
   - **Token name:** `parlay-bot`
   - **Expiration:** 1 year (you'll need to redo this when it expires)
   - **Repository access:** choose **Only select repositories**, then pick `cakepicks`
   - **Permissions → Repository permissions → Contents:** change to **Read and write**
5. Click **Generate token** and copy it. It starts with `github_pat_` and is shown
   **only once**.
6. Back in your app: **⋮ → Settings → Secrets**, and add two more lines so the box
   reads:

   ```toml
   ODDS_API_KEY = "your_odds_key"
   GITHUB_TOKEN = "github_pat_your_token_here"
   GITHUB_REPO  = "vibetom/cakepicks"
   ```

7. Save. The app restarts, and the sidebar's **Storage** section should turn green
   with "Saving to GitHub".

From then on the app saves its odds snapshot, rosters and projections there
automatically, and reloads them on the next start — so restarts stop costing
credits. **💾 Save this run** in the History tab commits that week's picks too,
and your Win/Loss grades and season totals survive restarts. You can browse the files on GitHub, and even fix a grade by editing
the JSON there directly.

**How to tell whether it worked:** the app shows a red banner reading *"Nothing
here will survive your next redeploy"* whenever storage is local-only. Once the
secrets are right that banner disappears and the sidebar's **Storage** section
turns green. You can also check GitHub directly: a `parlay-data` branch appears
in the branch list the first time anything is saved. **No branch means nothing
has been saved yet**, and every redeploy is costing you a fresh fetch.

If you skip this step nothing breaks, but every redeploy costs another ~100 API
credits. As a stopgap, **Back up / restore odds snapshot** (next to the Fetch
button) downloads the snapshot to a file and reloads it afterwards — same
result, done by hand.

### Things to know about free hosting

| Behavior | What it means for you |
|---|---|
| The app sleeps after ~12 hours idle | The next visitor wakes it; takes ~30 seconds. Normal. |
| The disk is wiped on every restart | By default, saved runs, odds snapshots, aliases and settings do **not** survive — meaning a restart would cost you another ~100 API credits. Step 6 fixes this permanently, and is the main reason to do it. |
| Anyone who can view it can spend your credits | Hence Step 4. |

---

## Part 2 — Using it each week

1. **Download this week's PFF projections.** In PFF+, go to Fantasy →
   Projections and export the **WEEKLY** file as CSV. The app rejects the
   season-long file on purpose — the two look similar, and the season file would
   silently produce nonsense.
2. **Upload the CSV** in the app.
3. **Click "Fetch fresh odds."** This is the only action that spends API credits
   (roughly 7 per game, so ~100 for a full slate). It also reloads your ESPN
   rosters, which is free.

   Every fetch is saved, and the app **reloads it automatically the next time it
   starts** — so restarting, editing a secret, or coming back tomorrow costs
   nothing. The status line says "restored, no credits spent" when that happens,
   and warns you once the odds are more than a day old. Fetch again before you
   actually place the bet; lines move.
4. **Read the table.** One row per fantasy team, with the prop, the FanDuel
   price, the score, and the tier.
5. **Move the sliders** to taste. Re-scoring is instant and **never** re-calls
   the API — only the Fetch button does that.
6. **Check the "Review & overrides" tab** if any leg is flagged 🚩, and swap in
   an alternative for any team you disagree with.
7. **Send it on.** Under *"Send it to whoever is placing the bet"* there's a single
   link that loads **every leg at once** onto a FanDuel betslip — the recipient
   doesn't add them one at a time. Copy that link, or copy the share block (which
   contains it) into your league chat. Whoever opens it needs their own FanDuel
   account and to be somewhere FanDuel operates.
8. **Hit "💾 Save this run"** in the History tab. Once the games finish, come back
   and mark each leg Win/Loss/Push — the season totals build up from there.

Free tier is 500 credits/month, so about 4–5 full fetches. If you're running
short, uncheck markets under **Markets** in the sidebar — each one you drop
saves about one credit per game.

---

## Part 3 — How it picks

**Yardage props** (rec yds, rush yds, pass yds, rush attempts) are scored by
**gap** = `(projection − line) / line`.

**EV props** (receptions, anytime TD, pass TDs) are scored by **expected value**
= `P(win) × decimal odds − 1`, where the probability comes from a Poisson model:

- Anytime TD: `λ = rushTD + recvTD + returnTD`, `P = 1 − e^(−λ)`. (Expected TDs is
  not the same as the chance of scoring one — multi-TD games collapse into a
  single "yes", which is what this correction handles.)
- Receptions / pass TDs: `P(over) = 1 − PoissonCDF(⌊line⌋, λ)`.

Then, per fantasy team:

1. **Tier 1** — the best yardage prop clears the gap threshold. An EV prop that
   clears the EV threshold overrides it.
2. **Tier 2** — nothing cleared a threshold, so the best available prop is taken
   anyway.
3. **Tier 3** — nothing passed the hard filters at all: `NONE`, with the reason.

Ties break on the better price, then alphabetically. No player can fill two slots.

The results table's **Why** column says which rule produced each leg — `Gap`,
`EV`, `EV override`, `Best available` or `Manual`. Expanding a team under
**Review & overrides** spells it out in full, naming the prop an EV override
displaced. That is usually the answer when a pick changes unexpectedly after
moving a slider: the EV override is what most often swaps a strong yardage prop
for a longer-priced one.

### Why the volume floors exist — don't zero them out

PFF projections are per-game **means** of right-skewed distributions, while
sportsbook lines sit near **medians**. Comparing a mean to a line therefore
flatters overs on low-volume players: a receiver projected for 18 yards has a
mean well above his median outcome. The volume floors restrict gap scoring to
players where mean ≈ median. Setting them to zero will produce confident-looking
garbage.

### Why the ceiling matters

Ten legs multiply. A parlay of longshot anytime-TD props prices like a lottery
ticket rather than a bet: on one real slate, three legs at +330/+440/+460 took
the whole parlay to **+1,072,419**. Capping single legs at +300 dropped it to
**+54,687** with all ten slots still filled — the longshot picks simply fell
back to each team's next-best prop.

Anytime TD is where these turn up, since a low-usage player's TD price runs
long. If you want some of them back, slide the ceiling to +400.

### Why you may see a lot of NONE at first

The default odds floor is **-110**, but FanDuel routinely prices yardage props at
**-114 or -115**. A -110 floor rejects those. That's the design working as
specified, not a bug — but if nearly every slot comes back NONE, move the odds
floor to **-120** and it will look much healthier.

---

## Settings reference

| Setting | Default | What it does |
|---|---|---|
| Yardage gap threshold (X) | +10% | How far a projection must beat the line for Tier 1 |
| EV override threshold (Y) | +20% | EV needed to steal a slot from a qualifying yardage prop |
| Sanity ceiling | +35% | EV above this is flagged for review, never auto-picked |
| Odds floor | −110 | Shortest price accepted — rejects props too juiced to be worth taking |
| Odds ceiling | +300 | Longest price accepted — rejects longshots. Set to *No ceiling* to disable |
| Rec/rush yards floor | 25 yds | Minimum projection to gap-score a yardage prop |
| Passing yards floor | 175 yds | Same, for QBs |
| Rush attempts floor | 8 att | Same, for carries |
| Receptions floor | 1.0 | Minimum λ for a receptions prop |
| EV props enabled | ON | Off = yardage props only |
| Starters only | OFF | On = ignore bench and IR |
| Exclude Questionable | OFF | Off = still eligible, flagged ⚠️ |
| Stake | $10 | Display only — the app never places a bet |
| Season year | 2026 | ESPN season |
| ESPN league ID | 563635 | Change this in the sidebar to point at a different league — no redeploy needed |
| Fuzzy match threshold | 90 | Below this, a name is reported unmatched rather than guessed. Names with disagreeing first names are rejected at any score, so a lower cutoff is safer than it looks |
| Markets | all 7 | Unchecking one drops it from the picks immediately and from the next fetch. Re-checking is **not** symmetric: a market can only appear if it was included when the odds were fetched, so re-check it *then fetch fresh odds* |

Players who are OUT, on IR, suspended, doubtful, or whose NFL team has no game
in the window are always excluded — those are not adjustable.

---

## Run it on your own computer instead

```bash
git clone https://github.com/vibetom/cakepicks.git
cd cakepicks
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env and paste your key
streamlit run app.py
```

It opens at http://localhost:8501. Running locally, saved runs and aliases
persist properly, since the disk isn't ephemeral.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "No Odds API key found" | Secrets aren't set. **⋮ → Settings → Secrets**, add `ODDS_API_KEY = "..."`, save. |
| "This looks like a SEASON projections file" | You exported the season projections. Re-export the **weekly** file. |
| "Could not load ESPN league" | The league must be public: ESPN → League Settings → Basic Settings → Visibility → Public. Also check the season year in the sidebar. |
| Quota exhausted (429) | You've used 500 credits this month. The app falls back to the last cached odds; selection still runs. |
| Lots of empty slots | See "Why you may see a lot of NONE" above — usually the odds floor. |
| A whole market never appears, even in the override lists | The cached snapshot was fetched while that market was unchecked, so it holds no prices for it. The app now says so above the results; fetch fresh odds to include it. |
| A player's props are ignored | Check **Diagnostics → Names the matcher wouldn't guess** and choose *Same player* to link them. Nicknames like Kenny/Kenneth need this; the matcher will not guess them. |
| Diagnostics lists a name that isn't your player | That is the normal case, not an error — an NFL player nobody rosters, whose name resembles one of yours. The bot has already ignored him. Click *Different player — hide this* to stop it being listed. |
| A leg looks far too good to be true | Check **Approximate name matches** in Diagnostics. Two different players with the same surname (Brian/Bijan Robinson, Malik/Mike Washington) are the classic cause — one player's longshot price scored against another's projections. |
| Repo doesn't show up in Streamlit | Streamlit's GitHub authorization doesn't cover it. Re-authorize and grant access to the repo. |
| Storage says "token lacks Contents: Read and write" | The fine-grained token was created without write permission. Regenerate it with **Contents: Read and write** (Step 6.4). |
| Storage says "GitHub rejected the token (401)" | The token expired or was mistyped. Generate a new one and update `GITHUB_TOKEN` in Secrets. |
| Storage says "could not find ... (404)" | `GITHUB_REPO` is wrong, or the token wasn't granted access to that specific repository. |
| History is empty after a restart | You're on local-disk storage. Do Step 6. |
| Asked to fetch odds again after every restart | Same cause — without Step 6 the saved snapshot is wiped with the disk. |
| "The odds snapshot is N MB, too large to store" | A very large slate. Uncheck a market or two under **Markets** and fetch again. |
| "All games fall outside your date window" | The saved snapshot is from a previous week, or the games have kicked off. Widen the dates or fetch fresh odds. |

---

## Project layout

```
app.py              Streamlit UI
core/
  config.py         Tunables and their defaults
  teams.py          NFL team code normalization across all three sources
  rosters.py        ESPN league ingestion and roster filters
  projections.py    PFF weekly CSV parsing and validation
  odds.py           OddsProvider interface + The Odds API client
  matching.py       Name normalization, aliases, fuzzy matching
  scoring.py        Odds conversion, Poisson, gap and EV math
  selection.py      Hard filters and the three-tier pick logic
  parlay.py         Combined odds, payout, share text
  betslip.py        Best-effort FanDuel deep links
  runlog.py         Run logs and season history
  store.py          Persistence: local disk, or commits to the parlay-data branch
tests/              Test suite; run with `pytest`
data/               Local-disk fallback storage
  aliases.json      Your name-match overrides
  runs/             Saved run logs
```

With GitHub storage configured, those same files live on the `parlay-data`
branch instead, and the local `data/` directory is only a fallback.

The odds vendor sits behind an `OddsProvider` interface, so swapping it doesn't
touch the scoring or selection code.

## Note on the design document

Two corrections were made while building against the spec:

- The acceptance checklist quotes "λ=2.27 receptions over 1.5 → P(X≥2) ≈ 68.5%".
  The correct value is **66.2%**; 68.5% corresponds to λ=2.37. The code implements
  the formula, and the test asserts 66.2%.
- The λ=0.96 anytime-TD check (≈61.7%) is correct and is tested as such.

## The FanDuel betslip link

The whole-parlay link needs a `marketId` and a `selectionId` for every leg. Those
come from the odds feed's `sid` fields when it supplies them, and otherwise are
recovered from each leg's own FanDuel link. **This URL format is unofficial** —
FanDuel can change it without notice — so treat it as a convenience, not a
guarantee: whoever opens it should check the slip matches the table before
staking anything.

If no link can be built, expand **"What the odds feed actually returned"** under
the warning. It reports whether the feed gave you IDs, links, both or neither.
Neither means your The Odds API plan does not include `includeSids` /
`includeLinks` — those are not available on every plan, and no amount of
refetching will change it.

The link is all-or-nothing by design. If any leg is missing its IDs, no link is
produced and the app names the teams involved, because a partial slip that
silently drops legs is worse than no link at all. Missing IDs usually mean a
market was posted late; refetching odds normally fixes it. The per-leg links in
the results table remain as a fallback.

---

## The Prop Finder — a second app in this repo

`props.py` is a separate app that answers a different question: **what are the
best plays on the whole slate**, with no fantasy league involved. It shares
everything below the UI with the parlay bot — the same scoring, the same name
matching, the same price limits, and **the same stored odds snapshot**, so a
slate fetched by either app costs the other nothing.

It does not touch `app.py`, and its settings are stored separately
(`props_config.json`), so tuning one never disturbs the other.

**What's different:**

- No ESPN league. Every player PFF projects and FanDuel prices is scored — on
  one real slate that was 825 props instead of ~320.
- Filters live on the page rather than the sidebar: game, team, position,
  player, market, and whether a play is scored by gap or EV. Their options come
  from whatever slate is loaded, so they always match the data.
- Two listing thresholds instead of tiers, because a 10% gap and a 10% EV are
  not the same claim: **minimum gap** for yardage props, **minimum EV** for
  receptions and touchdowns.
- Results are one ranked table with a Why line per play, and a CSV download.
- **Build a parlay by hand.** Tick rows in the table, press *Add selected to
  slip*, and the slip shows combined odds, payout and a single FanDuel link
  loading every leg — the same maths and the same link builder the parlay bot
  uses. Legs are removed with ✕, and the slip survives a restart.

**To deploy it**, create a *second* Streamlit app from this same repository:

1. **share.streamlit.io → Create app**, same repository and branch.
2. Set **Main file path** to `props.py` (this is the only difference).
3. Give it **the same secrets** — `ODDS_API_KEY`, `GITHUB_TOKEN`, `GITHUB_REPO`.
   The shared `GITHUB_REPO` is what makes both apps read one odds snapshot.

Running it locally is `streamlit run props.py`.

### Placing a slip somewhere other than FanDuel

The slip has a **PrizePicks / pick'em** tab. Odds come from FanDuel either way —
that is the only feed the app reads — but a pick'em app doesn't price individual
selections, so what travels there is the player, the stat and the line.

The tab gives you those as a copyable block, plus two things you need before
entering them:

- **A line-comparison table.** *Room* is how far the projection sits above
  FanDuel's line. Pick'em lines are close but rarely identical, so if the app
  shows a higher line than that room, the play no longer stands.
- **"All legs land together" and "Payout must beat".** A pick'em slip pays a
  fixed multiplier, so what matters is how often every leg lands, and whether
  the multiplier beats `1 ÷ that`.

**There is no pick'em deep link, and there can't be one from this data.** The
FanDuel link works because the odds feed hands us FanDuel's own `marketId` and
`selectionId`. A pick'em slip URL needs *that operator's* projection IDs, which
cannot be derived from a player name, stat and line — and nothing in the feed
carries them. Rather than ship a URL that might load the wrong slip, the tab
gives per-player copy buttons so each name can be pasted straight into the
app's search.

**Read that percentage carefully.** Receptions and touchdown props have a real
Poisson probability. Yardage props do not — yards are not Poisson — so their
figure comes from FanDuel's price, which is *the market's* opinion. Since this
whole app exists to find lines the projections beat, a price-derived figure is
the bar you are trying to clear, not a forecast of how often these land. The tab
says which legs are which.

### Password-protecting it

Streamlit's free tier allows only one private app, so if the parlay bot is
already using that allowance the Prop Finder has to be public. Set a password
instead: add one more line to **that app's** secrets (⋮ → Settings → Secrets):

```toml
APP_PASSWORD = "pick-something-long"
```

The app restarts and asks for it before rendering anything. Leave the line out
and there is no gate at all, so the parlay bot is unaffected.

**What this does and doesn't do.** It stops someone who finds the URL from
using the app — no fetching, no credits spent, no writes to your data branch.
The gate runs before any of that, so a locked-out visitor doesn't even cause a
GitHub read. It is *not* authentication: one shared password, no accounts, and
anyone you give it to has full use of the app. Your keys never reach the
browser either way — everything runs server-side — so the password is
protecting your API budget, not a secret.

**If a leg vanishes from your slip's odds**, the market moved or was withdrawn
between fetches, or your current thresholds now filter it out. The app says so
rather than quietly leaving it out of the bet.

**Overs only, still.** Every play is an OVER or an anytime-TD "Yes", and the
stored snapshot has the Under side pruned out, so unders are not available here
even in principle without a separate fetch.

---

## What this app does not do

It does not scrape FanDuel, call FanDuel's endpoints, automate a login, or place
a bet. It reads odds from a licensed API and hands you links. A person places
the bet.
