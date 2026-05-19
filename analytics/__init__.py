"""SI candidate-generation analytics layer.

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
