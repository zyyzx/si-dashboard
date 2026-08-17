#!/usr/bin/env python3
"""Patch si_dashboard.html to overlay price on the Trend chart.

Input:  prices_settlement.csv  (from fetch_prices.py)
Output: si_dashboard.html, in place

    WHY INDEXED AND NOT A SECOND Y-AXIS

    Short interest and price have unrelated scales, so the tempting move is a
    second y-axis. Don't. A dual-axis chart lets whoever renders it choose
    where the two lines cross, which manufactures a visual correlation the
    data does not contain. Two measures of different scale belong on one axis
    indexed to a common base, or in two stacked panes.

    This patch takes the indexed route because the dashboard already has the
    machinery: the Normalized (Base=100) view rebases the SI series to 100 at
    the start of the selected window. The overlay rebases price the same way,
    onto the same axis. Turning the overlay on therefore switches the view to
    Normalized and says so — because in Shares Short or SI% of Float the two
    series genuinely cannot share an axis.

    Absolute prices are not lost: the tooltip shows the real close alongside
    the index value.

Encoding: price lines reuse their ticker's colour and are dashed. Colour
carries the entity, dash carries the measure — so an SI/price pair reads as
one ticker, and the two measures stay distinguishable without relying on
colour alone.

Splits: the embedded series is ADJUSTED close (continuous through splits,
correct for an indexed comparison). FINRA share counts are as-reported and do
jump at splits, so an SI-shares line can step where the price line does not.
SI % of float is immune to this; see fetch_prices.py for the full note.

Idempotent — strips any prior patch by sentinel before re-inserting.
Uses pure Python string replacement, never the Edit tool (all dashboard JS
lives in the last 5% of a ~35 MB file; see SI_TRACKER_PROJECT_NOTES.md §7).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT / "si_dashboard.html"
PRICES_CSV = ROOT / "prices_settlement.csv"

PATCH_START = "<!-- PRICE_OVERLAY_PATCH_START -->"
PATCH_END = "<!-- PRICE_OVERLAY_PATCH_END -->"

# Anchor: the Normalized view button in the Trend controls.
NORM_BTN = ('<button class="btn" id="btn-norm" onclick="setView(\'norm\')">'
            'Normalized (Base=100)</button>')
TOGGLE_HTML = (
    '<label id="priceOverlayWrap" style="display:inline-flex;gap:5px;align-items:center;'
    'margin-left:10px;font-size:.8rem;color:#a0aec0;cursor:pointer" '
    'title="Rebases price to 100 alongside SI on one axis">'
    '<input type="checkbox" id="priceOverlayChk" onchange="togglePriceOverlay(this)"/>'
    '&#128200; Overlay price</label>'
)


def read_dashboard_dates(html: str) -> list[str]:
    """RAW.dates, which defines the index positions the overlay must match."""
    i = html.find("RAW={")
    if i < 0:
        raise SystemExit("ERROR: could not locate RAW={ in dashboard")
    m = re.search(r'"dates":\[([^\]]+)\]', html[i:i + 200_000])
    if not m:
        raise SystemExit("ERROR: could not parse RAW.dates")
    return [d.strip().strip('"') for d in m.group(1).split(",")]


def build_payload(df: pd.DataFrame, dates: list[str]) -> tuple[dict, dict]:
    """Run-encoded price series per ticker: [startIdx, cents, cents, ...].

    Values are positional from startIdx (null for a gap) and stored as integer
    cents. Against the obvious [[idx, price], ...] shape this drops the
    per-point index and the decimal point — roughly 6 bytes per point instead
    of 13, which is the difference between a ~20 MB payload and a ~10 MB one
    across the full universe. The chart divides by 100 on read.
    """
    idx_of = {d: i for i, d in enumerate(dates)}
    df = df.copy()
    df["settlement_date"] = df["settlement_date"].astype(str).str.replace("-", "", regex=False)
    df["idx"] = df["settlement_date"].map(idx_of)

    unmapped = int(df["idx"].isna().sum())
    df = df.dropna(subset=["idx", "close_adj"])
    df["idx"] = df["idx"].astype(int)

    out: dict[str, list] = {}
    points = 0
    for tk, g in df.groupby("ticker", sort=False):
        g = g.sort_values("idx")
        idxs = g["idx"].tolist()
        vals = g["close_adj"].tolist()
        clean = [(i, v) for i, v in zip(idxs, vals) if v == v and v > 0]
        if not clean:
            continue
        start = clean[0][0]
        end = clean[-1][0]
        run: list = [None] * (end - start + 1)
        for i, v in clean:
            run[i - start] = int(round(float(v) * 100))
        out[str(tk)] = [start] + run
        points += len(clean)

    stats = {"tickers": len(out), "points": points, "unmapped_rows": unmapped}
    return out, stats


def render_patch(prices: dict, meta: dict) -> str:
    payload = json.dumps(prices, separators=(",", ":"), ensure_ascii=False)
    meta_json = json.dumps(meta, separators=(",", ":"))

    return f"""
{PATCH_START}
<script>
/* Price overlay. Indexed to a common base on ONE axis - never a second
   y-scale; see add_price_overlay.py for why. */
var PRICES = {payload};
var PRICE_META = {meta_json};
var PRICE_ON = false;

/* PRICES[tk] = [startIdx, cents, cents, null, ...] - positional from
   startIdx, integer cents. See build_payload() for why. */
function getPriceWindowed(sym){{
  var p = PRICES[sym];
  if(!p || p.length < 2) return null;
  var base = p[0];
  var r = getDateRange(), start = r.start, end = r.end;
  var arr = new Array(end - start + 1).fill(null);
  var any = false;
  for(var k=1;k<p.length;k++){{
    var idx = base + k - 1;
    if(idx>=start && idx<=end){{
      var c = p[k];
      if(c!==null && c!==undefined){{ arr[idx-start] = c/100; any = true; }}
    }}
  }}
  return any ? arr : null;
}}

function rebasePrice(arr){{
  var base = null;
  for(var i=0;i<arr.length;i++){{ if(arr[i]!==null && arr[i]>0){{ base = arr[i]; break; }} }}
  if(!base) return null;
  return arr.map(function(v){{ return v===null ? null : (v/base)*100; }});
}}

function togglePriceOverlay(cb){{
  PRICE_ON = !!cb.checked;
  // Price cannot share an axis with Shares Short or SI% of Float, so the
  // overlay only makes sense in the indexed view. setView re-renders.
  if(PRICE_ON && currentView!=='norm') setView('norm');
  else renderChart();
}}

(function(){{
  var orig = window.renderChart;
  if(typeof orig !== 'function') return;

  window.renderChart = function(){{
    orig.apply(this, arguments);
    var note = document.getElementById('coverageNote');

    if(!PRICE_ON || !chart || !selectedTickers.length) return;

    if(currentView!=='norm'){{
      // Guard: something else switched the view while the overlay was on.
      if(note) note.textContent =
        'Price overlay needs the Normalized (Base=100) view - both series are rebased to 100 so they share one axis.';
      return;
    }}

    var added = 0, missing = [];
    selectedTickers.forEach(function(sym, i){{
      var raw = getPriceWindowed(sym);
      if(!raw){{ missing.push(sym); return; }}
      var idxSeries = rebasePrice(raw);
      if(!idxSeries){{ missing.push(sym); return; }}
      var c = COLORS[i % COLORS.length];
      chart.data.datasets.push({{
        label: sym + ' price',
        data: idxSeries,
        borderColor: c,
        backgroundColor: 'transparent',
        borderWidth: 2,
        borderDash: [5,4],
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.3,
        fill: false,
        spanGaps: true,
        _priceRaw: raw
      }});
      added++;
    }});

    if(added){{
      // Show the real close beside the index value, so rebasing does not
      // hide the actual price level.
      chart.options.plugins.tooltip.callbacks.label = function(ctx){{
        var v = ctx.raw;
        if(v===null||v===undefined) return ctx.dataset.label + ': N/A';
        var praw = ctx.dataset._priceRaw;
        if(praw){{
          var a = praw[ctx.dataIndex];
          return ctx.dataset.label + ': ' + v.toFixed(1) +
                 (a===null||a===undefined ? '' : '  ($' + a.toFixed(2) + ')');
        }}
        return ctx.dataset.label + ': ' + v.toFixed(1);
      }};
      chart.update('none');
    }}

    if(note){{
      note.textContent = missing.length
        ? 'No price history for: ' + missing.join(', ')
        : '';
    }}
  }};
}})();
</script>
{PATCH_END}
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Inject the price overlay")
    ap.add_argument("--prices", default=str(PRICES_CSV))
    ap.add_argument("--remove", action="store_true", help="strip the patch and exit")
    args = ap.parse_args(argv)

    if not DASHBOARD.exists():
        print(f"ERROR: {DASHBOARD} not found", file=sys.stderr)
        return 1

    print(f"Reading dashboard ({DASHBOARD.stat().st_size/1e6:.1f} MB) ...")
    html = DASHBOARD.read_text(encoding="utf-8")
    before_len = len(html)

    # Strip any prior patch. Newlines collapse to one so reruns are byte-stable.
    if PATCH_START in html and PATCH_END in html:
        n0 = len(html)
        html = re.sub(
            r"\n*" + re.escape(PATCH_START) + r".*?" + re.escape(PATCH_END) + r"\n*",
            "\n", html, flags=re.DOTALL,
        )
        html = html.replace(TOGGLE_HTML, "")
        print(f"  removed prior patch ({n0 - len(html):,} bytes)")

    if args.remove:
        DASHBOARD.write_text(html, encoding="utf-8")
        print(f"Removed. {DASHBOARD.name} now {DASHBOARD.stat().st_size/1e6:.1f} MB")
        return 0

    prices_path = Path(args.prices)
    if not prices_path.exists():
        print(f"ERROR: {prices_path} not found — run fetch_prices.py first",
              file=sys.stderr)
        return 1

    print(f"Loading {prices_path.name} ...")
    df = pd.read_csv(prices_path, dtype={"ticker": str, "settlement_date": str})
    print(f"  {len(df):,} rows, {df['ticker'].nunique():,} tickers")

    dates = read_dashboard_dates(html)
    print(f"  dashboard grid: {len(dates)} settlement dates "
          f"({dates[0]} -> {dates[-1]})")

    prices, stats = build_payload(df, dates)
    if not prices:
        print("ERROR: no price points aligned to RAW.dates — check that "
              "prices_settlement.csv was built against this dashboard.",
              file=sys.stderr)
        return 2
    if stats["unmapped_rows"]:
        print(f"  NOTE: {stats['unmapped_rows']:,} rows had a settlement date not in "
              f"RAW.dates and were skipped (regenerate prices after adding periods)")

    ticker_count = len(read_raw_ticker_names(html))
    covered = stats["tickers"]
    print(f"  coverage: {covered:,} of {ticker_count:,} dashboard tickers "
          f"({covered/max(ticker_count,1):.0%})")

    meta = {"tickers": covered, "points": stats["points"], "basis": "adjusted close"}
    patch = render_patch(prices, meta)

    if NORM_BTN not in html:
        print("ERROR: could not find the Normalized view button to anchor the toggle.",
              file=sys.stderr)
        return 3
    html = html.replace(NORM_BTN, NORM_BTN + TOGGLE_HTML, 1)

    if "</body>" not in html:
        print("ERROR: no </body> found.", file=sys.stderr)
        return 4
    html = html.replace("</body>", patch + "\n</body>", 1)

    DASHBOARD.write_text(html, encoding="utf-8")
    print(f"\nWrote {DASHBOARD.name}  ({DASHBOARD.stat().st_size/1e6:.1f} MB, "
          f"{len(html)-before_len:+,} bytes)")
    print(f"  price points embedded: {stats['points']:,}")
    print("\nNEXT: run `python validate_dashboard.py` before committing.")
    return 0


def read_raw_ticker_names(html: str) -> list[str]:
    """Ticker keys in RAW.tickers — used only to report overlay coverage."""
    i = html.find('"tickers":{')
    if i < 0:
        return []
    # Keys are the only thing needed; scan for `"KEY":{"name"` patterns.
    return re.findall(r'"([A-Z0-9.\-]{1,10})":\{"name"', html[i:i + 40_000_000])


if __name__ == "__main__":
    sys.exit(main())
