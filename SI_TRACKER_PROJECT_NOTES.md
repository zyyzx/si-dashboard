# SI Tracker — Full Project Notes for Integration

> **Purpose of this document:** Complete context for a Claude agent in another project to understand the SI Tracker dashboard — what was built, how it works, every significant design decision, all errors encountered and how they were fixed, and the state of the codebase at handoff. This document should be read alongside the actual source files.

---

## 1. What This Project Is

A **FINRA short interest dashboard** (`si_dashboard.html`) that tracks U.S. equity short interest from 2020 through the present across ~13,800 actively traded tickers. It is a **single self-contained HTML file** (~25.4 MB) with all data embedded inside it. No server, no database, no external API calls at runtime. The user opens the file locally in a browser.

### Why a single-file approach?

The original brief was a portable tool the user could open anywhere without infrastructure. All data processing happens offline in Python; the HTML file is the final frozen output. This means:

- Editing it is dangerous: the Edit tool can silently truncate the file when replacing large blocks near EOF (see Section 7 for details and mitigations).
- The file must be regenerated from scripts when data changes, not hand-edited.
- All JS functions and chart rendering logic live at the very end of the file (95–100% position), making them the highest-risk zone for Edit-tool truncation.

---

## 2. Data Source & Pipeline

### Source

**FINRA bi-weekly short interest files**, publicly available at:
```
https://cdn.finra.org/equity/otcmarket/biweekly/shrt{YYYYMMDD}.csv
```

Each file is pipe-delimited and contains one row per ticker per settlement period with:
- `symbolCode` — ticker symbol
- `issueName` — company name
- `marketClassCode` — market class (NMS, OTC, SmallCap, etc.)
- `issuerServicesGroupExchangeCode` — exchange
- `currentShortPositionQuantity` — absolute short interest (shares)
- `changePercent` — % change vs prior period
- `settlementDate` — the date string (YYYYMMDD)

### Settlement date cadence

FINRA publishes approximately every two weeks. The project has hardcoded all known settlement dates from 2020 through 2026 in `fetch_short_interest.py`. Future dates are probed gracefully (HTTP 404 = not yet published, silently skipped).

### History file

`si_history_full.csv` is the canonical master data store (renamed from `si_history.csv` on 2026-05-11; see ISSUES.md). It is built by appending each downloaded FINRA file. As of 2026-05-11:
- ~2.97M rows
- ~26,678 unique tickers
- 151 settlement dates from 2020-01-15 through 2026-04-15

The legacy `si_history.csv` (last data 2022-08-15) and `si_history_clean.csv` (last data 2022-03-15) live in `snapshots/` and are not consumed by any active script.

### Key pipeline scripts

| Script | Purpose |
|---|---|
| `fetch_short_interest.py` | Downloads new FINRA files, appends to `si_history_full.csv` |
| `build_dashboard.py` | Reads `si_history_full.csv`, computes time series + float data, generates `si_dashboard.html` |
| `regenerate_dashboard.py` | Earlier regeneration helper (partially superseded by build_dashboard.py) |
| `fix_smallcap_inclusion.py` | One-time fix to ensure SmallCap/OTC tickers weren't excluded |
| `validate_dashboard.py` | Post-edit health checker (see Section 7) |

### Scheduled task

A scheduled task runs `fetch_short_interest.py` on FINRA's bi-weekly publish schedule to keep `si_history_full.csv` current. The dashboard must be manually regenerated after each fetch.

---

## 3. Dashboard Structure

### File facts (as of v7, 2026-05-04)

| Property | Value |
|---|---|
| File size | 25.41 MB |
| RAW data block | 24.11 MB (dominant cost) |
| Script blocks | 4 |
| Settlement dates embedded | 151 (2020-01-15 → 2026-04-15) |
| Unique tickers in RAW | ~13,800 |
| INSIGHTS_DATA themes | 40 |

### Five tabs

1. **Trend** (id: `trend`) — The main screener. Shows all tickers sorted by short interest trend score. Columns include ticker, name, market class, exchange, latest SI, SI% of float, 2W/6W/6M z-scores, and trend direction. Clicking a ticker opens a chart showing raw SI over time with a selectable time window.

2. **Screener** (id: `screener`) — Filtered view with user-configurable constraints: min/max market cap, min/max SI%, direction (rising/covering/both), sort key, time window. Exposes the same per-ticker chart view as Trend.

3. **Sector Sentiment** (id: `sector`) — Aggregated SI view by sector (Biotech, Fintech, Tech, Energy, etc.) using GICS-aligned groupings defined in `SECTOR_DATA`. Each sector shows a heat chart with selectable Y-axis (raw SI, SI% of float, Z-score) and a period selector. Constituent tickers are shown below the chart.

4. **Rising Shorts** (id: `rising`) — Table of tickers with the largest recent SI increases, filtered by Z-score thresholds. Sortable by 2W change, 6W change, 6M change, or Z-score.

5. **Themes** (id: `themes`) — The most analytically rich tab. Shows 40 thematic baskets (e.g., AI/Cloud, BDC, Fintech/BNPL, Critical Metals, Crypto, Clean Energy). Each theme has: aggregate SI heat score, a chart of constituent ticker SI over time, a detail panel showing individual ticker lines, declining/covering signal detection, and an alert list.

### Data blocks embedded in the HTML

The HTML file contains three large embedded JS data structures:

1. **`const RAW = {...}`** — The raw time series. 24.11 MB. Structure:
   ```js
   RAW = {
     dates: ["20200115", "20200131", ...],  // 151 settlement dates
     tickers: {
       "AAPL": {
         name: "Apple Inc.",
         si: [null, null, 12345678, ...],  // sparse SI array (index = date index)
         pct: [[idx, value], ...],          // sparse SI%float array (only non-null)
         z6m: 1.23,                          // current 6-month z-score
         // etc.
       },
       ...
     }
   }
   ```

2. **`var SECTOR_DATA = {...}`** — Sector groupings with constituent tickers and metadata. Hand-curated.

3. **`var INSIGHTS_DATA = {...}`** — Theme definitions. 40 themes as of v7. Structure per theme:
   ```js
   {
     id: "ai_cloud",
     label: "AI / Cloud Infrastructure",
     heat: 0.19,          // aggregate z-score heat signal
     declining: false,    // true if heat <= -0.4 (covering/long signal)
     constInfo: [         // constituent tickers
       { tk: "SNOW", label: "Snowflake", z6m: 0.5, latest_si: 12345678 },
       ...
     ],
     constSeries: [],     // legacy field, now always empty — see Section 6
     alerts: [...]        // pre-computed alert strings
   }
   ```

---

## 4. Features Added (Chronological)

### Phase 1: Foundation (initial build)

- Downloaded all available FINRA history (2020–2026), 151 settlement dates
- Built `si_history.csv` as the master data store
- Created `si_dashboard.html` v1: single Trend tab with raw SI chart per ticker
- Added SmallCap/OTC inclusion (FINRA separates these into the same files but they were initially filtered out by a marketClass check)

### Phase 2: Shares outstanding / Float

- Fetched shares outstanding from Yahoo Finance for all ~13,800 actively tracked tickers
- Fetched `floatShares` (public float) from Yahoo Finance in batches across all 13,800 tickers
- Added **SI% of Float** as a display toggle in the Trend and Screener tabs
- Discovered that Yahoo Finance `commonStockSharesOutstanding` understates float for multi-class share companies (e.g. DUOL, BRZE, MIR report only Class A)
- Supplemented with **FinViz public float** for 718 tickers where SI% > 50% (likely bad denominator)
- After FinViz fix: MIR 118% → 9.42%, BRZE 63% → 15.98%, DUOL 47% → 20.21%

### Phase 3: Market cap filter + Screener tab

- Added market cap data (market cap = shares × price, fetched from Yahoo Finance)
- Added **Screener tab** with min/max market cap, min/max SI%, direction, and sort controls
- Fixed: tickers with 0 market cap were showing up in screener results (should be filtered)

### Phase 4: Sector Sentiment tab

- Curated `SECTOR_DATA`: hand-mapped ~300 tickers into 12 sector buckets
- Built Sector tab: heat chart per sector, Y-axis selector (raw/pct/z-score), constituent list
- Added **Y-axis dropdown** to the Themes tab as well (same logic, reused)

### Phase 5: Rising Shorts + Themes tabs

- Built **Rising Shorts tab**: Z-score ranked table of tickers with the largest recent short interest increases
- Built **Themes tab** with 37 initial themes (INSIGHTS_DATA v4)
- v5: Added spike filter (removes single-period outliers that distort Z-scores), added Ad Tech theme
- v6: 37 → 38 themes (added BDC — Business Development Companies)
- v7: 38 → 40 themes (added 3 new themes), added declining/covering signal UI

### Phase 6: SI% of Float as chart Y-axis in Themes

- Added `getPctSeries(tk)` function to extract sparse SI%float array from RAW data
- The Themes chart can now show SI% of Float for constituent tickers (toggle in Y-axis dropdown)
- `rebase100()` normalization function added to fix extreme index values when tickers start at very different SI levels (e.g. QUBT had 2M+ index value with all-time base-100)

### Phase 7: Validation tooling + snapshots

- Created `validate_dashboard.py` — 11-check health checker (see Section 7)
- Created `validate-dashboard-skill/` — Cowork skill package for reuse across projects
- Created `snapshots/v7_20260504/` — rollback snapshot of this exact version

---

## 5. Key Technical Decisions and Nuances

### The `RAW` data structure and sparse arrays

All SI time series are **sparse**: only periods where a ticker had a non-zero short position are stored. The full dates array has 151 entries; a ticker that only appeared in 2024–2026 will have nulls for index 0–80. The JS functions must handle null gracefully everywhere (charts skip nulls, Z-score computations ignore them).

The `pct` field (SI% of float) is stored differently from the `si` field:
- `si` is a dense array of length N (= number of dates), with null for missing
- `pct` is a sparse array of `[index, value]` pairs — only non-null entries stored

This asymmetry exists because SI% was added later and embedded space-efficiently. The `getPctSeries(tk)` function converts `pct` back to a dense array for charting.

### `constInfo` vs `constSeries` in INSIGHTS_DATA

When themes were first built (v4), constituent ticker SI series were stored in `constSeries` (a full array of values). By v7, the build process stores `constSeries: []` (empty) and instead puts metadata in `constInfo` (ticker id, label, z6m, latest_si). The JS chart code was updated to read from `constInfo` and then look up the actual time series from `RAW.tickers[tk].si` at runtime. There is a fallback in the JS:

```js
var constData = (t.constSeries && t.constSeries.length > 0) 
  ? t.constSeries 
  : (t.constInfo || []);
```

This fallback exists because older theme entries in the JSON may still have `constSeries` populated; both code paths must work. New code should always use `constInfo`.

### `rebase100()` — Why it exists

When charting multiple constituent tickers on the same Themes chart, their raw SI values differ by orders of magnitude (e.g., SNOW at 50M shares short vs QUBT at 800K shares short). Without normalization, the chart is unreadable.

The initial approach used all-time base-100 (divide by the first non-zero value across all dates). This caused problems for tickers with long history: QUBT had essentially zero SI from 2020–2023, then spiked in 2024, so its base-100 value starting from 2020 rendered as 2,000,000 by the spike. Fix: `rebase100()` now normalizes to base-100 at the **start of the currently selected time window**, not the all-time first value.

```js
function rebase100(arr) {
  var base = null;
  for (var j = 0; j < arr.length; j++) {
    if (arr[j] !== null && arr[j] > 0) { base = arr[j]; break; }
  }
  if (!base) return arr;
  return arr.map(function(v) { return v !== null ? Math.round(v/base*1000)/10 : null; });
}
```

Called after slicing the series to the selected window, not on the full series.

### The `declining` flag and covering UI

Themes with `heat <= -0.4` are considered to have a "declining short interest" signal (i.e., shorts are covering — potentially a long signal if the thesis has played out). These get:
- An orange/amber card border in the Themes list
- A "🟢 Covering (long signal):" alert in the alerts section
- `declining: true` in the JSON

The -0.4 threshold is consistent across:
- Card border CSS threshold check
- Badge coloring logic
- Alert generation (`return (t.heat || 0) <= -0.4`)

All three must use -0.4; inconsistency here would cause visual mismatches. The `validate_dashboard.py` script checks all three.

### Z-score field name: `z6m` not `zscore`

An early bug: `renderThemeDetail` was reading `c.zscore` but the field is named `c.z6m` in `constInfo`. This caused the detail panel to show "–" for all Z-scores. Fixed by correcting the field name. When reading constituent data from `constInfo`, always use `c.z6m` (6-month Z-score) for constituent-level signals.

### BDC theme: shorts are long positions

The BDC (Business Development Company) theme tracks **short-selling activity against BDCs**. BDC shorts are counterintuitive: high SI against BDCs may indicate short sellers are wrong (BDCs are yield vehicles that often outperform bearish expectations). The theme label and descriptions in `constInfo` reflect this nuance, but the heat score is computed the same way as all other themes.

The user specifically noted that BDC shorts include only the companies in the basket that have meaningful short interest; "longs" (i.e., positions via long BDC ETFs like FSK, Blue Owl) are not included in the theme basket. The basket tracks the SI against the underlying BDC equities only.

### BNPL theme naming note

The theme is stored as `fintech_bnpl` but the display label includes both BNPL companies (AFRM, SEZL) and broader fintech (LMND, etc.). The theme was originally "Fintech/BNPL" and the basket expanded to include adjacent fintech names. The id `fintech_bnpl` should not be changed as it is referenced in several places.

### Sector data is hand-curated

`SECTOR_DATA` is not auto-generated from any external source. It was manually assembled by reviewing tickers in the Rising Shorts tab and grouping by known sector membership. Adding new sectors requires editing the build scripts or directly editing the embedded JSON in the HTML (using Python replacement, not the Edit tool — see Section 7).

### BNKG ticker was excluded

The user noted that BNKG showed an implausible SI% (suspected bad source data — shares floating changed dramatically period-over-period while shares outstanding did not). BNKG is excluded from thematic baskets.

### Internet niche themes: IAS, TTD, Shutterstock

The user discussed adding internet niches (IAS — Integral Ad Science, TTD — The Trade Desk, SSTK — Shutterstock) to the Ad Tech theme. These were considered but not all finalized; check the current `constInfo` for `ad_tech` to see what's actually in the basket.

---

## 6. Errors Encountered and Fixed

### E1: Edit-tool truncation (occurred twice)

**What happened:** The Edit tool was used to replace a large block of JS code near the end of `si_dashboard.html`. On two separate occasions, it silently dropped everything after the end of the matched block. The file appeared to be saved successfully but was 500–1,000 lines shorter, causing the dashboard to render as a blank page.

**Root cause:** The Edit tool pattern-matches a region and replaces it, but when the replaced block is very large and near EOF, the tool drops the file tail. No error is thrown. The file ends with truncated JS (unclosed braces) and no `</html>` tag.

**Detection:** After the first truncation we had no automated check. We noticed it when the browser showed a blank page. After the second truncation, we built `validate_dashboard.py`.

**Fix applied each time:** Python script to:
1. Read the truncated file
2. Reconstruct the missing tail from memory/earlier known-good version
3. Append the tail using `open(path, 'a')` — append mode, NOT the Edit tool

**Prevention going forward:**
- Never use the Edit tool for any replacement in the last 15% of the file
- Always use Python writes for large replacements
- Run `validate_dashboard.py` after every edit (see Section 7)
- The `NEAR_EOF_RISK_SYMBOLS` check in the validator warns when critical functions are in the danger zone

### E2: `validate_dashboard.py` itself was truncated by the Write tool

**What happened:** When writing `validate_dashboard.py` (a 364-line file), the Write tool also truncated it. The file ended mid-function.

**Fix:** Rewrote the entire file using a `bash` heredoc (`cat > file << 'EOF' ... EOF`) which bypasses the Write tool entirely and is safe for large files.

**Lesson:** For files over ~200 lines, prefer `bash` heredoc writes over the Write tool.

### E3: Em-dash (`—`) in Python f-strings caused syntax error

**What happened:** Python script contained Unicode em-dashes (—) inside f-string expressions (copy-pasted from prose). Python's tokenizer treats the em-dash as an unterminated string literal.

**Symptom:** `SyntaxError: unterminated string literal` on a line containing `—`

**Fix:** Replace all em-dashes with plain ASCII hyphens (`-`). Easy one-liner:
```python
src = open(path).read().replace('—', '-')
```

### E4: `const RAW = {` marker had spaces — DATA_MARKERS config mismatch

**What happened:** In `validate_dashboard.py`, the DATA_MARKERS config specified `"const RAW="` (no space before `{`) but the actual HTML contained `const RAW={` (no space). The validation check was failing with "Missing data block: const RAW" even though the block was present.

**Fix:** Changed the marker to `"const RAW="` (no space) to match the actual generated HTML. **Note:** Always verify exact whitespace when setting DATA_MARKERS — the Python build scripts don't add a space between `=` and `{`.

### E5: `c.zscore` vs `c.z6m` — wrong field name in renderThemeDetail

**What happened:** The `renderThemeDetail` JS function was reading `c.zscore` to display Z-scores in the constituent table. The actual field name in `constInfo` objects is `c.z6m`. All Z-score cells showed "–".

**Fix:** Changed `c.zscore` → `c.z6m` in `renderThemeDetail`. Added `REQUIRED_JS` check for this sentinel in the validator.

### E6: All-time base-100 made QUBT chart unreadable (index value 2,000,000+)

**What happened:** For tickers with very low historical SI that then spiked, the all-time base-100 index was enormous. QUBT (quantum computing) had essentially zero SI from 2020–2023, so when it spiked to 10x its historical baseline in 2024, the chart rendered with an index value of ~2,000,000 — making the scale useless.

**Fix:** `rebase100()` now applies base-100 normalization to the **window-sliced** series, not the full all-time series. The first non-null value in the selected window becomes 100. See full function in Section 5.

### E7: `constSeries` empty in v7 themes — chart showed no constituent lines

**What happened:** The build script that generated INSIGHTS_DATA v7 set `constSeries: []` for all themes (it was moved to `constInfo`). The chart rendering code was reading from `constSeries` and found nothing to plot.

**Fix:** Added fallback logic in the JS chart renderer:
```js
var constData = (t.constSeries && t.constSeries.length > 0)
  ? t.constSeries
  : (t.constInfo || []);
```

Then look up each ticker's actual series from `RAW.tickers[tk].si`. This is now the canonical code path; `constSeries` is legacy.

### E8: ASAN/NTST float disagreement between FinViz and ChartExchange

**What happened:** Even after applying FinViz public float for ASAN and NTST, the resulting SI% did not match ChartExchange's figures. ASAN: our 35.06% vs CX 24.3%. NTST: our 29.76% vs CX 34.4%.

**Root cause:** Different float data providers define "public float" differently — some include employee/restricted shares, some exclude Rule 144 shares, etc. There is no single authoritative float number.

**Status:** Partially fixed. ASAN and NTST remain as known discrepancies. The float-source disagreement is documented in `ISSUES.md` but not resolved.

---

## 7. The Validate Dashboard Script

`validate_dashboard.py` is a post-edit health checker for the HTML file. Run it after every significant edit:

```bash
python validate_dashboard.py                         # checks si_dashboard.html in same folder
python validate_dashboard.py path/to/dashboard.html  # explicit path
```

Exit code 0 = all clear. Exit code 1 = errors found.

### The 11 checks

1. **File structure** — DOCTYPE present, file ends with `</html>`, single `<body>` pair
2. **Script tag balance** — equal `<script>` and `</script>` counts
3. **JS brace balance** — counts `{` vs `}` per script block, skipping strings and comments; catches mid-function truncation
4. **Required JS symbols** — every named function and variable expected to be present
5. **Function body sentinels** — unique strings near the END of key function bodies; verifies functions weren't cut short
6. **Near-EOF risk audit** — warns if critical symbols (like `window.renderThemes`) are in the last 10% of the file
7. **Required DOM element IDs** — all IDs referenced by JS exist in the HTML
8. **INSIGHTS_DATA JSON** — fully parseable, correct theme count, required theme IDs present
9. **Embedded data blocks** — RAW, SECTOR_DATA, INSIGHTS_DATA all present
10. **UI threshold consistency** — the three places the -0.4 threshold appears all agree
11. **File size bounds** — flags accidental deletions (< 24 MB) or double-embeds (> 35 MB)

### Key innovation: JS brace balance checker

The `js_brace_balance()` function is the core detection mechanism for Edit-tool truncation. It counts `{` vs `}` in a JS block while correctly skipping string literals (single, double, and template), single-line comments (`//`), and block comments (`/* */`). Returns net unclosed brace count: 0 = balanced, > 0 = truncated, < 0 = extra closes.

All JS functions in si_dashboard.html live at 95–100% of file position — well within the danger zone. The brace balance check catches truncation immediately rather than letting a broken file reach the browser.

### Reusable skill

The validator is packaged as a Cowork skill at `validate-dashboard-skill/`. It includes:
- `SKILL.md` — skill definition for Claude
- `scripts/validate_dashboard.py` — the validator
- `install_skill.ps1` — one-click Windows installer

---

## 8. Current File Inventory

| File | Purpose | Size |
|---|---|---|
| `si_dashboard.html` | Main dashboard (v7) | ~32 MB |
| `si_history_full.csv` | **Canonical** short interest history (FINRA append target) | ~275 MB (~2.97M rows) |
| `equities.csv` | Equity universe metadata | ~89 MB |
| `fetch_short_interest.py` | FINRA data pipeline | ~5 KB |
| `build_dashboard.py` | Dashboard generator | ~25 KB |
| `regenerate_dashboard.py` | Earlier generator (partial legacy) | ~10 KB |
| `fix_smallcap_inclusion.py` | One-time fix script | ~7 KB |
| `create_simple_dashboard.py` | Earlier prototype (not used) | ~12 KB |
| `validate_dashboard.py` | Post-edit health checker | ~12 KB |
| `ISSUES.md` | Known issues and backlog | — |
| `snapshots/v7_20260504/` | Rollback snapshot of v7 dashboard build | — |
| `snapshots/stale_si_history_through_20220815.csv` | Archived legacy si_history (last data 2022-08-15) | ~110 MB |
| `snapshots/stale_si_history_clean_through_20220315.csv` | Archived legacy cleaned history | ~51 MB |
| `validate-dashboard-skill/` | Cowork skill package | — |

---

## 9. Integration Notes for the VIC Project

The SI dashboard is being integrated into the VIC buy-side alpha tracker as an additional tab or panel. Key considerations:

### What to extract from si_dashboard.html

The VIC project should not embed the entire 25.41 MB `si_dashboard.html`. Instead, the integration should reference or extract:

1. **`INSIGHTS_DATA`** — The themes JSON (~100 KB). Extract this for theme-level SI signals that can be shown alongside VIC pitch data (e.g., "This ticker is in the Fintech/BNPL basket, which has heat = 1.55 and is Rising").

2. **Ticker-level SI data** — For any ticker in the VIC pitch database, you can look up its SI series in `RAW.tickers[tk]`. Key fields: `z6m` (6-month Z-score), `pct` (SI% of float sparse array), `si` (raw SI sparse array).

3. **The Z-score as a signal overlay** — The most natural integration point. Showing SI Z-score alongside VIC author alpha at the same ticker and time would let the user see if high-conviction VIC pitches were preceded or accompanied by unusual short interest.

### SI data as a counter-signal for VIC longs

High SI (high Z-score, rising shorts) is a headwind for long pitches. The VIC pitch tracker already flags high-conviction long ideas; knowing that a ticker has a 6M SI Z-score > 2.0 would add a risk overlay. Conversely, a declining short signal (heat ≤ -0.4, covering) could reinforce a VIC long thesis.

### The Watchlist tab in VIC

The VIC Watchlist tab already uses 13F Z-scores as a conviction signal. The SI Z-score is a complementary signal — both measure institutional behavior but from different angles (13F = what funds are buying; SI = what shorts are betting against). A combined score (13F Z + SI Z-score inverted) could strengthen the signal.

### Data freshness

The SI data is updated every two weeks (FINRA cadence). The VIC pitch data updates whenever new pitches are scraped. The integration needs to handle the fact that these have different cadences and different date reference points.

### Sharing data between the two dashboards

Option A — **Reference the SI history CSV directly**: The VIC project reads `SI Tracker/si_history_full.csv` and computes Z-scores on the fly. Clean but computationally expensive per page load.

Option B — **Extract a per-ticker SI summary JSON**: A lightweight file (one record per ticker, latest Z-scores, theme memberships) generated from the SI pipeline and read by the VIC dashboard. Recommended.

Option C — **Add a "Short Interest" tab to the VIC HTML**: Embed a subset of `INSIGHTS_DATA` and key per-ticker SI signals directly into the VIC HTML file. The SI themes most relevant to VIC pitches (based on ticker overlap) would be surfaced here.

---

## 10. Known Remaining Issues / Improvement Pathways

From `ISSUES.md` and session discussions:

### Data accuracy
- **ASAN/NTST float disagreement**: Float source (FinViz vs ChartExchange-implied) gives different denominators. The discrepancy is documented but not resolved.
- **`shares` field shows 0 for some tickers**: The display column for shares outstanding is not always populated; float data was applied only to pct calculations, not the shares display field.

### Feature improvements discussed but not implemented
- **IRR as a win condition for VIC pitches** (Track E in VIC ISSUES.md): Annualized IRR at each horizon, peak IRR as optimal exit timing, thesis half-life metric. Data already exists in VIC's 6 horizon returns.
- **More granular time frame in Themes tab**: The period selector currently offers 3M/6M/1Y/2Y/All. The user asked about more granular options (e.g., bi-weekly aligned to FINRA periods).
- **Sector Sentiment in Themes**: The Themes tab has a period selector but it was noted that the Sector Sentiment tab has a richer chart view; the user requested parity. This was partially addressed but the Themes Y-axis dropdown was separately added.
- **Internet niche themes**: IAS (Integral Ad Science), TTD (The Trade Desk), SSTK (Shutterstock) discussed as additions to the Ad Tech theme basket.
- **Declining theme email alert**: Themes crossing the -0.4 threshold could trigger a notification. Not implemented.
- **Float as a visible column**: Float is used in SI% calculations but not displayed as a standalone column in the Trend/Screener tabs.

### Infrastructure
- **[RESOLVED 2026-05-11]** Canonical CSV ambiguity — `si_history_full.csv` is now the single master; legacy `si_history.csv` and `si_history_clean.csv` archived to `snapshots/`.
- **build_dashboard.py is the canonical generator** but `regenerate_dashboard.py` and `create_simple_dashboard.py` are older scripts that may diverge. These should be cleaned up to avoid confusion.

---

## 11. Safe Editing Practices for si_dashboard.html

This section is critical for any agent that needs to modify the dashboard:

1. **Never use the Edit tool for any change in the last 15% of the file.** All JS functions live at 95–100% file position. Edit any of these using Python instead:
   ```python
   html = open('si_dashboard.html', encoding='utf-8').read()
   html = html.replace(OLD_BLOCK, NEW_BLOCK, 1)
   open('si_dashboard.html', 'w', encoding='utf-8').write(html)
   ```

2. **For JSON blocks (RAW, SECTOR_DATA, INSIGHTS_DATA):** Use Python brace-counting replacement, not regex. Regex on 24 MB JSON with nested structures is error-prone and slow.

3. **Run the validator after every edit:**
   ```bash
   python validate_dashboard.py
   ```
   All 11 checks must pass before the edit is considered complete.

4. **Take a snapshot before any major edit:**
   ```bash
   cp si_dashboard.html snapshots/vN_YYYYMMDD/si_dashboard.html
   ```

5. **If the file is truncated**, do NOT use the Edit or Write tool to fix it — that risks a second truncation. Restore from the snapshot, or reconstruct the tail and append it using Python `open(path, 'a')`.
