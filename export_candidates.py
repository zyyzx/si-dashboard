#!/usr/bin/env python3
"""Export the latest candidate snapshot to analyst-friendly files.

Writes:
  exports/YYYYMMDD/candidates_short_outlier.csv  (Signal D — Phase 1A)
  exports/YYYYMMDD/candidates_all.csv            (every signal, full schema)
  exports/YYYYMMDD/manifest.txt                  (run metadata)
  exports/latest/                                 (mirror of latest dated folder)

Where YYYYMMDD = the FINRA settlement date the candidates are as of.
"""

import shutil
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from analytics import CANDIDATES_PARQUET, EXPORTS_DIR


# Columns (in display order) per signal type.
SIGNAL_COLS = {
    "outlier_short": [
        "ticker", "name", "sector", "industry_group", "industry",
        "market_class", "mc_bucket", "market_cap_bucket",
        "composite_score", "decile", "pct_rank",
        "si_pct_float", "si_shares", "dtc",
        "si_pct_float_sector_rel", "si_pct_float_sector_z",
        "si_pct_float_history_pct", "si_pct_float_z36m",
        "cover_velocity", "si_shares_z6m",
        "fc_sector_z", "fc_sector_rel", "fc_history_pct", "fc_cover_velocity",
        "sector_median_si_pct_float", "sector_n",
        "settlement_date", "notes",
    ],
}


def export_signal(snap: pd.DataFrame, signal_type: str, out_dir: Path) -> Path:
    """Write one CSV per signal: filtered, sorted, polished column order."""
    sig = snap[snap["signal_type"] == signal_type].copy()
    sig = sig.sort_values("composite_score", ascending=False)
    cols = [c for c in SIGNAL_COLS.get(signal_type, sig.columns) if c in sig.columns]
    out_path = out_dir / f"candidates_{signal_type}.csv"
    sig[cols].to_csv(out_path, index=False, float_format="%.6f")
    return out_path


def main() -> None:
    print("=" * 70)
    print("EXPORT CANDIDATES")
    print("=" * 70)

    t0 = time.time()
    cands = pd.read_parquet(CANDIDATES_PARQUET)
    print(f"\nLoaded candidates: {len(cands):,} rows  ({time.time()-t0:.1f}s)")

    latest = cands["settlement_date"].max()
    snap = cands[(cands["settlement_date"] == latest) & cands["gates_passed"]].copy()
    asof_str = latest.strftime("%Y%m%d")

    out_dir = EXPORTS_DIR / asof_str
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"As-of date:    {latest.date()}")
    print(f"Output folder: {out_dir}")
    print(f"Rows in snap:  {len(snap):,}")

    # Per-signal CSVs
    for st in sorted(snap["signal_type"].unique()):
        n = (snap["signal_type"] == st).sum()
        path = export_signal(snap, st, out_dir)
        print(f"  wrote {path.name:<40s} ({n:,} rows)")

    # All-candidates CSV (single sheet, every gate-passing row)
    all_path = out_dir / "candidates_all.csv"
    snap.sort_values(["signal_type", "composite_score"], ascending=[True, False]).to_csv(
        all_path, index=False, float_format="%.6f"
    )
    print(f"  wrote {all_path.name:<40s} ({len(snap):,} rows)")

    # Manifest
    manifest = out_dir / "manifest.txt"
    with open(manifest, "w", encoding="utf-8") as f:
        f.write(f"SI Candidate Export Manifest\n")
        f.write(f"Generated:    {datetime.now().isoformat()}\n")
        f.write(f"As-of:        {latest.date()}\n")
        f.write(f"Pipeline:     Phase 1A (SI-only; Signal D only)\n")
        f.write(f"\n")
        f.write(f"Source files:\n")
        f.write(f"  candidates.parquet rows: {len(cands):,}\n")
        f.write(f"  passed gates:            {cands['gates_passed'].sum():,}\n")
        f.write(f"  in latest snapshot:      {len(snap):,}\n")
        f.write(f"\n")
        f.write(f"Per-signal breakdown:\n")
        for st, n in snap["signal_type"].value_counts().items():
            top10 = (snap[snap["signal_type"] == st]["decile"] == 10).sum()
            f.write(f"  {st}: {n:,} candidates, {top10:,} in top decile\n")
    print(f"  wrote {manifest.name}")

    # Mirror to exports/latest/
    latest_dir = EXPORTS_DIR / "latest"
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    shutil.copytree(out_dir, latest_dir)
    print(f"\nMirrored to {latest_dir}")


if __name__ == "__main__":
    main()
