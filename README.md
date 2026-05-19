# FINRA Short Interest Tracker

Live dashboard: https://zyyzx.github.io/si-dashboard/si_dashboard.html

Bi-weekly FINRA short interest data for ~13,800 US-listed securities (Jan 2020 → latest settlement), with SI % of Float computed from Capital IQ historical float data, plus a candidates engine that surfaces sector/history outlier shorts.

## Folder layout

This is the single canonical project folder. Everything lives here. Tracked-in-git files are the source code and the built dashboard HTML; large data files are gitignored and rebuilt locally.

```
si-dashboard/
├── si_dashboard.html          # Built artifact (~33 MB, tracked)
├── analytics/                 # Pipeline module + parquet outputs (parquets gitignored)
├── exports/                   # Per-snapshot candidate CSVs (gitignored)
├── snapshots/                 # Historical backups (gitignored)
├── validate-dashboard-skill/  # Cowork skill package for the validator
└── *.py                       # Pipeline scripts
```

### Data files (gitignored, rebuilt locally)
- `si_history_full.csv` — Canonical FINRA history (~275 MB, ~3M rows). Master append target for `fetch_short_interest.py`.
- `equities.csv` — Equity universe metadata
- `analytics/features.parquet`, `analytics/candidates.parquet`, `analytics/float_panel.parquet`
- `capiq_float_historical.xlsx` — CapIQ template with `IQ_FLOAT` formulas
- `exports/{YYYYMMDD}/`, `exports/latest/` — Per-snapshot candidate CSVs

## Multi-machine setup

Clone the repo on the new machine, then bootstrap the data:

```powershell
git clone https://github.com/zyyzx/si-dashboard.git
cd si-dashboard
pip install requests pandas pyarrow openpyxl
$env:PYTHONIOENCODING = "utf-8"   # Windows console needs this for the print arrows

# 1. Bootstrap the FINRA history from scratch (~5 min for 150+ periods)
python fetch_short_interest.py

# 2. Build features + candidates + exports (~5 min)
python update_analytics.py

# 3. Refresh the Candidates tab on the dashboard
python add_candidates_tab.py
```

That's it. `git pull` brings down new dashboard HTML and any pipeline updates; rerun steps 1-3 locally whenever new FINRA periods land. Your data stays local; only the built dashboard and source code sync via GitHub.

## Refresh workflow on every FINRA publish

FINRA publishes settlements roughly every two weeks; new files land ~8 business days after the settlement date.

```powershell
$env:PYTHONIOENCODING = "utf-8"
python fetch_short_interest.py     # Appends new periods to si_history_full.csv
python update_analytics.py         # Rebuilds features, candidates, exports
python add_candidates_tab.py       # Re-patches the Candidates tab
python validate_dashboard.py       # Sanity check (54 checks)
git add si_dashboard.html
git commit -m "Refresh dashboard for FINRA YYYYMMDD"
git push
```

## Files

### Dashboard
- `si_dashboard.html` — Single-file dashboard (CSS, JS, data embedded). Deployed via GitHub Pages.

### Data pipeline
- `fetch_short_interest.py` — Downloads FINRA bi-weekly files, appends to `si_history_full.csv`
- `update_analytics.py` — Orchestrator: `build_features` → `build_candidates` → `export_candidates`
- `build_features.py` — Builds `analytics/features.parquet` from the history CSV
- `build_candidates.py` — Scores + applies gates, writes `analytics/candidates.parquet`
- `export_candidates.py` — Writes per-snapshot CSVs to `exports/{date}/` and mirrors `exports/latest/`
- `add_candidates_tab.py` — Idempotent patch that injects the Candidates tab into `si_dashboard.html`
- `analytics/` — Python module: `loaders.py`, `features.py`, `score.py`
- `integrate_float_data.py` — Integrates CapIQ float data into the dashboard to compute SI % of Float
- `create_capiq_template.py` — Generates the CapIQ Excel template with `IQ_FLOAT` formulas
- `fix_smallcap_inclusion.py` — One-time fix to ensure SmallCap tickers are included
- `validate_dashboard.py` — Post-edit health check (54 validations including JS brace balance to catch Edit-tool truncation near EOF)

> Note: `build_dashboard.py` only generates a minimal 3-tab stub (Tracker / Screener / Help). It is **not** the canonical regenerator for the live 6-tab dashboard (Guide / Trend / Themes / SI Movers / Sector Sentiment / Screener) and will overwrite the rich version if run. Treat it as a prototype until a proper rich-dashboard regenerator is written.

### Reference
- `quarterly_dates.json` — 26 quarterly dates used for CapIQ float snapshots
- `all_tickers_for_float.txt` — Full ticker list for float data
- `tickers_needing_float.txt` — Tickers still missing float coverage
- `short interest CapIQ.txt` — CapIQ field reference notes
- `SI_TRACKER_PROJECT_NOTES.md` — Detailed project documentation (canonical engineering notes)
- `ISSUES.md` — Known issues and fixes
