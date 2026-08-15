#!/usr/bin/env python3
"""Fetch price history and sample it at FINRA settlement dates.

Produces `prices_settlement.csv` — one row per (ticker, settlement_date) with
the last close on or before that settlement, so it lines up index-for-index
with the dashboard's RAW.dates array.

Why sampled, not daily: the dashboard is a single self-contained HTML file and
the SI series is bi-weekly. Daily prices for ~13,800 tickers over six years
would add hundreds of MB for resolution the chart cannot show.

    ADJUSTED vs RAW CLOSE — read this before changing anything

    Both are stored. They are not interchangeable:

      close_adj  retroactively adjusted for splits and dividends. Continuous
                 through a split. Correct for returns and for any indexed
                 (base-100) comparison. This is what the overlay embeds.
      close_raw  as printed on the tape that day. Halves at a 2:1 split.

    FINRA's currentShortPositionQuantity is as-reported and is NOT
    retroactively split-adjusted, so SI *shares* jump at a split while
    adjusted price does not. That asymmetry is inherent to the sources, not a
    bug here. SI % of float is immune (numerator and denominator scale
    together), so pct is the cleaner basis for comparing against price.

Two sources:

  stooq  ONE bulk download covering every US ticker, parsed locally. Minutes
         instead of hours, and no rate limiting. Best for the initial backfill.
         Stooq's daily bars are SPLIT-adjusted but not dividend-adjusted, so
         close_adj is filled and close_raw is left empty — see below.
  yahoo  Per-ticker requests against the chart endpoint. Slower (hours for the
         full universe) but returns raw AND dividend-adjusted close, and is the
         right tool for topping up a handful of names between backfills.

Both are resumable — reruns skip tickers already in the output, so an
interrupted run continues where it stopped.

Getting the Stooq bulk file: download it in a browser from
https://stooq.com/db/h/ (choose "Daily / US / TXT" -> d_us_txt.zip, roughly
100-200 MB). Downloading by hand is deliberate: Stooq throttles and
bot-blocks scripted pulls of the bulk archives, and a browser download
sidesteps that entirely. Then point this script at the file.

Usage
  python fetch_prices.py --source stooq --stooq-zip C:\\Downloads\\d_us_txt.zip
  python fetch_prices.py                     # yahoo; every ticker in the SI history
  python fetch_prices.py --limit 50          # smoke test
  python fetch_prices.py --tickers AAPL,GME  # specific names
  python fetch_prices.py --pause 0.4         # be gentler on the source
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
HISTORY_CSV = ROOT / "si_history_full.csv"
DASHBOARD = ROOT / "si_dashboard.html"
OUT_CSV = ROOT / "prices_settlement.csv"

CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{tk}"
    "?period1={p1}&period2={p2}&interval=1d"
)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "\
     "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"

FIELDNAMES = ["ticker", "settlement_date", "close_adj", "close_raw", "src"]


# ------------------------------------------------------------ stooq bulk

def stooq_variants(stem: str) -> list[str]:
    """Candidate FINRA symbols for a Stooq filename stem.

    Stooq writes share classes with a hyphen (``brk-b.us.txt``) while FINRA
    variously uses a dot or nothing at all (``BRK.B`` / ``BRKB``). Returning
    all plausible spellings and matching against the real SI universe is what
    keeps multi-class names from silently dropping out of the overlay.
    """
    s = stem.upper()
    out = [s]
    if "-" in s:
        out.append(s.replace("-", "."))
        out.append(s.replace("-", ""))
    return out


def _parse_stooq_txt(text: str) -> tuple[list[str], list[float]]:
    """Return (YYYYMMDD dates, closes) from one Stooq file.

    Handles both layouts seen in the wild: the bulk archive's
    ``<TICKER>,<PER>,<DATE>,...`` header with YYYYMMDD dates, and the
    per-ticker web export's ``Date,Open,...`` header with YYYY-MM-DD.
    """
    lines = text.strip().splitlines()
    if len(lines) < 2:
        return [], []
    header = [h.strip().strip("<>").upper() for h in lines[0].split(",")]
    try:
        di = header.index("DATE")
        ci = header.index("CLOSE")
    except ValueError:
        return [], []

    dates: list[str] = []
    closes: list[float] = []
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) <= max(di, ci):
            continue
        d = parts[di].strip().replace("-", "")
        if len(d) != 8 or not d.isdigit():
            continue
        try:
            c = float(parts[ci])
        except ValueError:
            continue
        if c > 0:
            dates.append(d)
            closes.append(c)
    return dates, closes


def load_stooq_zip(zip_path: Path, grid: list[str], universe: set[str],
                   done: set[str]):
    """Yield (ticker, rows) for every US ticker in the bulk archive.

    Only tickers present in ``universe`` are emitted — the archive carries
    thousands of names the SI history has never seen.
    """
    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        names = [
            n for n in zf.namelist()
            if n.lower().endswith(".txt") and "/us/" in n.lower().replace("\\", "/")
        ]
        if not names:   # some archives omit the /us/ path segment
            names = [n for n in zf.namelist() if n.lower().endswith(".us.txt")]
        print(f"  {len(names):,} US ticker files in archive")

        matched = skipped = empty = 0
        for n in names:
            stem = Path(n).name
            for suffix in (".us.txt", ".txt"):
                if stem.lower().endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break

            sym = next((v for v in stooq_variants(stem) if v in universe), None)
            if sym is None:
                skipped += 1
                continue
            if sym in done:
                continue

            try:
                text = zf.read(n).decode("utf-8", errors="replace")
            except (KeyError, zipfile.BadZipFile):
                continue
            dates, closes = _parse_stooq_txt(text)
            if not dates:
                empty += 1
                continue

            rows = sample_at_settlements(dates, closes, [None] * len(closes), grid)
            if rows:
                matched += 1
                yield sym, rows

        print(f"  matched {matched:,} to the SI universe; "
              f"{skipped:,} not in universe; {empty:,} unparseable")


# ------------------------------------------------------------ settlements

def settlement_dates() -> list[str]:
    """The canonical settlement grid, as YYYYMMDD strings.

    Prefers the dashboard's own RAW.dates (guarantees index alignment with
    what the overlay will patch); falls back to the history CSV.
    """
    if DASHBOARD.exists():
        try:
            import re
            with open(DASHBOARD, encoding="utf-8") as f:
                head = f.read(200_000)      # RAW.dates sits at the top of RAW
            m = re.search(r'"dates":\[([^\]]+)\]', head)
            if m:
                ds = [d.strip().strip('"') for d in m.group(1).split(",")]
                ds = [d for d in ds if d.isdigit()]
                if ds:
                    print(f"Settlement grid: {len(ds)} dates from dashboard RAW.dates")
                    return ds
        except OSError:
            pass

    if not HISTORY_CSV.exists():
        raise SystemExit(
            f"ERROR: need either {DASHBOARD.name} or {HISTORY_CSV.name} "
            f"to know the settlement grid"
        )
    seen: set[str] = set()
    for ch in pd.read_csv(HISTORY_CSV, usecols=["settlementDate"], dtype=str,
                          chunksize=500_000):
        seen.update(ch["settlementDate"].dropna().astype(str).unique().tolist())
    ds = sorted(d for d in seen if d.isdigit())
    print(f"Settlement grid: {len(ds)} dates from {HISTORY_CSV.name}")
    return ds


def universe_from_history(limit: int | None = None) -> list[str]:
    """Every ticker that appears in the SI history."""
    if not HISTORY_CSV.exists():
        raise SystemExit(f"ERROR: {HISTORY_CSV} not found")
    seen: set[str] = set()
    for ch in pd.read_csv(HISTORY_CSV, usecols=["symbolCode"], dtype=str,
                          chunksize=500_000):
        seen.update(ch["symbolCode"].dropna().astype(str).str.strip().unique().tolist())
    out = sorted(t for t in seen if t)
    return out[:limit] if limit else out


# ------------------------------------------------------------------ fetch

def fetch_one(ticker: str, p1: int, p2: int, timeout: float = 25.0):
    """Return (dates, adj_closes, raw_closes) or None if unavailable."""
    url = CHART_URL.format(tk=urllib.parse.quote(ticker), p1=p1, p2=p2)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (404, 401):
            return None                      # delisted / not covered
        raise
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    try:
        res = payload["chart"]["result"][0]
        ts = res["timestamp"]
        quote = res["indicators"]["quote"][0]
        raw = quote.get("close") or []
        adj_block = res["indicators"].get("adjclose") or [{}]
        adj = adj_block[0].get("adjclose") or raw
    except (KeyError, IndexError, TypeError):
        return None

    dates = [
        datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y%m%d") for t in ts
    ]
    return dates, adj, raw


def sample_at_settlements(dates, adj, raw, grid: list[str]) -> list[tuple]:
    """As-of join: for each settlement, the last close on or before it."""
    rows, i, last_a, last_r = [], 0, None, None
    n = len(dates)
    for g in grid:
        while i < n and dates[i] <= g:
            a, r = adj[i], raw[i]
            if a is not None:
                last_a = a
            if r is not None:
                last_r = r
            i += 1
        if last_a is not None:
            rows.append((g, round(float(last_a), 4),
                         round(float(last_r), 4) if last_r is not None else None))
    return rows


# ------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fetch settlement-aligned prices")
    ap.add_argument("--source", choices=["yahoo", "stooq"], default="yahoo",
                    help="stooq = one bulk archive (fast); yahoo = per-ticker (slow)")
    ap.add_argument("--stooq-zip", default=None,
                    help="path to d_us_txt.zip downloaded from https://stooq.com/db/h/")
    ap.add_argument("--tickers", default=None, help="comma-separated; default = SI universe")
    ap.add_argument("--limit", type=int, default=None, help="cap the universe (testing)")
    ap.add_argument("--pause", type=float, default=0.25, help="seconds between requests")
    ap.add_argument("--out", default=str(OUT_CSV))
    ap.add_argument("--restart", action="store_true", help="ignore existing output and refetch all")
    args = ap.parse_args(argv)

    out_path = Path(args.out)
    grid = settlement_dates()
    p1 = int(datetime.strptime(grid[0], "%Y%m%d").replace(tzinfo=timezone.utc).timestamp()) - 86400 * 10
    p2 = int(datetime.strptime(grid[-1], "%Y%m%d").replace(tzinfo=timezone.utc).timestamp()) + 86400 * 5

    if args.tickers:
        universe = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        universe = universe_from_history(args.limit)
    print(f"Universe: {len(universe):,} tickers")

    # Resume: skip tickers already written.
    done: set[str] = set()
    if out_path.exists() and not args.restart:
        try:
            prior = pd.read_csv(out_path, usecols=["ticker"], dtype=str)
            done = set(prior["ticker"].dropna().unique().tolist())
            print(f"Resuming — {len(done):,} tickers already fetched")
        except (OSError, ValueError, KeyError):
            done = set()

    todo = [t for t in universe if t not in done]
    if not todo:
        print("Nothing to do — every ticker already fetched.")
        return 0
    print(f"To fetch: {len(todo):,}\n")

    write_header = args.restart or not out_path.exists()
    mode = "w" if write_header else "a"
    ok = miss = err = 0
    t0 = time.time()

    # ---------------------------------------------------------- stooq bulk
    if args.source == "stooq":
        if not args.stooq_zip:
            print("ERROR: --source stooq needs --stooq-zip PATH\n"
                  "Download the daily US TXT archive from https://stooq.com/db/h/ "
                  "in a browser, then pass its path.", file=sys.stderr)
            return 2
        zip_path = Path(args.stooq_zip)
        if not zip_path.exists():
            print(f"ERROR: {zip_path} not found", file=sys.stderr)
            return 2

        print(f"Reading {zip_path.name} ({zip_path.stat().st_size/1e6:.0f} MB) ...")
        with open(out_path, mode, newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if write_header:
                w.writerow(FIELDNAMES)
            for sym, rows in load_stooq_zip(zip_path, grid, set(universe), done):
                # Stooq bars are split-adjusted but not dividend-adjusted, so
                # close_raw stays empty rather than claiming a tape price.
                w.writerows([(sym, d, a, "", "stooq") for d, a, _ in rows])
                ok += 1
                if ok % 500 == 0:
                    fh.flush()
                    print(f"  wrote {ok:,} tickers")
        print(f"\nWrote {out_path}  ({out_path.stat().st_size/1e6:.1f} MB)")
        print(f"  tickers: {ok:,}   elapsed: {(time.time()-t0)/60:.1f} min")
        print("\nNEXT: python add_price_overlay.py")
        return 0

    # -------------------------------------------------------------- yahoo
    with open(out_path, mode, newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if write_header:
            w.writerow(FIELDNAMES)

        for n, tk in enumerate(todo, 1):
            try:
                got = fetch_one(tk, p1, p2)
            except Exception as e:                    # noqa: BLE001 - keep going
                err += 1
                print(f"  ! {tk}: {type(e).__name__}: {e}")
                got = None

            if got:
                rows = sample_at_settlements(*got, grid)
                if rows:
                    w.writerows([(tk, d, a, r, "yahoo") for d, a, r in rows])
                    ok += 1
                else:
                    miss += 1
            else:
                miss += 1

            if n % 100 == 0:
                fh.flush()
                rate = n / max(time.time() - t0, 1e-9)
                eta = (len(todo) - n) / max(rate, 1e-9) / 60
                print(f"  {n:,}/{len(todo):,}  ok={ok:,} miss={miss:,} err={err:,}"
                      f"  {rate:.1f}/s  eta {eta:.0f}m")
            time.sleep(args.pause)

    print(f"\nWrote {out_path}  ({out_path.stat().st_size/1e6:.1f} MB)")
    print(f"  fetched: {ok:,}   no data: {miss:,}   errors: {err:,}")
    print(f"  elapsed: {(time.time()-t0)/60:.1f} min")
    print("\nNEXT: python add_price_overlay.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
