#!/usr/bin/env python3
"""Patch si_dashboard.html to add a Candidates tab fed by exports/latest/.

Phase 1A integration: Signal D (sector & history outlier short) only.
Re-running this script on a freshly regenerated dashboard or after a new
candidates refresh is idempotent — it strips any prior patch block
(by unique sentinel) before re-inserting.

Inputs:
  exports/latest/candidates_outlier_short.csv

Output:
  si_dashboard.html (in place; original backed up to .bak_pre_candidates
  on first run by hand)
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
CSV = ROOT / "exports" / "latest" / "candidates_outlier_short.csv"

PATCH_START = "<!-- CANDIDATES_PATCH_START -->"
PATCH_END = "<!-- CANDIDATES_PATCH_END -->"


def _f(v, ndigits=3):
    """Round to ndigits, return None if NaN/None so JSON omits/uses null."""
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
            "n": (r.get("name") or "")[:60],
            "s": r.get("sector") or "",
            "mc": r.get("mc_bucket") or "",
            "si": _f(r.get("si_pct_float"), 4),
            "rel": _f(r.get("si_pct_float_sector_rel"), 2),
            "hist": _f(r.get("si_pct_float_history_pct"), 3),
            "cv": _f(r.get("cover_velocity"), 3),
            "dtc": _f(r.get("dtc"), 1),
            "cs": _f(r.get("composite_score"), 1),
            "d": int(r["decile"]) if pd.notna(r.get("decile")) else None,
            "note": r.get("notes") or "",
        })
    return rows


def render_patch(rows: list[dict], asof: str) -> str:
    payload = json.dumps(
        {"asof": asof, "rows": rows},
        separators=(",", ":"),
        ensure_ascii=False,
    )

    nav_tab = (
        '  <div class="tab" onclick="switchTab(\'candidates\')">'
        '&#127919; Candidates</div>\n'
    )

    tab_html = f"""
{PATCH_START}
<div id="tab-candidates" class="tab-content">
<style>
.cd-wrap{{max-width:1400px;margin:0 auto}}
.cd-card{{background:#1a202c;padding:16px;border-radius:10px;border:1px solid #2d3748;margin-bottom:14px}}
.cd-label{{font-size:.72rem;color:#a0aec0;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}}
.cd-kpi{{font-size:1.35rem;color:#e2e8f0;font-weight:600}}
.cd-help{{font-size:.78rem;color:#a0aec0;line-height:1.45}}
.cd-controls{{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:12px}}
.cd-input,.cd-select{{background:#0f1419;border:1px solid #2d3748;color:#e2e8f0;padding:6px 10px;border-radius:6px;font-size:.82rem}}
.cd-input:focus,.cd-select:focus{{outline:none;border-color:#4299e1}}
.cd-table{{width:100%;border-collapse:collapse;font-size:.82rem}}
.cd-table th{{position:sticky;top:0;background:#1a202c;color:#a0aec0;font-weight:600;text-align:left;padding:8px 10px;border-bottom:1px solid #2d3748;cursor:pointer;user-select:none;white-space:nowrap}}
.cd-table th:hover{{color:#e2e8f0}}
.cd-table th.cd-sorted::after{{content:" \\25B4";color:#63b3ed}}
.cd-table th.cd-sorted-desc::after{{content:" \\25BE";color:#63b3ed}}
.cd-table td{{padding:7px 10px;border-bottom:1px solid #2d3748;color:#e2e8f0;white-space:nowrap}}
.cd-table tr:hover{{background:#2d3748;cursor:pointer}}
.cd-tk{{color:#63b3ed;font-weight:600}}
.cd-num{{text-align:right;font-variant-numeric:tabular-nums}}
.cd-dec-10{{color:#fc8181}}
.cd-dec-9{{color:#f6ad55}}
.cd-dec-default{{color:#e2e8f0}}
.cd-pill{{display:inline-block;padding:1px 7px;border-radius:10px;font-size:.7rem;background:#2d3748;color:#a0aec0}}
.cd-count{{font-size:.78rem;color:#718096;margin-bottom:8px}}
</style>
<div class="cd-wrap">
  <div class="cd-card">
    <div class="cd-label">Signal D &mdash; Sector &amp; History Outlier Short</div>
    <div class="cd-help">
      Surfaces names where absolute SI% is modest but is structurally elevated
      both <strong>relative to sector peers</strong> and <strong>vs the ticker's own 3-year history</strong>.
      Catches BRBR-style differentiated bearish positioning that absolute-SI screens miss.
      Composite is the mean of four equal-weighted percentile ranks (sector Z,
      sector multiple, history %ile, cover velocity) computed within (date, market-class bucket).
    </div>
  </div>

  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px">
    <div class="cd-card"><div class="cd-label">As-of</div><div class="cd-kpi" id="cd-asof">&mdash;</div></div>
    <div class="cd-card"><div class="cd-label">Candidates (gates passed)</div><div class="cd-kpi" id="cd-count-all">&mdash;</div></div>
    <div class="cd-card"><div class="cd-label">Top decile</div><div class="cd-kpi" id="cd-count-top">&mdash;</div></div>
    <div class="cd-card"><div class="cd-label">BRBR-style (SI&lt;10%)</div><div class="cd-kpi" id="cd-count-brbr">&mdash;</div></div>
  </div>

  <div class="cd-card" style="padding:14px">
    <div class="cd-controls">
      <input id="cd-search" class="cd-input" type="text" placeholder="Filter ticker / name&hellip;" style="min-width:200px"/>
      <select id="cd-sector" class="cd-select"><option value="">All sectors</option></select>
      <select id="cd-mc" class="cd-select">
        <option value="">All mkt class</option>
        <option value="NMS">NMS</option>
        <option value="SC">SmallCap</option>
        <option value="OTC">OTC</option>
        <option value="OTHER">Other</option>
      </select>
      <select id="cd-decile" class="cd-select">
        <option value="">All deciles</option>
        <option value="10">Decile 10 only</option>
        <option value="9">Decile 9+</option>
        <option value="8">Decile 8+</option>
        <option value="7">Decile 7+</option>
      </select>
      <label style="display:flex;gap:6px;align-items:center;font-size:.78rem;color:#a0aec0;cursor:pointer">
        <input type="checkbox" id="cd-brbr-only"/>BRBR-style only (SI&lt;10%, sector&ge;2x)
      </label>
      <span class="cd-pill" id="cd-shown">0 shown</span>
      <a id="cd-csv" href="exports/latest/candidates_outlier_short.csv" download style="margin-left:auto;color:#63b3ed;font-size:.8rem;text-decoration:none">&#11015; Download CSV</a>
    </div>
    <div style="max-height:70vh;overflow:auto;border:1px solid #2d3748;border-radius:6px">
      <table class="cd-table" id="cd-table">
        <thead><tr>
          <th data-k="t">Tk</th>
          <th data-k="n">Name</th>
          <th data-k="s">Sector</th>
          <th data-k="mc">MC</th>
          <th data-k="cs" class="cd-num cd-sorted-desc">Composite</th>
          <th data-k="d" class="cd-num">Decile</th>
          <th data-k="si" class="cd-num">SI%float</th>
          <th data-k="rel" class="cd-num">vs Sector</th>
          <th data-k="hist" class="cd-num">Hist %ile</th>
          <th data-k="cv" class="cd-num">Cover Vel.</th>
          <th data-k="dtc" class="cd-num">DTC</th>
          <th data-k="note">Note</th>
        </tr></thead>
        <tbody id="cd-tbody"></tbody>
      </table>
    </div>
    <div class="cd-count" style="margin-top:8px">
      Tip: click a row to chart it on the Trend tab. Click column headers to sort.
    </div>
  </div>
</div>
</div>

<script>
var CANDIDATES = {payload};

window.renderCandidates = (function(){{
  var state = {{sortKey:"cs", sortDesc:true}};

  function fmtPct(v, d){{
    if(v===null||v===undefined) return "&mdash;";
    return (v*100).toFixed(d===undefined?1:d) + "%";
  }}
  function fmtMul(v){{
    if(v===null||v===undefined) return "&mdash;";
    return v.toFixed(2) + "x";
  }}
  function fmtNum(v, d){{
    if(v===null||v===undefined) return "&mdash;";
    return v.toFixed(d===undefined?1:d);
  }}
  function decileClass(d){{
    if(d===10) return "cd-dec-10";
    if(d===9) return "cd-dec-9";
    return "cd-dec-default";
  }}

  function uniqueSectors(rows){{
    var seen={{}}, out=[];
    rows.forEach(function(r){{if(r.s && !seen[r.s]){{seen[r.s]=1;out.push(r.s);}}}});
    out.sort();
    return out;
  }}

  function populateSectorOptions(rows){{
    var sel=document.getElementById("cd-sector");
    if(!sel || sel.dataset.populated) return;
    uniqueSectors(rows).forEach(function(s){{
      var o=document.createElement("option");
      o.value=s; o.textContent=s;
      sel.appendChild(o);
    }});
    sel.dataset.populated="1";
  }}

  function applyFilters(rows){{
    var q=(document.getElementById("cd-search").value||"").trim().toLowerCase();
    var sec=document.getElementById("cd-sector").value;
    var mc=document.getElementById("cd-mc").value;
    var dMin=parseInt(document.getElementById("cd-decile").value||"0",10);
    var brbrOnly=document.getElementById("cd-brbr-only").checked;
    return rows.filter(function(r){{
      if(q){{
        var hay=((r.t||"")+" "+(r.n||"")).toLowerCase();
        if(hay.indexOf(q)<0) return false;
      }}
      if(sec && r.s!==sec) return false;
      if(mc && r.mc!==mc) return false;
      if(dMin && (r.d===null || r.d<dMin)) return false;
      if(brbrOnly){{
        if(r.si===null || r.si>=0.10) return false;
        if(r.rel===null || r.rel<2.0) return false;
      }}
      return true;
    }});
  }}

  function sortRows(rows){{
    var k=state.sortKey, desc=state.sortDesc;
    var copy=rows.slice();
    copy.sort(function(a,b){{
      var av=a[k], bv=b[k];
      var an=(av===null||av===undefined), bn=(bv===null||bv===undefined);
      if(an && bn) return 0;
      if(an) return 1;
      if(bn) return -1;
      if(typeof av==="string"){{
        var s=av.localeCompare(bv);
        return desc?-s:s;
      }}
      return desc?(bv-av):(av-bv);
    }});
    return copy;
  }}

  function renderRow(r){{
    return '<tr data-ticker="'+(r.t||"")+'">'
      + '<td class="cd-tk">'+(r.t||"")+'</td>'
      + '<td style="color:#a0aec0">'+(r.n||"")+'</td>'
      + '<td style="color:#a0aec0">'+(r.s||"")+'</td>'
      + '<td style="color:#718096">'+(r.mc||"")+'</td>'
      + '<td class="cd-num"><strong>'+fmtNum(r.cs,1)+'</strong></td>'
      + '<td class="cd-num '+decileClass(r.d)+'">'+(r.d===null?"&mdash;":r.d)+'</td>'
      + '<td class="cd-num">'+fmtPct(r.si,1)+'</td>'
      + '<td class="cd-num">'+fmtMul(r.rel)+'</td>'
      + '<td class="cd-num">'+(r.hist===null?"&mdash;":(r.hist*100).toFixed(0)+"th")+'</td>'
      + '<td class="cd-num">'+fmtPct(r.cv,1)+'</td>'
      + '<td class="cd-num">'+fmtNum(r.dtc,1)+'</td>'
      + '<td style="color:#a0aec0;font-size:.76rem">'+(r.note||"")+'</td>'
      + '</tr>';
  }}

  function renderTable(){{
    var rows = applyFilters(CANDIDATES.rows);
    rows = sortRows(rows);
    document.getElementById("cd-tbody").innerHTML = rows.map(renderRow).join("");
    document.getElementById("cd-shown").textContent = rows.length + " shown";
  }}

  function updateSortHeaders(){{
    var ths=document.querySelectorAll("#cd-table th");
    ths.forEach(function(th){{
      th.classList.remove("cd-sorted","cd-sorted-desc");
      if(th.dataset.k===state.sortKey){{
        th.classList.add(state.sortDesc?"cd-sorted-desc":"cd-sorted");
      }}
    }});
  }}

  function wireHeaderSort(){{
    document.querySelectorAll("#cd-table th").forEach(function(th){{
      th.onclick=function(){{
        var k=th.dataset.k;
        if(!k) return;
        if(state.sortKey===k) state.sortDesc=!state.sortDesc;
        else {{ state.sortKey=k; state.sortDesc=(k!=="t" && k!=="n" && k!=="s" && k!=="mc"); }}
        updateSortHeaders();
        renderTable();
      }};
    }});
  }}

  function wireFilters(){{
    ["cd-search","cd-sector","cd-mc","cd-decile"].forEach(function(id){{
      var el=document.getElementById(id);
      if(el) el.oninput=el.onchange=renderTable;
    }});
    var br=document.getElementById("cd-brbr-only");
    if(br) br.onchange=renderTable;
  }}

  function wireRowClick(){{
    document.getElementById("cd-tbody").onclick=function(e){{
      var tr=e.target.closest("tr");
      if(!tr) return;
      var tk=tr.getAttribute("data-ticker");
      if(!tk) return;
      switchTab("trend");
      if(window.addTicker) addTicker(tk);
    }};
  }}

  return function render(){{
    if(!CANDIDATES || !CANDIDATES.rows){{ return; }}
    var rows = CANDIDATES.rows;
    document.getElementById("cd-asof").textContent = CANDIDATES.asof || "—";
    document.getElementById("cd-count-all").textContent = rows.length.toLocaleString();
    document.getElementById("cd-count-top").textContent = rows.filter(function(r){{return r.d===10;}}).length.toLocaleString();
    document.getElementById("cd-count-brbr").textContent = rows.filter(function(r){{return r.si!==null && r.si<0.10 && r.rel!==null && r.rel>=2.0;}}).length.toLocaleString();
    populateSectorOptions(rows);
    if(!document.getElementById("cd-table").dataset.wired){{
      wireHeaderSort();
      wireFilters();
      wireRowClick();
      document.getElementById("cd-table").dataset.wired="1";
    }}
    updateSortHeaders();
    renderTable();
  }};
}})();

(function(){{
  var prior = window.switchTab;
  window.switchTab = function(name){{
    prior(name);
    if(name==="candidates"){{ setTimeout(window.renderCandidates||function(){{}},60); }}
  }};
}})();
</script>
{PATCH_END}
"""

    return nav_tab, tab_html


def main() -> int:
    if not CSV.exists():
        print(f"ERROR: {CSV} not found. Run update_analytics.py first.", file=sys.stderr)
        return 1
    if not DASHBOARD.exists():
        print(f"ERROR: {DASHBOARD} not found.", file=sys.stderr)
        return 1

    print(f"Loading {CSV} ...")
    df = pd.read_csv(CSV)
    print(f"  {len(df):,} rows")

    rows = build_rows(df)
    asof = str(df["settlement_date"].iloc[0]) if "settlement_date" in df.columns and len(df) else datetime.now().strftime("%Y-%m-%d")
    nav_tab, tab_html = render_patch(rows, asof)

    print(f"Reading dashboard ({DASHBOARD.stat().st_size/1e6:.1f} MB) ...")
    html = DASHBOARD.read_text(encoding="utf-8")

    # Strip any prior patch
    if PATCH_START in html and PATCH_END in html:
        before = len(html)
        html = re.sub(
            re.escape(PATCH_START) + r".*?" + re.escape(PATCH_END) + r"\n?",
            "",
            html,
            flags=re.DOTALL,
        )
        print(f"  removed prior patch ({before - len(html):,} bytes)")
        nav_marker = (
            '  <div class="tab" onclick="switchTab(\'candidates\')">'
            '&#127919; Candidates</div>\n'
        )
        html = html.replace(nav_marker, "")

    # Inject nav tab right after Screener
    screener_anchor = (
        "  <div class=\"tab\" onclick=\"switchTab('screener')\">"
        "&#128269; Screener</div>\n"
    )
    if screener_anchor not in html:
        print("ERROR: could not find Screener tab anchor in nav.", file=sys.stderr)
        return 2
    html = html.replace(
        screener_anchor,
        screener_anchor + nav_tab,
        1,
    )

    # Inject tab body + script just before </body>
    if "</body>" not in html:
        print("ERROR: no </body> found.", file=sys.stderr)
        return 3
    html = html.replace("</body>", tab_html + "\n</body>", 1)

    DASHBOARD.write_text(html, encoding="utf-8")
    new_size = DASHBOARD.stat().st_size
    print(f"\nWrote {DASHBOARD}  ({new_size/1e6:.1f} MB)")
    print(f"  rows embedded: {len(rows):,}")
    print(f"  as-of:         {asof}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
