"""Monthly series arithmetic: MoM, seasonal factors, the seasonal+drift model,
and the chain-linked RPI contribution.

Conventions used everywhere in this package:

* A series is a ``dict[date, float]`` keyed by the first of the month.
* MoM is in **percent**: ``100 * (I_t / I_{t-1} - 1)``.
* Forecast error is in **percent of the index level** ("pp of level"):
  ``100 * (forecast / actual - 1)``. The brief's MAE table and its 4.2bp/pp
  conversion assume this unit; Task 0b asks for it to be confirmed.
* Excluded years (2020 and 2021 by default) are dropped from *estimation* of
  seasonal factors and variances, never from the series itself, and never from
  the set of backtest origins unless a caller says so.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
import statistics
from typing import Callable, Iterable, Mapping, Sequence

Series = Mapping[dt.date, float]

DEFAULT_EXCLUDE_YEARS: frozenset[int] = frozenset({2020, 2021})


def add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    return dt.date(d.year + y, m + 1, 1)


def mom(series: Series) -> dict[dt.date, float]:
    """Percent month-on-month change, keyed by the later month."""
    out: dict[dt.date, float] = {}
    for d, v in series.items():
        prev = series.get(add_months(d, -1))
        if prev:
            out[d] = 100.0 * (v / prev - 1.0)
    return out


def yoy(series: Series, d: dt.date) -> float | None:
    prev = series.get(add_months(d, -12))
    cur = series.get(d)
    if prev is None or cur is None:
        return None
    return 100.0 * (cur / prev - 1.0)


def _in_window(
    d: dt.date,
    start: dt.date | None,
    end: dt.date | None,
    exclude_years: frozenset[int],
) -> bool:
    if d.year in exclude_years:
        return False
    if start is not None and d < start:
        return False
    if end is not None and d > end:
        return False
    return True


@dataclasses.dataclass(frozen=True)
class SeasonalFit:
    """Per-calendar-month factors, in pp of MoM.

    ``raw`` is the plain mean MoM for each calendar month; its sum over the
    twelve months is the average annual growth (roughly), so it carries drift.
    ``demeaned`` subtracts each year's own mean MoM first, so its twelve values
    sum to approximately zero and drift has to be added back separately.
    """

    raw: dict[int, float]
    demeaned: dict[int, float]
    n: dict[int, int]
    residual_sd: float
    raw_sd: float

    @property
    def raw_sum(self) -> float:
        return sum(self.raw.values())

    @property
    def demeaned_sum(self) -> float:
        return sum(self.demeaned.values())


def fit_seasonal(
    changes: Mapping[dt.date, float],
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
    exclude_years: frozenset[int] = DEFAULT_EXCLUDE_YEARS,
    complete_years_only: bool = False,
) -> SeasonalFit:
    """Estimate seasonal factors from MoM changes within [start, end].

    Year-demeaning uses the mean of whatever months of that year fall inside
    the window. With ``complete_years_only`` a year contributes only if all
    twelve of its months are present, which avoids a partial year (say Jan-Jun
    2026) being demeaned against the wrong annual mean.
    """
    used = {d: v for d, v in changes.items() if _in_window(d, start, end, exclude_years)}
    by_year: dict[int, dict[int, float]] = {}
    for d, v in used.items():
        by_year.setdefault(d.year, {})[d.month] = v
    if complete_years_only:
        by_year = {y: ms for y, ms in by_year.items() if len(ms) == 12}
    raw_acc: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    dm_acc: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    for _, months in sorted(by_year.items()):
        ymean = statistics.fmean(months.values())
        for m, v in months.items():
            raw_acc[m].append(v)
            dm_acc[m].append(v - ymean)
    raw = {m: statistics.fmean(v) if v else math.nan for m, v in raw_acc.items()}
    dem = {m: statistics.fmean(v) if v else math.nan for m, v in dm_acc.items()}
    resid = [v - raw[m] for m, vs in raw_acc.items() for v in vs]
    allv = [v for vs in raw_acc.values() for v in vs]
    return SeasonalFit(
        raw=raw,
        demeaned=dem,
        n={m: len(v) for m, v in raw_acc.items()},
        residual_sd=statistics.stdev(resid) if len(resid) > 1 else math.nan,
        raw_sd=statistics.stdev(allv) if len(allv) > 1 else math.nan,
    )


def residuals_by_month(
    changes: Mapping[dt.date, float],
    fit: SeasonalFit,
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
    exclude_years: frozenset[int] = DEFAULT_EXCLUDE_YEARS,
) -> dict[int, list[float]]:
    out: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    for d, v in sorted(changes.items()):
        if _in_window(d, start, end, exclude_years):
            out[d.month].append(v - fit.raw[d.month])
    return out


# --- the seasonal + drift model (the incumbent) ------------------------------


@dataclasses.dataclass(frozen=True)
class SeasonalDriftSpec:
    """Parameters of the seasonal + drift forecaster.

    ``seasonal_years``: how many trailing calendar years (ending at the origin)
    feed the seasonal factors; None = everything from ``seasonal_start``.
    ``drift_months``: trailing window for the drift (mean MoM); 0 = no separate
    drift, i.e. forecast with the *raw* seasonal means, which carry the
    sample-average drift implicitly.
    """

    seasonal_years: int | None = 8
    seasonal_start: dt.date | None = None
    drift_months: int = 12
    exclude_years: frozenset[int] = DEFAULT_EXCLUDE_YEARS
    #: Exclude the excluded years from the drift window too.
    drift_excludes: bool = False

    def label(self) -> str:
        sy = "all" if self.seasonal_years is None else f"{self.seasonal_years}y"
        dx = "x" if self.drift_excludes else ""
        return f"seas={sy} drift={self.drift_months}m{dx}"


def seasonal_drift_path(
    level: Series,
    origin: dt.date,
    horizons: int,
    spec: SeasonalDriftSpec,
    *,
    changes: Mapping[dt.date, float] | None = None,
) -> dict[int, float]:
    """Forecast levels for origin+1 .. origin+horizons, using only data <= origin."""
    changes = changes if changes is not None else mom(level)
    known = {d: v for d, v in changes.items() if d <= origin}
    start = spec.seasonal_start
    if spec.seasonal_years is not None:
        start = dt.date(origin.year - spec.seasonal_years, origin.month, 1)
        start = add_months(start, 1)
    fit = fit_seasonal(known, start=start, end=origin, exclude_years=spec.exclude_years)
    if spec.drift_months > 0:
        window = [
            v
            for d, v in sorted(known.items())[::-1]
            if not (spec.drift_excludes and d.year in spec.exclude_years)
        ][: spec.drift_months]
        drift = statistics.fmean(window)
        factors = {m: fit.demeaned[m] + drift for m in range(1, 13)}
    else:
        factors = dict(fit.raw)
    out: dict[int, float] = {}
    lvl = level[origin]
    for h in range(1, horizons + 1):
        m = add_months(origin, h).month
        f = factors[m]
        if math.isnan(f):
            f = 0.0
        lvl *= 1.0 + f / 100.0
        out[h] = lvl
    return out


def rolling_origin_errors(
    level: Series,
    origins: Sequence[dt.date],
    horizons: Sequence[int],
    forecaster: Callable[[dt.date, int], Mapping[int, float]],
) -> dict[int, list[tuple[dt.date, float]]]:
    """Errors in pp of level, per horizon, for each origin where the actual exists."""
    hmax = max(horizons)
    out: dict[int, list[tuple[dt.date, float]]] = {h: [] for h in horizons}
    for o in origins:
        if o not in level:
            continue
        path = forecaster(o, hmax)
        for h in horizons:
            target = add_months(o, h)
            if target in level and h in path:
                out[h].append((target, 100.0 * (path[h] / level[target] - 1.0)))
    return out


def mae(errors: Iterable[tuple[dt.date, float]] | Iterable[float]) -> float:
    vals = [abs(e[1]) if isinstance(e, tuple) else abs(e) for e in errors]
    return statistics.fmean(vals) if vals else math.nan


def month_range(start: dt.date, end: dt.date) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        out.append(d)
        d = add_months(d, 1)
    return out


# --- chain-linked RPI contribution (trap 1) ----------------------------------


def link_january(d: dt.date) -> dt.date:
    """January base that month ``d``'s change is measured against.

    RPI links each year's chain in January, and the December->January change is
    measured on the *previous* year's January base and weights (January is
    effectively month 13 of the old chain). So the base for any month's change
    is the January strictly before that month: Feb..Dec -> this January;
    January -> last January.
    """
    return dt.date(d.year if d.month > 1 else d.year - 1, 1, 1)


def weight_year(d: dt.date) -> int:
    """Calendar year whose weights govern the change *into* month ``d``."""
    return link_january(d).year


def contribution_bp(
    section: Series,
    all_items: Series,
    weights_ppt: Mapping[int, float],
    d: dt.date,
) -> float | None:
    """Section's contribution to the all-items MoM, in bp of all-items.

        contrib_t = w * (I_c,t - I_c,t-1) / I_c,base  /  (I_all,t-1 / I_all,base)

    with base = the governing January (see ``link_january``). This is exact for a
    Laspeyres-type chain of January-based sections. The naive ``w * MoM`` it
    replaces ignores both ratios.
    """
    prev = add_months(d, -1)
    base = link_january(d)
    w = weights_ppt.get(weight_year(d))
    need = (section.get(d), section.get(prev), section.get(base), all_items.get(prev), all_items.get(base))
    if w is None or any(v is None for v in need):
        return None
    c_t, c_prev, c_base, a_prev, a_base = need  # type: ignore[misc]
    return 1e4 * (w / 1000.0) * ((c_t - c_prev) / c_base) / (a_prev / a_base)


def effective_weight_ppt(
    section: Series, all_items: Series, weights_ppt: Mapping[int, float], d: dt.date
) -> float | None:
    """The weight that, multiplied by the section MoM, gives the exact contribution."""
    prev = add_months(d, -1)
    base = link_january(d)
    w = weights_ppt.get(weight_year(d))
    need = (section.get(prev), section.get(base), all_items.get(prev), all_items.get(base))
    if w is None or any(v is None for v in need):
        return None
    c_prev, c_base, a_prev, a_base = need  # type: ignore[misc]
    return w * (c_prev / c_base) / (a_prev / a_base)
