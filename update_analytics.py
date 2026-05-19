#!/usr/bin/env python3
"""End-to-end refresh of the SI candidate-generation pipeline.

Chains:
  build_features  ->  build_candidates  ->  export_candidates

Note: This wrapper does NOT run fetch_short_interest.py. Run that
separately (or via a cron) on FINRA publish days; this pipeline reads
whatever is currently in si_history_full.csv.

Phase 1A — Signal D (sector & history outlier short) only.
"""

import sys
import time

import build_features
import build_candidates
import export_candidates


def main() -> int:
    overall_t0 = time.time()
    try:
        build_features.main()
        build_candidates.main()
        export_candidates.main()
    except Exception as e:
        print(f"\nERROR: pipeline failed at runtime: {e}", file=sys.stderr)
        raise

    print(f"\n{'=' * 70}")
    print(f"FULL PIPELINE COMPLETE  ({time.time()-overall_t0:.1f}s)")
    print(f"{'=' * 70}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
