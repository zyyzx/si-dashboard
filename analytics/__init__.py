"""SI candidate-generation analytics layer.

.. deprecated::
   This package's CANONICAL HOME has moved to ``alpha-unified/analytics/``.
   The SI math (``features.py``, ``score.py``) and the loaders now live there and
   read from the DuckDB master (``alpha.duckdb``) raw/reference tiers instead of
   CSV/XLSX. Do NOT edit the math here — change it in alpha-unified to avoid drift.
   This copy is retained only for the legacy single-file dashboard build
   (``build_dashboard.py`` et al.), which will be migrated to read from DuckDB in
   a later pass. See ``alpha-unified/RECOVERY.md``.

Phase 1A — SI-only signals (no prices required):
  - Signal D: sector & history outlier short (BRBR-style names)

Phase 1B (planned) adds yfinance price data and:
  - Signal A: crowded-short fade long

This package consumes ``../si_history_full.csv``,
``../equities.csv``, and ``../capiq_float_historical.xlsx`` read-only,
and writes derived parquet artifacts to ``./`` (this folder).

See ../README and the planning doc at
``~/.claude/plans/review-these-two-folders-ancient-naur.md`` for the
full design.
"""

from pathlib import Path

ANALYTICS_DIR = Path(__file__).parent
TRACKER_DIR = ANALYTICS_DIR.parent

SI_HISTORY_CSV = TRACKER_DIR / "si_history_full.csv"
EQUITIES_CSV = TRACKER_DIR / "equities.csv"
CAPIQ_FLOAT_XLSX = TRACKER_DIR / "capiq_float_historical.xlsx"

FEATURES_PARQUET = ANALYTICS_DIR / "features.parquet"
CANDIDATES_PARQUET = ANALYTICS_DIR / "candidates.parquet"
FLOAT_PANEL_PARQUET = ANALYTICS_DIR / "float_panel.parquet"

EXPORTS_DIR = TRACKER_DIR / "exports"
