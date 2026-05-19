"""Composite scoring for SI candidate signals.

Phase 1A implements Signal D — sector & history outlier short — the
BRBR-style signal that surfaces names whose absolute SI% is modest
but whose SI is structurally elevated relative to peers and to the
ticker's own history.

Scoring approach: rank-percentile composite, equal-weighted within
signal, computed cross-sectionally per (settlement_date, market_class)
so micro-caps don't dominate. Each factor → percentile rank in [0,1];
composite = 100 × mean of factor ranks. Hard gates are applied
separately and recorded so the analyst sees why a name was excluded.

Public surface:
    score_outlier_short(features) -> DataFrame with composite + gates
    score_all(features) -> concatenated DataFrame with all signal scores
"""

from __future__ import annotations

import pandas as pd

# Signal-D gates
GATE_SECTOR_REL_OR_Z = {"sector_rel": 1.5, "sector_z": 1.5}  # either/or
GATE_HISTORY_PCT = 0.80                                       # in top quintile of own history
GATE_MIN_HISTORY_OBS = 78                                     # ≥ 3y of bi-weekly observations
GATE_MIN_SECTOR_PEERS = 5                                     # min peers for trustworthy sector stats

# Market-class buckets used as the cross-sectional ranking universe.
# Actual FINRA marketClassCode values seen in si_history_full.csv:
#   NNM (NASDAQ NMS), NYSE, AMEX (NYSE American), ARCA (NYSE Arca/ETFs),
#   BZX (Cboe BZX/ETFs), SC (NASDAQ SmallCap), OTC.
# We bucket coarsely so SmallCap doesn't pollute large/mid-cap ranks.
NMS_CLASSES = {"NNM", "NYSE", "AMEX", "ARCA", "BZX"}
SC_CLASSES = {"SC"}
OTC_CLASSES = {"OTC"}


def _pct_rank(s: pd.Series) -> pd.Series:
    """Cross-sectional percentile rank in [0, 1]; NaN preserved."""
    return s.rank(pct=True, method="average")


def _bucket_market_class(mc: str | None) -> str:
    if not isinstance(mc, str):
        return "OTHER"
    if mc in NMS_CLASSES:
        return "NMS"
    if mc in SC_CLASSES:
        return "SC"
    if mc in OTC_CLASSES:
        return "OTC"
    return "OTHER"


def score_outlier_short(features: pd.DataFrame) -> pd.DataFrame:
    """Score Signal D — sector & history outlier short.

    Returns the full features DataFrame augmented with:
      mc_bucket, gates_passed, gate_failures (list[str]),
      fc_sector_z, fc_sector_rel, fc_history_pct, fc_cover_velocity,
        (factor contributions = percentile ranks ∈ [0,1])
      composite_score (0..100), pct_rank, decile,
      signal_type ("outlier_short"), notes (one-line human readable)
    """
    df = features.copy()
    df["signal_type"] = "outlier_short"
    df["mc_bucket"] = df["market_class"].map(_bucket_market_class)

    # -------------------- gates --------------------
    # Use fillna with neutral values so gate logic doesn't crash on NaN.
    sector_rel_ok = df["si_pct_float_sector_rel"].fillna(-1) >= GATE_SECTOR_REL_OR_Z["sector_rel"]
    sector_z_ok = df["si_pct_float_sector_z"].fillna(-99) >= GATE_SECTOR_REL_OR_Z["sector_z"]
    relative_ok = sector_rel_ok | sector_z_ok

    history_ok = df["si_pct_float_history_pct"].fillna(-1) >= GATE_HISTORY_PCT
    history_obs_ok = df["n_history_obs"].fillna(0) >= GATE_MIN_HISTORY_OBS
    sector_peers_ok = df["sector_n"].fillna(0) >= GATE_MIN_SECTOR_PEERS

    has_si_pct_float = df["si_pct_float"].notna()

    df["gates_passed"] = (
        relative_ok & history_ok & history_obs_ok & sector_peers_ok & has_si_pct_float
    )

    # Record which gates failed for audit
    def _failures(row) -> list[str]:
        f = []
        if not isinstance(row["si_pct_float"], float) or pd.isna(row["si_pct_float"]):
            f.append("no_float")
        if not (
            (row["si_pct_float_sector_rel"] is not None
             and not pd.isna(row["si_pct_float_sector_rel"])
             and row["si_pct_float_sector_rel"] >= GATE_SECTOR_REL_OR_Z["sector_rel"])
            or
            (row["si_pct_float_sector_z"] is not None
             and not pd.isna(row["si_pct_float_sector_z"])
             and row["si_pct_float_sector_z"] >= GATE_SECTOR_REL_OR_Z["sector_z"])
        ):
            f.append("not_sector_outlier")
        if pd.isna(row["si_pct_float_history_pct"]) or row["si_pct_float_history_pct"] < GATE_HISTORY_PCT:
            f.append("not_history_outlier")
        if pd.isna(row["n_history_obs"]) or row["n_history_obs"] < GATE_MIN_HISTORY_OBS:
            f.append("insufficient_history")
        if pd.isna(row["sector_n"]) or row["sector_n"] < GATE_MIN_SECTOR_PEERS:
            f.append("thin_sector")
        return f

    # Only annotate failures for rows that didn't pass — saves time on the apply
    df["gate_failures"] = ""
    failed_idx = df.index[~df["gates_passed"]]
    if len(failed_idx) > 0:
        df.loc[failed_idx, "gate_failures"] = (
            df.loc[failed_idx].apply(_failures, axis=1).map(lambda xs: ",".join(xs))
        )

    # -------------------- composite score --------------------
    # Compute percentile ranks WITHIN (settlement_date, mc_bucket) for
    # the universe of rows that pass gates. Rows that fail gates get
    # composite_score = NaN.
    gated = df[df["gates_passed"]].copy()

    # Factor 1: sector_z (high = more outlier vs current peers)
    # Factor 2: sector_rel (high = more multiple of sector median)
    # Factor 3: history_pct (high = at top of own history)
    # Factor 4: cover_velocity (high = SI growing, not covering)
    rank_groups = gated.groupby(["settlement_date", "mc_bucket"], sort=False)

    gated["fc_sector_z"] = rank_groups["si_pct_float_sector_z"].transform(_pct_rank)
    gated["fc_sector_rel"] = rank_groups["si_pct_float_sector_rel"].transform(_pct_rank)
    gated["fc_history_pct"] = rank_groups["si_pct_float_history_pct"].transform(_pct_rank)
    gated["fc_cover_velocity"] = rank_groups["cover_velocity"].transform(_pct_rank)

    factor_cols = ["fc_sector_z", "fc_sector_rel", "fc_history_pct", "fc_cover_velocity"]
    gated["composite_score"] = 100 * gated[factor_cols].mean(axis=1, skipna=True)

    # Universe-level percentile + decile (within asof × mc_bucket)
    gated["pct_rank"] = rank_groups["composite_score"].transform(_pct_rank)
    gated["decile"] = (
        (gated["pct_rank"] * 10).clip(upper=10).round().astype("Int64")
    )

    # Merge factor + composite back into df (NaN for non-gated rows)
    out_cols = factor_cols + ["composite_score", "pct_rank", "decile"]
    df = df.merge(
        gated[["ticker", "settlement_date"] + out_cols],
        on=["ticker", "settlement_date"],
        how="left",
    )

    # -------------------- human-readable note --------------------
    def _note(row) -> str:
        if not row["gates_passed"]:
            return ""
        bits = []
        if not pd.isna(row["si_pct_float"]):
            bits.append(f"SI {row['si_pct_float']*100:.1f}% of float")
        if not pd.isna(row["si_pct_float_sector_rel"]):
            bits.append(f"{row['si_pct_float_sector_rel']:.1f}x sector median")
        if not pd.isna(row["si_pct_float_history_pct"]):
            bits.append(f"{row['si_pct_float_history_pct']*100:.0f}th pct of own 3y")
        if not pd.isna(row["cover_velocity"]):
            verb = "growing" if row["cover_velocity"] > 0 else "covering"
            bits.append(f"SI {verb} {abs(row['cover_velocity'])*100:.0f}% in 1mo")
        return "; ".join(bits)

    df["notes"] = ""
    passed_idx = df.index[df["gates_passed"]]
    if len(passed_idx) > 0:
        df.loc[passed_idx, "notes"] = df.loc[passed_idx].apply(_note, axis=1)

    return df


def score_all(features: pd.DataFrame) -> pd.DataFrame:
    """Run all Phase-1A signal scorers and return concatenated output.

    Phase 1A: Signal D only. Phase 1B will add Signal A (fade long).
    """
    return score_outlier_short(features)


if __name__ == "__main__":
    import time
    from . import FEATURES_PARQUET, CANDIDATES_PARQUET

    t0 = time.time()
    feats = pd.read_parquet(FEATURES_PARQUET)
    print(f"Loaded features: {len(feats):,} rows  ({time.time()-t0:.1f}s)")

    t0 = time.time()
    cands = score_all(feats)
    print(f"Scored: {len(cands):,} rows  ({time.time()-t0:.1f}s)")
    print(f"  passed gates: {cands['gates_passed'].sum():,}")
    print(f"  by signal_type:\n{cands[cands['gates_passed']]['signal_type'].value_counts()}")

    # Latest snapshot: top candidates per signal
    latest = cands["settlement_date"].max()
    snap = cands[(cands["settlement_date"] == latest) & cands["gates_passed"]]
    print(f"\nLatest snapshot {latest.date()}: {len(snap):,} candidates passed gates")

    print(f"\nTop 15 Signal D (outlier short) candidates by composite:")
    cols = [
        "ticker", "name", "sector", "mc_bucket",
        "si_pct_float", "si_pct_float_sector_rel", "si_pct_float_history_pct",
        "cover_velocity", "composite_score", "decile", "notes",
    ]
    top = snap.nlargest(15, "composite_score")[cols]
    pd.set_option("display.width", 200)
    pd.set_option("display.max_colwidth", 60)
    print(top.to_string(index=False))

    # Specific BRBR check
    brbr = snap[snap["ticker"] == "BRBR"]
    print(f"\nBRBR in candidate list: {'YES ✓' if len(brbr) > 0 else 'NO'}")
    if len(brbr):
        print(brbr[cols].to_string(index=False))

    cands.to_parquet(CANDIDATES_PARQUET, index=False)
    print(f"\nWrote {CANDIDATES_PARQUET}  ({CANDIDATES_PARQUET.stat().st_size / 1e6:.1f} MB)")
