"""Workstream 2a research run: sync ONS quotes, rebuild the section under every
variant, validate, and report the formula wedge and the sampling floor.

    python -m rpiclothing.ws2a --cache cache --out ws2a.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import pathlib
import statistics
import sys
from collections import defaultdict

from . import nowcast, onsfetch, quotes, recon

log = logging.getLogger(__name__)


def _summ(errs):
    errs = [e for e in errs if e is not None and math.isfinite(e)]
    if not errs:
        return None
    return {"n": len(errs), "mae": round(statistics.fmean(abs(e) for e in errs), 4),
            "max": round(max(abs(e) for e in errs), 4), "bias": round(statistics.fmean(errs), 4)}


def run(cache_dir: str, *, since_year: int = 2010, floor_months: int = 18, sync: bool = True) -> dict:
    cache = quotes.QuoteCache(pathlib.Path(cache_dir))
    out: dict = {}
    if sync:
        s = quotes.sync(cache, since_year=since_year)
        out["sync"] = {"fetched": len(s["fetched"]), "failures": s["failures"][:20],
                       "n_failures": len(s["failures"]),
                       "skipped_duplicate_months": s.get("skipped_duplicate_months", [])[:30],
                       "months": [s["months"][0], s["months"][-1], len(s["months"])] if s["months"] else None,
                       "item_months": len(s.get("item_months", []))}
    months = cache.months()
    item_months = cache.item_months()
    out["coverage"] = {
        "quote_months": len(months), "first": months[0] if months else None,
        "last": months[-1] if months else None,
        "missing_months": [m for m in _all_months(months) if m not in set(months)],
        "item_months": len(item_months),
        "item_years": sorted({m[:4] for m in item_months}),
    }
    mm23 = onsfetch.fetch_mm23()
    published = {c: mm23[c].as_dict() for c in ["CHBJ", "D7BW"] + [v["index"] for v in quotes.SUBSECTIONS.values()]}
    subw = {p: mm23[v["weight"]].annual_dict() for p, v in quotes.SUBSECTIONS.items()}
    out["subsection_weights_2026"] = {v["name"]: subw[p].get(2026) for p, v in quotes.SUBSECTIONS.items()}
    q_by = {m: cache.read_quotes(m) for m in months}
    i_by = {m: cache.read_items(m) for m in item_months}
    results = {}
    for v in recon.VARIANTS:
        rows = recon.reconstruct(q_by, i_by, subw, published, v)
        results[v.label()] = rows
        by_year = defaultdict(list)
        for r in rows:
            if "error_pct" in r:
                by_year[r["month"][:4]].append(r["error_pct"])
        feb_dec = [r.get("error_pct") for r in rows if r["month"][4:] != "01"]
        jan = [r.get("error_pct") for r in rows if r["month"][4:] == "01"]
        out.setdefault("variants", {})[v.label()] = {
            "chbj_feb_dec": _summ(feb_dec), "chbj_jan": _summ(jan),
            "chbj_2016on": _summ([r.get("error_pct") for r in rows if r["month"] >= "2016"]),
            "segment_2026": _summ([r.get("segment_mae_index_pts") for r in rows]),
            "by_year": {y: _summ(e) for y, e in sorted(by_year.items())},
            "subsections_2016on": {
                meta["name"]: _summ([r.get(f"err_{meta['name']}_pct") for r in rows if r["month"] >= "2016"])
                for meta in quotes.SUBSECTIONS.values()
            },
        }
    best = min(out["variants"], key=lambda k: (out["variants"][k]["chbj_feb_dec"] or {"mae": 9e9})["mae"])
    out["best_variant"] = best
    rows = results[best]
    out["weight_basis_by_year"] = {r["month"][:4]: r.get("weight_basis") for r in rows}
    out["worst_months"] = sorted(
        ({"month": r["month"], "err": round(r["error_pct"], 3), "n_quotes": r.get("n_quotes")}
         for r in rows if "error_pct" in r), key=lambda x: -abs(x["err"]))[:15]
    # Formula wedge vs observed wedge.
    fe = recon.formula_effect_yoy(rows)
    obs = {d: nowcast.log_wedge(published["CHBJ"], published["D7BW"], d) for d in fe}
    resid = {d: obs[d] - fe[d] for d in fe if obs[d] is not None}
    out["formula_wedge"] = {
        "recent": [[d.isoformat(), round(fe[d], 3), round(obs[d], 3) if obs[d] is not None else None]
                   for d in sorted(fe)[-14:]],
        "residual_2016on": _summ([v for d, v in resid.items() if d.year >= 2016]),
        "mean_formula_share_2016on": round(statistics.fmean(
            fe[d] / obs[d] for d in fe if d.year >= 2016 and obs.get(d))
        , 3) if any(d.year >= 2016 for d in fe) else None,
    }
    out["dispersion_recent"] = [
        [r["month"], round(r.get("var_log", math.nan), 4), round(r.get("sale_share", math.nan), 3),
         round(math.log(r["section_rpi_index"] / r["section_jevons_index"]) * 100, 3)
         if r.get("section_rpi_index") and r.get("section_jevons_index") else None]
        for r in rows[-20:]]
    # Sampling floor on recent months where m and m-1 share a chain year.
    v = next(x for x in recon.VARIANTS if x.label() == best)
    floors = []
    cand = [m for m in months if m[4:] not in ("01", "02")][-floor_months:]
    for m in cand:
        d = recon._mdate(m)
        pm = f"{(d.year if d.month > 1 else d.year - 1):04d}{(d.month - 2) % 12 + 1:02d}"
        if pm not in q_by:
            continue
        cy = recon.chain_year(m)
        w, _ = recon.item_weights_for_year(i_by, cy)
        sw = {s: subw[s].get(cy) for s in quotes.SUBSECTIONS if subw[s].get(cy)}
        sd = recon.bootstrap_mom_sd(q_by[m], q_by[pm], w, sw, v, reps=100, seed=int(m))
        if sd is not None:
            floors.append((m, sd))
    if floors:
        sds = [s for _, s in floors]
        out["sampling_floor"] = {
            "months": [[m, round(s, 3)] for m, s in floors],
            "mean_sd_pct": round(statistics.fmean(sds), 3),
            "rms_sd_pct": round(math.sqrt(statistics.fmean(s * s for s in sds)), 3),
            "implied_mae_floor_pct": round(math.sqrt(2 / math.pi) * math.sqrt(statistics.fmean(s * s for s in sds)), 3),
            "ws3_target_pct": 0.30,
        }
    return out


def _all_months(months):
    if not months:
        return []
    y, m = int(months[0][:4]), int(months[0][4:])
    out = []
    while f"{y:04d}{m:02d}" <= months[-1]:
        out.append(f"{y:04d}{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--out", default="ws2a.json")
    ap.add_argument("--since-year", type=int, default=2010)
    ap.add_argument("--no-sync", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    res = run(args.cache, since_year=args.since_year, sync=not args.no_sync)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, sort_keys=True, default=str)
    for k, v in res.items():
        print(f"WS2A::{k}::" + json.dumps(v, sort_keys=True, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
