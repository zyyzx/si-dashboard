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
    for r in range(2, ws.max_row + 1):
        t = ws.cell(row=r, column=1).value
        mc = ws.cell(row=r, column=2).value
        if t is None:
            continue
        t = str(t).strip().upper()
        if not isinstance(mc, (int, float)):
            no_value.append(t)
            continue
        if mc >= threshold_m:
            kept.append((t, float(mc)))
        else:
            below.append((t, float(mc)))

    kept.sort(key=lambda x: -x[1])
    out_path = os.path.join(TRACKER_DIR, f"tickers_above_{int(threshold_m)}m.txt")
    with open(out_path, "w") as f:
        for t, _ in kept:
            f.write(t + "\n")

    total = len(kept) + len(below) + len(no_value)
    print(f"Threshold:        ${threshold_m:,.0f}M ({threshold_m/1000:.1f}B)")
    print(f"Kept:             {len(kept):,} / {total:,} tickers")
    print(f"Below threshold:  {len(below):,}")
    print(f"No mcap (NA):     {len(no_value):,}  "
          f"(CapIQ returned blank/error; usually delisted or pre-IPO)")
    print(f"Output:           {out_path}")
    print()
    if kept:
        print(f"Sample of top 5 by mcap:")
        for t, mc in kept[:5]:
            print(f"  {t:6}  ${mc:,.0f}M")
    print(f"\nNext: python create_capiq_template.py")
    print(f"      (auto-detects {os.path.basename(out_path)} and uses it)")


if __name__ == "__main__":
    main()
