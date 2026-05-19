#!/usr/bin/env python3
"""Build the per-(ticker, settlement_date) feature table.

Reads:  si_history_full.csv, equities.csv, capiq_float_historical.xlsx
Writes: analytics/features.parquet

Phase 1A — SI-only features. No price data required.
"""

import time

from analytics.features import build_features
from analytics import FEATURES_PARQUET


def main() -> None:
    print("=" * 70)
    print("BUILD FEATURES (Phase 1A)")
    print("=" * 70)
    t0 = time.time()
    feats = build_features()
    print(f"\nBuilt {len(feats):,} feature rows × {len(feats.columns)} cols  ({time.time()-t0:.1f}s)")

    feats.to_parquet(FEATURES_PARQUET, index=False)
    print(f"Wrote {FEATURES_PARQUET}  ({FEATURES_PARQUET.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
