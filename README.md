# League Parlay Bot

Builds a weekly 10-leg NFL parlay for a fantasy league. Each fantasy team
contributes exactly one leg — the best-value FanDuel player prop belonging to a
player on that team's roster. The app picks the legs and shows its reasoning;
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

From then on, **💾 Save this run** in the History tab commits that week to the
`parlay-data` branch, and your Win/Loss grades and season totals survive
restarts. You can browse the files on GitHub, and even fix a grade by editing
the JSON there directly.

If you skip this step nothing breaks — the app just falls back to local disk and
tells you so, and the Download buttons still let you keep records by hand.

### Things to know about free hosting

| Behavior | What it means for you |
|---|---|
| The app sleeps after ~12 hours idle | The next visitor wakes it; takes ~30 seconds. Normal. |
| The disk is wiped on every restart | By default, saved runs, aliases and settings do **not** survive. Step 6 fixes this permanently. |
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
4. **Read the table.** One row per fantasy team, with the prop, the FanDuel
   price, the score, and the tier.
5. **Move the sliders** to taste. Re-scoring is instant and **never** re-calls
   the API — only the Fetch button does that.
6. **Check the "Review & overrides" tab** if any leg is flagged 🚩, and swap in
   an alternative for any team you disagree with.
7. **Copy the share block** into your league chat, and place the bet at FanDuel.
8. **Hit "💾 Save this run"** in the History tab. Once the games finish, come back
   and mark each leg Win/Loss/Push — the season totals build up from there.

Free tier is 500 credits/month, so about 4–5 full fetches. If you're running
short, trim the **Markets to fetch** list under **Misc** in the sidebar.

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

### Why the volume floors exist — don't zero them out

PFF projections are per-game **means** of right-skewed distributions, while
sportsbook lines sit near **medians**. Comparing a mean to a line therefore
flatters overs on low-volume players: a receiver projected for 18 yards has a
mean well above his median outcome. The volume floors restrict gap scoring to
players where mean ≈ median. Setting them to zero will produce confident-looking
garbage.

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
| Odds floor | −110 | Worst price accepted, any prop type |
| Rec/rush yards floor | 25 yds | Minimum projection to gap-score a yardage prop |
| Passing yards floor | 175 yds | Same, for QBs |
| Rush attempts floor | 8 att | Same, for carries |
| Receptions floor | 1.0 | Minimum λ for a receptions prop |
| EV props enabled | ON | Off = yardage props only |
| Starters only | OFF | On = ignore bench and IR |
| Exclude Questionable | OFF | Off = still eligible, flagged ⚠️ |
| Stake | $10 | Display only — the app never places a bet |
| Season year | 2026 | ESPN season |
| Fuzzy match threshold | 90 | Below this, a name is reported unmatched rather than guessed |

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
| A player's props are ignored | Check the **Diagnostics** tab for unmatched names and click "Alias →" to fix it. |
| Repo doesn't show up in Streamlit | Streamlit's GitHub authorization doesn't cover it. Re-authorize and grant access to the repo. |
| Storage says "token lacks Contents: Read and write" | The fine-grained token was created without write permission. Regenerate it with **Contents: Read and write** (Step 6.4). |
| Storage says "GitHub rejected the token (401)" | The token expired or was mistyped. Generate a new one and update `GITHUB_TOKEN` in Secrets. |
| Storage says "could not find ... (404)" | `GITHUB_REPO` is wrong, or the token wasn't granted access to that specific repository. |
| History is empty after a restart | You're on local-disk storage. Do Step 6. |

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

## What this app does not do

It does not scrape FanDuel, call FanDuel's endpoints, automate a login, or place
a bet. It reads odds from a licensed API and hands you links. You place the bet.
