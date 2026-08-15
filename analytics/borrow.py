"""Borrow-cost loaders and features, read from the IBKR poller's borrow.db.

si-dashboard remains canonical for short interest. This module reads
``borrow.db`` for **borrow data only** — fee / rebate / share availability.
The ``short_interest``, ``si_history``, ``float_shares`` and ``market_cap``
tables that also live in that database are deliberately NOT used as signal
inputs: ``si_history`` there is a stale mirror of this repo's own data, and
``short_interest`` covers only the last handful of settlements, which is far
too short for the 6-month / 3-year windows the feature layer depends on.
(``market_cap`` is exposed via an explicitly opt-in helper below, for the
instrument-type filter and last-sale price only.)

The database is always opened read-only. The poller writes to it every
15 minutes and its ``borrow`` / ``feed_pull`` tables are the only copy of
observations that cannot be re-collected.

Two data layers, with very different shapes:

``borrow_daily``  — daily OHLC-style fee/rebate/availability, ~1 year of
    history, ~3,600 symbols (backfilled from iborrowdesk, capped at roughly
    $300mm market cap). This is what trend and percentile features are built
    from. It is a *daily bar* table: ``fee`` is the close.

``latest`` + ``symbol`` — the current IBKR shortable snapshot, ~19,900
    contracts. Much broader coverage than the daily history but no history
    (the high-frequency ``borrow`` table only starts when the poller does).
    This is what current-state gates are built from.

Public surface:
    load_borrow_daily(...)      -> tidy daily bars
    load_latest_snapshot(...)   -> one row per symbol, current state
    build_borrow_features(...)  -> one row per symbol: trend + current state
    load_market_cap_snapshot(...) -> OPT-IN, non-canonical (see docstring)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from . import BORROW_DB

# Rolling windows, in *trading days* (borrow_daily is a daily bar series).
WIN_SHORT = 5
WIN_MED = 20
WIN_LONG = 60
WIN_1Y = 252

# A daily bar older than this many calendar days means the symbol has gone
# quiet in the backfill and its "trend" features are not to be trusted.
DEFAULT_MAX_STALENESS_DAYS = 10


# ----------------------------------------------------------- connection

def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open borrow.db read-only. Never opens a write handle."""
    p = Path(db_path) if db_path else Path(BORROW_DB)
    if not p.exists():
        raise FileNotFoundError(
            f"borrow.db not found at {p}\n"
            f"Set $BORROW_DB (full path) or $BORROW_DATA_ROOT (folder), or place "
            f"the database at {Path(BORROW_DB)}"
        )
    # mode=ro (not immutable=1): read-only, but still sees the poller's commits.
    return sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)


# ------------------------------------------------------------- raw loads

def load_borrow_daily(
    db_path: Path | str | None = None,
    country: str = "usa",
    since: str | None = None,
) -> pd.DataFrame:
    """Load daily borrow bars.

    Returns columns: ticker, date (datetime64), fee, high_fee, low_fee,
    rebate, available. ``fee`` is the daily close in annualised percent
    (0.4635 = 0.46%/yr; 150.14 = hard-to-borrow).
    """
    sql = (
        "SELECT symbol AS ticker, date, fee, high_fee, low_fee, rebate, available "
        "FROM borrow_daily WHERE country = ?"
    )
    params: list = [country]
    if since:
        sql += " AND date >= ?"
        params.append(since)

    with _connect(db_path) as con:
        df = pd.read_sql_query(sql, con, params=params)

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ("fee", "high_fee", "low_fee", "rebate", "available"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["ticker", "date"])
    return df.sort_values(["ticker", "date"]).reset_index(drop=True)


def load_latest_snapshot(
    db_path: Path | str | None = None,
    country: str = "usa",
    currency: str = "USD",
) -> pd.DataFrame:
    """Current IBKR shortable state, one row per ticker.

    Joins ``latest`` (keyed by IBKR con_id) to ``symbol`` to recover the
    ticker. A handful of symbols map to more than one con_id (different
    contracts for the same listed symbol); we keep the contract that is
    currently present in the feed, breaking further ties on the most recent
    feed timestamp.
    """
    sql = """
        SELECT s.symbol      AS ticker,
               s.name        AS ib_name,
               l.con_id,
               l.fee         AS fee_now,
               l.rebate      AS rebate_now,
               l.available   AS available_now,
               l.available_gt,
               l.present,
               l.feed_ts
        FROM latest l
        JOIN symbol s ON s.con_id = l.con_id
        WHERE s.country = ?
    """
    params: list = [country]
    if currency:
        sql += " AND s.currency = ?"
        params.append(currency)

    with _connect(db_path) as con:
        df = pd.read_sql_query(sql, con, params=params)

    df["feed_ts"] = pd.to_datetime(df["feed_ts"], errors="coerce")
    for c in ("fee_now", "rebate_now", "available_now"):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Dedupe symbol -> con_id: prefer present contracts, then freshest feed_ts.
    df = df.sort_values(
        ["ticker", "present", "feed_ts"], ascending=[True, False, False]
    )
    n_before = len(df)
    df = df.drop_duplicates(subset=["ticker"], keep="first").reset_index(drop=True)
    df.attrs["duplicate_con_ids_dropped"] = n_before - len(df)
    return df


def load_market_cap_snapshot(db_path: Path | str | None = None) -> pd.DataFrame:
    """OPT-IN, NON-CANONICAL. The poller's nasdaq_screener snapshot.

    si-dashboard's own ``equities.csv`` is canonical for sector and market
    cap; this exists only because two fields have no equivalent here:
    ``instrument_type`` (lets the screen drop units/warrants/preferreds) and
    ``last_sale`` (a current price, useful for dollar-liquidity sizing).

    ``last_sale`` is a *snapshot overwritten on every refresh* — there is no
    price history in borrow.db. Do not use it for returns.
    """
    sql = (
        "SELECT symbol AS ticker, market_cap, sector AS mcap_sector, "
        "industry AS mcap_industry, last_sale, instrument_type, as_of_utc "
        "FROM market_cap"
    )
    with _connect(db_path) as con:
        df = pd.read_sql_query(sql, con)
    for c in ("market_cap", "last_sale"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.drop_duplicates(subset=["ticker"], keep="first").reset_index(drop=True)


# ------------------------------------------------------------- features

def _pct_of_history(s: pd.Series, window: int) -> float:
    """Where does the last value sit within its own trailing distribution?

    Returns a value in [0, 1] (1.0 = highest fee in the window), or NaN when
    there are too few observations to be meaningful.
    """
    hist = s.tail(window).dropna()
    if len(hist) < 20:
        return float("nan")
    return float((hist <= hist.iloc[-1]).sum() / len(hist))


def _summarise_symbol(g: pd.DataFrame) -> pd.Series:
    """Collapse one symbol's daily bars into a single feature row."""
    fee = g["fee"]
    avail = g["available"]

    fee_latest = fee.iloc[-1] if len(fee) else float("nan")
    fee_5d = fee.tail(WIN_SHORT).mean()
    fee_20d = fee.tail(WIN_MED).mean()
    fee_60d = fee.tail(WIN_LONG).mean()

    # Change vs ~1 month ago, in percentage *points* of fee.
    fee_prev_20d = fee.iloc[-(WIN_MED + 1)] if len(fee) > WIN_MED else float("nan")
    fee_chg_20d = fee_latest - fee_prev_20d

    # Volatility-scaled move: how unusual is the current fee vs its own 60d?
    fee_std_60d = fee.tail(WIN_LONG).std(ddof=1)
    fee_z_60d = (
        (fee_latest - fee_60d) / fee_std_60d
        if fee_std_60d and fee_std_60d > 0
        else float("nan")
    )

    avail_latest = avail.iloc[-1] if len(avail) else float("nan")
    avail_20d = avail.tail(WIN_MED).mean()
    avail_chg_20d_pct = (
        (avail_latest - avail_20d) / avail_20d
        if avail_20d and avail_20d > 0
        else float("nan")
    )

    return pd.Series({
        "fee_daily_latest": fee_latest,
        "fee_5d": fee_5d,
        "fee_20d": fee_20d,
        "fee_60d": fee_60d,
        "fee_chg_20d": fee_chg_20d,
        "fee_z_60d": fee_z_60d,
        "fee_pctile_1y": _pct_of_history(fee, WIN_1Y),
        "avail_daily_latest": avail_latest,
        "avail_20d": avail_20d,
        "avail_chg_20d_pct": avail_chg_20d_pct,
        "avail_pctile_1y": _pct_of_history(avail, WIN_1Y),
        "borrow_obs_days": int(fee.notna().sum()),
        "borrow_last_date": g["date"].iloc[-1],
    })


def build_borrow_features(
    db_path: Path | str | None = None,
    country: str = "usa",
    asof: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """One row per ticker: borrow trend features + current shortable state.

    ``asof`` truncates the daily history (point-in-time safety when
    back-testing); default is "use everything".

    The result is an OUTER join of the two layers, so a ticker present in
    the live feed but missing from the daily backfill still appears (with
    NaN trend features), and vice versa. ``has_daily`` / ``has_live`` say
    which layers a row actually has.
    """
    daily = load_borrow_daily(db_path=db_path, country=country)
    if asof is not None:
        daily = daily[daily["date"] <= pd.Timestamp(asof)]

    if len(daily):
        grouped = daily.groupby("ticker", sort=False)
        # include_groups= landed in pandas 2.2; the desktop may be older.
        try:
            feats = grouped.apply(_summarise_symbol, include_groups=False).reset_index()
        except TypeError:
            feats = grouped.apply(_summarise_symbol).reset_index()
    else:
        feats = pd.DataFrame(columns=["ticker"])

    live = load_latest_snapshot(db_path=db_path, country=country)

    out = feats.merge(live, on="ticker", how="outer")
    out["has_daily"] = out["borrow_obs_days"].notna() if "borrow_obs_days" in out else False
    out["has_live"] = out["feed_ts"].notna() if "feed_ts" in out else False

    # Staleness of the daily backfill for this symbol.
    if "borrow_last_date" in out:
        ref = pd.Timestamp(asof) if asof is not None else out["borrow_last_date"].max()
        out["borrow_stale_days"] = (ref - out["borrow_last_date"]).dt.days

    # Prefer the live feed's fee; fall back to the daily close.
    out["fee_eff"] = out["fee_now"].where(out["fee_now"].notna(), out.get("fee_daily_latest"))
    out["avail_eff"] = out["available_now"].where(
        out["available_now"].notna(), out.get("avail_daily_latest")
    )
    return out


if __name__ == "__main__":
    import time

    t0 = time.time()
    f = build_borrow_features()
    print(f"borrow features: {len(f):,} tickers  ({time.time()-t0:.1f}s)")
    print(f"  with daily history: {int(f['has_daily'].sum()):,}")
    print(f"  with live feed:     {int(f['has_live'].sum()):,}")
    print(f"  both:               {int((f['has_daily'] & f['has_live']).sum()):,}")
    cols = ["ticker", "fee_eff", "fee_pctile_1y", "fee_chg_20d", "avail_eff", "borrow_obs_days"]
    print("\nMost expensive to borrow (live feed):")
    print(f.nlargest(10, "fee_eff")[cols].to_string(index=False))
