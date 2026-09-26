"""Workstream 2a: rebuild RPI clothing & footwear from ONS price quotes, and
validate against published CHBJ, the sub-section indices (DOCK-DOCO) and,
from 2026, the per-segment RPI_INDEX.

Construction, per month m of year y:

1. Elementary aggregate per (item, stratum cell) from valid quotes, relatives
   against the January base (trap 4). For January the relatives are against
   the *previous* January, and the previous year's weights apply (the chain
   link; see ``series.link_january``).
2. Item index = sum_s W_s * EA_s / sum_s W_s * 100 (stratum weights from the
   quotes).
3. Sub-section index = item-weighted mean of item indices.
4. Section index = sub-section-weighted mean (weights CZXY..CZYC).
5. Level: published CHBJ(link January) * section index / 100. Compared to
   published CHBJ(m) that gives the reconstruction error.

Three things are not established by any public source and are therefore
computed as variants and settled by validation (trap 10):

* what INDEX_ALGORITHM_RPI 1 and 2 mean (Carli vs Dutot);
* whether shop weights enter the elementary aggregate;
* item weights before 2026, when ONS published no RPI item weights -- CPI
  item weights are used within each RPI sub-section, scaled to the published
  sub-section weights, and the 2026 overlap (which has both) measures the
  cost of that proxy.

The formula effect is computed on identical quotes and identical weights:
Carli (RPI) against Jevons on the same RPI relatives. Chained through each
January, its 12-month log change is the bottom-up clothing formula wedge that
CRFV no longer provides.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
import random
import statistics
from collections import defaultdict
from typing import Iterable, Mapping, Sequence

from .quotes import SUBSECTIONS, subsection_of

#: Variants of the elementary-aggregate definition.
ALGO_MAPS: dict[str, dict[str, str]] = {
    "1dutot_2carli": {"1": "dutot", "2": "carli", "": "carli"},
    "1carli_2dutot": {"1": "carli", "2": "dutot", "": "carli"},
    "all_carli": {"1": "carli", "2": "carli", "": "carli"},
}


@dataclasses.dataclass(frozen=True)
class Variant:
    algo_map: str = "1dutot_2carli"
    shop_weighted: bool = False

    def label(self) -> str:
        return f"{self.algo_map}{'+shopw' if self.shop_weighted else ''}"


VARIANTS = tuple(Variant(a, s) for a in ALGO_MAPS for s in (False, True))


def _usable(q: Mapping) -> bool:
    r = q.get("rel_rpi")
    return bool(q.get("valid")) and r is not None and r > 0 and math.isfinite(r)


def elementary_rpi(qs: Sequence[Mapping], formula: str, shop_weighted: bool) -> float | None:
    """RPI elementary aggregate as a relative (1.0 = unchanged since base)."""
    qs = [q for q in qs if _usable(q)]
    if not qs:
        return None
    w = [(q.get("shop_weight") or 1.0) if shop_weighted else 1.0 for q in qs]
    if formula == "dutot":
        num = sum(wi * q["price"] for wi, q in zip(w, qs) if q.get("price") and q.get("base_rpi"))
        den = sum(wi * q["base_rpi"] for wi, q in zip(w, qs) if q.get("price") and q.get("base_rpi"))
        return num / den if den > 0 else None
    tw = sum(w)
    return sum(wi * q["rel_rpi"] for wi, q in zip(w, qs)) / tw


def elementary_jevons(qs: Sequence[Mapping], shop_weighted: bool, rel_key: str = "rel_rpi") -> float | None:
    qs = [q for q in qs if q.get("valid") and q.get(rel_key) and q[rel_key] > 0]
    if not qs:
        return None
    w = [(q.get("shop_weight") or 1.0) if shop_weighted else 1.0 for q in qs]
    tw = sum(w)
    return math.exp(sum(wi * math.log(q[rel_key]) for wi, q in zip(w, qs)) / tw)


@dataclasses.dataclass
class ItemResult:
    item: str
    rpi: float  # Jan=100
    jevons: float  # Jan=100, same quotes, same stratum weights
    var_log: float
    n: int
    sale_share: float


def item_indices(quotes: Iterable[Mapping], variant: Variant) -> dict[str, ItemResult]:
    amap = ALGO_MAPS[variant.algo_map]
    by: dict[str, dict[str, list[Mapping]]] = defaultdict(lambda: defaultdict(list))
    for q in quotes:
        by[str(q["item_id"])][f"{q.get('stratum_type')}:{q.get('stratum_cell')}"].append(q)
    out = {}
    for item, strata in by.items():
        num = numj = den = 0.0
        logs: list[tuple[float, float]] = []
        n = sale = 0
        for qs in strata.values():
            use = [q for q in qs if _usable(q)]
            if not use:
                continue
            sw = use[0].get("stratum_weight") or 0.0
            if sw <= 0:
                continue
            formula = amap.get(str(use[0].get("algo_rpi") or ""), "carli")
            ea = elementary_rpi(use, formula, variant.shop_weighted)
            ej = elementary_jevons(use, variant.shop_weighted)
            if ea is None or ej is None:
                continue
            num += sw * ea
            numj += sw * ej
            den += sw
            for q in use:
                logs.append((sw / len(use), math.log(q["rel_rpi"])))
            n += len(use)
            sale += sum(1 for q in use if q.get("indicator") == "S")
        if den <= 0:
            continue
        tw = sum(w for w, _ in logs)
        ml = sum(w * l for w, l in logs) / tw
        var = sum(w * (l - ml) ** 2 for w, l in logs) / tw
        out[item] = ItemResult(item, 100 * num / den, 100 * numj / den, var, n, sale / n if n else 0.0)
    return out


def item_weights_for_year(
    items_by_month: Mapping[str, Mapping[str, Mapping[str, float]]], year: int
) -> tuple[dict[str, float], str]:
    """Item weights for a chain year: RPI weights if the year's files carry them
    (2026+), else CPI item weights. Returns (weights, basis)."""
    months = sorted(m for m in items_by_month if m.startswith(str(year)))
    if not months:
        return {}, "none"
    items = items_by_month[months[-1]]
    rpi = {i: v["rpi_weight"] for i, v in items.items() if v.get("rpi_weight")}
    if rpi:
        return rpi, "rpi"
    return {i: v["cpi_weight"] for i, v in items.items() if v.get("cpi_weight")}, "cpi_proxy"


def subsection_indices(items: Mapping[str, ItemResult], weights: Mapping[str, float], field: str = "rpi"
                       ) -> dict[str, tuple[float, float]]:
    """{subsection prefix: (index, weight coverage share)}."""
    acc: dict[str, list[tuple[float, float]]] = defaultdict(list)
    tot: dict[str, float] = defaultdict(float)
    for i, w in weights.items():
        sub = subsection_of(i)
        if sub and w > 0:
            tot[sub] += w
            if i in items:
                acc[sub].append((w, getattr(items[i], field)))
    out = {}
    for sub, pairs in acc.items():
        tw = sum(w for w, _ in pairs)
        out[sub] = (sum(w * v for w, v in pairs) / tw, tw / tot[sub] if tot[sub] else 0.0)
    return out


def section_index(sub: Mapping[str, tuple[float, float]], sub_weights: Mapping[str, float]) -> float | None:
    pairs = [(sub_weights[s], v[0]) for s, v in sub.items() if sub_weights.get(s, 0) > 0]
    tw = sum(w for w, _ in pairs)
    return sum(w * v for w, v in pairs) / tw if tw > 0 else None


# --- the monthly run ------------------------------------------------------------------


def _mdate(m: str) -> dt.date:
    return dt.date(int(m[:4]), int(m[4:6]), 1)


def chain_year(m: str) -> int:
    d = _mdate(m)
    return d.year if d.month > 1 else d.year - 1


def reconstruct(
    quotes_by_month: Mapping[str, Sequence[Mapping]],
    items_by_month: Mapping[str, Mapping[str, Mapping[str, float]]],
    subsection_weights: Mapping[str, Mapping[int, float]],
    published: Mapping[str, Mapping[dt.date, float]],
    variant: Variant,
) -> list[dict]:
    """One row per month: reconstructed section and sub-section indices vs
    published, plus Carli/Jevons formula components and dispersion.

    ``subsection_weights``: {prefix: {year: weight}}; ``published``:
    {'CHBJ': series, 'DOCK': series, ...}.
    """
    rows = []
    chbj = published["CHBJ"]
    for m in sorted(quotes_by_month):
        d = _mdate(m)
        cy = chain_year(m)
        link = dt.date(cy, 1, 1)
        items = item_indices(quotes_by_month[m], variant)
        weights, basis = item_weights_for_year(items_by_month, cy)
        if not items or not weights:
            rows.append({"month": m, "error": "no items or no weights", "weight_basis": basis})
            continue
        subw = {s: subsection_weights[s].get(cy) for s in SUBSECTIONS}
        subw = {s: w for s, w in subw.items() if w}
        sub_r = subsection_indices(items, weights, "rpi")
        sub_j = subsection_indices(items, weights, "jevons")
        sec_r = section_index(sub_r, subw)
        sec_j = section_index(sub_j, subw)
        row = {
            "month": m, "chain_year": cy, "weight_basis": basis, "variant": variant.label(),
            "n_items": len(items), "n_quotes": sum(i.n for i in items.values()),
            "section_rpi_index": sec_r, "section_jevons_index": sec_j,
            "sale_share": statistics.fmean(i.sale_share for i in items.values()),
        }
        wsum = sum(weights.get(i, 0) for i in items)
        if wsum > 0:
            row["var_log"] = sum(weights.get(i, 0) * r.var_log for i, r in items.items()) / wsum
        if sec_r is not None and link in chbj and d in chbj:
            rec = chbj[link] * sec_r / 100.0
            row["chbj_published"] = chbj[d]
            row["chbj_reconstructed"] = rec
            row["error_pct"] = 100.0 * (rec / chbj[d] - 1.0)
        for s, meta in SUBSECTIONS.items():
            ser = published.get(meta["index"], {})
            if s in sub_r and link in ser and d in ser:
                rec = ser[link] * sub_r[s][0] / 100.0
                row[f"err_{meta['name']}_pct"] = 100.0 * (rec / ser[d] - 1.0)
                row[f"cov_{meta['name']}"] = sub_r[s][1]
        # 2026+: item-level check against ONS's own segment RPI_INDEX.
        seg = items_by_month.get(m, {})
        diffs = [items[i].rpi - v["rpi_index"] for i, v in seg.items() if v.get("rpi_index") and i in items]
        if diffs:
            row["segment_mae_index_pts"] = statistics.fmean(abs(x) for x in diffs)
            row["segment_n"] = len(diffs)
        rows.append(row)
    return rows


def chained(rows: Sequence[Mapping], key: str) -> dict[dt.date, float]:
    """Chain a Jan=100-within-year section series through each January link.

    Feb..Dec: I(m) = I(Jan) * S(m)/100. January: I(Jan y) = I(Jan y-1) * S(Jan y)/100
    (January relatives are against the previous January). Starts at 100 at the
    first January present; breaks if a January is missing.
    """
    by = {_mdate(r["month"]): r.get(key) for r in rows if r.get(key)}
    out: dict[dt.date, float] = {}
    jans = sorted(d for d in by if d.month == 1)
    if not jans:
        return out
    level_jan = {jans[0]: 100.0}
    for j in jans[1:]:
        prev = dt.date(j.year - 1, 1, 1)
        if prev in level_jan:
            level_jan[j] = level_jan[prev] * by[j] / 100.0
    for d, s in sorted(by.items()):
        base = dt.date(d.year, 1, 1)
        if d.month == 1:
            if d in level_jan:
                out[d] = level_jan[d]
        elif base in level_jan:
            out[d] = level_jan[base] * s / 100.0
    return out


def formula_effect_yoy(rows: Sequence[Mapping]) -> dict[dt.date, float]:
    """12-month log change of chained Carli minus chained Jevons (pp), on
    identical quotes and weights: the bottom-up clothing formula wedge."""
    c = chained(rows, "section_rpi_index")
    j = chained(rows, "section_jevons_index")
    out = {}
    for d in c:
        p = dt.date(d.year - 1, d.month, 1)
        if p in c and d in j and p in j:
            out[d] = 100.0 * (math.log(c[d] / c[p]) - math.log(j[d] / j[p]))
    return out


# --- sampling floor ---------------------------------------------------------------------


def bootstrap_mom_sd(
    cur: Sequence[Mapping], prev: Sequence[Mapping], weights: Mapping[str, float],
    subw: Mapping[str, float], variant: Variant, *, reps: int = 200, seed: int = 0,
) -> float | None:
    """sd (pct) of the section MoM under resampling of ONS's own quotes.

    Resamples quote identities (shop, item, stratum cell) with replacement
    within each (item, stratum), and recomputes both months on the same
    resample so the month-to-month correlation of the panel is kept. That sd
    is ONS's quote-sampling noise: the part of the published MoM no external
    panel can predict.
    """
    def ident(q):
        return (q["item_id"], q.get("stratum_cell"), q.get("shop_code"), q.get("region"))
    prev_by = defaultdict(list)
    for q in prev:
        prev_by[ident(q)].append(q)
    groups: dict[tuple, list[tuple]] = defaultdict(list)
    cur_by = defaultdict(list)
    for q in cur:
        cur_by[ident(q)].append(q)
    for k in cur_by:
        groups[(k[0], k[1])].append(k)
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        c_s, p_s = [], []
        for keys in groups.values():
            for _ in keys:
                k = rng.choice(keys)
                c_s.extend(cur_by[k])
                p_s.extend(prev_by.get(k, []))
        ic = subsection_indices(item_indices(c_s, variant), weights)
        ip = subsection_indices(item_indices(p_s, variant), weights)
        sc, sp = section_index(ic, subw), section_index(ip, subw)
        if sc and sp:
            vals.append(100.0 * (sc / sp - 1.0))
    return statistics.stdev(vals) if len(vals) > 10 else None
