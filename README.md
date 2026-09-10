# parlay-data

Saved state for the League Parlay Bot, written by the app.

- `runs/` — one JSON per saved run: config, picks, and Win/Loss/Push grades.
- `odds/latest.json` — the last odds snapshot, so a restart costs no API credits.
- `rosters/latest.json` — the last ESPN roster pull.
- `projections/latest.json` — the last PFF file, so it need not be re-uploaded.
- `aliases.json` — manual name-match overrides.
- `config.json` — saved slider settings.

This branch deliberately holds no application code, so writing to it never
triggers a redeploy of the app. Editing these files by hand is fine.
