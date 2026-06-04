# si-dashboard TODO

Follow-up tasks tracked outside the more comprehensive
alpha-unified TODO.md / HANDOFF.md (those track the broader
project; this file is for things scoped to the si-dashboard repo
itself).

## CapIQ float panel — second batch for $100M-$1B names

The current `capiq_float_historical.xlsx` panel is built from
`tickers_above_1000m.txt`, scoping the universe to >=$1B
US-tradeable market cap. That's ~3,100 names with full quarterly
float coverage for SI %-of-float computation.

The next universe expansion should sweep the **$100M-$1B layer**:

```cmd
:: After the >=$1B batch is loaded into ref_float_panel:
python filter_tickers_above_mcap.py 100     # writes tickers_above_100m.txt
:: Manually edit tickers_above_100m.txt to remove the >=$1B names
:: already covered (or just let the loader's date-level replace handle
:: the duplicates -- harmless, just a bit of wasted CapIQ formula time).
python create_capiq_template.py
:: Open in Excel + CapIQ Pro, recalc (the long tail is ~1,500 extra
:: names = ~40k extra formulas, +15-20 min on top of the first batch),
:: save, and load via etl.load_ref_float in alpha-unified.
```

Approximate numbers from the latest filter run:

| Threshold | Tickers | Notes |
|---|---|---|
| >= $1B (1000M) | 3,130 | Current Phase 1 -- institutional-grade names |
| >= $500M | 3,716 | +586 |
| >= $200M | 4,384 | +1,254 |
| >= $100M | 4,726 | +1,596 over Phase 1 |
| (universe-total) | 13,819 | Includes 7,691 non-US + 264 NA |

Phase 2 cost is the extra ~1,600 tickers x ~26 dates = ~40,000
CapIQ formula evaluations. Same loader (`etl.load_ref_float` in
alpha-unified) handles it; ref_float_panel just gets more rows.

Defer until Phase 1 float lands and we've validated the
SI %-of-float join is producing sensible numbers in the unified DB.

## Done in this session

- `create_mcap_check.py`: USD-forced mcap + `IQ_PRIMARY_EXCHANGE`
  column. Workbook is the pre-filter step.
- `filter_tickers_above_mcap.py`: drops non-US exchanges via
  CapIQ-reported primary exchange + applies $1B mcap threshold +
  $10T sanity bound for residual outliers.
- `create_capiq_template.py`: auto-detects
  `tickers_above_<N>m.txt`, falls back to full dashboard ticker
  set when absent. `TRACKER_DIR` portable across machines.
