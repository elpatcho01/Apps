"""Workstream 2a core: elementary aggregates from quote-level price relatives.

Schema-independent. It takes quotes already reduced to
``Quote(item, stratum, relative, shop_weight, stratum_weight, flags)`` and
builds, per item and stratum:

* **Carli** -- arithmetic mean of ``p_t / p_base`` (the RPI formula for
  clothing);
* **Jevons** -- geometric mean of the same relatives (the CPI formula);
* **dispersion** -- var(log r), which drives the gap: to second order
  ``ln(Carli) - ln(Jevons) ~= var(log r) / 2``.

The relative is against the **January base price**, never last month's (trap
4). That is the whole reason the Carli-Jevons gap accumulates through the year
and resets each January, and the reason sale bounce inflates Carli:

    on sale in Jan (50% off), full price later -> r = 2.0
    full price in Jan, on sale later (50% off) -> r = 0.5
    Carli = 1.25,  Jevons = 1.0

``test_carli.py`` asserts exactly that.

Whether ONS weight quotes by shop weight inside an elementary aggregate is
computed both ways (``shop_weighted``) and settled by validation against
published CHBJ/D7BW rather than assumed (trap 10).
"""

from __future__ import annotations

import dataclasses
import math
import random
import statistics
from collections import defaultdict
from typing import Iterable, Mapping, Sequence


@dataclasses.dataclass(frozen=True, slots=True)
class Quote:
    item: str
    stratum: str
    #: p_t / p_base, base = the January price (or the re-based price after a
    #: replacement, per the ONS rules applied upstream).
    relative: float
    shop_weight: float = 1.0
    stratum_weight: float = 1.0
    #: Upstream flags, e.g. {'sale'} or {'comparable'}; used for diagnostics only.
    on_sale: bool = False
    base_on_sale: bool = False


@dataclasses.dataclass(frozen=True)
class Aggregate:
    carli: float
    jevons: float
    var_log: float
    n: int

    @property
    def gap_log(self) -> float:
        """ln(Carli) - ln(Jevons)."""
        return math.log(self.carli) - math.log(self.jevons)


def elementary(
    relatives: Sequence[float], weights: Sequence[float] | None = None
) -> Aggregate:
    """Carli and Jevons over one elementary aggregate.

    With ``weights`` both means are weighted (shop weights); without, they are
    the plain unweighted means the RPI definition describes.
    """
    rs = [r for r in relatives]
    if not rs:
        raise ValueError("empty elementary aggregate")
    if any(r <= 0 or not math.isfinite(r) for r in rs):
        raise ValueError("price relatives must be positive and finite")
    ws = list(weights) if weights is not None else [1.0] * len(rs)
    tw = sum(ws)
    if tw <= 0:
        raise ValueError("weights sum to zero")
    logs = [math.log(r) for r in rs]
    carli = sum(w * r for w, r in zip(ws, rs)) / tw
    mlog = sum(w * lr for w, lr in zip(ws, logs)) / tw
    var = sum(w * (lr - mlog) ** 2 for w, lr in zip(ws, logs)) / tw
    return Aggregate(carli=carli, jevons=math.exp(mlog), var_log=var, n=len(rs))


@dataclasses.dataclass(frozen=True)
class ItemIndex:
    item: str
    carli: float
    jevons: float
    #: Stratum-weighted mean of within-stratum var(log r).
    var_log: float
    n_quotes: int
    n_strata: int
    sale_share: float
    base_sale_share: float


def item_indices(
    quotes: Iterable[Quote], *, shop_weighted: bool = False
) -> dict[str, ItemIndex]:
    """Per item: stratum EAs combined with stratum weights.

    Item index = sum_s W_s * EA_s / sum_s W_s, for Carli and Jevons alike. The
    stratum weight is taken from the quotes (ONS publish it per quote; it is
    constant within a stratum). A stratum whose weights disagree raises, since
    that means the stratum key is wrong.
    """
    by: dict[str, dict[str, list[Quote]]] = defaultdict(lambda: defaultdict(list))
    for q in quotes:
        by[q.item][q.stratum].append(q)
    out: dict[str, ItemIndex] = {}
    for item, strata in by.items():
        num_c = num_j = num_v = den = 0.0
        nq = 0
        sale = base_sale = 0
        for key, qs in strata.items():
            sw = {q.stratum_weight for q in qs}
            if len(sw) > 1 and (max(sw) - min(sw)) > 1e-9 * max(1.0, max(sw)):
                raise ValueError(f"{item}/{key}: inconsistent stratum weights {sorted(sw)[:4]}")
            w = qs[0].stratum_weight
            agg = elementary(
                [q.relative for q in qs], [q.shop_weight for q in qs] if shop_weighted else None
            )
            num_c += w * agg.carli
            num_j += w * agg.jevons
            num_v += w * agg.var_log
            den += w
            nq += len(qs)
            sale += sum(q.on_sale for q in qs)
            base_sale += sum(q.base_on_sale for q in qs)
        if den <= 0:
            continue
        out[item] = ItemIndex(
            item=item,
            carli=num_c / den,
            jevons=num_j / den,
            var_log=num_v / den,
            n_quotes=nq,
            n_strata=len(strata),
            sale_share=sale / nq,
            base_sale_share=base_sale / nq,
        )
    return out


def section_index(items: Mapping[str, ItemIndex], weights: Mapping[str, float], which: str) -> float:
    """Weighted mean of item indices (``which`` = 'carli' or 'jevons').

    Items without a weight are excluded, and so are weights without an item;
    the caller reports coverage (``weight_coverage``) so the exclusion is seen.
    """
    common = [i for i in items if weights.get(i, 0) > 0]
    tw = sum(weights[i] for i in common)
    if tw <= 0:
        raise ValueError("no weighted items in common")
    return sum(weights[i] * getattr(items[i], which) for i in common) / tw


def weight_coverage(items: Mapping[str, ItemIndex], weights: Mapping[str, float]) -> float:
    total = sum(w for w in weights.values() if w > 0)
    if total <= 0:
        return 0.0
    return sum(w for i, w in weights.items() if i in items and w > 0) / total


# --- sampling floor (WS3 step 1) ----------------------------------------------


def bootstrap_item_relatives(
    quotes: Sequence[Quote],
    *,
    reps: int = 500,
    seed: int = 0,
    shop_weighted: bool = False,
) -> dict[str, list[float]]:
    """Resample quotes with replacement *within each stratum* and recompute the
    item Carli, ``reps`` times. Returns item -> list of bootstrap Carli values.

    The spread of these is ONS's own quote-sampling noise: a different draw of
    shops and products from the same strata would have produced a different
    index. No external panel can predict that component, which is what makes
    it the floor under any nowcast of the published number.
    """
    rng = random.Random(seed)
    by: dict[tuple[str, str], list[Quote]] = defaultdict(list)
    for q in quotes:
        by[(q.item, q.stratum)].append(q)
    groups = list(by.items())
    out: dict[str, list[float]] = defaultdict(list)
    for _ in range(reps):
        resampled: list[Quote] = []
        for _key, qs in groups:
            resampled.extend(rng.choice(qs) for _ in qs)
        for item, idx in item_indices(resampled, shop_weighted=shop_weighted).items():
            out[item].append(idx.carli)
    return dict(out)


def bootstrap_section_sd(
    quotes: Sequence[Quote],
    item_weights: Mapping[str, float],
    *,
    reps: int = 500,
    seed: int = 0,
    shop_weighted: bool = False,
) -> tuple[float, float]:
    """(sd, mean) of the bootstrap section Carli, as a fraction of its level.

    Resamples within strata for all items jointly in each replicate, so the
    section figure carries the right covariance (none, across strata).
    """
    rng = random.Random(seed)
    by: dict[tuple[str, str], list[Quote]] = defaultdict(list)
    for q in quotes:
        by[(q.item, q.stratum)].append(q)
    groups = list(by.values())
    vals = []
    for _ in range(reps):
        resampled = [rng.choice(qs) for qs in groups for _ in qs]
        items = item_indices(resampled, shop_weighted=shop_weighted)
        vals.append(section_index(items, item_weights, "carli"))
    m = statistics.fmean(vals)
    return statistics.stdev(vals) / m, m
