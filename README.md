# FINRA Short Interest Tracker

Live dashboard: https://zyyzx.github.io/si-dashboard/si_dashboard.html

Bi-weekly FINRA short interest data for 13,819 US-listed securities (Jan 2020 - Apr 2026), with SI % of Float computed from Capital IQ historical float data.

## Files

### Dashboard
- `si_dashboard.html` — Single-file dashboard (all CSS, JS, and data embedded). Deployed via GitHub Pages.

### Data Pipeline
- `fetch_short_interest.py` — Fetches FINRA short interest settlement data, appends to `si_history_full.csv`
- `build_dashboard.py` — Reads `si_history_full.csv`, builds sparse time-series, generates the HTML dashboard
- `integrate_float_data.py` — Integrates CapIQ float data into the dashboard to compute SI % of Float
- `create_capiq_template.py` — Generates the CapIQ Excel template with `IQ_FLOAT` formulas
- `validate_dashboard.py` — QA validation of the dashboard data
- `regenerate_dashboard.py` — Full rebuild pipeline
- `fix_smallcap_inclusion.py` — Ensures small-cap tickers are included

### Reference
- `quarterly_dates.json` — 26 quarterly dates used for CapIQ float snapshots
- `all_tickers_for_float.txt` — Full ticker list for float data
- `tickers_needing_float.txt` — Tickers still missing float coverage
- `short interest CapIQ.txt` — CapIQ field reference notes
- `SI_TRACKER_PROJECT_NOTES.md` — Detailed project documentation
- `ISSUES.md` — Known issues and fixes

### Data files (not in repo, kept locally in `../SI Tracker/`)
- `si_history_full.csv` — Canonical FINRA short-interest history (~275 MB, ~2.97M rows, 2020-01-15 → latest settlement). Master append target for `fetch_short_interest.py`.
- `equities.csv` — Equity universe metadata (tickers, names, sectors, market cap)
- `capiq_float_historical.xlsx` — CapIQ template with IQ_FLOAT formulas (13,819 tickers x 26 dates)
- `snapshots/` — Archived stale CSVs and historical script versions; not consumed by the live pipeline

## Rebuild workflow

```bash
# 1. Fetch latest FINRA data
python fetch_short_interest.py

# 2. Build dashboard from CSV
python build_dashboard.py

# 3. Integrate float data (after filling CapIQ template)
python integrate_float_data.py --capiq capiq_float_historical.xlsx

# 4. Deploy
git add si_dashboard.html
git commit -m "Update with latest data"
git push origin main
```
