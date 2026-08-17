"""Render the analytics export as a standalone HTML dashboard.

Reads `reports/data/analytics.json` -- committed by the monthly digest workflow --
and writes a self-contained page. No network, no BigQuery credentials, no
dependencies beyond the standard library, because the whole point of the export
is that reading the numbers should not require cloud access.

Two things here are decisions rather than defaults, and both come from looking at
the rendered output rather than from reasoning about the data:

  * **Small multiples with one line per year, not a continuous series.** Every
    January in the ONS data is exactly 100 -- the index rebases annually -- so a
    single 2016-2026 line is eleven sawtooth resets with the actual seasonal
    signal buried inside them. One panel per series, one thin line per year
    against a bold median, on a shared y-scale so panels stay comparable.

  * **Gaps are drawn as breaks.** Nine months are missing from every series (the
    2020-21 lockdown windows, when ONS suspended collection and imputed the
    item). Joining across them would draw a straight line through four months of
    nothing and read as data. `segments()` exists solely to prevent that.

Peak labels sit on filled chips rather than bare text: the year spaghetti passes
behind them and struck-through digits were the result. A stroke halo was tried
first and made it worse -- 3.5px on 12px monospace closes the counters.

Usage:  python tools/dashboard.py [--in PATH] [--out PATH]
"""

import json, collections, statistics as st, pathlib

import argparse

_p = argparse.ArgumentParser(description=__doc__)
_p.add_argument("--in", dest="src", type=pathlib.Path,
                default=pathlib.Path("reports/data/analytics.json"))
_p.add_argument("--out", type=pathlib.Path,
                default=pathlib.Path("reports/dashboard.html"))
_a = _p.parse_args()
SRC, OUT = _a.src, _a.out
d = json.loads(SRC.read_text(encoding="utf-8"))

SERIES = [("domestic",1,"Domestic","1 month","dom"),
          ("european",1,"European","1 month","eur"),
          ("european",3,"European","3 months","eur"),
          ("long_haul",1,"Long haul","1 month","lh"),
          ("long_haul",3,"Long haul","3 months","lh"),
          ("long_haul",6,"Long haul","6 months","lh")]
MI = ["J","F","M","A","M","J","J","A","S","O","N","D"]
MN = ["January","February","March","April","May","June","July","August",
      "September","October","November","December"]

g = collections.defaultdict(dict)
for r in d["published_series"]:
    g[(r["haul_category"], r["months_ahead"])][r["index_month"][:7]] = r["index_value"]

allm, y, m = [], 2016, 1
while (y, m) <= (2026, 2):
    allm.append(f"{y}-{m:02d}"); m += 1
    if m == 13: y, m = y+1, 1
present = set(g[("domestic",1)])
missing = [k for k in allm if k not in present]
years = sorted({k[:4] for k in allm})

PW, PH, L, R, T, B = 340, 190, 38, 10, 14, 24
YMIN, YMAX = 60, 300
sx = lambda i: L + i*(PW-L-R)/11
sy = lambda v: T + (YMAX-v)*(PH-T-B)/(YMAX-YMIN)
pth = lambda p: "M" + " L".join(f"{a:.1f},{b:.1f}" for a,b in p)

def segments(s, yr):
    """Split a year into contiguous runs, so a gap is a gap and not a straight
    line drawn across four missing months."""
    out, cur = [], []
    for i in range(12):
        k = f"{yr}-{i+1:02d}"
        if k in s: cur.append((sx(i), sy(s[k])))
        else:
            if len(cur) > 1: out.append(cur)
            cur = []
    if len(cur) > 1: out.append(cur)
    return out

panels, table, peaks, latest = [], [], [], []
for haul, ma, label, win, cls in SERIES:
    s = g[(haul, ma)]
    spa = "".join(f'<path class="yr" d="{pth(seg)}"/>'
                  for yr in years for seg in segments(s, yr))
    med, lo, hi, ns = [], [], [], []
    for i in range(12):
        v = [s[f"{yr}-{i+1:02d}"] for yr in years if f"{yr}-{i+1:02d}" in s]
        med.append(st.median(v)); lo.append(min(v)); hi.append(max(v)); ns.append(len(v))
    mp = [(sx(i), sy(med[i])) for i in range(12)]
    pi = max(range(12), key=lambda i: med[i])
    peaks.append((label, win, MN[pi], med[pi], max(s.values()), min(s.values())))
    feb = s.get("2026-02")
    febtyp = st.median([s[f"{yr}-02"] for yr in years if f"{yr}-02" in s and yr != "2026"])
    latest.append((label, win, feb, febtyp, cls))

    lw = round(len(f"{med[pi]:.0f}") * 7.3 + 9)
    gr = "".join(f'<line class="gl" x1="{L}" x2="{PW-R}" y1="{sy(v):.1f}" y2="{sy(v):.1f}"/>'
                 for v in (150,200,250,300))
    yt = "".join(f'<text class="yt" x="{L-7}" y="{sy(v)+3.5:.1f}">{v}</text>' for v in (100,200,300))
    xt = "".join(f'<text class="xt" x="{sx(i):.1f}" y="{PH-7}">{MI[i]}</text>' for i in range(12))
    hot = "".join(f'<rect class="hot" x="{sx(i)-14:.1f}" y="{T}" width="28" height="{PH-T-B}" '
                  f'data-m="{MN[i]}" data-med="{med[i]:.1f}" data-lo="{lo[i]:.1f}" '
                  f'data-hi="{hi[i]:.1f}" data-n="{ns[i]}"/>' for i in range(12))
    panels.append(f'''<figure class="panel {cls}">
 <figcaption><span class="pl">{label}</span><span class="pw">{win} ahead</span></figcaption>
 <svg viewBox="0 0 {PW} {PH}" role="img" aria-label="{label}, {win} ahead. Seasonal index, January equals 100. Median peaks in {MN[pi]} at {med[pi]:.0f}. Range across 2016 to 2026: {min(s.values()):.0f} to {max(s.values()):.0f}.">
  {gr}<line class="base" x1="{L}" x2="{PW-R}" y1="{sy(100):.1f}" y2="{sy(100):.1f}"/>
  {spa}<path class="med" d="{pth(mp)}"/>
  <circle class="pk" cx="{sx(pi):.1f}" cy="{sy(med[pi]):.1f}" r="4"/>
  <rect class="pkbg" x="{sx(pi)-lw/2:.1f}" y="{sy(med[pi])-23:.1f}" width="{lw}" height="15" rx="2.5"/>
  <text class="pkl" x="{sx(pi):.1f}" y="{sy(med[pi])-11.5:.1f}">{med[pi]:.0f}</text>
  {yt}{xt}<g class="cx" hidden><line y1="{T}" y2="{PH-B}"/></g>{hot}
 </svg></figure>''')
    for i in range(12):
        table.append((label, win, MN[i], med[i], lo[i], hi[i], ns[i]))

# --- Today's collection -------------------------------------------------------
# One day deep, so nothing here is a trend. These are diagnostics: is the
# selection rule picking comparable products, and is the target-time rule
# actually constraining the choice?
CLS = {("domestic",1):"dom", ("european",1):"eur", ("european",3):"eur",
       ("long_haul",1):"lh", ("long_haul",3):"lh", ("long_haul",6):"lh"}
LBL = {"domestic":"Domestic", "european":"European", "long_haul":"Long haul"}
dbs = sorted(d["daily_by_series"], key=lambda r: (r["haul_category"], r["months_ahead"]))
routes = d["latest_routes"]
cov = d["coverage"]
tot_ok = sum(r["ok"] for r in dbs)
tot_nd = sum(r["no_data"] for r in dbs)
tot_er = sum(r["errors"] for r in dbs)

def pct_above(r):
    c = r["mean_cheapest_gbp"]
    return None if not c else (r["mean_price_gbp"] - c) / c * 100

# Bar geometry, shared by both diagnostic charts.
BW, BH, BL, BR2 = 470, 26, 106, 78
def bars(rows, val, vmax, fmt, thresh=None, tlabel="", aria=""):
    W = BW - BL - BR2
    out = []
    for i, r in enumerate(rows):
        v = val(r) or 0
        y = i * BH + 5
        w = max(1.5, min(v, vmax) / vmax * W)
        out.append(
            f'<text class="bl" x="{BL-9}" y="{y+13.5}">{LBL[r["haul_category"]]} '
            f'{r["months_ahead"]}m</text>'
            f'<rect class="btr" x="{BL}" y="{y+3}" width="{W}" height="{BH-11}" rx="2.5"/>'
            f'<rect class="bar {CLS[(r["haul_category"], r["months_ahead"])]}" x="{BL}" '
            f'y="{y+3}" width="{w:.1f}" height="{BH-11}" rx="2.5"/>'
            f'<text class="bv" x="{BL+W+7}" y="{y+13.5}">{fmt(r)}</text>')
    base = len(rows) * BH + 2
    out.append(f'<line class="bax" x1="{BL}" x2="{BL}" y1="0" y2="{base}"/>')
    if thresh is not None:
        tx = BL + thresh / vmax * W
        out.append(f'<line class="thr" x1="{tx:.1f}" x2="{tx:.1f}" y1="0" y2="{base}"/>'
                   f'<text class="thl" x="{tx:.1f}" y="{base+15}">{tlabel}</text>')
    return (f'<svg viewBox="0 0 {BW} {base+22}" role="img" aria-label="{aria}">'
            + "".join(out) + "</svg>")

# Label is the percentage alone: the absolute fares are in the table above, and a
# "40%  £122 vs £87" label overruns the viewBox and gets clipped.
gap_svg = bars(dbs, pct_above, 60, lambda r: f'{pct_above(r):.0f}%',
               aria="Percent by which the ONS-rule fare exceeds the cheapest comparable fare, per series.")
mins_svg = bars(dbs, lambda r: r["mean_mins_off_target"], 240,
                lambda r: f'{r["mean_mins_off_target"]:.0f} min', thresh=180, tlabel="180 min",
                aria="Average minutes between the selected departure and the 09:00 target, per series.")

srow = "".join(
    f'<tr><td><span class="dot {CLS[(r["haul_category"], r["months_ahead"])]}"></span>'
    f'{LBL[r["haul_category"]]}</td>'
    f'<td>{r["months_ahead"]} month{"s" if r["months_ahead"] > 1 else ""}</td>'
    f'<td class=n>{r["ok"]}</td><td class=n>{r["no_data"]}</td><td class=n>{r["errors"]}</td>'
    f'<td class=n>£{r["mean_price_gbp"]:.0f}</td><td class=n>£{r["median_price_gbp"]:.0f}</td>'
    f'<td class=n>£{r["geomean_price_gbp"]:.0f}</td>'
    f'<td class=n>{r["mean_considered"]:.1f} / {r["mean_quotes"]:.1f}</td></tr>'
    for r in dbs)

money = lambda v: f"£{v:.0f}" if v else "—"
rrow = "".join(
    f'<tr><td class="mono">{r["route"]}</td><td class=n>{r["months_ahead"]}m</td>'
    f'<td class="mono">{r["departure_date"]}</td>'
    f'<td class=n>{money(r["price_gbp"])}</td>'
    f'<td class=n>{money(r["price_cheapest_gbp"])}</td>'
    f'<td>{r["selected_airline"] or "—"}</td>'
    f'<td class="mono">{(r["selected_departure_ts"] or "")[11:16] or "—"}</td>'
    f'<td class=n>{r["ons_rule_time_delta_minutes"] if r["ons_rule_time_delta_minutes"] is not None else "—"}</td>'
    f'<td class="cb">{(r["candidate_basis"] or "—").replace("_", " ")}</td>'
    f'<td class="st {r["status"]}">{r["status"]}</td></tr>'
    for r in sorted(routes, key=lambda r: (r["haul_category"], r["route"], r["months_ahead"])))

n_nodirect = sum(1 for r in routes if (r["candidate_basis"] or "") == "no_direct_available")
worst_mins = max(dbs, key=lambda r: r["mean_mins_off_target"] or 0)
nd_routes = ", ".join(sorted({r["route"] for r in routes if r["status"] == "no_data"})) or "none"

# Flattened for the f-string below: `{d[key]}` inside an f-string reads `key` as a
# name, not a string, so subscripts would each need nested quotes.
day       = cov.get("panel_last_day", "—")
n_queries = cov.get("panel_rows", 0)
dom_mean  = dbs[0]["mean_price_gbp"] if dbs else 0
dom_geo   = dbs[0]["geomean_price_gbp"] if dbs else 0
wm_label  = LBL[worst_mins["haul_category"]]
wm_win    = worst_mins["months_ahead"]
wm_mins   = worst_mins["mean_mins_off_target"] or 0

CW = 9
cells = "".join(
    f'<rect class="cell {"on" if k in present else "off"}" x="{i*CW}" y="0" '
    f'width="{CW-2}" height="24" rx="1.5" data-k="{k}" data-ok="{1 if k in present else 0}"/>'
    for i, k in enumerate(allm))
yrt = "".join(
    f'<text class="ct" x="{(min(ix)+max(ix))/2*CW+(CW-2)/2:.1f}" y="38">{yr[2:]}</text>'
    for yr in years for ix in [[i for i,k in enumerate(allm) if k[:4]==yr]] if ix)
SW = len(allm)*CW

trow = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td class=n>{e:.1f}</td>"
               f"<td class=n>{f:.1f}</td><td class=n>{h:.1f}</td><td class=n>{i}</td></tr>"
               for a,b,c,e,f,h,i in table)
prow = "".join(f'<tr><td><span class="dot {SERIES[i][4]}"></span>{a}</td><td>{b}</td>'
               f'<td class="pm">{c}</td><td class=n>{v:.0f}</td><td class=n>{mx:.1f}</td>'
               f'<td class=n>{mn:.1f}</td></tr>'
               for i,(a,b,c,v,mx,mn) in enumerate(peaks))
lrow = "".join(f'<tr><td><span class="dot {cl}"></span>{a}</td><td>{b}</td>'
               f'<td class=n>{v:.1f}</td><td class=n>{t:.1f}</td>'
               f'<td class="n dv">{v-t:+.1f}</td></tr>'
               for a,b,v,t,cl in latest)

errs = d.get("errors", {})
panel_state = ("<p class=\"note warn\"><strong>Panel data not yet readable.</strong> "
    "Every collection query in this export returned <code>NotFound</code>: the "
    "<code>current_scrapes</code> view had not been created when the export ran. "
    "<code>ensure_tables</code> creates it at the start of the next collection run, "
    "so this clears itself — but it means nothing below describes our own fares yet.</p>"
    if any("current_scrapes" in v or "NotFound" in v for v in errs.values()) else "")

HTML = f'''<title>Air Fare Seasonality</title>
<style>
:root{{
 color-scheme:light;
 --plane:#eef1f3; --surf:#fbfcfd; --ink:#0e1216; --ink2:#4b535b; --muted:#7d868f;
 --line:#dde3e8; --rule:#c6ced6; --base:#9aa4ad;
 --dom:#2a78d6; --eur:#eb6834; --lh:#1baf7a;
 --domq:rgba(42,120,214,.20); --eurq:rgba(235,104,52,.20); --lhq:rgba(27,175,122,.22);
 --warn:#fab219; --crit:#d03b3b; --good:#006300;
 --chipbg:#e6ebef; --shadow:0 1px 2px rgba(14,18,22,.06),0 6px 20px -12px rgba(14,18,22,.18);
}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
 color-scheme:dark;
 --plane:#0c0e0f; --surf:#171a1c; --ink:#f2f5f7; --ink2:#b3bcc4; --muted:#7f8890;
 --line:#262b2f; --rule:#343b40; --base:#4d555b;
 --dom:#3987e5; --eur:#d95926; --lh:#199e70;
 --domq:rgba(57,135,229,.26); --eurq:rgba(217,89,38,.26); --lhq:rgba(25,158,112,.28);
 --warn:#fab219; --crit:#e06060; --good:#0ca30c;
 --chipbg:#232829; --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -14px rgba(0,0,0,.7);
}}}}
:root[data-theme="dark"]{{
 color-scheme:dark;
 --plane:#0c0e0f; --surf:#171a1c; --ink:#f2f5f7; --ink2:#b3bcc4; --muted:#7f8890;
 --line:#262b2f; --rule:#343b40; --base:#4d555b;
 --dom:#3987e5; --eur:#d95926; --lh:#199e70;
 --domq:rgba(57,135,229,.26); --eurq:rgba(217,89,38,.26); --lhq:rgba(25,158,112,.28);
 --warn:#fab219; --crit:#e06060; --good:#0ca30c;
 --chipbg:#232829; --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -14px rgba(0,0,0,.7);
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--plane);color:var(--ink);
 font:400 16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;
 -webkit-text-size-adjust:100%}}
.wrap{{max-width:1080px;margin:0 auto;padding:clamp(20px,4vw,52px) clamp(16px,4vw,40px) 72px}}
code,.mono,.n,.val{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
 font-variant-numeric:tabular-nums}}
.eyebrow{{font:600 11px/1.4 ui-monospace,Menlo,monospace;letter-spacing:.14em;
 text-transform:uppercase;color:var(--muted);margin:0 0 14px}}
h1{{font-size:clamp(30px,5.2vw,46px);line-height:1.05;letter-spacing:-.022em;
 font-weight:680;margin:0 0 16px;text-wrap:balance}}
.standfirst{{font-size:clamp(17px,2.1vw,19px);color:var(--ink2);max-width:63ch;margin:0}}
.masthead{{border-bottom:2px solid var(--rule);padding-bottom:26px;margin-bottom:30px}}
.meta{{display:flex;flex-wrap:wrap;gap:8px;margin-top:22px}}
.chip{{background:var(--chipbg);color:var(--ink2);border-radius:3px;padding:4px 9px;
 font:500 12px/1.5 ui-monospace,Menlo,monospace}}
h2{{font-size:21px;letter-spacing:-.012em;font-weight:660;margin:0 0 6px;text-wrap:balance}}
h2 .num{{color:var(--muted);font:600 12px/1 ui-monospace,Menlo,monospace;
 letter-spacing:.1em;display:block;margin-bottom:9px}}
section{{margin-top:46px}}
.lede{{color:var(--ink2);max-width:66ch;margin:0 0 22px;font-size:15.5px}}
.tiles{{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(148px,1fr))}}
.tile{{background:var(--surf);border:1px solid var(--line);border-radius:5px;
 padding:15px 16px 14px;box-shadow:var(--shadow)}}
.tile .v{{font:640 27px/1.1 ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums;
 letter-spacing:-.02em;display:block}}
.tile .k{{font-size:12.5px;color:var(--muted);margin-top:5px;display:block;line-height:1.4}}
.tile.flag .v{{color:var(--warn)}}
.grid{{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(288px,1fr))}}
.panel{{background:var(--surf);border:1px solid var(--line);border-radius:5px;
 margin:0;padding:13px 12px 6px;box-shadow:var(--shadow);position:relative}}
figcaption{{display:flex;justify-content:space-between;align-items:baseline;
 gap:10px;padding:0 3px 5px}}
.pl{{font-weight:640;font-size:14.5px}}
.pw{{font:500 11.5px/1 ui-monospace,Menlo,monospace;color:var(--muted);
 letter-spacing:.04em;white-space:nowrap}}
.panel svg{{display:block;width:100%;height:auto;overflow:visible}}
.gl{{stroke:var(--line);stroke-width:1}}
.base{{stroke:var(--base);stroke-width:1;stroke-dasharray:2 3}}
.yr{{fill:none;stroke-width:1.25;stroke-linecap:round;opacity:.5}}
.med{{fill:none;stroke-width:2.4;stroke-linecap:round;stroke-linejoin:round}}
.yt,.xt,.ct{{font:500 10px ui-monospace,Menlo,monospace;fill:var(--muted)}}
.yt{{text-anchor:end}} .xt,.ct{{text-anchor:middle}}
.pkl{{font:640 12px ui-monospace,Menlo,monospace;text-anchor:middle;stroke:none}}
.pkbg{{fill:var(--surf)}}
.dom .yr{{stroke:var(--domq)}} .dom .med,.dom .pkl{{stroke:var(--dom)}}
.eur .yr{{stroke:var(--eurq)}} .eur .med,.eur .pkl{{stroke:var(--eur)}}
.lh  .yr{{stroke:var(--lhq)}}  .lh  .med,.lh  .pkl{{stroke:var(--lh)}}

.dom .pkl,.dom .pk{{fill:var(--dom)}} .eur .pkl,.eur .pk{{fill:var(--eur)}}
.lh .pkl,.lh .pk{{fill:var(--lh)}}
.pk{{stroke:var(--surf);stroke-width:2}}
.hot{{fill:transparent}} .cx line{{stroke:var(--rule);stroke-width:1}}
.tip{{position:absolute;pointer-events:none;background:var(--surf);color:var(--ink);
 border:1px solid var(--rule);border-radius:4px;padding:7px 10px;font-size:12.5px;
 line-height:1.55;box-shadow:var(--shadow);white-space:nowrap;z-index:5;opacity:0;
 transition:opacity .1s}}
.tip.on{{opacity:1}} .tip b{{font-family:ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums}}
.legend{{display:flex;flex-wrap:wrap;gap:16px;margin:0 0 18px;font-size:13.5px;
 color:var(--ink2);align-items:center}}
.legend span.i{{display:inline-flex;align-items:center;gap:7px}}
.dot{{width:11px;height:11px;border-radius:2px;display:inline-block;flex:none;
 margin-right:7px;vertical-align:-1px}}
.dot.dom{{background:var(--dom)}} .dot.eur{{background:var(--eur)}} .dot.lh{{background:var(--lh)}}
.swatch{{width:22px;height:0;border-top-width:2.4px;border-top-style:solid;display:inline-block}}
.swatch.thin{{border-top-width:1.25px;opacity:.5}}
.strip{{background:var(--surf);border:1px solid var(--line);border-radius:5px;
 padding:16px 16px 10px;box-shadow:var(--shadow);overflow-x:auto}}
.strip svg{{display:block;width:100%;height:auto}}
.cell.on{{fill:var(--lh)}} .cell.off{{fill:var(--warn)}}
.tblwrap{{overflow-x:auto;background:var(--surf);border:1px solid var(--line);
 border-radius:5px;box-shadow:var(--shadow)}}
table{{border-collapse:collapse;width:100%;font-size:13.5px;min-width:520px}}
th,td{{text-align:left;padding:9px 14px;border-bottom:1px solid var(--line)}}
th{{font:600 11px/1.4 ui-monospace,Menlo,monospace;letter-spacing:.09em;
 text-transform:uppercase;color:var(--muted);background:var(--chipbg);
 position:sticky;top:0}}
td.n{{text-align:right;font-family:ui-monospace,Menlo,monospace;
 font-variant-numeric:tabular-nums}}
tr:last-child td{{border-bottom:none}}
td.pm{{font-weight:600}} td.dv{{font-weight:600}}
.bars{{background:var(--surf);border:1px solid var(--line);border-radius:5px;
 padding:16px 14px 8px;box-shadow:var(--shadow);overflow-x:auto}}
.bars svg{{display:block;width:100%;height:auto;min-width:430px}}
.bl{{font:500 11.5px ui-monospace,Menlo,monospace;fill:var(--ink2);text-anchor:end}}
.bv{{font:600 11.5px ui-monospace,Menlo,monospace;fill:var(--ink2);
 font-variant-numeric:tabular-nums}}
.btr{{fill:var(--chipbg)}} .bax{{stroke:var(--rule);stroke-width:1}}
.bar.dom{{fill:var(--dom)}} .bar.eur{{fill:var(--eur)}} .bar.lh{{fill:var(--lh)}}
.thr{{stroke:var(--crit);stroke-width:1.5;stroke-dasharray:3 3}}
.thl{{font:600 10.5px ui-monospace,Menlo,monospace;fill:var(--crit);text-anchor:middle}}
.two{{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(330px,1fr))}}
.two h3{{font-size:15px;font-weight:640;margin:0 0 4px}}
.two p{{font-size:13.5px;color:var(--ink2);margin:0 0 12px;line-height:1.5}}
td.cb{{font:500 12px ui-monospace,Menlo,monospace;color:var(--muted);white-space:nowrap}}
td.st{{font:600 11px ui-monospace,Menlo,monospace;text-transform:uppercase;
 letter-spacing:.05em}}
td.st.ok{{color:var(--good)}} td.st.no_data{{color:var(--warn)}}
td.mono{{font-family:ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums}}
.note{{border-left:3px solid var(--rule);padding:2px 0 2px 16px;color:var(--ink2);
 font-size:15px;max-width:66ch}}
.note.warn{{border-left-color:var(--warn)}}
.note.crit{{border-left-color:var(--crit)}}
.note+.note{{margin-top:16px}}
details{{margin-top:16px;font-size:14.5px}}
summary{{cursor:pointer;color:var(--ink2);font-weight:560;padding:5px 0}}
summary:focus-visible,a:focus-visible{{outline:2px solid var(--dom);outline-offset:2px}}
footer{{margin-top:58px;padding-top:22px;border-top:1px solid var(--line);
 color:var(--muted);font-size:13px;max-width:70ch}}
@media (prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
</style>

<div class="wrap">
<header class="masthead">
 <p class="eyebrow">ONS CPI item 07.3.3 · Passenger transport by air</p>
 <h1>What ONS's own air fare data actually looks like</h1>
 <p class="standfirst">Six published sub-indices, 2016 to 2026 — the validation
 target this pipeline reconstructs. Read before judging any nowcast's error,
 because the seasonality is larger and stranger than you would guess.</p>
 <div class="meta">
  <span class="chip">{len(d["published_series"])} values</span>
  <span class="chip">6 series</span>
  <span class="chip">{allm[0]} → {allm[-1]}</span>
  <span class="chip">basis Jan = 100</span>
  <span class="chip">exported {d["generated_ts"][:16].replace("T"," ")}Z</span>
 </div>
</header>

<section>
 <h2><span class="num">01</span>Today&rsquo;s collection</h2>
 <p class="lede">One collection date, {day}, taken from the latest
 run for that date &mdash; earlier runs the same day are superseded, not averaged
 in. Departures target the 3rd Tuesday of each month ahead, the hypothesis until
 ONS confirm August&rsquo;s index day in the September bulletin.</p>
 <div class="tiles">
  <div class="tile"><span class="v">{tot_ok}</span><span class="k">fares collected, of {n_queries} queries</span></div>
  <div class="tile{" flag" if tot_nd else ""}"><span class="v">{tot_nd}</span><span class="k">no fares returned &mdash; {nd_routes}</span></div>
  <div class="tile"><span class="v">{tot_er}</span><span class="k">errors</span></div>
  <div class="tile"><span class="v">{n_nodirect}</span><span class="k">queries with no direct service</span></div>
 </div>
 <div class="tblwrap" style="margin-top:14px"><table>
  <thead><tr><th>Series</th><th>Window</th><th style="text-align:right">Ok</th>
   <th style="text-align:right">No data</th><th style="text-align:right">Err</th>
   <th style="text-align:right">Mean</th><th style="text-align:right">Median</th>
   <th style="text-align:right">Geomean</th>
   <th style="text-align:right">Considered / seen</th></tr></thead>
  <tbody>{srow}</tbody></table></div>
 <p class="note" style="margin-top:18px"><strong>Mean and geometric mean differ
 materially</strong> &mdash; domestic £{dom_mean:.0f} against
 £{dom_geo:.0f}. ONS use a Jevons (geometric) elementary
 aggregate for most CPI items, so which one is applied changes the index by more
 than a rounding error. All three are stored per row for exactly this reason.</p>
</section>

<section>
 <h2><span class="num">02</span>Two diagnostics on the selection rule</h2>
 <p class="lede">The ONS rule takes the flight departing closest to a fixed
 target time, whatever it costs. Both charts ask whether that rule is behaving:
 the first, what it costs against the cheapest comparable fare; the second,
 whether the target time is constraining the choice at all.</p>
 <div class="two">
  <div>
   <h3>Cost of the target-time rule</h3>
   <p>How far the selected fare sits above the cheapest direct alternative for the
   same query. The three pre-fix runs earlier today read 882% on domestic; this is
   the same metric after filtering to comparable products.</p>
   <div class="bars">{gap_svg}</div>
  </div>
  <div>
   <h3>Distance from the 09:00 target</h3>
   <p>Average gap between the selected departure and the target time. Past roughly
   three hours the rule is not really selecting on time any more &mdash; it is
   taking whatever exists.</p>
   <div class="bars">{mins_svg}</div>
  </div>
 </div>
 <p class="note crit" style="margin-top:20px"><strong>Long haul is barely
 constrained by the target time.</strong> {wm_label}
 {wm_win}m averages
 <strong>{wm_mins:.0f} minutes</strong> from 09:00 &mdash;
 over three hours. Long-haul departures cluster at times set by arrival slots and
 night-flight rules, so on many routes nothing departs near 09:00 at all. Our
 09:00 constant is a stand-in: ONS do not publish theirs. This is the first real
 evidence that the target should differ by haul, and because every row keeps its
 raw API response, it can be re-derived later without re-collecting.</p>
</section>

<section>
 <h2><span class="num">03</span>Every route, today</h2>
 <p class="lede">The full panel for {day}. &ldquo;Off target&rdquo;
 is minutes from 09:00; &ldquo;basis&rdquo; records what the candidate filter did
 before the rule was applied.</p>
 <div class="tblwrap" style="max-height:520px;overflow-y:auto"><table>
  <thead><tr><th>Route</th><th style="text-align:right">Win</th><th>Departs</th>
   <th style="text-align:right">ONS rule</th><th style="text-align:right">Cheapest</th>
   <th>Airline</th><th>Time</th><th style="text-align:right">Off target</th>
   <th>Basis</th><th>Status</th></tr></thead>
  <tbody>{rrow}</tbody></table></div>
</section>

<section>
 <h2><span class="num">04</span>The shape of the target</h2>
 <div class="tiles">
  <div class="tile"><span class="v">{len(present)}</span><span class="k">months published, of {len(allm)} in span</span></div>
  <div class="tile flag"><span class="v">{len(missing)}</span><span class="k">months missing — all six series</span></div>
  <div class="tile"><span class="v">2.9×</span><span class="k">peak-to-base, European 1-month</span></div>
  <div class="tile"><span class="v">100.0</span><span class="k">every January, every series</span></div>
 </div>
</section>

<section>
 <h2><span class="num">05</span>Seasonal profile, one panel per series</h2>
 <p class="lede">Every January is reset to exactly 100, so this is a
 <em>within-year</em> index, not a long-run level. A continuous 2016–2026 line
 would be eleven sawtooth resets hiding the actual signal — so each panel shows
 one thin line per year against the bold median, on a shared scale. Gaps in a
 line are months ONS did not publish, drawn as breaks rather than bridged.</p>
 <div class="legend">
  <span class="i"><span class="swatch" style="border-color:var(--dom)"></span> median across years</span>
  <span class="i"><span class="swatch thin" style="border-color:var(--dom)"></span> individual year</span>
  <span class="i"><span class="dot dom"></span>Domestic</span>
  <span class="i"><span class="dot eur"></span>European</span>
  <span class="i"><span class="dot lh"></span>Long haul</span>
 </div>
 <div class="grid" id="grid">{"".join(panels)}</div>
 <div class="tip" id="tip" role="status" aria-live="polite"></div>
</section>

<section>
 <h2><span class="num">06</span>The peak is not in the same month for every series</h2>
 <p class="lede">Domestic and both European windows peak in <strong>August</strong>.
 Long-haul 1-month and 6-month peak in <strong>December</strong>. Summer holidays
 against Christmas travel — which means any seasonal adjustment or sanity check
 applied uniformly across hauls is wrong for half the series.</p>
 <div class="tblwrap"><table>
  <thead><tr><th>Series</th><th>Window</th><th>Median peak</th>
   <th style="text-align:right">Peak</th><th style="text-align:right">Max seen</th>
   <th style="text-align:right">Min seen</th></tr></thead>
  <tbody>{prow}</tbody></table></div>
</section>

<section>
 <h2><span class="num">07</span>Nine months are simply absent</h2>
 <p class="lede">Identical gaps across all six series: 2020-04, 05, 06, 2020-11,
 and 2021-02 through 06. Those are the UK lockdown windows. With almost no
 flights to price, ONS suspended collection and imputed the item rather than
 publishing a collected index.</p>
 <div class="strip">
  <svg viewBox="0 0 {SW} 44" role="img" aria-label="Coverage strip, {allm[0]} to {allm[-1]}: {len(present)} months published, {len(missing)} missing, all within 2020 and 2021.">
   {cells}{yrt}
  </svg>
  <div class="legend" style="margin:12px 0 4px;font-size:13px">
   <span class="i"><span class="dot lh"></span>published</span>
   <span class="i"><span class="dot" style="background:var(--warn)"></span>no index published</span>
  </div>
 </div>
 <p class="note crit" style="margin-top:20px"><strong>This changes validation.</strong>
 A month-on-month relative spanning a hole is meaningless, so rolling-origin
 scoring has to skip these months — and the pipeline's &ldquo;one full quarter of
 overlap&rdquo; gate must mean three consecutive <em>published</em> months, not
 three consecutive calendar months.</p>
</section>

<section>
 <h2><span class="num">08</span>The most recent published month</h2>
 <p class="lede">February 2026 is the last month ONS have published. Because
 January is the base, the February value <em>is</em> the January-to-February
 change — compared here against the typical February of prior years. A positive difference means fares rose more
 steeply out of January than they usually do; it is a seasonal deviation, not a
 warning.</p>
 <div class="tblwrap"><table>
  <thead><tr><th>Series</th><th>Window</th><th style="text-align:right">Feb 2026</th>
   <th style="text-align:right">Typical Feb</th><th style="text-align:right">Difference</th></tr></thead>
  <tbody>{lrow}</tbody></table></div>
</section>

<section>
 <h2><span class="num">09</span>Where validation stands</h2>
 <p class="note"><strong>Collection began 2026-08-17.</strong> The published
 series ends 2026-02, six months earlier, so the overlap between what we collect
 and what we can check against is currently <strong>zero months</strong>.
 Validation correctly reports <code>INSUFFICIENT_DATA</code> and will keep doing
 so until ONS release a newer vintage &mdash; which the monthly backfill workflow
 discovers automatically. Nothing to fix; something to wait for.</p>
 <details>
  <summary>Full seasonal table &mdash; 72 rows, median and range per series and month</summary>
  <div class="tblwrap" style="margin-top:12px;max-height:440px;overflow-y:auto">
  <table><thead><tr><th>Series</th><th>Window</th><th>Month</th>
   <th style="text-align:right">Median</th><th style="text-align:right">Min</th>
   <th style="text-align:right">Max</th><th style="text-align:right">Years</th></tr></thead>
   <tbody>{trow}</tbody></table></div>
 </details>
</section>

<footer>
 Source: ONS published air fare sub-indices, loaded into BigQuery by the
 <code>airfares-backfill-ons</code> workflow and exported to
 <code>reports/data/analytics.json</code>. Values are ONS's own published
 figures, not reconstructions. Every January is 100 by construction
 (<code>annual_january_100</code>), so within-year movements are comparable and
 across-year levels are not.
</footer>
</div>

<script>
(function(){{
 var tip=document.getElementById('tip'),grid=document.getElementById('grid');
 function hide(){{tip.classList.remove('on');}}
 grid.addEventListener('pointerleave',hide);
 grid.querySelectorAll('.panel').forEach(function(p){{
  var svg=p.querySelector('svg'),cx=p.querySelector('.cx'),ln=cx.querySelector('line');
  var name=p.querySelector('.pl').textContent+' '+p.querySelector('.pw').textContent;
  p.querySelectorAll('.hot').forEach(function(h){{
   function show(e){{
    var x=+h.getAttribute('x')+ +h.getAttribute('width')/2;
    ln.setAttribute('x1',x);ln.setAttribute('x2',x);cx.removeAttribute('hidden');
    tip.innerHTML='<b>'+h.dataset.m+'</b> — '+name+'<br>median <b>'+h.dataset.med+
      '</b> · range <b>'+h.dataset.lo+'–'+h.dataset.hi+'</b><br>'+h.dataset.n+' years published';
    var r=p.getBoundingClientRect(),w=document.querySelector('.wrap').getBoundingClientRect();
    var px=(e.clientX-w.left)+14, py=(r.top-w.top)+8;
    if(px+230>w.width) px=(e.clientX-w.left)-230;
    tip.style.left=px+'px';tip.style.top=py+'px';tip.classList.add('on');
   }}
   h.addEventListener('pointerenter',show);
   h.addEventListener('pointermove',show);
  }});
  p.addEventListener('pointerleave',function(){{cx.setAttribute('hidden','');hide();}});
 }});
}})();
</script>'''
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(HTML, encoding="utf-8")
print(f"wrote {OUT} ({len(HTML)} bytes)")
print("months missing from every series:", ", ".join(missing))
