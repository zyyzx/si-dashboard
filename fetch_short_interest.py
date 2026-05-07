#!/usr/bin/env python3
"""
FINRA Short Interest History Pipeline
======================================
Downloads all available FINRA bi-weekly short interest files for a given year,
merges them into a single history CSV, and saves it to the SI Tracker folder.

Run manually or via scheduled task to keep the history up to date.
Usage: python fetch_short_interest.py
"""

import requests
import pandas as pd
import io
import os
from datetime import datetime, timedelta, date
import calendar

# ── Config ──────────────────────────────────────────────────────────────────
YEAR = 2026
BASE_URL = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{date}.csv"
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))   # SI Tracker folder
HISTORY_FILE = os.path.join(OUTPUT_DIR, "si_history_full.csv")

# All known FINRA settlement dates from 2020 onwards.
# Historical dates (2020-2025) are confirmed. 2026+ dates are probed gracefully.
SETTLEMENT_DATES = [
    # 2020
    "20200115","20200131","20200214","20200228","20200313","20200331",
    "20200415","20200430","20200515","20200529","20200615","20200630",
    "20200715","20200731","20200814","20200831","20200915","20200930",
    "20201015","20201030","20201113","20201130","20201215","20201231",
    # 2021
    "20210115","20210129","20210212","20210226","20210315","20210331",
    "20210415","20210430","20210514","20210528","20210615","20210630",
    "20210715","20210730","20210813","20210831","20210915","20210930",
    "20211015","20211029","20211115","20211130","20211215","20211231",
    # 2022
    "20220114","20220131","20220215","20220228","20220315","20220331",
    "20220414","20220429","20220513","20220531","20220615","20220630",
    "20220715","20220729","20220815","20220831","20220915","20220930",
    "20221014","20221031","20221115","20221130","20221215","20221230",
    # 2023
    "20230113","20230131","20230215","20230228","20230315","20230331",
    "20230414","20230428","20230515","20230531","20230615","20230630",
    "20230714","20230731","20230815","20230831","20230915","20230929",
    "20231013","20231031","20231115","20231130","20231215","20231229",
    # 2024
    "20240112","20240131","20240215","20240229","20240315","20240328",
    "20240415","20240430","20240515","20240531","20240614","20240628",
    "20240715","20240731","20240815","20240830","20240913","20240930",
    "20241015","20241031","20241115","20241129","20241213","20241231",
    # 2025
    "20250115","20250131","20250214","20250228","20250314","20250331",
    "20250415","20250430","20250515","20250530","20250613","20250630",
    "20250715","20250731","20250815","20250829","20250915","20250930",
    "20251015","20251031","20251114","20251128","20251215","20251231",
    # 2026 (confirmed through April; remainder probed gracefully)
    "20260115","20260130","20260213","20260227","20260313","20260331",
    "20260415","20260430","20260515","20260529","20260615","20260630",
    "20260715","20260731","20260814","20260831","20260915","20260930",
    "20261015","20261030","20261113","20261130","20261215","20261231",
]


def fetch_period(date_str: str) -> pd.DataFrame | None:
    """Download one settlement period CSV. Returns None if not yet published."""
    url = BASE_URL.format(date=date_str)
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            df = pd.read_csv(io.StringIO(r.text), sep="|", low_memory=False)
            df["settlementDate"] = date_str  # Ensure consistent date column
            print(f"  ✓ {date_str}: {len(df):,} tickers")
            return df
        else:
            print(f"  – {date_str}: not yet available (HTTP {r.status_code})")
            return None
    except Exception as e:
        print(f"  ✗ {date_str}: error — {e}")
        return None


def load_existing_history() -> pd.DataFrame:
    """Load previously saved history, or return empty frame."""
    if os.path.exists(HISTORY_FILE):
        df = pd.read_csv(HISTORY_FILE, low_memory=False)
        print(f"Loaded existing history: {len(df):,} rows across "
              f"{df['settlementDate'].nunique()} periods")
        return df
    return pd.DataFrame()


def run():
    print("=" * 60)
    print("FINRA Short Interest Pipeline")
    print(f"Run time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    existing = load_existing_history()
    already_have = set(existing["settlementDate"].astype(str).unique()) if not existing.empty else set()

    new_frames = []
    for d in SETTLEMENT_DATES:
        if d in already_have:
            print(f"  ↩ {d}: already in history, skipping")
            continue
        df = fetch_period(d)
        if df is not None:
            new_frames.append(df)

    if not new_frames:
        print("\nNo new periods found. History is up to date.")
    else:
        combined = pd.concat([existing] + new_frames, ignore_index=True)
        # Deduplicate on (date, ticker)
        combined = combined.drop_duplicates(subset=["settlementDate", "symbolCode"])
        # Sort chronologically
        combined = combined.sort_values(["settlementDate", "symbolCode"])
        combined.to_csv(HISTORY_FILE, index=False)
        periods = combined["settlementDate"].nunique()
        print(f"\n✓ History updated: {len(combined):,} rows across {periods} periods")
        print(f"  Saved to: {HISTORY_FILE}")

    print("=" * 60)


if __name__ == "__main__":
    run()
