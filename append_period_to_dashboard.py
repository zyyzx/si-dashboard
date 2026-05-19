#!/usr/bin/env python3
"""Append a new FINRA settlement period into the embedded RAW JSON in
si_dashboard.html, in place, without regenerating the dashboard from scratch.

Why this exists:
  - The rich 6-tab dashboard (Trend / Themes / SI Movers / Sector / Screener /
    Guide) has no canonical regen script. `build_dashboard.py` only emits a
    minimal 3-tab stub and overwrites the rich version if used. So when a new
    FINRA period lands and gets appended to si_history_full.csv, the only safe
    way to surface it in the rich tabs' time-series is to inject it directly
    into the existing RAW literal.

Operation:
  1. Snapshot si_dashboard.html → snapshots/append_period_<asof>.html
  2. Locate `RAW={...}` and the matching closing brace via bracket-counting
  3. json.loads() the literal (preserves dict insertion order)
  4. Append the new settlement date to RAW.dates
  5. Read si_history_full.csv, filter to settlementDate == NEW_DATE
  6. For every ticker that already exists in RAW.tickers, append a sparse
     [newIdx, si_value] pair to .si and [newIdx, pct_value] to .pct
  7. Skip tickers that aren't already tracked (keep universe stable to avoid
     ballooning the embedded payload with OTC entries the rich UI doesn't use)
  8. json.dumps with separators=(",", ":") to match the original compact format
  9. Splice the new RAW literal back into the HTML and write it
 10. Validator runs separately — invoke validate_dashboard.py after this.

Idempotency:
  If NEW_DATE is already in RAW.dates, the script exits without modification.

Usage:
  python append_period_to_dashboard.py 20260430
  python append_period_to_dashboard.py            # uses latest date in CSV not in dashboard
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT / "si_dashboard.html"
HISTORY_CSV = ROOT / "si_history_full.csv"
SNAPSHOT_DIR = ROOT / "snapshots"


def find_raw_span(html: str) -> tuple[int, int]:
    """Return (start, end) byte offsets such that html[start:end] is the
    JSON literal `{...}` of the RAW object (excluding the leading `RAW=`
    and the trailing `;` or whatever follows)."""
    needle = "RAW={"
    i = html.find(needle)
    if i < 0:
        raise RuntimeError("Could not locate `RAW={` in dashboard HTML")
    start = i + len(needle) - 1  # position of the `{`

    # Walk forward, counting braces, respecting strings.
    depth = 0
    j = start
    n = len(html)
    in_str = False
    escape = False
    while j < n:
        c = html[j]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
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
    raise RuntimeError("Unbalanced braces while scanning RAW literal")


def _yyyymmdd_to_slash(d: str) -> str:
    """20260430 -> 04/30/2026"""
    return f"{d[4:6]}/{d[6:8]}/{d[:4]}"


def patch_static_markup(html: str, dates: list[str]) -> tuple[str, list[str]]:
    """Update visible hardcoded strings (period count, latest date, and the
    DATES_LABELS array that drives the Screener period dropdowns) to reflect
    the current state of RAW.dates. Returns (new_html, list_of_changes).
    Idempotent: re-running with the same dates produces no change."""
    if not dates:
        return html, ["dates empty, skipping markup patches"]
    n = len(dates)
    last = dates[-1]  # e.g. "20260430"
    try:
        yyyy, mm, dd = last[:4], last[4:6], last[6:8]
        slash_date = f"{mm}/{dd}/{yyyy}"
        month_name = MONTH_NAMES[int(mm)]
    except (ValueError, IndexError):
        return html, [f"could not parse latest date '{last}', skipping markup"]

    first = dates[0]
    try:
        first_year = first[:4]
        first_month_name = MONTH_NAMES[int(first[4:6])]
    except (ValueError, IndexError):
        first_year = first[:4] if len(first) >= 4 else first
        first_month_name = "Jan"

    changes: list[str] = []

    # Patch 1: <div class="data-badge">{N} periods &middot; Updated MM/DD/YYYY</div>
    badge_pat = re.compile(
        r'(<div class="data-badge">)\d+ periods &middot; Updated \d{2}/\d{2}/\d{4}(</div>)'
    )
    new_badge = rf'\g<1>{n} periods &middot; Updated {slash_date}\g<2>'
    new_html, count = badge_pat.subn(new_badge, html, count=1)
    changes.append(
        f"data-badge -> '{n} periods · Updated {slash_date}'" if count
        else "data-badge pattern not found (skipped)"
    )
    html = new_html

    # Patch 2: "N periods from <Mon> YYYY to <Mon> YYYY" in the Guide tab
    range_pat = re.compile(
        r'(\d+) periods from (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4}) to '
        r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4})'
    )
    new_range = f"{n} periods from {first_month_name} {first_year} to {month_name} {yyyy}"
    new_html, count = range_pat.subn(new_range, html, count=1)
    changes.append(
        f"guide-range -> '{new_range}'" if count
        else "guide-range pattern not found (skipped)"
    )
    html = new_html

    # Patch 3: const DATES_LABELS=[...]  — drives the Screener period dropdowns
    labels = [_yyyymmdd_to_slash(d) for d in dates]
    new_labels_arr = "[" + ",".join(f'"{lbl}"' for lbl in labels) + "]"
    labels_pat = re.compile(r'const DATES_LABELS=\[[^\]]*\]')
    new_html, count = labels_pat.subn(
        f"const DATES_LABELS={new_labels_arr}", html, count=1
    )
    changes.append(
        f"DATES_LABELS -> {len(labels)} entries (last={labels[-1]})" if count
        else "DATES_LABELS pattern not found (skipped)"
    )
    html = new_html

    return html, changes


def main(argv: list[str]) -> int:
    if not DASHBOARD.exists():
        print(f"ERROR: {DASHBOARD} not found", file=sys.stderr)
        return 1
    if not HISTORY_CSV.exists():
        print(f"ERROR: {HISTORY_CSV} not found", file=sys.stderr)
        return 1

    t0 = time.time()
    print("=" * 70)
    print("APPEND PERIOD TO DASHBOARD")
    print("=" * 70)

    print(f"Reading dashboard ({DASHBOARD.stat().st_size / 1e6:.1f} MB) ...")
    html = DASHBOARD.read_text(encoding="utf-8")
    raw_start, raw_end = find_raw_span(html)
    raw_text = html[raw_start:raw_end]
    print(f"  RAW literal spans bytes {raw_start:,} .. {raw_end:,}  ({len(raw_text)/1e6:.1f} MB)")

    t_parse = time.time()
    raw = json.loads(raw_text)
    print(f"  parsed in {time.time()-t_parse:.1f}s")

    dates = raw.get("dates") or []
    tickers = raw.get("tickers") or {}
    if not dates or not tickers:
        print("ERROR: RAW.dates or RAW.tickers missing/empty", file=sys.stderr)
        return 2
    print(f"  current: {len(dates)} dates ({dates[0]} -> {dates[-1]}), {len(tickers):,} tickers")

    # Determine the new period: CLI arg, else first date in CSV not already in dashboard
    if len(argv) > 1:
        new_date = argv[1].strip()
    else:
        print("\nNo date arg — scanning si_history_full.csv for first date not in dashboard ...")
        existing = set(dates)
        chunks = pd.read_csv(HISTORY_CSV, usecols=["settlementDate"], dtype=str, chunksize=500_000)
        seen = set()
        for ch in chunks:
            seen.update(ch["settlementDate"].dropna().astype(str).unique().tolist())
        missing = sorted(d for d in seen if d not in existing)
        if not missing:
            print("Nothing to add — dashboard already has every date from si_history_full.csv.")
            return 0
        new_date = missing[0]
        print(f"  using {new_date}")

    if new_date in dates:
        print(f"\n{new_date} already present in RAW.dates — checking static markup ...")
        new_html, changes = patch_static_markup(html, dates)
        for c in changes:
            print(f"  {c}")
        if new_html != html:
            DASHBOARD.write_text(new_html, encoding="utf-8")
            print(f"\nWrote {DASHBOARD.name} with markup patches only "
                  f"(no RAW data changes; {DASHBOARD.stat().st_size/1e6:.1f} MB)")
        else:
            print("Markup already in sync. Nothing to do.")
        return 0

    if new_date < dates[-1]:
        print(
            f"\nWARNING: {new_date} is older than the dashboard's latest date {dates[-1]}.",
            file=sys.stderr,
        )
        print(
            "This script only supports appending to the END of the time series.",
            file=sys.stderr,
        )
        return 3

    # Snapshot
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    snap = SNAPSHOT_DIR / f"append_period_{new_date}_pre.html"
    if not snap.exists():
        shutil.copy2(DASHBOARD, snap)
        print(f"  snapshot: {snap.name}")
    else:
        print(f"  snapshot already exists: {snap.name}  (kept)")

    # Pull the new period's rows from si_history_full.csv
    print(f"\nReading rows for {new_date} from si_history_full.csv ...")
    rows = []
    chunks = pd.read_csv(
        HISTORY_CSV,
        usecols=[
            "symbolCode",
            "settlementDate",
            "currentShortPositionQuantity",
            "changePercent",
        ],
        dtype={"symbolCode": str, "settlementDate": str},
        chunksize=500_000,
        low_memory=False,
    )
    for ch in chunks:
        ch = ch[ch["settlementDate"] == new_date]
        if not ch.empty:
            rows.append(ch)
    if not rows:
        print(
            f"ERROR: si_history_full.csv has no rows with settlementDate=={new_date}",
            file=sys.stderr,
        )
        return 4
    finra = pd.concat(rows, ignore_index=True)
    finra = finra.drop_duplicates(subset=["symbolCode"], keep="last")
    print(f"  {len(finra):,} unique tickers in {new_date}")

    # Mutate
    new_idx = len(dates)  # the index position the new date will occupy
    dates.append(new_date)

    # Drop rows with bad symbols and coerce numerics once (much faster than per-row try/except)
    finra = finra.dropna(subset=["symbolCode"]).copy()
    finra["symbolCode"] = finra["symbolCode"].astype(str).str.strip()
    finra["currentShortPositionQuantity"] = pd.to_numeric(
        finra["currentShortPositionQuantity"], errors="coerce"
    ).fillna(0.0)
    finra["changePercent"] = pd.to_numeric(finra["changePercent"], errors="coerce").fillna(0.0)

    matched = 0
    skipped_unknown = 0
    for sym, si_val, pct_val in zip(
        finra["symbolCode"].tolist(),
        finra["currentShortPositionQuantity"].tolist(),
        finra["changePercent"].tolist(),
    ):
        if not sym or sym not in tickers:
            skipped_unknown += 1
            continue
        si_val = float(si_val)
        pct_val = float(pct_val)

        tk = tickers[sym]
        si_arr = tk.setdefault("si", [])
        pct_arr = tk.setdefault("pct", [])
        # Guard against double-append in case of partial prior runs
        if si_arr and si_arr[-1] and len(si_arr[-1]) == 2 and si_arr[-1][0] == new_idx:
            si_arr[-1] = [new_idx, si_val]
        else:
            si_arr.append([new_idx, si_val])
        if pct_arr and pct_arr[-1] and len(pct_arr[-1]) == 2 and pct_arr[-1][0] == new_idx:
            pct_arr[-1] = [new_idx, pct_val]
        else:
            pct_arr.append([new_idx, pct_val])
        matched += 1

    print(f"  appended {matched:,} ticker updates")
    print(f"  skipped {skipped_unknown:,} tickers not already in dashboard universe")

    # Re-serialize with the same compact separators used by build_dashboard.py
    t_dump = time.time()
    new_raw_text = json.dumps(raw, separators=(",", ":"), ensure_ascii=False)
    print(f"\nSerialized RAW ({len(new_raw_text)/1e6:.1f} MB) in {time.time()-t_dump:.1f}s")

    # Splice back
    new_html = html[:raw_start] + new_raw_text + html[raw_end:]

    # Patch visible markup (badge + guide-text references to period count / latest date)
    new_html, markup_changes = patch_static_markup(new_html, dates)
    print("\nMarkup patches:")
    for c in markup_changes:
        print(f"  {c}")

    DASHBOARD.write_text(new_html, encoding="utf-8")
    new_size = DASHBOARD.stat().st_size

    print(f"\nWrote {DASHBOARD.name}  ({new_size/1e6:.1f} MB)")
    print(f"  RAW.dates: {len(dates)} periods, latest = {dates[-1]}")
    print(f"  RAW.tickers: {len(tickers):,}")
    print(f"  total time: {time.time()-t0:.1f}s")
    print("=" * 70)
    print("NEXT: run `python validate_dashboard.py` before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
