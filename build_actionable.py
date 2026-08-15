#!/usr/bin/env python3
"""Actionable shorts screen — SI conviction × borrow feasibility.

The candidates engine (Signal D) answers "which names look structurally
short-worthy?" It says nothing about whether the trade can actually be put
on. This screen joins the borrow layer onto it and answers the second
question: of the conviction names, which are borrowable, at what cost, and
which are already so crowded that the borrow itself is the risk.

Inputs
  analytics/candidates.parquet   — canonical, built by update_analytics.py
  borrow.db                      — read-only, borrow data only (see analytics/borrow.py)

Output
  exports/{YYYYMMDD}/actionable_shorts.csv
  exports/latest/actionable_shorts.csv

Flags attached to each row (descriptive, not gates):
  EARLY       SI signal firing while the borrow is still cheap and in the low
              half of its own year — the crowd has not arrived yet.
  CROWDED     Fee in the top decile of its own year, or above the crowded
              cutoff outright. Squeeze risk; the cost of being wrong is high.
  TIGHTENING  Fee rising sharply over ~1 month. Borrow is daily and FINRA is
              bi-weekly and lagged ~9 days, so a fee spike against flat SI is
              often the first visible sign of the next print.
  SHRINKING   Share availability well below its 1-month average.
  NO_BORROW   Not found in either borrow layer (see coverage note below).

Coverage note: the daily backfill covers roughly 3,600 symbols (capped near
$300mm market cap), while the SI universe is ~13,800. Names outside it are
NOT bad borrows — they are unmeasured. They are dropped by default and
counted explicitly in the run summary; pass --include-uncovered to keep them.

Usage
  python build_actionable.py
  python build_actionable.py --max-fee 5 --min-available 50000 --top 40
  python build_actionable.py --include-uncovered --use-mcap
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from analytics import CANDIDATES_PARQUET, EXPORTS_DIR, BORROW_DB
from analytics.borrow import build_borrow_features, load_market_cap_snapshot

# ------------------------------------------------------------- defaults
# Fees are annualised percent, as IBKR publishes them (0.46 = 0.46%/yr).
DEFAULT_MAX_FEE = 10.0
DEFAULT_MIN_AVAILABLE = 25_000
DEFAULT_MAX_FEED_AGE_H = 48.0

CROWDED_FEE = 25.0
CROWDED_PCTILE = 0.90
EARLY_FEE = 5.0
EARLY_PCTILE = 0.50
TIGHTENING_FEE_PTS = 2.0
TIGHTENING_Z = 2.0
SHRINKING_AVAIL = -0.50

# actionable_score blend: conviction vs cost.
W_SI = 0.70
W_BORROW = 0.30

OUT_COLS = [
    "ticker", "name", "sector", "mc_bucket",
    "actionable_score", "flags",
    "composite_score", "decile",
    "si_pct_float", "si_shares", "dtc", "cover_velocity",
    "fee_eff", "fee_pctile_1y", "fee_chg_20d", "fee_z_60d", "fee_20d",
    "avail_eff", "avail_chg_20d_pct", "avail_pctile_1y",
    "borrow_obs_days", "borrow_stale_days", "has_daily", "has_live",
    "market_cap", "last_sale", "instrument_type",
    "settlement_date", "notes",
]


def _pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, method="average")


def build_flags(df: pd.DataFrame) -> pd.Series:
    """Descriptive tags per row, joined with '|'. Empty when nothing applies."""
    fee = df["fee_eff"]
    pctile = df["fee_pctile_1y"]

    early = (fee < EARLY_FEE) & (pctile.fillna(1.0) < EARLY_PCTILE)
    crowded = (fee >= CROWDED_FEE) | (pctile.fillna(0.0) >= CROWDED_PCTILE)
    tightening = (
        (df["fee_chg_20d"].fillna(0) >= TIGHTENING_FEE_PTS)
        | (df["fee_z_60d"].fillna(0) >= TIGHTENING_Z)
    )
    shrinking = df["avail_chg_20d_pct"].fillna(0) <= SHRINKING_AVAIL
    no_borrow = ~(df["has_daily"].fillna(False) | df["has_live"].fillna(False))

    parts = pd.DataFrame({
        "EARLY": early & ~crowded,      # a crowded name is never "early"
        "CROWDED": crowded,
        "TIGHTENING": tightening,
        "SHRINKING": shrinking,
        "NO_BORROW": no_borrow,
    })
    return parts.apply(lambda r: "|".join([c for c in parts.columns if r[c]]), axis=1)


def apply_borrow_gates(df: pd.DataFrame, args) -> pd.DataFrame:
    """Attach borrow_gates_passed + borrow_gate_failures."""
    fee = df["fee_eff"]
    avail = df["avail_eff"]

    has_any = df["has_daily"].fillna(False) | df["has_live"].fillna(False)
    # present is NaN for daily-only rows; treat missing as "not disqualifying"
    present_ok = df["present"].fillna(1).astype(float) == 1
    fee_ok = fee.notna() & (fee <= args.max_fee)
    avail_ok = avail.fillna(0) >= args.min_available

    if "feed_age_h" in df:
        fresh_ok = df["feed_age_h"].isna() | (df["feed_age_h"] <= args.max_feed_age_h)
    else:
        fresh_ok = pd.Series(True, index=df.index)

    passed = has_any & present_ok & fee_ok & avail_ok & fresh_ok

    fails = pd.DataFrame({
        "no_borrow_data": ~has_any,
        "not_present": ~present_ok,
        "fee_too_high": has_any & ~fee_ok,
        "insufficient_availability": has_any & ~avail_ok,
        "stale_feed": ~fresh_ok,
    })
    df = df.copy()
    df["borrow_gates_passed"] = passed
    df["borrow_gate_failures"] = fails.apply(
        lambda r: ",".join([c for c in fails.columns if r[c]]), axis=1
    )
    return df


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Build the actionable shorts screen")
    p.add_argument("--max-fee", type=float, default=DEFAULT_MAX_FEE,
                   help=f"max annualised borrow fee %%, default {DEFAULT_MAX_FEE}")
    p.add_argument("--min-available", type=int, default=DEFAULT_MIN_AVAILABLE,
                   help=f"min shares available, default {DEFAULT_MIN_AVAILABLE:,}")
    p.add_argument("--max-feed-age-h", type=float, default=DEFAULT_MAX_FEED_AGE_H,
                   help="max age of the live feed row, hours")
    p.add_argument("--include-uncovered", action="store_true",
                   help="keep candidates with no borrow coverage (flagged NO_BORROW)")
    p.add_argument("--use-mcap", action="store_true",
                   help="join borrow.db's nasdaq_screener snapshot for "
                        "instrument_type / last_sale (non-canonical)")
    p.add_argument("--common-only", action="store_true",
                   help="with --use-mcap, keep instrument_type == COMMON only")
    p.add_argument("--top", type=int, default=50, help="rows to print, default 50")
    p.add_argument("--db", default=None, help="path to borrow.db (overrides env)")
    args = p.parse_args(argv)

    print("=" * 74)
    print("ACTIONABLE SHORTS SCREEN")
    print("=" * 74)

    if not CANDIDATES_PARQUET.exists():
        print(f"ERROR: {CANDIDATES_PARQUET} not found — run update_analytics.py first",
              file=sys.stderr)
        return 1

    t0 = time.time()
    cands = pd.read_parquet(CANDIDATES_PARQUET)
    asof = cands["settlement_date"].max()
    snap = cands[(cands["settlement_date"] == asof) & cands["gates_passed"]].copy()
    print(f"\nCandidates: {len(cands):,} rows; as-of {pd.Timestamp(asof).date()}, "
          f"{len(snap):,} passed SI gates  ({time.time()-t0:.1f}s)")

    t0 = time.time()
    print(f"Borrow DB:  {Path(args.db) if args.db else BORROW_DB}")
    borrow = build_borrow_features(db_path=args.db)
    print(f"Borrow:     {len(borrow):,} tickers "
          f"({int(borrow['has_daily'].sum()):,} daily, "
          f"{int(borrow['has_live'].sum()):,} live)  ({time.time()-t0:.1f}s)")

    # ------------------------------------------------------------- join
    df = snap.merge(borrow, on="ticker", how="left")
    df["has_daily"] = df["has_daily"].fillna(False)
    df["has_live"] = df["has_live"].fillna(False)

    covered = int((df["has_daily"] | df["has_live"]).sum())
    print(f"\nJoin coverage: {covered:,} / {len(df):,} candidates have borrow data "
          f"({covered / max(len(df), 1):.0%})")
    print(f"  daily history: {int(df['has_daily'].sum()):,}")
    print(f"  live feed:     {int(df['has_live'].sum()):,}")
    print(f"  neither:       {len(df) - covered:,}  "
          f"({'kept, flagged NO_BORROW' if args.include_uncovered else 'dropped'})")

    if "feed_ts" in df:
        now = df["feed_ts"].max()
        df["feed_age_h"] = (now - df["feed_ts"]).dt.total_seconds() / 3600.0

    # Optional non-canonical metadata
    if args.use_mcap:
        mc = load_market_cap_snapshot(db_path=args.db)
        df = df.merge(mc, on="ticker", how="left")
        if args.common_only:
            before = len(df)
            df = df[df["instrument_type"].fillna("COMMON") == "COMMON"]
            print(f"  --common-only dropped {before - len(df):,} non-common instruments")
    else:
        for c in ("market_cap", "last_sale", "instrument_type"):
            if c not in df:
                df[c] = pd.NA

    # ------------------------------------------------------------ gates
    df = apply_borrow_gates(df, args)
    df["flags"] = build_flags(df)

    if not args.include_uncovered:
        df = df[df["has_daily"] | df["has_live"]]

    screened = df[df["borrow_gates_passed"]].copy()

    print(f"\nBorrow gates (fee <= {args.max_fee}%, available >= {args.min_available:,}):")
    print(f"  passed: {len(screened):,}")
    fail_counts: dict[str, int] = {}
    for s in df.loc[~df["borrow_gates_passed"], "borrow_gate_failures"]:
        for f in filter(None, str(s).split(",")):
            fail_counts[f] = fail_counts.get(f, 0) + 1
    for k, v in sorted(fail_counts.items(), key=lambda kv: -kv[1]):
        print(f"    {k}: {v:,}")

    if screened.empty:
        print("\nNothing passed. Loosen --max-fee / --min-available, or check "
              "that the borrow backfill covers this universe.")
        return 0

    # ------------------------------------------------------------ score
    si_pct = _pct_rank(screened["composite_score"])
    # Lower fee is better, so invert the cost rank.
    cost_pct = 1.0 - _pct_rank(screened["fee_eff"])
    screened["actionable_score"] = 100 * (W_SI * si_pct + W_BORROW * cost_pct)
    screened = screened.sort_values("actionable_score", ascending=False)

    # ----------------------------------------------------------- export
    asof_str = pd.Timestamp(asof).strftime("%Y%m%d")
    out_dir = EXPORTS_DIR / asof_str
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = [c for c in OUT_COLS if c in screened.columns]
    out_path = out_dir / "actionable_shorts.csv"
    screened[cols].to_csv(out_path, index=False, float_format="%.6f")
    print(f"\nWrote {out_path}  ({len(screened):,} rows)")

    latest_dir = EXPORTS_DIR / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out_path, latest_dir / "actionable_shorts.csv")
    print(f"Mirrored to {latest_dir / 'actionable_shorts.csv'}")

    manifest = out_dir / "actionable_manifest.txt"
    with open(manifest, "w", encoding="utf-8") as f:
        f.write("Actionable Shorts Screen\n")
        f.write(f"Generated:        {datetime.now().isoformat()}\n")
        f.write(f"SI as-of:         {pd.Timestamp(asof).date()}\n")
        f.write(f"Borrow DB:        {Path(args.db) if args.db else BORROW_DB}\n")
        f.write(f"Gates:            fee<={args.max_fee}%, available>={args.min_available}, "
                f"feed_age<={args.max_feed_age_h}h\n")
        f.write(f"Score weights:    SI {W_SI}, borrow cost {W_BORROW}\n")
        f.write(f"SI candidates:    {len(snap):,}\n")
        f.write(f"Borrow coverage:  {covered:,} ({covered/max(len(snap),1):.0%})\n")
        f.write(f"Passed:           {len(screened):,}\n")
        f.write("Flag counts:\n")
        for tag in ("EARLY", "CROWDED", "TIGHTENING", "SHRINKING", "NO_BORROW"):
            f.write(f"  {tag}: {screened['flags'].str.contains(tag).sum():,}\n")
    print(f"Wrote {manifest.name}")

    # ---------------------------------------------------------- console
    print(f"\nFlag counts (screened set):")
    for tag in ("EARLY", "CROWDED", "TIGHTENING", "SHRINKING"):
        print(f"  {tag}: {screened['flags'].str.contains(tag).sum():,}")

    show = [c for c in ["ticker", "name", "actionable_score", "composite_score",
                        "si_pct_float", "fee_eff", "fee_pctile_1y", "fee_chg_20d",
                        "avail_eff", "flags"] if c in screened.columns]
    pd.set_option("display.width", 220)
    pd.set_option("display.max_colwidth", 28)
    print(f"\nTop {min(args.top, len(screened))} actionable shorts:")
    print(screened.head(args.top)[show].to_string(index=False))

    early = screened[screened["flags"].str.contains("EARLY")]
    if len(early):
        print(f"\nEARLY (cheap borrow, crowd not there yet) — top 15:")
        print(early.head(15)[show].to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
