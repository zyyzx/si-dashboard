#!/usr/bin/env python3
"""Filter the tickers from capiq_mcap_check.xlsx by market cap threshold
and write the kept list to tickers_above_<N>m.txt.

create_capiq_template.py auto-detects the resulting file and uses it as
the ticker universe instead of every ticker in si_dashboard.html, so the
slim float workbook only contains names you actually want to score.

Run:  python filter_tickers_above_mcap.py [threshold_in_millions]
      (default: 1000  -> $1B)
"""

import os
import sys

import openpyxl


TRACKER_DIR = os.path.dirname(os.path.abspath(__file__))

# Anything above this in the "millions" column is treated as a CapIQ unit
# bug. The real top-of-market is NVDA / AAPL / MSFT around $3.5-4.5T as of
# 2026. Foreign ADRs and OTC pink sheets (IBN, PCRFF, PCRFY, PLIN, SSNLF,
# BBCA, BKRKF, PPERF, TLK, ...) return raw local-currency values that fall
# between $1T and $10T, slipping through a loose bound. $5T is tight
# enough to catch them and still leaves ~25% headroom over NVDA -- bump
# this if a real mega-cap crosses $5T in the future.
_SANE_MAX_MCAP_M = 5_000_000  # = $5 trillion


def main() -> None:
    threshold_m = float(sys.argv[1]) if len(sys.argv) > 1 else 1000.0

    src = os.path.join(TRACKER_DIR, "capiq_mcap_check.xlsx")
    if not os.path.exists(src):
        raise SystemExit(
            f"missing {src} -- run create_mcap_check.py first, "
            "then open in Excel + CapIQ Pro, let recalc finish, save."
        )

    wb = openpyxl.load_workbook(src, data_only=True)
    if "MarketCap" not in wb.sheetnames:
        raise SystemExit("capiq_mcap_check.xlsx has no 'MarketCap' sheet -- "
                          "re-run create_mcap_check.py")
    ws = wb["MarketCap"]

    kept: list[tuple[str, float]] = []
    no_value: list[str] = []
    below: list[tuple[str, float]] = []
    unit_bug: list[tuple[str, float]] = []  # implausibly large -> dropped
    for r in range(2, ws.max_row + 1):
        t = ws.cell(row=r, column=1).value
        mc = ws.cell(row=r, column=2).value
        if t is None:
            continue
        t = str(t).strip().upper()
        if not isinstance(mc, (int, float)):
            no_value.append(t)
            continue
        mc_f = float(mc)
        if mc_f > _SANE_MAX_MCAP_M:
            # Unit bug -- raw local currency leaking through. Drop and warn.
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

    total = len(kept) + len(below) + len(no_value) + len(unit_bug)
    print(f"Threshold:        ${threshold_m:,.0f}M ({threshold_m/1000:.1f}B)")
    print(f"Sanity upper bound:${_SANE_MAX_MCAP_M:,.0f}M "
          f"(${_SANE_MAX_MCAP_M/1_000_000:.0f}T)")
    print(f"Kept:             {len(kept):,} / {total:,} tickers")
    print(f"Below threshold:  {len(below):,}")
    print(f"No mcap (NA):     {len(no_value):,}  "
          f"(CapIQ returned blank/error; usually delisted or pre-IPO)")
    print(f"Unit bug (>${_SANE_MAX_MCAP_M/1_000_000:.0f}T):  {len(unit_bug):,}  "
          f"(OTC pink-sheet / foreign ordinaries where IQ_MARKETCAP "
          f"returned raw local currency instead of USD millions)")
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
