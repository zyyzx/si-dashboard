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
### [DONE] Actionable Shorts tab (SI × borrow) ✓ — *added 2026-08-17, PR #7*
### [DONE] Price overlay on Trend chart ✓ — *added 2026-08-17, PR #7*

---

## Price Overlay Coverage

### [OPEN] Top up the 35 US-style tickers Stooq does not carry
**Context:** The Stooq bulk archive covers 5,944 of 13,819 dashboard tickers (43%).
That is the structural ceiling for a listed-only source, not a fetch failure — of the
3,518 uncovered tickers still reporting SI, 2,918 are foreign ordinaries traded OTC
(`…F`) and a further 164 are ADRs (`…Y`); another 4,357 uncovered names stopped
reporting SI entirely and are delisted or acquired, so no current price source can
recover them.

**What is actually worth fixing:** 35 US-style symbols (≤4 chars) with latest SI ≥ 1M
and no price. Several are well-known short battlegrounds. Yahoo covers OTC symbols
Stooq does not, so this is a ~15-second run:

```powershell
python fetch_prices.py --tickers NWBO,NLST,CYDY,HYSR,ODV,FNMA,GTVH,FMCC,FRCB,TOI,RWAX,STEK,ZNOG,TSPH,QMMM,GGSM,ADTX,SRNE,INHD,ELTP,VXRT,AXXA,MAPS,INKW,DBMM,HGGG,ALBT,MDCE,CBDW,BYOC,IVPR,AIBT,SICP,TIOG,CBDL
python add_price_overlay.py
python validate_dashboard.py
```

**Alternative — close the whole gap:** plain `python fetch_prices.py` resumes and
Yahoo-fetches all 7,875 missing names (~45-60 min). Yahoo carries many `…F`
ordinaries, so this recovers a large share of the foreign block too.

**Re-derive this list at any time:** `python price_coverage_report.py --from-dashboard
--out missing_prices.csv`. The ticker list above is a snapshot as of the 20260715
settlement and will drift as names list, delist, and change SI.

### [DONE 2026-08-17] Borrow cost is confined to the Actionable tab
**Context:** Fee / availability data reaches the dashboard only through the Actionable
Shorts tab, which is filtered to candidates passing the Signal D gates. The Trend and
Screener tabs — where most browsing happens — show SI and price but no borrow cost, so
a name can look attractive there while being uneconomic to short.
**Fixed by:** `add_borrow_columns.py` — embeds a per-ticker borrow snapshot and adds
sortable **Borrow Fee** and **Available** columns to the Screener. Fee is colour-coded
(green < 10%, amber < 25%, red above) with a ⚠ mark in the top decile of its own year.
Per the coverage asymmetry, percentile and 20-day change ride in the cell tooltip
rather than in columns that would read mostly empty; an unmeasured borrow renders as a
muted em dash with an explanatory tooltip, never blank and never a number, because on a
screener an empty cost column reads as "free". Unmeasured names sort to the bottom in
both directions.
