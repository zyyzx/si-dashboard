#!/usr/bin/env python3
"""
Regenerate Dashboard with SmallCap Support
Extract ticker data from CSV and rebuild dashboard including SC stocks.
"""

import csv
from pathlib import Path
from collections import defaultdict

TRACKER_DIR = Path(__file__).parent
CSV_FILE = TRACKER_DIR / "si_history_full.csv"

print("Loading SI data from CSV (handling corruption)...")

# Parse CSV manually to handle corruption
tickers_data = defaultdict(lambda: {
    'name': '',
    'marketClass': '',
    'exchange': '',
    'records': []
})

dates_set = set()
total_rows = 0

with open(CSV_FILE, 'r', encoding='utf-8', errors='ignore') as f:
    reader = csv.DictReader(f)
    for row in reader:
        try:
            sym = row.get('symbolCode', '').strip()
            if not sym or len(sym) > 10:  # Skip invalid tickers
                continue

            tickers_data[sym]['name'] = row.get('issueName', '')
            tickers_data[sym]['marketClass'] = row.get('marketClassCode', '')
            tickers_data[sym]['exchange'] = row.get('issuerServicesGroupExchangeCode', '')
            tickers_data[sym]['records'].append({
                'date': row.get('settlementDate', ''),
                'si': float(row.get('currentShortPositionQuantity', 0) or 0),
                'pct': float(row.get('changePercent', 0) or 0),
            })

            date_val = row.get('settlementDate', '') or ''
            if date_val:
                dates_set.add(date_val)
            total_rows += 1
        except Exception as e:
            pass

print(f"✓ Loaded {total_rows:,} records for {len(tickers_data):,} tickers")

# Sort settlement dates
sorted_dates = sorted([d for d in dates_set if d])
date_index = {d: i for i, d in enumerate(sorted_dates)}

print(f"  Settlement dates: {len(sorted_dates)} periods")
print(f"  Range: {sorted_dates[0]} to {sorted_dates[-1]}")

# Check for LMB
if 'LMB' in tickers_data:
    lmb = tickers_data['LMB']
    print(f"\n✓ LMB FOUND:")
    print(f"    Name: {lmb['name']}")
    print(f"    Market Class: {lmb['marketClass']}")
    print(f"    Exchange: {lmb['exchange']}")
    print(f"    Records: {len(lmb['records'])}")
else:
    print(f"\n✗ LMB NOT FOUND in CSV")

# Build time series for each ticker
print(f"\nBuilding time series for {len(tickers_data):,} tickers...")

tickers_json = {}
for sym in sorted(tickers_data.keys()):
    t = tickers_data[sym]
    if not t['records']:
        continue

    si_series = []
    pct_series = []

    for record in sorted([r for r in t['records'] if r['date']], key=lambda x: x['date']):
        date = record['date']
        if date and date in date_index:
            idx = date_index[date]
            si_series.append([idx, record['si']])
            pct_series.append([idx, record['pct']])

    if si_series:
        tickers_json[sym] = {
            'name': t['name'],
            'marketClass': t['marketClass'],
            'exchange': t['exchange'],
            'si': si_series,
            'pct': pct_series
        }

print(f"✓ Built time series for {len(tickers_json):,} tickers")

# Market class distribution
market_classes = {}
for sym, data in tickers_json.items():
    mc = data['marketClass']
    market_classes[mc] = market_classes.get(mc, 0) + 1

print(f"\nMarket Class Distribution:")
for mc in sorted([k for k in market_classes.keys() if k]):
    note = " <- SmallCap" if mc == "SC" else ""
    print(f"  {mc}: {market_classes[mc]:,}{note}")

# Verify LMB in output
if 'LMB' in tickers_json:
    print(f"\nSUCCESS: LMB will be included in new dashboard")
else:
    print(f"\nWARNING: LMB still not in output")

print(f"\nGenerated {len(tickers_json):,} tickers with time series data")
