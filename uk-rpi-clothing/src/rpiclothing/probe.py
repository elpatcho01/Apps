"""Task 0: establish the facts on real ONS data before anything is built on them.

Runs inside GitHub Actions (ons.gov.uk is unreachable from the dev sandbox).
Every check runs independently: a failure becomes an entry in the report, not
an exception that hides the other checks. The report is written as JSON and
echoed to stdout so it can be read straight from the job log.

    python -m rpiclothing.probe --out probe.json
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import logging
import math
import re
import statistics
import sys
import traceback
import zipfile
from collections import Counter
from typing import Any, Callable

import requests

from . import onsfetch
from .series import (
    SeasonalDriftSpec,
    add_months,
    fit_seasonal,
    mae,
    mom,
    month_range,
    rolling_origin_errors,
    seasonal_drift_path,
    yoy,
)

log = logging.getLogger(__name__)

TARGET_CDIDS = ("CHBJ", "D7BW", "CHAW", "CZHJ", "D7BT")

#: The brief's stated factors, for side-by-side comparison.
BRIEF_SEASONAL = {
    1: -3.60, 2: 4.25, 3: 2.24, 4: 0.87, 5: 0.76, 6: -0.60,
    7: -2.19, 8: 1.88, 9: 3.22, 10: 0.80, 11: 0.72, 12: -0.58,
}
BRIEF_MAE = {1: 0.90, 3: 1.38, 6: 1.72, 12: 2.74}

CLOTHING_WORDS = re.compile(
    r"\b(men'?s|women'?s|ladies|child|boy'?s|girl'?s|infant|baby|shirt|blouse|dress|"
    r"skirt|trouser|jeans|jacket|coat|suit|jumper|cardigan|knit|sweat|t-?shirt|"
    r"underwear|pants|bra|socks|tights|pyjama|nightwear|swim|shoes?|trainers?|boots?|"
    r"sandals?|footwear|slippers?|clothing|hat|scarf|gloves?|belt|tie)\b",
    re.IGNORECASE,
)


def _check(report: dict[str, Any], name: str, fn: Callable[[], Any]) -> Any:
    try:
        result = fn()
        report["checks"][name] = {"ok": True, "result": result}
        return result
    except Exception as exc:  # noqa: BLE001 - every failure is reported, none hidden
        report["checks"][name] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=4),
        }
        return None


def _series_summary(s: onsfetch.Series) -> dict[str, Any]:
    d = s.as_dict()
    jun26 = dt.date(2026, 6, 1)
    return {
        "cdid": s.cdid,
        "title": s.title,
        "source_url": s.source_url,
        "release_date": s.release_date,
        "first": [s.first[0].isoformat(), s.first[1]] if s.first else None,
        "last": [s.last[0].isoformat(), s.last[1]] if s.last else None,
        "annual_tail": list(s.annual[-12:]),
        "n_months": len(s.values),
        "jun2026": d.get(jun26),
        "jun2025": d.get(dt.date(2025, 6, 1)),
        "yoy_jun2026_pct": yoy(d, jun26),
        "tail": [[k.isoformat(), v] for k, v in s.values[-4:]],
    }


def check_series(session: requests.Session) -> dict[str, Any]:
    out = {}
    for cdid in TARGET_CDIDS:
        try:
            out[cdid] = _series_summary(onsfetch.fetch_timeseries(cdid, session=session))
        except Exception as exc:  # noqa: BLE001
            out[cdid] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def check_mm23_from(mm23: dict[str, onsfetch.Series]) -> dict[str, Any]:
    hits: dict[str, Any] = {"n_series": len(mm23)}
    for cdid in TARGET_CDIDS:
        s = mm23.get(cdid)
        hits[f"title_{cdid}"] = s.title if s else None
    def rows(ss):
        return [
            {"cdid": s.cdid, "title": s.title,
             "first": s.first[0].isoformat() if s.first else None,
             "last": [s.last[0].isoformat(), s.last[1]] if s.last else None,
             "annual_tail": list(s.annual[-3:])}
            for s in ss
        ]
    hits["annual_only_clothing_or_weight"] = rows(
        [s for s in mm23.values() if not s.values
         and re.search(r"cloth|footwear|weight", s.title, re.I)]
    )[:80]
    hits["weight_titles_any"] = rows(
        [s for s in mm23.values() if re.search(r"weight", s.title, re.I)
         and re.search(r"cloth|footwear|rpi", s.title, re.I)]
    )[:80]
    hits["rpi_clothing_subsections"] = rows(
        [s for s in mm23.values() if s.title.lower().startswith("rpi")
         and re.search(r"cloth|footwear|outerwear", s.title, re.I)]
    )[:40]
    hits["formula_effect_titles"] = rows(
        [s for s in mm23.values() if re.search(r"formula", s.title, re.I)]
    )[:60]
    # The weight series itself, year by year, for anything that looks like the
    # RPI clothing & footwear weight.
    for cdid in ("CZHJ", "CZFY", "CZGN"):
        s = mm23.get(cdid)
        hits[f"series_{cdid}"] = (
            {"title": s.title, "annual": list(s.annual), "n_monthly": len(s.values)} if s else None
        )
    return hits


def check_contribution(
    chbj: dict[dt.date, float], chaw: dict[dt.date, float],
    czfy: dict[dt.date, float], weights: dict[int, float],
) -> dict[str, Any]:
    """Trap 1 against ONS's own published contribution (CZFY, pp, 2dp).

    Compares the exact chain-linked formula and the naive w x MoM, both using
    the published weights, and infers the weight each year by least squares
    from CZFY as an independent check on the weight series.
    """
    from .series import contribution_bp, effective_weight_ppt

    rows = []
    for d in month_range(dt.date(2010, 1, 1), max(czfy)):
        if d not in czfy:
            continue
        exact = contribution_bp(chbj, chaw, weights, d)
        m = mom(chbj).get(d)
        w = weights.get(d.year if d.month > 1 else d.year - 1)
        naive = None if (m is None or w is None) else w / 1000 * m * 100
        naive_cal = None if (m is None or weights.get(d.year) is None) else weights[d.year] / 1000 * m * 100
        if exact is None:
            continue
        rows.append((d, czfy[d] * 100, exact, naive, naive_cal))
    def err(i):
        e = [abs(r[i] - r[1]) for r in rows if r[i] is not None]
        return {"mae_bp": round(statistics.fmean(e), 3), "max_bp": round(max(e), 3), "n": len(e)}
    by_month = {}
    for mth in range(1, 13):
        sel = [r for r in rows if r[0].month == mth and r[0].year >= 2016]
        if sel:
            by_month[mth] = {
                "published_mean_bp": round(statistics.fmean(r[1] for r in sel), 2),
                "exact_mean_bp": round(statistics.fmean(r[2] for r in sel), 2),
                "naive_mean_bp": round(statistics.fmean(r[3] for r in sel if r[3] is not None), 2),
                "effective_weight_mean": round(statistics.fmean(
                    effective_weight_ppt(chbj, chaw, weights, r[0]) for r in sel), 2),
            }
    # implied weight per chain year: CZFY = w * f, f = exact / w
    implied = {}
    for y in sorted({(r[0].year if r[0].month > 1 else r[0].year - 1) for r in rows}):
        sel = [r for r in rows if (r[0].year if r[0].month > 1 else r[0].year - 1) == y]
        wy = weights.get(y)
        if not wy or len(sel) < 6:
            continue
        f = [r[2] / wy for r in sel]
        num = sum(fi * r[1] for fi, r in zip(f, sel))
        den = sum(fi * fi for fi in f)
        if den:
            implied[y] = {"published_w": wy, "implied_w": round(num / den, 2)}
    return {
        "exact_vs_published": err(2),
        "naive_chainyear_weight_vs_published": err(3),
        "naive_calendar_weight_vs_published": err(4),
        "by_month_2016on": by_month,
        "implied_weights": implied,
        "last_12": [[r[0].isoformat(), r[1], round(r[2], 2), None if r[3] is None else round(r[3], 2)]
                    for r in rows[-12:]],
    }


def check_seasonal(chbj: dict[dt.date, float]) -> dict[str, Any]:
    ch = mom(chbj)
    variants = {}
    for label, kwargs in {
        "2016-01..2026-06 all months": dict(start=dt.date(2016, 1, 1), end=dt.date(2026, 6, 1)),
        "2016-01..2025-12 complete years": dict(
            start=dt.date(2016, 1, 1), end=dt.date(2025, 12, 1), complete_years_only=True
        ),
        "2016-02..2026-06": dict(start=dt.date(2016, 2, 1), end=dt.date(2026, 6, 1)),
    }.items():
        fit = fit_seasonal(ch, **kwargs)
        variants[label] = {
            "raw": {m: round(v, 3) for m, v in fit.raw.items()},
            "raw_sum": round(fit.raw_sum, 3),
            "demeaned": {m: round(v, 3) for m, v in fit.demeaned.items()},
            "demeaned_sum": round(fit.demeaned_sum, 3),
            "n": fit.n,
            "raw_mom_sd": round(fit.raw_sd, 3),
            "residual_sd": round(fit.residual_sd, 3),
            "max_abs_diff_vs_brief_raw": round(
                max(abs(fit.raw[m] - BRIEF_SEASONAL[m]) for m in range(1, 13)), 3
            ),
            "max_abs_diff_vs_brief_demeaned": round(
                max(abs(fit.demeaned[m] - BRIEF_SEASONAL[m]) for m in range(1, 13)), 3
            ),
        }
    return {"brief_sum": round(sum(BRIEF_SEASONAL.values()), 3), "variants": variants}


def check_wedge(chbj: dict[dt.date, float], d7bw: dict[dt.date, float]) -> dict[str, Any]:
    d = dt.date(2026, 6, 1)
    p = add_months(d, -12)
    r_yoy = yoy(chbj, d)
    c_yoy = yoy(d7bw, d)
    return {
        "chbj": [chbj.get(p), chbj.get(d)],
        "d7bw": [d7bw.get(p), d7bw.get(d)],
        "from_1dp_levels_pp": round(r_yoy - c_yoy, 4) if r_yoy is not None and c_yoy is not None else None,
        "from_rounded_rates_pp": round(round(r_yoy, 1) - round(c_yoy, 1), 4)
        if r_yoy is not None and c_yoy is not None else None,
        "log_diff_pp": round(
            100 * (math.log(chbj[d] / chbj[p]) - math.log(d7bw[d] / d7bw[p])), 4
        ) if all(k in s for s in (chbj, d7bw) for k in (d, p)) else None,
        "brief_value_pp": 5.99,
    }


def check_baseline_grid(chbj: dict[dt.date, float]) -> dict[str, Any]:
    """Search seasonal+drift parameterisations for the one that reproduces the
    brief's MAE table (h1 0.90, h3 1.38, h6 1.72, h12 2.74) on 121 origins."""
    origins = month_range(dt.date(2015, 6, 1), dt.date(2025, 6, 1))
    changes = mom(chbj)
    horizons = [1, 3, 6, 12]
    results = []
    for sy in (3, 5, 6, 8, 10, None):
        for dm in (0, 6, 12, 24, 36, 60):
            for dx in (False, True):
                if dm == 0 and dx:
                    continue
                spec = SeasonalDriftSpec(
                    seasonal_years=sy,
                    seasonal_start=dt.date(2000, 1, 1) if sy is None else None,
                    drift_months=dm,
                    drift_excludes=dx,
                )
                errs = rolling_origin_errors(
                    chbj, origins, horizons,
                    lambda o, H, spec=spec: seasonal_drift_path(chbj, o, H, spec, changes=changes),
                )
                row = {"spec": spec.label(), "n": {h: len(errs[h]) for h in horizons}}
                row["mae"] = {h: round(mae(errs[h]), 3) for h in horizons}
                row["distance"] = round(
                    math.sqrt(sum((row["mae"][h] - BRIEF_MAE[h]) ** 2 for h in horizons)), 3
                )
                results.append(row)
    results.sort(key=lambda r: r["distance"])
    return {"n_origins": len(origins), "brief": BRIEF_MAE, "closest": results[:12], "all": results}


def _open_table(blob: bytes, name: str) -> tuple[list[str], list[dict[str, str]]]:
    if name.lower().endswith(".zip") or blob[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            inner = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not inner:
                raise ValueError(f"{name}: zip holds no csv ({zf.namelist()})")
            blob = zf.read(inner[0])
    text = blob.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    return list(reader.fieldnames or []), rows


def _summ(rows: list[dict[str, str]], clothing: list[dict[str, str]], desc_key: str, id_key: str) -> dict[str, Any]:
    items = Counter((r.get(id_key), r.get(desc_key)) for r in clothing)
    cells = {}
    for r in clothing:
        cells.setdefault((r.get(id_key), r.get("STRATUM_CELL")), set()).add(r.get("STRATUM_WEIGHT"))
    out = {
        "n_rows": len(rows),
        "n_clothing_rows": len(clothing),
        "n_clothing_items": len(items),
        "clothing_items": [f"{k[0]}|{k[1]}|{n}" for k, n in sorted(items.items())],
        "n_strata": len(cells),
        "strata_with_inconsistent_weight": sum(1 for v in cells.values() if len(v) > 1),
        "sample_rows": clothing[:4],
    }
    for col in ("INDICATOR_BOX", "VALIDITY", "SHOP_TYPE", "STRATUM_TYPE", "REGION",
                "INDEX_ALGORITHM_RPI", "BASE_VALIDITY", "ORIG_INDICATOR_BOX"):
        if clothing and col in clothing[0]:
            out[f"counts_{col}"] = Counter(r.get(col, "") for r in clothing).most_common(15)
    # Do the published relatives equal price / base price?
    for suffix in ("_RPI", "_CPI", ""):
        rel, base = f"PRICE_RELATIVE{suffix}", f"BASE_PRICE{suffix}"
        if clothing and rel in clothing[0] and base in clothing[0]:
            diffs, n = [], 0
            for r in clothing:
                try:
                    pr, bp, rr = float(r["PRICE"]), float(r[base]), float(r[rel])
                except (ValueError, KeyError):
                    continue
                if bp > 0 and pr > 0:
                    diffs.append(abs(pr / bp - rr))
                    n += 1
            if diffs:
                diffs.sort()
                out[f"relative_vs_price_over_base{suffix or '_single'}"] = {
                    "n": n, "median_absdiff": diffs[len(diffs) // 2], "p95_absdiff": diffs[int(0.95 * len(diffs))],
                }
    if clothing and "BASE_PRICE_RPI" in clothing[0] and "BASE_PRICE_CPI" in clothing[0]:
        same = sum(1 for r in clothing if r.get("BASE_PRICE_RPI") == r.get("BASE_PRICE_CPI"))
        out["share_rpi_base_equals_cpi_base"] = round(same / len(clothing), 4)
    return out


def check_price_quotes(session: requests.Session) -> dict[str, Any]:
    editions = onsfetch.dataset_editions(session=session)
    short = [e.rstrip("/").rsplit("/", 1)[-1] for e in editions]
    out: dict[str, Any] = {"n_editions": len(editions), "edition_names": short}
    def first_file(pred_ed, pred_file):
        for uri, name in zip(editions, short):
            if pred_ed(name):
                for f in onsfetch.edition_downloads(uri, session=session):
                    if pred_file(f["file"].lower()):
                        return f
        return None
    targets = {
        "quotes_2026_08": (lambda n: n == "pricequotesaugust2026", lambda f: "pricequote" in f),
        "segments_2026_08": (lambda n: n == "consumptionsegmentindicesaugust2026", lambda f: f.endswith(".csv")),
        "quotes_2025_12": (lambda n: "pricequotes" in n and "2025" in n and "december" in n, lambda f: "pricequote" in f),
        "quotes_2019_any": (lambda n: "pricequotes" in n and "2019" in n, lambda f: "pricequote" in f or f.endswith(".csv") or f.endswith(".zip")),
        "itemindices_2019": (lambda n: "itemindices" in n and "2019" in n, lambda f: f.endswith(".csv") or f.endswith(".zip")),
    }
    for key, (pe, pf) in targets.items():
        try:
            f = first_file(pe, pf)
            if f is None:
                out[key] = {"error": "no matching edition/file"}
                continue
            fields, rows = _open_table(onsfetch.download(f["url"], session=session), f["file"])
            desc_key = "CS_DESC" if "CS_DESC" in fields else "ITEM_DESC"
            id_key = "CS_ID" if "CS_ID" in fields else "ITEM_ID"
            clothing = [r for r in rows if CLOTHING_WORDS.search(r.get(desc_key, "") or "")]
            summ = _summ(rows, clothing, desc_key, id_key) if "PRICE" in fields else {
                "n_rows": len(rows), "sample_clothing_rows": clothing[:6],
                "n_clothing_rows": len(clothing),
            }
            out[key] = {"file": f["file"], "edition": f["edition"], "fields": fields, **summ}
        except Exception as exc:  # noqa: BLE001
            out[key] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


CHECKS = ("series", "mm23", "seasonal", "baseline", "wedge", "contribution", "price_quotes")


def run(out_path: str | None, checks: tuple[str, ...] = CHECKS) -> dict[str, Any]:
    session = requests.Session()
    report: dict[str, Any] = {
        "generated_ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "ons_base": onsfetch.base_url(),
        "checks": {},
    }
    if "series" in checks:
        _check(report, "series", lambda: check_series(session))
    mm23 = None
    try:
        mm23 = onsfetch.fetch_mm23(session=session)
    except Exception as exc:  # noqa: BLE001
        report["checks"]["fetch_mm23"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if mm23 and "mm23" in checks:
        _check(report, "mm23", lambda: check_mm23_from(mm23))
    get = (lambda c: mm23[c].as_dict()) if mm23 else None
    if mm23:
        chbj, d7bw, chaw = get("CHBJ"), get("D7BW"), get("CHAW")
        if "seasonal" in checks:
            _check(report, "seasonal", lambda: check_seasonal(chbj))
        if "baseline" in checks:
            _check(report, "baseline_grid", lambda: check_baseline_grid(chbj))
        if "wedge" in checks:
            _check(report, "wedge", lambda: check_wedge(chbj, d7bw))
        if "contribution" in checks and "CZFY" in mm23 and "CZHJ" in mm23:
            _check(report, "contribution", lambda: check_contribution(
                chbj, chaw, get("CZFY"), mm23["CZHJ"].annual_dict()))
    if "price_quotes" in checks:
        _check(report, "price_quotes", lambda: check_price_quotes(session))
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, default=str, sort_keys=True)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="probe.json")
    ap.add_argument("--checks", default=",".join(CHECKS))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    report = run(args.out, tuple(c.strip() for c in args.checks.split(",")))
    # Echo without the bulky 'all' grid, which is in the artifact.
    slim = json.loads(json.dumps(report, default=str))
    bg = slim["checks"].get("baseline_grid", {}).get("result")
    if isinstance(bg, dict):
        bg.pop("all", None)
    # One line per check keeps the job log readable and parseable.
    for name, val in slim["checks"].items():
        print(f"PROBE::{name}::" + json.dumps(val, sort_keys=True, separators=(",", ":")))
    failed = [k for k, v in report["checks"].items() if not v.get("ok")]
    print(f"\nPROBE: {len(report['checks']) - len(failed)} ok, {len(failed)} failed: {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
