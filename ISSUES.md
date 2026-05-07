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

### [OPEN] `shares` field shows 0 in dashboard for some tickers
**Root cause:** `trend_data.json` stores `shares: None` (field not written by build_trend6.py). `gen_dashboard.py` converts None → 0.  
**Note:** Float data was embedded directly into pct arrays in the 2026-05-01 rebuild for 718 tickers; shares display field not yet updated.
**Fix:** Store latest float value in trend_data.json output per ticker

### [OPEN] si_history.csv in workspace still has old 39-period version
**Root cause:** PermissionError when writing to workspace path during rebuild. New 151-period data is in `/tmp/si_history_new.csv` but wasn't copied.  
**Fix:** Re-copy `/tmp/si_history_new.csv` → `SI Tracker/si_history.csv`

---

## Dashboard Features

### [OPEN] No public float data — only shares outstanding shown
**Note:** Float data applied to pct% calculations for 718 tickers (2026-05-01). Float not yet surfaced as a display column.
**Fix:** Rebuild cf_shares.csv using float instead of total shares; expose float as visible column in ticker detail view

### [DONE] Screener tab added ✓
### [DONE] Market cap filter (min) added ✓
### [DONE] SI% filter (min/max) added ✓ — *added 2026-04-30*
### [DONE] Max market cap filter added ✓ — *added 2026-04-30*
