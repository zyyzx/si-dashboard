#!/usr/bin/env python3
"""Add Borrow Fee / Available columns to the Screener tab.

Until now borrow data reached the dashboard only through the Actionable
Shorts tab, which is filtered to candidates passing the Signal D gates — a
few hundred names. The Screener is where most browsing happens, and there a
name could look compelling on SI and price while being uneconomic to short,
with nothing on screen to say so. This closes that gap.

    THE COVERAGE ASYMMETRY THAT SHAPES THIS

    The two borrow layers are not equally broad:

      latest + symbol   current fee / availability for ~19.9K IBKR contracts
      borrow_daily      ~1 year of daily bars, but only ~3.6K symbols

    So a *current fee* column is well covered while any *fee trend* column
    would be mostly empty and would read as "no trend" rather than "not
    measured". Fee and availability therefore get real columns; percentile
    and 20-day change ride along in the cell tooltip, present only where the
    daily history actually exists.

    A missing fee is never rendered as a number and never blank. It shows a
    muted em dash with a tooltip saying the borrow is unmeasured, because on
    a screener a blank cost column reads as "free" — the most expensive
    possible misreading.

Inputs:  borrow.db (read-only, via analytics/borrow.py)
Output:  si_dashboard.html, in place

Idempotent — strips the prior patch by sentinel and un-splices the injected
table cells before re-inserting, so it is safe on every refresh.

Usage
  python add_borrow_columns.py
  python add_borrow_columns.py --db C:\\path\\to\\borrow.db
  python add_borrow_columns.py --remove
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT / "si_dashboard.html"

PATCH_START = "<!-- BORROW_COLS_PATCH_START -->"
PATCH_END = "<!-- BORROW_COLS_PATCH_END -->"

# --- table splices (into the original Screener markup / renderer) ---------
TH_ANCHOR = ('<th onclick="sortTable(\'cur_pct\')">Current SI% '
             '<span class="sort-arrow">&#8597;</span></th>')
TH_ADD = (
    '<th onclick="sortTable(\'fee\')" title="Annualised IBKR borrow fee. '
    'Hover a cell for its 1-year percentile where daily history exists.">'
    'Borrow Fee <span class="sort-arrow">&#8597;</span></th>'
    '<th onclick="sortTable(\'avail\')" title="Shares available to borrow">'
    'Available <span class="sort-arrow">&#8597;</span></th>'
)

TD_ANCHOR = "<td>${r.cur_pct!==null?r.cur_pct.toFixed(2)+'%':'&mdash;'}</td>"
TD_ADD = "<td>${bwFee(r.sym)}</td><td>${bwAvail(r.sym)}</td>"


def load_borrow(db_path: str | None, universe: set[str]) -> tuple[dict, dict]:
    """Per-ticker [fee, available, pctile_1y, chg_20d]; None where unknown."""
    sys.path.insert(0, str(ROOT))
    from analytics.borrow import build_borrow_features

    df = build_borrow_features(db_path=db_path)
    out: dict[str, list] = {}
    n_daily = 0

    def num(v, nd=None):
        if v is None or v != v:
            return None
        v = float(v)
        return round(v, nd) if nd is not None else v

    for row in df.itertuples(index=False):
        tk = getattr(row, "ticker", None)
        if not tk or tk not in universe:
            continue
        fee = num(getattr(row, "fee_eff", None), 4)
        avail = getattr(row, "avail_eff", None)
        avail = None if avail is None or avail != avail else int(avail)
        pct = num(getattr(row, "fee_pctile_1y", None), 3)
        chg = num(getattr(row, "fee_chg_20d", None), 3)
        if fee is None and avail is None:
            continue
        if pct is not None:
            n_daily += 1
        out[str(tk)] = [fee, avail, pct, chg]

    meta = {"tickers": len(out), "with_daily": n_daily}
    return out, meta


def render_patch(borrow: dict, meta: dict) -> str:
    payload = json.dumps(borrow, separators=(",", ":"), ensure_ascii=False)
    meta_json = json.dumps(meta, separators=(",", ":"))
    return f"""
{PATCH_START}
<script>
/* BORROW[tk] = [fee, available, fee_pctile_1y, fee_chg_20d]; null = unknown.
   Fee/availability come from the broad live IBKR feed; percentile and 20d
   change exist only for the ~3.6K symbols with daily history, so they ride
   in the tooltip rather than in columns that would mostly read empty. */
var BORROW = {payload};
var BORROW_META = {meta_json};

function bwFee(sym){{
  var b = BORROW[sym];
  if(!b || b[0]===null || b[0]===undefined){{
    /* Never blank, never a number: an empty cost column reads as "free". */
    return '<span style="color:#718096" title="Borrow not measured for this '
         + 'ticker - absent from the IBKR feed. This is not a cheap borrow.">'
         + '&mdash;</span>';
  }}
  var fee = b[0], pct = b[2], chg = b[3];
  var colour = fee >= 25 ? '#fc8181' : fee >= 10 ? '#f6ad55' : '#68d391';
  var tip = 'Annualised fee ' + fee.toFixed(2) + '%';
  if(pct !== null && pct !== undefined){{
    tip += ' \\u00b7 ' + Math.round(pct*100) + 'th pctile of its own year';
    if(chg !== null && chg !== undefined){{
      tip += ' \\u00b7 ' + (chg>0?'+':'') + chg.toFixed(2) + ' pts vs 20d ago';
    }}
  }} else {{
    tip += ' \\u00b7 no daily history for this symbol, so no percentile';
  }}
  var mark = (pct !== null && pct !== undefined && pct >= 0.9) ? ' \\u26A0' : '';
  return '<span style="color:' + colour + '" title="' + tip + '">'
       + fee.toFixed(2) + '%' + mark + '</span>';
}}

function bwAvail(sym){{
  var b = BORROW[sym];
  if(!b || b[1]===null || b[1]===undefined){{
    return '<span style="color:#718096" title="Availability not measured">&mdash;</span>';
  }}
  var v = b[1];
  var s = v>=1e6 ? (v/1e6).toFixed(1)+'M' : v>=1e3 ? (v/1e3).toFixed(0)+'K' : String(v);
  return '<span title="' + v.toLocaleString() + ' shares available to borrow">'
       + s + '</span>';
}}

/* Sorting needs the values ON the result rows, and the original comparator
   is not null-aware, so annotate after each run and take over the two new
   columns with a comparator that always sinks unmeasured names. */
(function(){{
  var origRun = window.runScreener;
  if(typeof origRun === 'function'){{
    window.runScreener = function(){{
      origRun.apply(this, arguments);
      if(typeof screenerResults !== 'undefined' && screenerResults.forEach){{
        screenerResults.forEach(function(r){{
          var b = BORROW[r.sym];
          r.fee   = (b && b[0]!==null && b[0]!==undefined) ? b[0] : null;
          r.avail = (b && b[1]!==null && b[1]!==undefined) ? b[1] : null;
        }});
      }}
    }};
  }}

  var origSort = window.sortTable;
  if(typeof origSort === 'function'){{
    window.sortTable = function(col){{
      if(col !== 'fee' && col !== 'avail') return origSort.apply(this, arguments);
      if(sortCol === col) sortAsc = !sortAsc;
      else {{ sortCol = col; sortAsc = true; }}   // cheapest / most available first
      screenerResults.sort(function(a,b){{
        var av=a[col], bv=b[col];
        var an=(av===null||av===undefined), bn=(bv===null||bv===undefined);
        if(an&&bn) return 0;
        if(an) return 1;      // unmeasured always last, either direction
        if(bn) return -1;
        return sortAsc ? (av-bv) : (bv-av);
      }});
      renderResults(true);
    }};
  }}
}})();
</script>
{PATCH_END}
"""


def strip_patch(html: str) -> str:
    """Exact inverse of the injection, so --remove restores byte-for-byte.

    The injection contributes precisely "\\n" + block + "\\n\\n" before
    </body>, so match that. An earlier greedy "\\n*" prefix here swallowed the
    blank line belonging to whichever patch was injected before this one —
    cosmetic, but it meant remove-then-re-add was not a round trip. The
    tolerant pattern is kept as a fallback for hand-edited files.
    """
    if PATCH_START in html and PATCH_END in html:
        exact = (r"\n" + re.escape(PATCH_START) + r".*?"
                 + re.escape(PATCH_END) + r"\n\n")
        new = re.sub(exact, "", html, flags=re.DOTALL)
        if new == html:            # not the shape we wrote - fall back
            new = re.sub(
                r"\n*" + re.escape(PATCH_START) + r".*?"
                + re.escape(PATCH_END) + r"\n*",
                "\n", html, flags=re.DOTALL,
            )
        html = new
    html = html.replace(TH_ANCHOR + TH_ADD, TH_ANCHOR)
    html = html.replace(TD_ANCHOR + TD_ADD, TD_ANCHOR)
    return html


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Add borrow columns to the Screener")
    ap.add_argument("--db", default=None, help="path to borrow.db (overrides env)")
    ap.add_argument("--remove", action="store_true", help="strip the patch and exit")
    args = ap.parse_args(argv)

    if not DASHBOARD.exists():
        print(f"ERROR: {DASHBOARD} not found", file=sys.stderr)
        return 1

    print(f"Reading dashboard ({DASHBOARD.stat().st_size/1e6:.1f} MB) ...")
    html = DASHBOARD.read_text(encoding="utf-8")
    before = len(html)

    had = PATCH_START in html
    html = strip_patch(html)
    if had:
        print("  removed prior patch")

    if args.remove:
        DASHBOARD.write_text(html, encoding="utf-8")
        print(f"Removed. {DASHBOARD.name} now {DASHBOARD.stat().st_size/1e6:.1f} MB")
        return 0

    i = html.find('"tickers":{')
    universe = set(re.findall(r'"([A-Z0-9.\-]{1,10})":\{"name"', html[i:])) if i >= 0 else set()
    print(f"Dashboard universe: {len(universe):,} tickers")

    try:
        borrow, meta = load_borrow(args.db, universe)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not borrow:
        print("ERROR: no borrow rows matched the dashboard universe.", file=sys.stderr)
        return 3

    print(f"Borrow rows embedded: {meta['tickers']:,} "
          f"({meta['tickers']/max(len(universe),1):.0%} of universe)")
    print(f"  with daily history (tooltip percentile): {meta['with_daily']:,}")
    print(f"  fee/availability only:                   "
          f"{meta['tickers']-meta['with_daily']:,}")

    if TH_ANCHOR not in html:
        print("ERROR: Screener 'Current SI%' header not found.", file=sys.stderr)
        return 4
    if TD_ANCHOR not in html:
        print("ERROR: Screener cur_pct cell not found in renderResults.", file=sys.stderr)
        return 5

    html = html.replace(TH_ANCHOR, TH_ANCHOR + TH_ADD, 1)
    html = html.replace(TD_ANCHOR, TD_ANCHOR + TD_ADD, 1)
    html = html.replace("</body>", render_patch(borrow, meta) + "\n</body>", 1)

    DASHBOARD.write_text(html, encoding="utf-8")
    print(f"\nWrote {DASHBOARD.name}  ({DASHBOARD.stat().st_size/1e6:.1f} MB, "
          f"{len(html)-before:+,} bytes)")
    print("\nNEXT: run `python validate_dashboard.py` before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
