#!/usr/bin/env python3
"""
Dashboard Generator for FINRA Short Interest Tracker
Builds a complete HTML dashboard with SmallCap support, including LMB.
"""

import csv
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime

TRACKER_DIR = Path(__file__).parent
CSV_FILE = TRACKER_DIR / "si_history_full.csv"
OUTPUT_FILE = TRACKER_DIR / "si_dashboard.html"

print("=" * 70)
print("BUILDING DASHBOARD WITH SMALLCAP SUPPORT")
print("=" * 70)

# ============================================================================
# STEP 1: LOAD DATA
# ============================================================================
print("\n[1/3] Loading ticker data from CSV...")

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
            if not sym or len(sym) > 10:
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

sorted_dates = sorted([d for d in dates_set if d])
date_index = {d: i for i, d in enumerate(sorted_dates)}

print(f"✓ Loaded {total_rows:,} records for {len(tickers_data):,} tickers")
print(f"  Settlement dates: {len(sorted_dates)} periods ({sorted_dates[0]} to {sorted_dates[-1]})")

# ============================================================================
# STEP 2: BUILD TIME SERIES
# ============================================================================
print("\n[2/3] Building ticker time series...")

tickers_json = {}
market_caps = {}

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
        market_caps[sym] = si_series[-1][1] if si_series else 0

print(f"✓ Built time series for {len(tickers_json):,} tickers")

# Market class distribution
market_classes = {}
for sym, data in tickers_json.items():
    mc = data['marketClass']
    market_classes[mc] = market_classes.get(mc, 0) + 1

print(f"\n  Market Class Distribution:")
for mc in sorted([k for k in market_classes.keys() if k]):
    note = " <- SmallCap stocks" if mc == "SC" else ""
    print(f"    {mc}: {market_classes[mc]:,}{note}")

# Verify LMB
lmb_status = "✓ INCLUDED" if 'LMB' in tickers_json else "✗ MISSING"
print(f"\n  LMB Status: {lmb_status}")

# ============================================================================
# STEP 3: GENERATE HTML DASHBOARD
# ============================================================================
print("\n[3/3] Generating HTML dashboard...")

raw_data = {
    'lastUpdated': datetime.now().isoformat(),
    'dates': sorted_dates,
    'tickers': tickers_json,
    'marketCaps': market_caps
}

# Serialize data (minified for size)
data_json = json.dumps(raw_data, separators=(',', ':'))

# Load HTML template
html_template = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FINRA Short Interest Tracker</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh}
.header{background:linear-gradient(135deg,#1a1d2e,#16213e);border-bottom:1px solid #2d3748;padding:16px 24px;display:flex;align-items:center;justify-content:space-between}
.header h1{font-size:1.4rem;font-weight:700;color:#63b3ed;letter-spacing:-0.5px}
.header .subtitle{font-size:0.75rem;color:#718096;margin-top:2px}
.data-badge{background:#2d3748;border-radius:20px;padding:4px 12px;font-size:0.72rem;color:#68d391;border:1px solid #48bb78}
.tabs{display:flex;background:#1a1d2e;border-bottom:1px solid #2d3748;padding:0 24px}
.tab{padding:12px 20px;cursor:pointer;font-size:0.85rem;font-weight:500;color:#718096;border-bottom:2px solid transparent;transition:all 0.2s;user-select:none}
.tab:hover{color:#a0aec0}
.tab.active{color:#63b3ed;border-bottom-color:#63b3ed}
.tab-content{display:none;padding:24px}
.tab-content.active{display:block}
.search-row{display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;margin-bottom:16px}
.search-wrap{position:relative;flex:0 0 280px}
.search-input{width:100%;background:#1a1d2e;border:1px solid #2d3748;border-radius:8px;padding:10px 14px;font-size:0.875rem;color:#e2e8f0;outline:none;transition:border 0.2s}
.search-input:focus{border-color:#63b3ed}
.autocomplete-list{position:absolute;top:100%;left:0;right:0;background:#1a1d2e;border:1px solid #2d3748;border-top:none;border-radius:0 0 8px 8px;max-height:220px;overflow-y:auto;z-index:100}
.autocomplete-item{padding:8px 14px;cursor:pointer;font-size:0.82rem;display:flex;justify-content:space-between;align-items:center;gap:8px}
.autocomplete-item:hover{background:#2d3748}
.autocomplete-item .sym{color:#63b3ed;font-weight:600;flex-shrink:0}
.autocomplete-item .nm{color:#718096;font-size:0.75rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chips{display:flex;flex-wrap:wrap;gap:8px;align-items:center;flex:1}
.chip{display:inline-flex;align-items:center;gap:6px;background:#2d3748;border:1px solid #4a5568;border-radius:20px;padding:4px 10px 4px 12px;font-size:0.8rem}
.chip .sym{font-weight:600}
.chip .nm{color:#718096;font-size:0.72rem}
.chip .x{color:#718096;cursor:pointer;font-size:1rem;line-height:1}
.chip .x:hover{color:#fc8181}
.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
.btn{background:#2d3748;border:1px solid #4a5568;border-radius:6px;padding:6px 12px;font-size:0.78rem;color:#a0aec0;cursor:pointer;transition:all 0.15s}
.btn:hover{background:#3d4a5c;color:#e2e8f0}
.btn.active{background:#2b4c7e;border-color:#63b3ed;color:#63b3ed}
.label{font-size:0.75rem;color:#718096}
.chart-wrap{background:#1a1d2e;border:1px solid #2d3748;border-radius:12px;padding:20px;position:relative;margin-top:12px}
.chart-wrap canvas{max-height:450px}
.no-data{text-align:center;color:#718096;padding:60px 20px;font-size:0.9rem}
.screener-controls{background:#1a1d2e;border:1px solid #2d3748;border-radius:12px;padding:20px;margin-bottom:20px;display:flex;gap:16px;flex-wrap:wrap;align-items:flex-end}
.form-group{display:flex;flex-direction:column;gap:6px}
.form-group label{font-size:0.75rem;color:#718096;font-weight:500}
.form-select{background:#0f1117;border:1px solid #2d3748;border-radius:6px;padding:7px 10px;font-size:0.82rem;color:#e2e8f0;outline:none;cursor:pointer;min-width:120px}
.form-select:focus{border-color:#63b3ed}
.run-btn{background:#2b4c7e;border:1px solid #63b3ed;border-radius:6px;padding:8px 20px;font-size:0.85rem;color:#63b3ed;cursor:pointer;font-weight:600;transition:all 0.15s;white-space:nowrap}
.run-btn:hover{background:#3d5a8f}
.empty-state{text-align:center;color:#718096;padding:40px 20px}
.results-wrap{background:#1a1d2e;border:1px solid #2d3748;border-radius:12px;overflow:hidden;margin-top:20px}
.results-header{background:#16213e;border-bottom:1px solid #2d3748;padding:16px;display:flex;justify-content:space-between;align-items:center}
.results-header .title{font-weight:600;color:#63b3ed}
.results-table{width:100%;border-collapse:collapse}
.results-table th{background:#1a1d2e;border-bottom:1px solid #2d3748;padding:12px;text-align:left;font-size:0.8rem;color:#a0aec0;font-weight:600;cursor:pointer;user-select:none}
.results-table th:hover{background:#252e3f}
.results-table td{padding:12px;border-bottom:1px solid #2d3748;font-size:0.85rem}
.results-table tr:hover{background:#16213e}
.sym-col{color:#63b3ed;font-weight:600}
.positive{color:#68d391}
.negative{color:#fc8181}
.badge{display:inline-block;background:#2d3748;padding:2px 8px;border-radius:4px;font-size:0.7rem;color:#a0aec0}
.container{max-width:1400px;margin:0 auto}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>📊 FINRA Short Interest Tracker</h1>
    <div class="subtitle">SmallCap & All-Cap Short Interest Analysis</div>
  </div>
  <div class="data-badge">Updated: <span id="lastUpdate"></span></div>
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab(event,'tracker')">📈 Tracker</div>
  <div class="tab" onclick="switchTab(event,'screener')">🔍 Screener</div>
  <div class="tab" onclick="switchTab(event,'help')">❓ Help</div>
</div>

<div class="container">
  <div id="tracker" class="tab-content active">
    <div class="search-row">
      <div class="search-wrap">
        <input type="text" class="search-input" id="searchInput" placeholder="Search tickers (e.g., LMB, AAPL)...">
        <div class="autocomplete-list" id="autocompleteList"></div>
      </div>
      <div class="chips" id="selectedChips"></div>
    </div>
    <div class="controls">
      <button class="btn" onclick="clearAllTickers()">Clear All</button>
    </div>
    <div class="chart-wrap" id="chartWrap" style="display:none">
      <canvas id="chart"></canvas>
    </div>
    <div id="noDataMsg" class="no-data">Select one or more tickers to view charts</div>
  </div>

  <div id="screener" class="tab-content">
    <div class="screener-controls">
      <div class="form-group">
        <label>Min Market Cap ($M)</label>
        <input type="number" class="form-select" id="minMcap" value="0" style="width:120px">
      </div>
      <div class="form-group">
        <label>From Period</label>
        <select class="form-select" id="fromPeriod"></select>
      </div>
      <div class="form-group">
        <label>To Period</label>
        <select class="form-select" id="toPeriod"></select>
      </div>
      <div class="form-group">
        <label>Metric</label>
        <select class="form-select" id="metric">
          <option value="si">Short Interest (Shares)</option>
          <option value="pct">Change (%)</option>
        </select>
      </div>
      <div class="form-group">
        <label>Direction</label>
        <select class="form-select" id="direction">
          <option value="any">Any</option>
          <option value="increase">Increase Only</option>
          <option value="decrease">Decrease Only</option>
        </select>
      </div>
      <div class="form-group">
        <label>Max Results</label>
        <input type="number" class="form-select" id="maxResults" value="50" style="width:80px">
      </div>
      <button class="run-btn" onclick="runScreener()">Run Screener ▶</button>
    </div>
    <div class="results-wrap" id="resultsWrap" style="display:none">
      <div class="results-header">
        <span class="title">Screener Results</span>
        <span id="resultCount"></span>
      </div>
      <table class="results-table">
        <thead>
          <tr>
            <th onclick="sortTable('sym')">Ticker</th>
            <th onclick="sortTable('name')">Company</th>
            <th onclick="sortTable('marketClass')">Market Class</th>
            <th onclick="sortTable('from_val')">From</th>
            <th onclick="sortTable('to_val')">To</th>
            <th onclick="sortTable('change')">Change %</th>
            <th onclick="sortTable('change_abs')">Change (Shares)</th>
          </tr>
        </thead>
        <tbody id="resultsBody"></tbody>
      </table>
    </div>
    <div class="empty-state" id="screenerEmpty">Configure filters above and click Run Screener to find the biggest short interest movers.</div>
  </div>

  <div id="help" class="tab-content">
    <h2 style="color:#63b3ed;margin-bottom:16px">About This Dashboard</h2>
    <p style="margin-bottom:12px;line-height:1.6">
      This dashboard displays FINRA bi-weekly short interest data with full SmallCap support.
    </p>
    <p style="margin-bottom:12px;line-height:1.6">
      <strong>Tracker Tab:</strong> View short interest trends for specific tickers over time.
    </p>
    <p style="margin-bottom:12px;line-height:1.6">
      <strong>Screener Tab:</strong> Find stocks with the largest changes in short interest.
    </p>
    <p style="margin-bottom:12px;line-height:1.6">
      <strong>SmallCap Support:</strong> Now includes all tickers with FINRA data, including SmallCap stocks.
    </p>
  </div>
</div>

<script>
var RAW = PLACEHOLDER_DATA;
var SYM_LIST = Object.keys(RAW.tickers).sort();
var TICKERS = RAW.tickers;
var MARKET_CAPS = RAW.marketCaps;
var SETTLEMENT_DATES = RAW.dates;

var selectedTickers = [];
var screenerResults = [];
var sortCol = 'change';
var sortAsc = false;
var chart = null;

document.addEventListener('DOMContentLoaded', function() {
  document.getElementById('lastUpdate').textContent = new Date(RAW.lastUpdated).toLocaleDateString();

  var fromSel = document.getElementById('fromPeriod');
  var toSel = document.getElementById('toPeriod');
  SETTLEMENT_DATES.forEach(function(date, idx) {
    var opt1 = document.createElement('option');
    opt1.value = idx;
    opt1.textContent = date;
    fromSel.appendChild(opt1);

    var opt2 = document.createElement('option');
    opt2.value = idx;
    opt2.textContent = date;
    toSel.appendChild(opt2);
  });

  fromSel.value = Math.max(0, SETTLEMENT_DATES.length - 10);
  toSel.value = SETTLEMENT_DATES.length - 1;

  var searchInput = document.getElementById('searchInput');
  searchInput.addEventListener('input', function() {
    var q = this.value.toUpperCase();
    if(!q) {
      document.getElementById('autocompleteList').innerHTML = '';
      return;
    }
    var matches = SYM_LIST.filter(function(s) {
      return s.startsWith(q) || TICKERS[s].name.toUpperCase().includes(q);
    }).slice(0, 12);
    var html = matches.map(function(s) {
      var mc = TICKERS[s].marketClass || '?';
      var mcBadge = mc === 'SC' ? '<span class="badge" style="color:#f6ad55">SmallCap</span>' : '';
      return '<div class="autocomplete-item" onclick="addTicker(\'' + s + '\')"><span class="sym">' + s + '</span><span class="nm">' + TICKERS[s].name.substring(0, 40) + ' ' + mcBadge + '</span></div>';
    }).join('');
    document.getElementById('autocompleteList').innerHTML = html;
  });
});

function addTicker(sym) {
  if(!selectedTickers.includes(sym)) {
    selectedTickers.push(sym);
  }
  document.getElementById('searchInput').value = '';
  document.getElementById('autocompleteList').innerHTML = '';
  renderChips();
  renderChart();
}

function removeTicker(sym) {
  selectedTickers = selectedTickers.filter(function(s) { return s !== sym; });
  renderChips();
  renderChart();
}

function clearAllTickers() {
  selectedTickers = [];
  renderChips();
  renderChart();
}

function renderChips() {
  var chips = document.getElementById('selectedChips');
  var colors = ['#63b3ed', '#68d391', '#f6ad55', '#fc8181'];
  chips.innerHTML = selectedTickers.map(function(sym, idx) {
    var c = colors[idx % colors.length];
    var mc = TICKERS[sym].marketClass;
    var mcLabel = mc === 'SC' ? ' (SC)' : '';
    return '<div class="chip" style="border-color:' + c + '44"><span class="sym" style="color:' + c + '">' + sym + mcLabel + '</span><span class="nm">' + TICKERS[sym].name.substring(0, 25) + '</span><span class="x" onclick="removeTicker(\'' + sym + '\')">×</span></div>';
  }).join('');
}

function renderChart() {
  var wrap = document.getElementById('chartWrap');
  var noDataMsg = document.getElementById('noDataMsg');

  if(!selectedTickers.length) {
    wrap.style.display = 'none';
    noDataMsg.style.display = 'block';
    if(chart) chart.destroy();
    return;
  }

  wrap.style.display = 'block';
  noDataMsg.style.display = 'none';

  var ctx = document.getElementById('chart').getContext('2d');
  var colors = ['#63b3ed', '#68d391', '#f6ad55', '#fc8181'];

  var datasets = selectedTickers.map(function(sym, idx) {
    var t = TICKERS[sym];
    var data = t.si.map(function(pair) { return {x: pair[0], y: pair[1] / 1e6}; });
    return {
      label: sym,
      data: data,
      borderColor: colors[idx % colors.length],
      backgroundColor: colors[idx % colors.length] + '15',
      fill: true,
      tension: 0.3,
      borderWidth: 2,
      pointRadius: 3,
      pointHoverRadius: 5
    };
  });

  if(chart) chart.destroy();

  chart = new Chart(ctx, {
    type: 'line',
    data: {datasets: datasets},
    options: {
      responsive: true,
      maintainAspectRatio: true,
      interaction: {mode: 'index', intersect: false},
      plugins: {
        legend: {labels: {color: '#a0aec0', usePointStyle: true}},
        tooltip: {backgroundColor: '#1a1d2e', borderColor: '#2d3748', borderWidth: 1, titleColor: '#e2e8f0', bodyColor: '#a0aec0'}
      },
      scales: {
        x: {display: true, ticks: {color: '#718096'}, grid: {color: '#2d3748', drawBorder: false}},
        y: {display: true, ticks: {color: '#718096'}, grid: {color: '#2d3748', drawBorder: false}, title: {display: true, text: 'Short Interest (Millions)', color: '#a0aec0'}}
      }
    }
  });
}

function runScreener() {
  var minMcap = parseFloat(document.getElementById('minMcap').value) || 0;
  var fromIdx = parseInt(document.getElementById('fromPeriod').value);
  var toIdx = parseInt(document.getElementById('toPeriod').value);
  var dir = document.getElementById('direction').value;
  var metric = document.getElementById('metric').value;
  var maxN = parseInt(document.getElementById('maxResults').value) || 50;

  if(fromIdx >= toIdx) { alert('From period must be before To period.'); return; }

  var rows = [];
  SYM_LIST.forEach(function(sym) {
    var mc = MARKET_CAPS[sym] || 0;
    if(mc < minMcap) return;
    var t = TICKERS[sym];
    if(!t) return;

    var series = metric === 'pct' ? t.pct : t.si;
    if(!series || !series.length) return;

    var vals = {};
    series.forEach(function(pair) { vals[pair[0]] = pair[1]; });

    var fromVal = null;
    for(var i = fromIdx; i >= 0; i--) {
      if(vals[i] !== undefined) { fromVal = vals[i]; break; }
    }

    var toVal = null;
    for(var i = toIdx; i >= 0; i--) {
      if(vals[i] !== undefined) { toVal = vals[i]; break; }
    }

    if(fromVal === null || toVal === null || fromVal === 0) return;

    var change = ((toVal - fromVal) / fromVal) * 100;
    var changeAbs = toVal - fromVal;
    rows.push({sym: sym, name: t.name, marketClass: t.marketClass, from_val: fromVal, to_val: toVal, change: change, change_abs: changeAbs, metric: metric});
  });

  var filtered = rows;
  if(dir === 'increase') filtered = rows.filter(function(r) { return r.change > 0; });
  else if(dir === 'decrease') filtered = rows.filter(function(r) { return r.change < 0; });

  filtered.sort(function(a, b) { return Math.abs(b.change) - Math.abs(a.change); });
  screenerResults = filtered.slice(0, maxN);
  sortCol = 'change';
  sortAsc = false;
  renderResults();
}

function sortTable(col) {
  if(sortCol === col) sortAsc = !sortAsc;
  else { sortCol = col; sortAsc = col === 'sym' || col === 'name'; }

  screenerResults.sort(function(a, b) {
    var av = a[col], bv = b[col];
    if(typeof av === 'string') return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
    return sortAsc ? av - bv : bv - av;
  });

  renderResults(true);
}

function renderResults(keepWrap) {
  var wrap = document.getElementById('resultsWrap');
  var empty = document.getElementById('screenerEmpty');

  if(!screenerResults.length) {
    wrap.style.display = 'none';
    empty.style.display = 'block';
    empty.textContent = 'No results match the current filters.';
    return;
  }

  wrap.style.display = 'block';
  empty.style.display = 'none';
  document.getElementById('resultCount').textContent = screenerResults.length + ' results';

  var html = screenerResults.map(function(r) {
    var changeClass = r.change > 0 ? 'positive' : 'negative';
    var mcLabel = r.marketClass === 'SC' ? '<span class="badge" style="margin-left:8px;color:#f6ad55">SmallCap</span>' : '';
    return '<tr><td class="sym-col">' + r.sym + mcLabel + '</td><td>' + r.name.substring(0, 40) + '</td><td>' + r.marketClass + '</td><td>' + (r.from_val / 1e6).toFixed(1) + '</td><td>' + (r.to_val / 1e6).toFixed(1) + '</td><td class="' + changeClass + '">' + r.change.toFixed(2) + '%</td><td class="' + changeClass + '">' + (r.change_abs / 1e6).toFixed(2) + '</td></tr>';
  }).join('');

  document.getElementById('resultsBody').innerHTML = html;
}

function switchTab(e, tab) {
  document.querySelectorAll('.tab-content').forEach(function(el) { el.classList.remove('active'); });
  document.querySelectorAll('.tab').forEach(function(el) { el.classList.remove('active'); });
  document.getElementById(tab).classList.add('active');
  e.target.classList.add('active');
}
</script>
</body>
</html>
'''

# Replace placeholder with actual data
html_content = html_template.replace('PLACEHOLDER_DATA', data_json)

# Write HTML file
with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
    f.write(html_content)

print(f"✓ Generated HTML dashboard")
print(f"\n" + "=" * 70)
print("✓ DASHBOARD REGENERATED SUCCESSFULLY")
print("=" * 70)
print(f"\nSummary:")
print(f"  • Total tickers: {len(tickers_json):,}")
print(f"  • Settlement periods: {len(sorted_dates)}")
print(f"  • SmallCap (SC) stocks: {market_classes.get('SC', 0):,}")
print(f"  • LMB status: {'✓ INCLUDED' if 'LMB' in tickers_json else '✗ MISSING'}")
print(f"\nFile: {OUTPUT_FILE}")
print(f"Size: {OUTPUT_FILE.stat().st_size / (1024*1024):.1f} MB")
print(f"\nOpen the dashboard to start analyzing SmallCap stocks!")
print("=" * 70)
