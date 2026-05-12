# SI Tracker — Known Issues / Backlog

## Data Accuracy

### [FIXED 2026-05-01] SI% wrong for multi-class share companies (EDGAR under-reports shares)
**Affected:** MIR, BRZE, DUOL (and likely others)  
**Root cause:** `CommonStockSharesOutstanding` in SEC EDGAR XBRL only captures one share class (e.g. Class A), not the full float.
**Fix applied:** Replaced EDGAR shares with FinViz public float for 718 tickers. Results:
- MIR: 118% → **9.42%** (CX: 10.5%) ✓
- BRZE: 63% → **15.98%** (CX: 16.5%) ✓
- DUOL: 47% → **20.21%** (CX: 19.8%) ✓

### [PARTIAL 2026-05-01] SI% understated for companies with high insider ownership (total shares > float)
**Affected:** ASAN, NTST, BROS, TRIP, SYM (and others)  
**Root cause:** We use total shares outstanding; ChartExchange uses public float. When insiders hold large locked blocks, float is significantly smaller → our denominator is too large.
**Fix applied:** FinViz float used for BROS, TRIP, SYM → now close to CX. ASAN/NTST still off:
- BROS: **15.90%** (CX: 16.1%) ✓
- ASAN: **35.06%** (CX: 24.3%) — FinViz float=90.56M vs CX-implied 130.9M (float source disagreement)
- NTST: **29.76%** (CX: 34.4%) — similar float-definition mismatch
**Remaining fix:** Investigate why FinViz float for ASAN/NTST differs from ChartExchange's implied float

### [OBSOLETE — pipeline replaced 2026-05-06] `shares` field shows 0 in dashboard for some tickers
**Original root cause:** `trend_data.json` stored `shares: None`; `gen_dashboard.py` converted None → 0.
**Status:** Both `trend_data.json` and `gen_dashboard.py` no longer exist. The pipeline now uses `build_dashboard.py` with inline-embedded JSON; if `shares` is still 0 in the current dashboard for any ticker, file a new issue with the specific ticker.

### [RESOLVED 2026-05-11] si_history.csv canonical-source ambiguity
**Original root cause:** PermissionError on workspace `si_history.csv` rebuild left a stale 39-period file alongside the dashboard's newer data.
**Resolution:** Canonical filename was switched from `si_history.csv` → `si_history_full.csv`. All active scripts (`fetch_short_interest.py`, `build_dashboard.py`, `regenerate_dashboard.py`, `fix_smallcap_inclusion.py`) read/write `si_history_full.csv`. The stale `si_history.csv` (last data 2022-08-15) and `si_history_clean.csv` (last data 2022-03-15) were moved to `snapshots/stale_si_history_through_20220815.csv` and `snapshots/stale_si_history_clean_through_20220315.csv` respectively. `create_simple_dashboard.py` was updated to reference `si_history_full.csv`.

---

## Dashboard Features

### [OPEN] No public float data — only shares outstanding shown
**Note:** Float data applied to pct% calculations for 718 tickers (2026-05-01). Float not yet surfaced as a display column.
**Fix:** Rebuild cf_shares.csv using float instead of total shares; expose float as visible column in ticker detail view

### [DONE] Screener tab added ✓
### [DONE] Market cap filter (min) added ✓
### [DONE] SI% filter (min/max) added ✓ — *added 2026-04-30*
### [DONE] Max market cap filter added ✓ — *added 2026-04-30*
