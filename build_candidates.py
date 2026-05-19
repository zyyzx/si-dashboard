#!/usr/bin/env python3
"""Score features into candidate signals.

Reads:  analytics/features.parquet
Writes: analytics/candidates.parquet

Phase 1A — Signal D (sector & history outlier short) only.
Phase 1B will add Signal A (crowded-short fade long) once prices land.
"""

import time

import pandas as pd

from analytics.score import score_all
from analytics import FEATURES_PARQUET, CANDIDATES_PARQUET


def main() -> None:
    print("=" * 70)
    print("BUILD CANDIDATES (Phase 1A)")
    print("=" * 70)

    t0 = time.time()
    feats = pd.read_parquet(FEATURES_PARQUET)
    print(f"\nLoaded features: {len(feats):,} rows  ({time.time()-t0:.1f}s)")

    t0 = time.time()
    cands = score_all(feats)
    print(f"Scored: {len(cands):,} rows  ({time.time()-t0:.1f}s)")
    passed = cands["gates_passed"].sum()
    print(f"  passed gates: {passed:,}  ({passed/len(cands)*100:.1f}%)")
    print(f"  by signal_type:")
    for st, n in cands[cands["gates_passed"]]["signal_type"].value_counts().items():
        print(f"    {st}: {n:,}")

    cands.to_parquet(CANDIDATES_PARQUET, index=False)
    print(f"\nWrote {CANDIDATES_PARQUET}  ({CANDIDATES_PARQUET.stat().st_size / 1e6:.1f} MB)")

    # Latest snapshot summary
    latest = cands["settlement_date"].max()
    snap = cands[(cands["settlement_date"] == latest) & cands["gates_passed"]]
    print(f"\nLatest snapshot {latest.date()}: {len(snap):,} candidates passed gates")
    if len(snap):
        per_sig = snap.groupby("signal_type").size()
        for st, n in per_sig.items():
            print(f"  {st}: top decile = {(snap[snap['signal_type']==st]['decile']==10).sum():,}")


if __name__ == "__main__":
    main()
