#!/usr/bin/env python3
"""Characterise which tickers the price overlay is missing, and whether it matters.

A bare coverage percentage cannot answer the only question worth asking: are
the uncovered names ones anybody would chart? Half a universe of dead OTC
shells is a very different situation from a handful of liquid large-caps
falling through a symbology crack, and both look identical at "43%".

So this ranks the gap by latest short interest — the names at the top are the
ones whose absent price line you would actually notice — and splits it by
whether the ticker still reports SI in the most recent settlement (live) or
stopped at some point (delisted/acquired, and unfixable from any current
price source).

Usage
  python price_coverage_report.py
  python price_coverage_report.py --top 40 --out missing_prices.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT / "si_dashboard.html"
PRICES_CSV = ROOT / "prices_settlement.csv"


def raw_span(html: str) -> tuple[int, int]:
    needle = "RAW={"
    i = html.find(needle)
    if i < 0:
        raise SystemExit("ERROR: could not locate RAW={ in dashboard")
    start = i + len(needle) - 1
    depth, j, in_str, esc = 0, start, False, False
    while j < len(html):
        c = html[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return start, j + 1
        j += 1
    raise SystemExit("ERROR: unbalanced braces scanning RAW")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Report price-overlay coverage gaps")
    ap.add_argument("--prices", default=str(PRICES_CSV))
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--out", default=None, help="write the full missing list to CSV")
    args = ap.parse_args(argv)

    prices_path = Path(args.prices)
    if not prices_path.exists():
        print(f"ERROR: {prices_path} not found — run fetch_prices.py first",
              file=sys.stderr)
        return 1
    if not DASHBOARD.exists():
        print(f"ERROR: {DASHBOARD} not found", file=sys.stderr)
        return 1

    have: set[str] = set()
    with open(prices_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            have.add(row["ticker"])
    print(f"Priced tickers:    {len(have):,}")

    print(f"Parsing {DASHBOARD.name} ...")
    html = DASHBOARD.read_text(encoding="utf-8")
    s, e = raw_span(html)
    raw = json.loads(html[s:e])
    dates = raw["dates"]
    tickers = raw["tickers"]
    last_idx = len(dates) - 1
    print(f"Dashboard tickers: {len(tickers):,}  (grid {dates[0]} -> {dates[-1]})")

    rows = []
    for tk, t in tickers.items():
        if tk in have:
            continue
        si = t.get("si") or []
        latest_si, latest_i = 0.0, -1
        for pair in si:
            if isinstance(pair, list) and len(pair) == 2 and pair[1]:
                if pair[0] > latest_i:
                    latest_i, latest_si = pair[0], float(pair[1])
        rows.append({
            "ticker": tk,
            "name": (t.get("name") or "")[:48],
            "latest_si_shares": int(latest_si),
            "last_period": dates[latest_i] if latest_i >= 0 else "",
            "still_reporting": "yes" if latest_i == last_idx else "no",
        })

    missing = len(rows)
    total = len(tickers)
    live = [r for r in rows if r["still_reporting"] == "yes"]
    dead = missing - len(live)

    print()
    print(f"Missing prices:    {missing:,} of {total:,} "
          f"({missing/max(total,1):.0%})")
    print(f"  still reporting SI in the latest period: {len(live):,}")
    print(f"  stopped reporting (delisted/acquired):   {dead:,}  "
          f"<- no current price source can fix these")

    live.sort(key=lambda r: -r["latest_si_shares"])
    if live:
        print(f"\nLargest gaps by latest short interest "
              f"(these are the ones you would notice):")
        print(f"  {'ticker':<9}{'latest SI':>14}  name")
        for r in live[: args.top]:
            print(f"  {r['ticker']:<9}{r['latest_si_shares']:>14,}  {r['name']}")
        big = sum(1 for r in live if r["latest_si_shares"] >= 1_000_000)
        print(f"\n  {big:,} live uncovered tickers have SI >= 1M shares.")
        if big == 0:
            print("  Nothing sizeable is missing — the gap is small/illiquid names.")

    if args.out:
        out = Path(args.out)
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else
                               ["ticker", "name", "latest_si_shares",
                                "last_period", "still_reporting"])
            w.writeheader()
            w.writerows(sorted(rows, key=lambda r: -r["latest_si_shares"]))
        print(f"\nWrote {out}  ({missing:,} rows)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
