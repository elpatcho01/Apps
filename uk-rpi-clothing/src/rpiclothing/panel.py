"""Workstream 2b: licensed vendor panel -- interface, mock, mapping, calibration,
sale-timing detection.

No vendor is signed yet. This module fixes the contract a vendor adapter must
meet (``PanelProvider``), provides a deterministic ``MockPanel`` so the whole
nowcast path is exercisable in tests, and implements everything downstream of
the adapter. Swapping in a real vendor means writing one adapter; nothing else
changes.

Hard requirements from the brief that the interface encodes:

* SKU-level observations with **full price, current price and a markdown
  flag** (``PanelObservation``);
* observation **dates**, not just week numbers, so the week containing index
  day can be isolated (``observations_between``);
* stable SKU identity (``sku``) so matched relatives can be computed;
* a vendor category that can be **mapped to ONS consumption segments**, with a
  confidence per mapping that is never forced (``SegmentMapping``).

A guard (``assert_not_mock``) stops mock data reaching production tables, as in
the sibling projects.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import math
import random
import statistics
from collections import defaultdict
from typing import Iterable, Mapping, Protocol, Sequence

from .carli import Quote, item_indices


@dataclasses.dataclass(frozen=True, slots=True)
class PanelObservation:
    obs_date: dt.date
    fascia: str
    sku: str
    vendor_category: str
    full_price: float
    price: float
    on_markdown: bool
    source: str

    @property
    def markdown_depth(self) -> float:
        return 0.0 if self.full_price <= 0 else max(0.0, 1.0 - self.price / self.full_price)


class PanelProvider(Protocol):
    name: str

    def observations_between(self, start: dt.date, end: dt.date) -> Iterable[PanelObservation]:
        """All observations dated in [start, end]."""


class MockDataReachedProduction(RuntimeError):
    pass


def assert_not_mock(rows: Iterable[PanelObservation], *, production: bool) -> None:
    if production and any(r.source == "mock" for r in rows):
        raise MockDataReachedProduction("mock panel observations must never be written to production")


# --- mock -------------------------------------------------------------------------


class MockPanel:
    """Deterministic synthetic panel.

    Generates SKUs per vendor category and fascia with persistent full prices,
    a per-SKU sale calendar (January and summer sales, starting on a
    category/year-specific date so sale onset relative to index day varies),
    and churn. Seeded by date so any window is reproducible.
    """

    name = "mock"

    def __init__(
        self,
        categories: Sequence[str] = ("womens_dresses", "mens_jeans", "kids_tops", "footwear_women"),
        fascias: Sequence[str] = ("fascia_a", "fascia_b", "fascia_c"),
        skus_per_cell: int = 40,
        seed: int = 7,
    ) -> None:
        self.categories = tuple(categories)
        self.fascias = tuple(fascias)
        self.n = skus_per_cell
        self.seed = seed

    def _rng(self, *parts: object) -> random.Random:
        h = hashlib.sha256(("|".join(map(str, (self.seed, *parts)))).encode()).digest()
        return random.Random(int.from_bytes(h[:8], "big"))

    def sale_onset(self, category: str, year: int, season: str) -> dt.date:
        r = self._rng("onset", category, year, season)
        if season == "winter":
            return dt.date(year - 1, 12, 26) + dt.timedelta(days=r.randint(0, 20))
        return dt.date(year, 6, 20) + dt.timedelta(days=r.randint(0, 30))

    def observations_between(self, start: dt.date, end: dt.date) -> Iterable[PanelObservation]:
        d = start
        while d <= end:
            if d.weekday() == 1:  # one observation a week, on Tuesdays
                yield from self._day(d)
            d += dt.timedelta(days=1)

    def _day(self, d: dt.date) -> Iterable[PanelObservation]:
        for cat in self.categories:
            w_on = self.sale_onset(cat, d.year + (1 if d.month == 12 else 0), "winter")
            s_on = self.sale_onset(cat, d.year, "summer")
            for fa in self.fascias:
                for i in range(self.n):
                    # 5% of SKUs are replaced each quarter: churn.
                    gen = (d.year * 4 + (d.month - 1) // 3) if i % 20 == 0 else 0
                    sku = f"{fa}-{cat}-{i}-{gen}"
                    r = self._rng("sku", sku)
                    full = round(r.uniform(10, 80), 2) * (1 + 0.004 * (d.year - 2020) * 12)
                    in_winter = w_on <= d <= w_on + dt.timedelta(days=35)
                    in_summer = s_on <= d <= s_on + dt.timedelta(days=40)
                    marked = (in_winter or in_summer) and r.random() < 0.6
                    depth = r.choice((0.2, 0.3, 0.5)) if marked else 0.0
                    yield PanelObservation(d, fa, sku, cat, round(full, 2),
                                           round(full * (1 - depth), 2), marked, "mock")


# --- mapping and relatives ----------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SegmentMapping:
    vendor_category: str
    cs_id: str
    confidence: float  # 0..1; below the threshold the category is dropped, never forced

    def usable(self, threshold: float = 0.7) -> bool:
        return self.confidence >= threshold


def index_day_week(obs_dates: Iterable[dt.date], index_day: dt.date) -> dt.date | None:
    """The observation date to use for an index day: the latest observation on
    or before index day within the same 7-day window, else the first after it
    within 3 days. None if the panel has nothing close enough."""
    ds = sorted(set(obs_dates))
    before = [d for d in ds if index_day - dt.timedelta(days=6) <= d <= index_day]
    if before:
        return before[-1]
    after = [d for d in ds if index_day < d <= index_day + dt.timedelta(days=3)]
    return after[0] if after else None


def panel_quotes(
    base: Sequence[PanelObservation],
    current: Sequence[PanelObservation],
    mappings: Mapping[str, SegmentMapping],
    *,
    threshold: float = 0.7,
) -> list[Quote]:
    """Matched-SKU relatives current/base, as Carli ``Quote`` objects.

    Only SKUs present on both dates contribute (matched sample, as in the
    siblings); categories below the mapping threshold are dropped. Stratum =
    fascia, a stand-in for ONS shop-type strata, equally weighted.
    """
    b = {(o.fascia, o.sku): o for o in base}
    out = []
    for o in current:
        m = mappings.get(o.vendor_category)
        if m is None or not m.usable(threshold):
            continue
        ob = b.get((o.fascia, o.sku))
        if ob is None or ob.price <= 0 or o.price <= 0:
            continue
        out.append(Quote(item=m.cs_id, stratum=o.fascia, relative=o.price / ob.price,
                         on_sale=o.on_markdown, base_on_sale=ob.on_markdown))
    return out


# --- sale timing -------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SaleShare:
    obs_date: dt.date
    group: str
    share_marked_down: float
    mean_depth: float
    n: int


def sale_shares(obs: Iterable[PanelObservation], mappings: Mapping[str, SegmentMapping]) -> list[SaleShare]:
    acc: dict[tuple[dt.date, str], list[PanelObservation]] = defaultdict(list)
    for o in obs:
        m = mappings.get(o.vendor_category)
        if m is not None and m.usable():
            acc[(o.obs_date, m.cs_id)].append(o)
    return [
        SaleShare(d, g, sum(x.on_markdown for x in xs) / len(xs),
                  statistics.fmean(x.markdown_depth for x in xs), len(xs))
        for (d, g), xs in sorted(acc.items())
    ]


def sale_onset(shares: Sequence[SaleShare], group: str, window: tuple[dt.date, dt.date],
               *, threshold: float = 0.2) -> dt.date | None:
    """First date in ``window`` where the group's markdown share crosses
    ``threshold`` having been below it at the window's first observation."""
    s = [x for x in shares if x.group == group and window[0] <= x.obs_date <= window[1]]
    if not s or s[0].share_marked_down >= threshold:
        return None
    for x in s:
        if x.share_marked_down >= threshold:
            return x.obs_date
    return None


# --- calibration ----------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Calibration:
    """published_mom = alpha + beta * panel_mom (percent), fitted on overlap months."""

    alpha: float
    beta: float
    n: int
    resid_sd: float

    def apply(self, panel_mom_pct: float) -> float:
        return self.alpha + self.beta * panel_mom_pct


def fit_calibration(pairs: Sequence[tuple[float, float]], *, min_n: int = 12) -> Calibration | None:
    """OLS of published MoM on panel MoM over the overlap. Refuses below
    ``min_n`` pairs rather than returning a calibration nobody should trust.
    Must be fitted per backtest origin on months before the origin only."""
    if len(pairs) < min_n:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    alpha = my - beta * mx
    resid = [y - (alpha + beta * x) for x, y in pairs]
    return Calibration(alpha, beta, len(pairs), statistics.stdev(resid) if len(resid) > 2 else math.nan)


def section_mom_from_panel(
    base_obs: Sequence[PanelObservation],
    prev_obs: Sequence[PanelObservation],
    cur_obs: Sequence[PanelObservation],
    mappings: Mapping[str, SegmentMapping],
    segment_weights: Mapping[str, float],
) -> float | None:
    """Section Carli MoM (percent) implied by the panel: ratio of Carli-vs-base
    at the current and previous index days, weighted by segment weights, the
    same construction the RPI uses (fixed January base)."""
    cur = item_indices(panel_quotes(base_obs, cur_obs, mappings))
    prev = item_indices(panel_quotes(base_obs, prev_obs, mappings))
    common = [i for i in cur if i in prev and segment_weights.get(i, 0) > 0]
    if not common:
        return None
    tw = sum(segment_weights[i] for i in common)
    c = sum(segment_weights[i] * cur[i].carli for i in common) / tw
    p = sum(segment_weights[i] * prev[i].carli for i in common) / tw
    return 100.0 * (c / p - 1.0)
