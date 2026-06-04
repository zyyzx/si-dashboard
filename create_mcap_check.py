#!/usr/bin/env python3
"""Generate a minimal CapIQ workbook to grab current market cap for every
ticker in the dashboard. Used as a pre-filter step before
create_capiq_template.py -- after this recalcs, run
filter_tickers_above_mcap.py to write the filtered list, then
create_capiq_template.py picks it up automatically.

Cheap one-column workbook: ~13k formulas, recalcs in ~5-10 min instead of
the ~340k formulas the full float panel would need.

Run:  python create_mcap_check.py
"""

import json
import os
import re

import openpyxl
from openpyxl.styles import Font, PatternFill


TRACKER_DIR = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    print("Loading tickers from dashboard...")
    with open(os.path.join(TRACKER_DIR, "si_dashboard.html"), encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"const RAW=(\{.*?\});", text)
    if not m:
        raise SystemExit("could not find RAW={...} in si_dashboard.html "
                          "-- regenerate the dashboard first")
    raw = json.loads(m.group(1))
    tickers = sorted(raw["tickers"].keys())
    print(f"Tickers: {len(tickers):,}")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MarketCap"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="16213E")
    for col, label in enumerate(("Ticker", "Market Cap ($M)"), start=1):
        c = ws.cell(row=1, column=col, value=label)
        c.font = header_font
        c.fill = header_fill
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 18

    # One formula per ticker. CapIQ Pro's IQ_MARKETCAP returns values
    # already in MILLIONS for this tier (Agilent / "A" comes back as
    # ~38806, i.e. $38.8B = $38,806M). filter_tickers_above_mcap.py
    # interprets its threshold argument in millions to match.
    for i, t in enumerate(tickers, start=2):
        ws.cell(row=i, column=1, value=t)
        ws.cell(row=i, column=2, value=f'=@CIQ($A{i},"IQ_MARKETCAP")')
    ws.freeze_panes = "B2"

    out = os.path.join(TRACKER_DIR, "capiq_mcap_check.xlsx")
    wb.save(out)
    print(f"Saved {out}")
    print(f"\nNext: open in Excel with CapIQ Pro, let recalc finish "
          f"(~5-10 min for {len(tickers):,} formulas), save in place, then:")
    print(f"      python filter_tickers_above_mcap.py 1000")
    print(f"      (1000 = $1B floor; pass a different number to tune)")


if __name__ == "__main__":
    main()
