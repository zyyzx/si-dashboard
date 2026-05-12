#!/usr/bin/env python3
"""
Create a working dashboard that loads CSV data directly
"""

import csv
from pathlib import Path

TRACKER_DIR = Path(__file__).parent
CSV_FILE = TRACKER_DIR / "si_history_full.csv"
OUTPUT_FILE = TRACKER_DIR / "si_dashboard.html"

print("Creating simplified dashboard...")

# Read CSV and extract LMB + top tickers only
tickers_data = {}
lmb_found = False

print("Scanning CSV for ticker data...")
with open(CSV_FILE, 'r', encoding='utf-8', errors='ignore') as f:
    reader = csv.DictReader(f)
    for row in reader:
        sym = row.get('symbolCode', '').strip()
        if not sym or len(sym) > 5:
            continue

        # Prioritize: Always include LMB, top 200 tickers, all SC stocks
        is_lmb = (sym == 'LMB')
        is_sc = (row.get('marketClassCode', '') == 'SC')

        if sym not in tickers_data:
            tickers_data[sym] = {
                'name': row.get('issueName', ''),
                'mc': row.get('marketClassCode', ''),
                'records': []
            }

        tickers_data[sym]['records'].append({
            'date': row.get('settlementDate', ''),
            'si': row.get('currentShortPositionQuantity', '0'),
            'pct': row.get('changePercent', '0'),
        })

        if sym == 'LMB':
            lmb_found = True

print(f"✓ Loaded {len(tickers_data):,} tickers from CSV")
print(f"  LMB status: {'✓ FOUND' if lmb_found else '✗ NOT FOUND'}")

# Create simple HTML that reads CSV in browser
html = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>SI Tracker - LMB Search</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #0f1117;
            color: #e2e8f0;
            padding: 20px;
            margin: 0;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        h1 { color: #63b3ed; }
        input, select, button {
            padding: 8px;
            margin: 5px;
            font-size: 14px;
            background: #1a1d2e;
            color: #e2e8f0;
            border: 1px solid #2d3748;
            border-radius: 4px;
        }
        button {
            background: #2b4c7e;
            cursor: pointer;
            color: #63b3ed;
        }
        button:hover {
            background: #3d5a8f;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            background: #1a1d2e;
        }
        th, td {
            padding: 10px;
            border-bottom: 1px solid #2d3748;
            text-align: left;
        }
        th {
            background: #16213e;
            color: #63b3ed;
            cursor: pointer;
        }
        th:hover {
            background: #1a1d2e;
        }
        .positive { color: #68d391; }
        .negative { color: #fc8181; }
        .sc-badge {
            background: #f6ad55;
            color: #000;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 11px;
            font-weight: bold;
        }
        .loading {
            color: #718096;
            font-style: italic;
        }
        .status {
            margin: 10px 0;
            padding: 10px;
            background: #16213e;
            border-left: 4px solid #63b3ed;
        }
    </style>
</head>
<body>
<div class="container">
    <h1>📊 FINRA Short Interest Tracker</h1>
    <p>SmallCap & All-Cap Short Interest Data - Including LMB</p>

    <div class="status">
        <strong>Status:</strong> <span id="status" class="loading">Loading data from CSV...</span>
    </div>

    <div>
        <input type="text" id="search" placeholder="Search ticker (e.g., LMB, AAPL)" onkeyup="filterTable()">
        <button onclick="sortByChange()">Sort by Change %</button>
        <button onclick="toggleSmallCap()">Toggle SmallCap Only</button>
    </div>

    <table id="resultsTable">
        <thead>
            <tr>
                <th onclick="sortBy('ticker')">Ticker</th>
                <th onclick="sortBy('name')">Company Name</th>
                <th>Market Class</th>
                <th onclick="sortBy('latestSI')">Latest SI</th>
                <th onclick="sortBy('change')">Change %</th>
            </tr>
        </thead>
        <tbody id="tableBody">
            <tr><td colspan="5" class="loading">Loading...</td></tr>
        </tbody>
    </table>
</div>

<script>
let allData = [];
let filteredData = [];
let showSmallCapOnly = false;
let sortColumn = 'ticker';
let sortAsc = true;

document.addEventListener('DOMContentLoaded', function() {
    loadCSVData();
});

function loadCSVData() {
    fetch('si_history_full.csv')
        .then(response => response.text())
        .then(csv => {
            parseCSV(csv);
            document.getElementById('status').innerHTML = 'Data loaded successfully! Found ' + allData.length + ' unique tickers.';
            renderTable();
        })
        .catch(error => {
            document.getElementById('status').innerHTML = 'Error loading CSV: ' + error.message;
            console.error(error);
        });
}

function parseCSV(csv) {
    const lines = csv.split('\\n');
    const header = lines[0].split(',');

    const tickerMap = {};

    for (let i = 1; i < lines.length; i++) {
        if (!lines[i].trim()) continue;

        const values = lines[i].split(',');
        const row = {};
        for (let j = 0; j < header.length; j++) {
            row[header[j].trim()] = values[j] ? values[j].trim() : '';
        }

        const ticker = row.symbolCode;
        const si = parseFloat(row.currentShortPositionQuantity) || 0;
        const pct = parseFloat(row.changePercent) || 0;

        if (!ticker || ticker.length > 5) continue;

        if (!tickerMap[ticker]) {
            tickerMap[ticker] = {
                ticker: ticker,
                name: row.issueName,
                mc: row.marketClassCode,
                latestSI: si,
                change: pct,
                recordCount: 0
            };
        } else {
            tickerMap[ticker].latestSI = si;
            tickerMap[ticker].change = pct;
        }
        tickerMap[ticker].recordCount++;
    }

    allData = Object.values(tickerMap).sort((a, b) => a.ticker.localeCompare(b.ticker));
    filteredData = [...allData];
}

function filterTable() {
    const search = document.getElementById('search').value.toUpperCase();

    filteredData = allData.filter(item => {
        const matchesSearch = item.ticker.includes(search) ||
                            item.name.toUpperCase().includes(search);
        const matchesFilter = !showSmallCapOnly || item.mc === 'SC';
        return matchesSearch && matchesFilter;
    });

    sortData();
    renderTable();
}

function sortBy(col) {
    if (sortColumn === col) {
        sortAsc = !sortAsc;
    } else {
        sortColumn = col;
        sortAsc = true;
    }
    sortData();
    renderTable();
}

function sortByChange() {
    sortColumn = 'change';
    sortAsc = false;
    sortData();
    renderTable();
}

function sortData() {
    filteredData.sort((a, b) => {
        let aVal = a[sortColumn];
        let bVal = b[sortColumn];

        if (typeof aVal === 'string') {
            return sortAsc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
        } else {
            return sortAsc ? aVal - bVal : bVal - aVal;
        }
    });
}

function toggleSmallCap() {
    showSmallCapOnly = !showSmallCapOnly;
    filterTable();
}

function renderTable() {
    const tbody = document.getElementById('tableBody');

    if (filteredData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5">No results found</td></tr>';
        return;
    }

    tbody.innerHTML = filteredData.map(item => {
        const changeClass = item.change > 0 ? 'positive' : 'negative';
        const scBadge = item.mc === 'SC' ? ' <span class="sc-badge">SmallCap</span>' : '';
        return `<tr>
            <td><strong>${item.ticker}</strong>${scBadge}</td>
            <td>${item.name.substring(0, 50)}</td>
            <td>${item.mc}</td>
            <td>${(item.latestSI / 1e6).toFixed(1)}M</td>
            <td class="${changeClass}">${item.change.toFixed(2)}%</td>
        </tr>`;
    }).join('');
}
</script>
</body>
</html>
'''

with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"\n✓ Dashboard created: {OUTPUT_FILE}")
print(f"  Size: {OUTPUT_FILE.stat().st_size / 1024:.1f} KB")
print(f"\nTo use: Open si_dashboard.html in your browser")
print(f"        Make sure si_history_full.csv is in the same folder")
