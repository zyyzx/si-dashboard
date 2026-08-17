#!/usr/bin/env python3
"""Patch si_dashboard.html to add an Actionable Shorts tab.

Fed by exports/latest/actionable_shorts.csv (written by build_actionable.py):
the SI candidates joined to the IBKR borrow layer, so the tab answers "can
this short actually be put on, and at what cost?"

Idempotent — strips any prior patch block by sentinel before re-inserting,
so it is safe to re-run after every refresh.

Follows the same injection strategy as add_candidates_tab.py: pure Python
string replacement, never the Edit tool (si_dashboard.html is ~35 MB and all
JS lives in the last 5% of the file, where the Edit tool has twice silently
truncated it — see SI_TRACKER_PROJECT_NOTES.md §7).

Inputs:
  exports/latest/actionable_shorts.csv

Output:
  si_dashboard.html (in place)

Run `python validate_dashboard.py` afterwards.
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT / "si_dashboard.html"
CSV = ROOT / "exports" / "latest" / "actionable_shorts.csv"

PATCH_START = "<!-- ACTIONABLE_PATCH_START -->"
PATCH_END = "<!-- ACTIONABLE_PATCH_END -->"

NAV_TAB = (
    '  <div class="tab" onclick="switchTab(\'actionable\')">'
    '&#128274; Actionable</div>\n'
)

# Anchor: inject directly after the Candidates nav tab.
CANDIDATES_ANCHOR = (
    '  <div class="tab" onclick="switchTab(\'candidates\')">'
    '&#127919; Candidates</div>\n'
)


def _f(v, ndigits=3):
    """Round to ndigits; None for NaN so the JSON carries null."""
    if v is None:
        return None
    try:
        if isinstance(v, float) and math.isnan(v):
            return None
    except Exception:
        pass
    try:
        return round(float(v), ndigits)
    except Exception:
        return None


def build_rows(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "t": r.get("ticker"),
            "n": (str(r.get("name")) if pd.notna(r.get("name")) else "")[:60],
            "s": r.get("sector") if pd.notna(r.get("sector")) else "",
            "mc": r.get("mc_bucket") if pd.notna(r.get("mc_bucket")) else "",
            "act": _f(r.get("actionable_score"), 1),
            "fl": str(r.get("flags")) if pd.notna(r.get("flags")) else "",
            "cs": _f(r.get("composite_score"), 1),
            "d": int(r["decile"]) if pd.notna(r.get("decile")) else None,
            "si": _f(r.get("si_pct_float"), 4),
            "fee": _f(r.get("fee_eff"), 4),
            "fp": _f(r.get("fee_pctile_1y"), 3),
            "fc": _f(r.get("fee_chg_20d"), 3),
            "av": _f(r.get("avail_eff"), 0),
            "ac": _f(r.get("avail_chg_20d_pct"), 3),
            "dtc": _f(r.get("dtc"), 1),
            "ob": _f(r.get("borrow_obs_days"), 0),
        })
    return rows


def render_patch(rows: list[dict], asof: str, meta: dict) -> str:
    payload = json.dumps(
        {"asof": asof, "meta": meta, "rows": rows},
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return f"""
{PATCH_START}
<div id="tab-actionable" class="tab-content">
<style>
/* Status palette: dark-surface steps, all >= 4.9:1 on the #2d3748 chip.
   Every flag ships a glyph AND a word, so colour never carries meaning
   alone (warning/serious sit close in hue by design). */
.ac-wrap{{max-width:1500px;margin:0 auto}}
.ac-card{{background:#1a202c;padding:16px;border-radius:10px;border:1px solid #2d3748;margin-bottom:14px}}
.ac-label{{font-size:.72rem;color:#a0aec0;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}}
.ac-kpi{{font-size:1.35rem;color:#e2e8f0;font-weight:600}}
.ac-kpi-sub{{font-size:.72rem;color:#718096;margin-top:2px}}
.ac-help{{font-size:.78rem;color:#a0aec0;line-height:1.45}}
.ac-controls{{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:12px}}
.ac-input,.ac-select{{background:#0f1419;border:1px solid #2d3748;color:#e2e8f0;padding:6px 10px;border-radius:6px;font-size:.82rem}}
.ac-input:focus,.ac-select:focus{{outline:none;border-color:#4299e1}}
.ac-table{{width:100%;border-collapse:collapse;font-size:.82rem}}
.ac-table th{{position:sticky;top:0;background:#1a202c;color:#a0aec0;font-weight:600;text-align:left;padding:8px 10px;border-bottom:1px solid #2d3748;cursor:pointer;user-select:none;white-space:nowrap}}
.ac-table th:hover{{color:#e2e8f0}}
.ac-table th.ac-sorted::after{{content:" \\25B4";color:#63b3ed}}
.ac-table th.ac-sorted-desc::after{{content:" \\25BE";color:#63b3ed}}
.ac-table td{{padding:7px 10px;border-bottom:1px solid #2d3748;color:#e2e8f0;white-space:nowrap}}
.ac-table tr:hover{{background:#2d3748;cursor:pointer}}
.ac-tk{{color:#63b3ed;font-weight:600}}
.ac-num{{text-align:right;font-variant-numeric:tabular-nums}}
.ac-muted{{color:#a0aec0}}
.ac-dim{{color:#718096}}
.ac-flag{{display:inline-block;padding:1px 6px;border-radius:9px;font-size:.68rem;
  background:#2d3748;margin-right:3px;white-space:nowrap;font-weight:600}}
.ac-f-EARLY{{color:#68d391}}
.ac-f-TIGHTENING{{color:#f6e05e}}
.ac-f-SHRINKING{{color:#f6ad55}}
.ac-f-CROWDED{{color:#fc8181}}
.ac-f-NO_BORROW{{color:#a0aec0}}
/* Fee-percentile meter: magnitude in its own 1y context -> single hue */
.ac-meter{{display:inline-block;width:46px;height:5px;background:#2d3748;border-radius:2px;
  overflow:hidden;vertical-align:middle;margin-left:6px}}
.ac-meter>i{{display:block;height:100%;background:#63b3ed;border-radius:2px}}
.ac-pill{{display:inline-block;padding:1px 7px;border-radius:10px;font-size:.7rem;background:#2d3748;color:#a0aec0}}
.ac-legend{{display:flex;gap:14px;flex-wrap:wrap;font-size:.72rem;color:#a0aec0;margin-top:10px}}
.ac-warn{{background:#2c2417;border-color:#5a4520;color:#f6ad55}}
</style>
<div class="ac-wrap">

  <div class="ac-card">
    <div class="ac-label">Actionable Shorts &mdash; SI conviction &times; borrow feasibility</div>
    <div class="ac-help">
      The Candidates tab asks <em>which names look structurally short-worthy</em>.
      This tab asks the second question: <strong>can the trade actually be put on, and at what cost?</strong>
      Each candidate is joined to the IBKR borrow layer &mdash; annualised fee, share
      availability, and where today's fee sits in its own trailing year.
      <strong>Actionable</strong> blends the SI conviction rank (70%) with the borrow-cost
      rank (30%), so a cheap-to-borrow name outranks an equally-convincing one that costs 40%/yr to hold.
      Borrow is daily while FINRA is bi-weekly and lagged ~9 days, so a fee spike against
      flat SI often front-runs the next print.
    </div>
  </div>

  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:14px">
    <div class="ac-card"><div class="ac-label">SI as-of</div><div class="ac-kpi" id="ac-asof">&mdash;</div><div class="ac-kpi-sub" id="ac-asof-sub">&nbsp;</div></div>
    <div class="ac-card"><div class="ac-label">Passed borrow gates</div><div class="ac-kpi" id="ac-count">&mdash;</div><div class="ac-kpi-sub" id="ac-count-sub">&nbsp;</div></div>
    <div class="ac-card"><div class="ac-label">Median fee</div><div class="ac-kpi" id="ac-medfee">&mdash;</div><div class="ac-kpi-sub">annualised</div></div>
    <div class="ac-card"><div class="ac-label">&#9670; Early</div><div class="ac-kpi" id="ac-early">&mdash;</div><div class="ac-kpi-sub">cheap, crowd not there</div></div>
    <div class="ac-card"><div class="ac-label">&#9888; Crowded</div><div class="ac-kpi" id="ac-crowded">&mdash;</div><div class="ac-kpi-sub">squeeze risk</div></div>
  </div>

  <div class="ac-card" id="ac-coverage-note" style="display:none;padding:10px 14px;font-size:.78rem"></div>

  <div class="ac-card" style="padding:14px">
    <div class="ac-controls">
      <input id="ac-search" class="ac-input" type="text" placeholder="Filter ticker / name&hellip;" style="min-width:190px"/>
      <select id="ac-sector" class="ac-select"><option value="">All sectors</option></select>
      <select id="ac-flag" class="ac-select">
        <option value="">All flags</option>
        <option value="EARLY">&#9670; Early only</option>
        <option value="CROWDED">&#9888; Crowded only</option>
        <option value="TIGHTENING">&#8593; Tightening only</option>
        <option value="SHRINKING">&#8595; Shrinking only</option>
        <option value="-CROWDED">Exclude crowded</option>
      </select>
      <label class="ac-dim" style="font-size:.78rem">Max fee %
        <input id="ac-maxfee" class="ac-input" type="number" step="0.5" min="0" placeholder="any" style="width:78px"/>
      </label>
      <label class="ac-dim" style="font-size:.78rem">Min available
        <input id="ac-minavail" class="ac-input" type="number" step="10000" min="0" placeholder="any" style="width:104px"/>
      </label>
      <span class="ac-pill" id="ac-shown">0 shown</span>
      <a id="ac-csv" href="exports/latest/actionable_shorts.csv" download style="margin-left:auto;color:#63b3ed;font-size:.8rem;text-decoration:none">&#11015; Download CSV</a>
    </div>

    <div style="max-height:68vh;overflow:auto;border:1px solid #2d3748;border-radius:6px">
      <table class="ac-table" id="ac-table">
        <thead><tr>
          <th data-k="t">Tk</th>
          <th data-k="n">Name</th>
          <th data-k="s">Sector</th>
          <th data-k="act" class="ac-num ac-sorted-desc">Actionable</th>
          <th data-k="cs" class="ac-num">Composite</th>
          <th data-k="si" class="ac-num">SI%float</th>
          <th data-k="fee" class="ac-num">Fee</th>
          <th data-k="fp" class="ac-num">Fee %ile (1y)</th>
          <th data-k="fc" class="ac-num">&Delta;Fee 20d</th>
          <th data-k="av" class="ac-num">Available</th>
          <th data-k="dtc" class="ac-num">DTC</th>
          <th data-k="fl">Flags</th>
        </tr></thead>
        <tbody id="ac-tbody"></tbody>
      </table>
    </div>

    <div class="ac-legend">
      <span><span class="ac-flag ac-f-EARLY">&#9670; EARLY</span> cheap borrow, low in its own year</span>
      <span><span class="ac-flag ac-f-TIGHTENING">&#8593; TIGHTENING</span> fee rising fast</span>
      <span><span class="ac-flag ac-f-SHRINKING">&#8595; SHRINKING</span> availability falling</span>
      <span><span class="ac-flag ac-f-CROWDED">&#9888; CROWDED</span> top-decile fee &mdash; squeeze risk</span>
    </div>
    <div class="ac-dim" style="font-size:.76rem;margin-top:8px">
      Tip: click a row to chart it on the Trend tab. Click headers to sort. Fees are annualised percent.
    </div>
  </div>
</div>
</div>

<script>
var ACTIONABLE = {payload};

window.renderActionable = (function(){{
  var state = {{sortKey:"act", sortDesc:true}};
  var FLAG_GLYPH = {{EARLY:"\\u25C6", TIGHTENING:"\\u2191", SHRINKING:"\\u2193",
                    CROWDED:"\\u26A0", NO_BORROW:"\\u25CB"}};

  function fmtPct(v,d){{ if(v===null||v===undefined) return "\\u2014"; return (v*100).toFixed(d===undefined?1:d)+"%"; }}
  function fmtFee(v){{ if(v===null||v===undefined) return "\\u2014"; return v.toFixed(2)+"%"; }}
  function fmtNum(v,d){{ if(v===null||v===undefined) return "\\u2014"; return v.toFixed(d===undefined?1:d); }}
  function fmtSignedPts(v){{
    if(v===null||v===undefined) return "\\u2014";
    return (v>0?"+":"")+v.toFixed(2);
  }}
  function fmtShares(v){{
    if(v===null||v===undefined) return "\\u2014";
    if(v>=1e6) return (v/1e6).toFixed(1)+"M";
    if(v>=1e3) return (v/1e3).toFixed(0)+"K";
    return v.toFixed(0);
  }}
  function meter(p){{
    if(p===null||p===undefined) return "";
    var w = Math.max(2, Math.min(100, p*100));
    return '<span class="ac-meter"><i style="width:'+w.toFixed(0)+'%"></i></span>';
  }}
  function flagCells(fl){{
    if(!fl) return "";
    return fl.split("|").filter(Boolean).map(function(f){{
      var g = FLAG_GLYPH[f] || "";
      return '<span class="ac-flag ac-f-'+f+'">'+g+' '+f+'</span>';
    }}).join("");
  }}

  function median(xs){{
    var v = xs.filter(function(x){{return x!==null&&x!==undefined;}}).slice().sort(function(a,b){{return a-b;}});
    if(!v.length) return null;
    var m = Math.floor(v.length/2);
    return v.length%2 ? v[m] : (v[m-1]+v[m])/2;
  }}

  function populateSectors(rows){{
    var sel=document.getElementById("ac-sector");
    if(!sel || sel.dataset.populated) return;
    var seen={{}}, out=[];
    rows.forEach(function(r){{ if(r.s && !seen[r.s]){{seen[r.s]=1;out.push(r.s);}} }});
    out.sort().forEach(function(s){{
      var o=document.createElement("option"); o.value=s; o.textContent=s; sel.appendChild(o);
    }});
    sel.dataset.populated="1";
  }}

  function applyFilters(rows){{
    var q=(document.getElementById("ac-search").value||"").trim().toLowerCase();
    var sec=document.getElementById("ac-sector").value;
    var flag=document.getElementById("ac-flag").value;
    var maxFee=parseFloat(document.getElementById("ac-maxfee").value);
    var minAv=parseFloat(document.getElementById("ac-minavail").value);
    return rows.filter(function(r){{
      if(q){{
        var hay=((r.t||"")+" "+(r.n||"")).toLowerCase();
        if(hay.indexOf(q)<0) return false;
      }}
      if(sec && r.s!==sec) return false;
      if(flag){{
        if(flag.charAt(0)==="-"){{
          if((r.fl||"").indexOf(flag.slice(1))>=0) return false;
        }} else if((r.fl||"").indexOf(flag)<0) return false;
      }}
      if(!isNaN(maxFee) && (r.fee===null || r.fee>maxFee)) return false;
      if(!isNaN(minAv) && (r.av===null || r.av<minAv)) return false;
      return true;
    }});
  }}

  function sortRows(rows){{
    var k=state.sortKey, desc=state.sortDesc;
    return rows.slice().sort(function(a,b){{
      var av=a[k], bv=b[k];
      var an=(av===null||av===undefined), bn=(bv===null||bv===undefined);
      if(an&&bn) return 0;
      if(an) return 1;
      if(bn) return -1;
      if(typeof av==="string"){{ var s=av.localeCompare(bv); return desc?-s:s; }}
      return desc?(bv-av):(av-bv);
    }});
  }}

  function renderRow(r){{
    return '<tr data-ticker="'+(r.t||"")+'">'
      + '<td class="ac-tk">'+(r.t||"")+'</td>'
      + '<td class="ac-muted">'+(r.n||"")+'</td>'
      + '<td class="ac-muted">'+(r.s||"")+'</td>'
      + '<td class="ac-num"><strong>'+fmtNum(r.act,1)+'</strong></td>'
      + '<td class="ac-num ac-muted">'+fmtNum(r.cs,1)+'</td>'
      + '<td class="ac-num">'+fmtPct(r.si,1)+'</td>'
      + '<td class="ac-num">'+fmtFee(r.fee)+'</td>'
      + '<td class="ac-num">'+(r.fp===null?"\\u2014":(r.fp*100).toFixed(0)+"th")+meter(r.fp)+'</td>'
      + '<td class="ac-num ac-muted">'+fmtSignedPts(r.fc)+'</td>'
      + '<td class="ac-num">'+fmtShares(r.av)+'</td>'
      + '<td class="ac-num ac-muted">'+fmtNum(r.dtc,1)+'</td>'
      + '<td>'+flagCells(r.fl)+'</td>'
      + '</tr>';
  }}

  function renderTable(){{
    var rows = sortRows(applyFilters(ACTIONABLE.rows));
    document.getElementById("ac-tbody").innerHTML = rows.map(renderRow).join("");
    document.getElementById("ac-shown").textContent = rows.length + " shown";
  }}

  function updateSortHeaders(){{
    document.querySelectorAll("#ac-table th").forEach(function(th){{
      th.classList.remove("ac-sorted","ac-sorted-desc");
      if(th.dataset.k===state.sortKey) th.classList.add(state.sortDesc?"ac-sorted-desc":"ac-sorted");
    }});
  }}

  function wire(){{
    document.querySelectorAll("#ac-table th").forEach(function(th){{
      th.onclick=function(){{
        var k=th.dataset.k; if(!k) return;
        if(state.sortKey===k) state.sortDesc=!state.sortDesc;
        else {{ state.sortKey=k; state.sortDesc=(["t","n","s","fl"].indexOf(k)<0); }}
        updateSortHeaders(); renderTable();
      }};
    }});
    ["ac-search","ac-sector","ac-flag","ac-maxfee","ac-minavail"].forEach(function(id){{
      var el=document.getElementById(id);
      if(el) el.oninput=el.onchange=renderTable;
    }});
    document.getElementById("ac-tbody").onclick=function(e){{
      var tr=e.target.closest("tr"); if(!tr) return;
      var tk=tr.getAttribute("data-ticker"); if(!tk) return;
      switchTab("trend");
      if(window.addTicker) addTicker(tk);
    }};
  }}

  function countFlag(rows, f){{
    return rows.filter(function(r){{return (r.fl||"").indexOf(f)>=0;}}).length;
  }}

  return function render(){{
    if(!ACTIONABLE || !ACTIONABLE.rows) return;
    var rows = ACTIONABLE.rows, m = ACTIONABLE.meta || {{}};
    document.getElementById("ac-asof").textContent = ACTIONABLE.asof || "\\u2014";
    document.getElementById("ac-asof-sub").textContent = m.borrow_asof ? ("borrow " + m.borrow_asof) : "\\u00a0";
    document.getElementById("ac-count").textContent = rows.length.toLocaleString();
    document.getElementById("ac-count-sub").textContent =
      (m.si_candidates ? ("of " + m.si_candidates.toLocaleString() + " SI candidates") : "\\u00a0");
    var mf = median(rows.map(function(r){{return r.fee;}}));
    document.getElementById("ac-medfee").textContent = mf===null ? "\\u2014" : mf.toFixed(2)+"%";
    document.getElementById("ac-early").textContent = countFlag(rows,"EARLY").toLocaleString();
    document.getElementById("ac-crowded").textContent = countFlag(rows,"CROWDED").toLocaleString();

    // Coverage is stated, never silently truncated.
    if(m.uncovered && m.uncovered > 0){{
      var el=document.getElementById("ac-coverage-note");
      el.className="ac-card ac-warn";
      el.style.display="block";
      el.innerHTML = "&#9888; " + m.uncovered.toLocaleString() + " of " +
        (m.si_candidates||0).toLocaleString() + " SI candidates have no borrow coverage and are " +
        "not shown. They are <strong>unmeasured, not un-borrowable</strong> \\u2014 the daily backfill " +
        "covers roughly " + (m.borrow_universe||0).toLocaleString() + " symbols.";
    }}

    populateSectors(rows);
    if(!document.getElementById("ac-table").dataset.wired){{
      wire();
      document.getElementById("ac-table").dataset.wired="1";
    }}
    updateSortHeaders();
    renderTable();
  }};
}})();

(function(){{
  var prior = window.switchTab;
  window.switchTab = function(name){{
    prior(name);
    if(name==="actionable"){{ setTimeout(window.renderActionable||function(){{}},60); }}
  }};
}})();
</script>
{PATCH_END}
"""


def read_manifest_meta(csv_path: Path) -> dict:
    """Pull coverage counts out of the run manifest, if present."""
    meta: dict = {}
    man = csv_path.parent / "actionable_manifest.txt"
    if not man.exists():
        return meta
    try:
        txt = man.read_text(encoding="utf-8")
    except OSError:
        return meta
    m = re.search(r"SI candidates:\s+([\d,]+)", txt)
    if m:
        meta["si_candidates"] = int(m.group(1).replace(",", ""))
    m = re.search(r"Borrow universe:\s+([\d,]+)", txt)
    if m:
        meta["borrow_universe"] = int(m.group(1).replace(",", ""))
    m = re.search(r"Borrow coverage:\s+([\d,]+)", txt)
    if m and "si_candidates" in meta:
        meta["uncovered"] = meta["si_candidates"] - int(m.group(1).replace(",", ""))
    return meta


def main() -> int:
    if not CSV.exists():
        print(f"ERROR: {CSV} not found. Run build_actionable.py first.", file=sys.stderr)
        return 1
    if not DASHBOARD.exists():
        print(f"ERROR: {DASHBOARD} not found.", file=sys.stderr)
        return 1

    print(f"Loading {CSV} ...")
    df = pd.read_csv(CSV)
    print(f"  {len(df):,} rows")

    rows = build_rows(df)
    asof = (
        str(df["settlement_date"].iloc[0])[:10]
        if "settlement_date" in df.columns and len(df)
        else datetime.now().strftime("%Y-%m-%d")
    )
    meta = read_manifest_meta(CSV)
    if not meta:
        print("  note: actionable_manifest.txt not found beside the CSV — "
              "the in-tab coverage note will be omitted")

    patch = render_patch(rows, asof, meta)

    print(f"Reading dashboard ({DASHBOARD.stat().st_size/1e6:.1f} MB) ...")
    html = DASHBOARD.read_text(encoding="utf-8")
    original_len = len(html)

    # Strip any prior patch (idempotency)
    if PATCH_START in html and PATCH_END in html:
        before = len(html)
        # Consume surrounding newlines too, collapsing to exactly one, so
        # repeated refreshes are byte-stable rather than leaking blank lines.
        html = re.sub(
            r"\n*" + re.escape(PATCH_START) + r".*?" + re.escape(PATCH_END) + r"\n*",
            "\n",
            html,
            flags=re.DOTALL,
        )
        html = html.replace(NAV_TAB, "")
        print(f"  removed prior patch ({before - len(html):,} bytes)")

    # Nav tab: after Candidates if present, else after Screener
    if CANDIDATES_ANCHOR in html:
        anchor = CANDIDATES_ANCHOR
    else:
        anchor = (
            '  <div class="tab" onclick="switchTab(\'screener\')">'
            '&#128269; Screener</div>\n'
        )
        if anchor not in html:
            print("ERROR: could not find a nav anchor (Candidates or Screener).",
                  file=sys.stderr)
            return 2
        print("  note: Candidates tab absent; anchoring to Screener")
    html = html.replace(anchor, anchor + NAV_TAB, 1)

    if "</body>" not in html:
        print("ERROR: no </body> found.", file=sys.stderr)
        return 3
    html = html.replace("</body>", patch + "\n</body>", 1)

    DASHBOARD.write_text(html, encoding="utf-8")
    print(f"\nWrote {DASHBOARD.name}  ({DASHBOARD.stat().st_size/1e6:.1f} MB, "
          f"{len(html)-original_len:+,} bytes)")
    print(f"  rows embedded: {len(rows):,}")
    print(f"  SI as-of:      {asof}")
    if meta.get("uncovered"):
        print(f"  coverage note: {meta['uncovered']:,} uncovered candidates disclosed in-tab")
    print("\nNEXT: run `python validate_dashboard.py` before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
