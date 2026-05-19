"""Feature engineering for SI candidate generation.

Computes per-(ticker, settlement_date) features from the joined SI +
sector + float panel. Phase 1A is SI-only — no price-derived features
yet (those land in Phase 1B with yfinance integration).

All cross-sectional features (sector medians, sector-Z) are computed
per (settlement_date, sector) and broadcast back to each row, so
ranks at any single asof are apples-to-apples.

Public surface:
    add_self_history_features(df) -> df with z-scores + percentile-of-own-history
    add_sector_relative_features(df) -> df with sector median / Z / ratio
    add_cover_velocity(df) -> df with cover_velocity column
    build_features(df=None) -> end-to-end DataFrame with all Phase-1A features
"""

from __future__ import annotations

import pandas as pd

from .loaders import load_joined_si_with_meta

# FINRA bi-weekly cadence:
#   6 months  ≈ 13 settlements
#   36 months ≈ 78 settlements
WIN_6M = 13
WIN_36M = 78

# Cover velocity lookback: 2 periods ≈ 1 month
COVER_VEL_LAG = 2


# ----------------------------- self-history ---------------------------

def _rolling_z(s: pd.Series, window: int) -> pd.Series:
    """Rolling Z-score: (current - mean) / std over a trailing window.

    Uses min_periods = max(5, window // 3) to avoid all-NaN early periods
    while still requiring enough data to make the Z meaningful.
    """
    minp = max(5, window // 3)
    mean = s.rolling(window, min_periods=minp).mean()
    std = s.rolling(window, min_periods=minp).std(ddof=1)
    return (s - mean) / std


def _rolling_pct_rank(s: pd.Series, window: int) -> pd.Series:
    """Percentile rank of current value vs the trailing-window distribution.

    Returns a value in [0, 1]; NaN if window has < 5 observations.
    """
    minp = max(5, window // 3)

    def _pct(x):
        if x.size < minp:
            return float("nan")
        # rank of last value among the window (1 = lowest, n = highest)
        last = x.iloc[-1]
        return (x <= last).sum() / x.size

    return s.rolling(window, min_periods=minp).apply(_pct, raw=False)


def add_self_history_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add rolling-window features computed per ticker over its own history.

    Adds: si_shares_z6m, si_pct_float_z6m, si_pct_float_z36m,
          si_pct_float_history_pct, si_shares_history_pct,
          n_history_obs (count of prior settlements for this ticker)
    """
    df = df.sort_values(["ticker", "settlement_date"]).reset_index(drop=True)
    g = df.groupby("ticker", sort=False)

    df["si_shares_z6m"] = g["si_shares"].transform(lambda s: _rolling_z(s, WIN_6M))
    df["si_pct_float_z6m"] = g["si_pct_float"].transform(lambda s: _rolling_z(s, WIN_6M))
    df["si_pct_float_z36m"] = g["si_pct_float"].transform(lambda s: _rolling_z(s, WIN_36M))

    # Percentile-of-own-history (3-year window for outlier signal)
    df["si_pct_float_history_pct"] = g["si_pct_float"].transform(
        lambda s: _rolling_pct_rank(s, WIN_36M)
    )
    df["si_shares_history_pct"] = g["si_shares"].transform(
        lambda s: _rolling_pct_rank(s, WIN_36M)
    )

    # Number of prior observations available for this row (for gating)
    df["n_history_obs"] = g.cumcount()  # 0-indexed: 0 means first observation
    return df


# ----------------------------- sector-relative ------------------------

def add_sector_relative_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add cross-sectional features per (settlement_date, sector).

    Adds: sector_median_si_pct_float, sector_mean_si_pct_float,
          sector_std_si_pct_float, si_pct_float_sector_rel,
          si_pct_float_sector_z
    """
    # Compute sector aggregates only on rows with valid SI%float
    valid = df.dropna(subset=["si_pct_float", "sector"])

    sector_stats = (
        valid.groupby(["settlement_date", "sector"])["si_pct_float"]
             .agg(["median", "mean", "std", "count"])
             .reset_index()
             .rename(columns={
                 "median": "sector_median_si_pct_float",
                 "mean": "sector_mean_si_pct_float",
                 "std": "sector_std_si_pct_float",
                 "count": "sector_n",
             })
    )

    df = df.merge(sector_stats, on=["settlement_date", "sector"], how="left")

    df["si_pct_float_sector_rel"] = df["si_pct_float"] / df["sector_median_si_pct_float"]
    df["si_pct_float_sector_z"] = (
        (df["si_pct_float"] - df["sector_mean_si_pct_float"]) / df["sector_std_si_pct_float"]
    )

    # Don't trust sector stats with too few peers
    too_thin = df["sector_n"].fillna(0) < 5
    df.loc[too_thin, ["si_pct_float_sector_rel", "si_pct_float_sector_z"]] = pd.NA

    return df


# ----------------------------- cover velocity -------------------------

def add_cover_velocity(df: pd.DataFrame) -> pd.DataFrame:
    """Δ in SI shares over the last 2 settlement periods (~1 month).

    Negative = shorts covering (bullish for fade-long thesis).
    Positive = shorts increasing (bearish; supports outlier-short thesis).
    """
    df = df.sort_values(["ticker", "settlement_date"]).reset_index(drop=True)
    g = df.groupby("ticker", sort=False)["si_shares"]
    si_lag = g.shift(COVER_VEL_LAG)
    df["cover_velocity"] = (df["si_shares"] - si_lag) / si_lag
    # cap pathological values from tiny denominators
    df["cover_velocity"] = df["cover_velocity"].clip(lower=-5, upper=5)
    return df


# ----------------------------- public entry ---------------------------

def build_features(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Compute the full Phase-1A feature table.

    If ``df`` is None, loads the joined SI+meta+float panel via loaders.
    """
    if df is None:
        df = load_joined_si_with_meta()

    df = add_self_history_features(df)
    df = add_sector_relative_features(df)
    df = add_cover_velocity(df)
    return df


if __name__ == "__main__":
    import time
    from . import FEATURES_PARQUET

    t0 = time.time()
    feats = build_features()
    print(f"Built features for {len(feats):,} rows × {len(feats.columns)} cols  ({time.time()-t0:.1f}s)")

    feats.to_parquet(FEATURES_PARQUET, index=False)
    print(f"Wrote {FEATURES_PARQUET}  ({FEATURES_PARQUET.stat().st_size / 1e6:.1f} MB)")

    # Inspect on the most recent settlement
    latest = feats["settlement_date"].max()
    snap = feats[feats["settlement_date"] == latest]
    print(f"\nLatest snapshot: {latest.date()}  ({len(snap):,} rows)")
    print(f"  rows w/ si_pct_float:        {snap['si_pct_float'].notna().sum():,}")
    print(f"  rows w/ sector_z:            {snap['si_pct_float_sector_z'].notna().sum():,}")
    print(f"  rows w/ history_pct (3y):    {snap['si_pct_float_history_pct'].notna().sum():,}")

    # Example: BRBR
    brbr = feats[feats["ticker"] == "BRBR"].sort_values("settlement_date").tail(3)
    if len(brbr):
        print(f"\nBRBR latest 3 observations:")
        print(brbr[[
            "settlement_date", "sector", "si_shares", "si_pct_float",
            "si_pct_float_sector_rel", "si_pct_float_sector_z",
            "si_pct_float_history_pct", "cover_velocity"
        ]].to_string(index=False))
    else:
        print("\nBRBR not found in feature table.")
