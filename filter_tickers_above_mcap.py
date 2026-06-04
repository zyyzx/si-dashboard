#!/usr/bin/env python3
"""Filter the tickers from capiq_mcap_check.xlsx by market cap threshold
and write the kept list to tickers_above_<N>m.txt.

create_capiq_template.py auto-detects the resulting file and uses it as
the ticker universe instead of every ticker in si_dashboard.html, so the
slim float workbook only contains names you actually want to score.

Run:  python filter_tickers_above_mcap.py [threshold_in_millions]
      (default: 1000  -> $1B)
"""

import json
import os
import re
import sys

import openpyxl


TRACKER_DIR = os.path.dirname(os.path.abspath(__file__))

# FINRA market class codes for the OTC tier. IQ_MARKETCAP returns
# local-currency-denominated values for many OTC pink sheets (SSNLF,
# BKRKF, PCRFF, ...), so they're stripped here as well as in
# create_mcap_check.py -- defense in depth catches workbooks that were
# built before the OTC exclusion was added at the source.
_OTC_CLASSES = {"OTC", "OTCBB", "OTCPK"}

# Even after OTC exclusion, some US-listed ADRs (IBN, IBND, PLIN, ...)
# return foreign-currency mcaps that sit between $1T and $10T. The real
# top-of-market is NVDA / AAPL / MSFT around $3.5-4.5T as of 2026, so
# $5T cleanly catches the leakers with ~25% headroom for future growth.
# Bump this constant if a real mega-cap crosses $5T USD market cap.
_SANE_MAX_MCAP_M = 5_000_000  # = $5 trillion


def _load_otc_set() -> set[str]:
    """Read si_dashboard.html and return the set of tickers whose
    FINRA marketClass is in the OTC tier. Empty set on parse failure --
    the sanity upper bound still catches the worst outliers."""
    path = os.path.join(TRACKER_DIR, "si_dashboard.html")
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"const RAW=(\{.*?\});", text)
    if not m:
        return set()
    raw = json.loads(m.group(1))
    return {
        t for t, info in raw["tickers"].items()
        if (info.get("marketClass") or "").upper() in _OTC_CLASSES
    }


def main() -> None:
    threshold_m = float(sys.argv[1]) if len(sys.argv) > 1 else 1000.0

    src = os.path.join(TRACKER_DIR, "capiq_mcap_check.xlsx")
    if not os.path.exists(src):
        raise SystemExit(
            f"missing {src} -- run create_mcap_check.py first, "
            "then open in Excel + CapIQ Pro, let recalc finish, save."
        )

    otc_set = _load_otc_set()
    wb = openpyxl.load_workbook(src, data_only=True)
    if "MarketCap" not in wb.sheetnames:
        raise SystemExit("capiq_mcap_check.xlsx has no 'MarketCap' sheet -- "
                          "re-run create_mcap_check.py")
    ws = wb["MarketCap"]

    kept: list[tuple[str, float]] = []
    no_value: list[str] = []
    below: list[tuple[str, float]] = []
    unit_bug: list[tuple[str, float]] = []   # implausibly large -> dropped
    otc_dropped: list[str] = []               # OTC pink / OTCBB / OTCPK
    for r in range(2, ws.max_row + 1):
        t = ws.cell(row=r, column=1).value
        mc = ws.cell(row=r, column=2).value
        if t is None:
            continue
        t = str(t).strip().upper()
        if t in otc_set:
            otc_dropped.append(t)
            continue
        if not isinstance(mc, (int, float)):
            no_value.append(t)
            continue
        mc_f = float(mc)
        if mc_f > _SANE_MAX_MCAP_M:
            unit_bug.append((t, mc_f))
            continue
        if mc_f >= threshold_m:
            kept.append((t, mc_f))
        else:
            below.append((t, mc_f))

    kept.sort(key=lambda x: -x[1])
    out_path = os.path.join(TRACKER_DIR, f"tickers_above_{int(threshold_m)}m.txt")
    with open(out_path, "w") as f:
        for t, _ in kept:
            f.write(t + "\n")

    total = (len(kept) + len(below) + len(no_value)
              + len(unit_bug) + len(otc_dropped))
    print(f"Threshold:        ${threshold_m:,.0f}M ({threshold_m/1000:.1f}B)")
    print(f"Sanity upper bound:${_SANE_MAX_MCAP_M:,.0f}M "
          f"(${_SANE_MAX_MCAP_M/1_000_000:.0f}T)")
    print(f"Kept:             {len(kept):,} / {total:,} tickers")
    print(f"Below threshold:  {len(below):,}")
    print(f"No mcap (NA):     {len(no_value):,}  "
          f"(CapIQ returned blank/error; usually delisted or pre-IPO)")
    print(f"OTC dropped:      {len(otc_dropped):,}  "
          f"(pink-sheet/OTCBB/OTCPK -- unreliable USD mcap from IQ_MARKETCAP)")
    print(f"Unit bug (>${_SANE_MAX_MCAP_M/1_000_000:.0f}T):  {len(unit_bug):,}  "
          f"(US-listed ADRs / ETFs where IQ_MARKETCAP returned raw "
          f"local currency despite passing the OTC filter)")
    print(f"Output:           {out_path}")
    print()
    if kept:
        print(f"Sample of top 10 by mcap:")
        for t, mc in kept[:10]:
            print(f"  {t:6}  ${mc:,.0f}M")
    if unit_bug:
        print(f"\nDropped due to unit bug (worth investigating manually if "
              f"these are names you actually want):")
        unit_bug.sort(key=lambda x: -x[1])
        for t, mc in unit_bug[:10]:
            print(f"  {t:6}  raw_value={mc:,.0f}M")
        if len(unit_bug) > 10:
            print(f"  ... ({len(unit_bug) - 10} more)")
    print(f"\nNext: python create_capiq_template.py")
    print(f"      (auto-detects {os.path.basename(out_path)} and uses it)")


if __name__ == "__main__":
    main()
