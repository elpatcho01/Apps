"""Workstream 3: index-day nowcast of CHBJ, and the wedge forecasters for WS2b.

Index day
---------
ONS collect locally on index day: working rule "the Tuesday nearest the 13th",
always the 10th-16th. The sibling air-fares calendar treats index day as the
2nd or 3rd Tuesday, confirmed retrospectively from the next bulletin; the
nearest-13th rule is a strict subset of that. ``predicted_index_day`` gives
the prediction; a bulletin-confirmed date always overrides it and every
nowcast row records which one it used.

Splice
------
The panel contributes only the change, and ONS the level (as in
``uk-airfares/src/ukairfares/index.py``):

    nowcast CHBJ(m) = published CHBJ(m-1) * (1 + calibrated panel MoM / 100)

The YoY base CHBJ(m-12) is already published, so the h1 annual rate follows
directly and its error equals the level error.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
import statistics
from typing import Mapping, Sequence

from .series import add_months, effective_weight_ppt, link_january, weight_year

TUESDAY = 1


def predicted_index_day(year: int, month: int) -> dt.date:
    """The Tuesday nearest the 13th (10th-16th inclusive)."""
    d13 = dt.date(year, month, 13)
    offset = (TUESDAY - d13.weekday()) % 7
    if offset > 3:
        offset -= 7
    return d13 + dt.timedelta(days=offset)


def is_second_or_third_tuesday(d: dt.date) -> bool:
    return d.weekday() == TUESDAY and 8 <= d.day <= 21


@dataclasses.dataclass(frozen=True)
class Nowcast:
    target_month: dt.date
    index_day: dt.date
    index_day_source: str  # "predicted" | "confirmed"
    level: float
    mom_pct: float
    yoy_pct: float | None
    headline_contribution_bp: float | None
    sd_pct: float | None
    method: str

    def interval(self, z: float = 1.6448536269514722) -> tuple[float, float] | None:
        if self.sd_pct is None:
            return None
        return (self.level * (1 - z * self.sd_pct / 100), self.level * (1 + z * self.sd_pct / 100))


def splice(
    published: Mapping[dt.date, float],
    target: dt.date,
    mom_pct: float,
    *,
    all_items: Mapping[dt.date, float] | None = None,
    weights_ppt: Mapping[int, float] | None = None,
    index_day: dt.date | None = None,
    index_day_source: str = "predicted",
    sd_pct: float | None = None,
    method: str = "panel_calibrated",
) -> Nowcast:
    prev = add_months(target, -1)
    if prev not in published:
        raise ValueError(f"cannot splice {target}: {prev} not published")
    level = published[prev] * (1 + mom_pct / 100.0)
    base12 = published.get(add_months(target, -12))
    yoy = None if base12 is None else 100.0 * (level / base12 - 1.0)
    contrib = None
    if all_items is not None and weights_ppt is not None:
        ew = effective_weight_ppt(published, all_items, weights_ppt, target)
        if ew is not None:
            contrib = ew / 1000.0 * mom_pct * 100.0
    return Nowcast(
        target_month=target,
        index_day=index_day or predicted_index_day(target.year, target.month),
        index_day_source=index_day_source,
        level=level,
        mom_pct=mom_pct,
        yoy_pct=yoy,
        headline_contribution_bp=contrib,
        sd_pct=sd_pct,
        method=method,
    )


# --- wedge forecasters (WS2b) --------------------------------------------------------


def log_wedge(rpi: Mapping[dt.date, float], cpi: Mapping[dt.date, float], d: dt.date) -> float | None:
    """RPI minus CPI 12-month log change, in pp. Task 0 established this is
    the brief's definition: it reproduces 5.99pp for Jun 2026 exactly, where
    differences of rates give 6.14 (1dp levels) or 6.2 (rounded rates)."""
    p = add_months(d, -12)
    need = (rpi.get(d), rpi.get(p), cpi.get(d), cpi.get(p))
    if any(v is None for v in need):
        return None
    r1, r0, c1, c0 = need  # type: ignore[misc]
    return 100.0 * (math.log(r1 / r0) - math.log(c1 / c0))


def wedge_decomposition(
    rpi: Mapping[dt.date, float], cpi: Mapping[dt.date, float], d: dt.date
) -> dict[str, float] | None:
    """Trap 6: split the YoY log wedge at month d into
    (a) the part-year wedge from last January to d, and
    (b) the remainder, which spans d-12 .. last January (the tail of last
        year's chain plus the January link).
    a + b = the YoY wedge exactly."""
    total = log_wedge(rpi, cpi, d)
    jan = dt.date(d.year, 1, 1)
    if total is None or jan not in rpi or jan not in cpi:
        return None
    part = 100.0 * (math.log(rpi[d] / rpi[jan]) - math.log(cpi[d] / cpi[jan]))
    return {"yoy": total, "this_year_part": part, "carried_in": total - part}


def random_walk_wedge(
    rpi: Mapping[dt.date, float], cpi: Mapping[dt.date, float], origin: dt.date, h: int
) -> float | None:
    """Candidate (i), the incumbent: the wedge at origin+h equals the wedge at origin."""
    del h
    return log_wedge(rpi, cpi, origin)


def seasonal_wedge(
    rpi: Mapping[dt.date, float], cpi: Mapping[dt.date, float], origin: dt.date, h: int,
    *, years: int = 8, exclude_years: frozenset[int] = frozenset({2020, 2021}),
) -> float | None:
    """Candidate (ii-a), no panel needed: random walk plus the average change in
    the wedge from origin's calendar month to the target's calendar month over
    past years. The within-year Carli wedge builds from January to December and
    drops at the January link, so the wedge has a seasonal profile a pure random
    walk ignores. Only past years with both ends known at origin are used."""
    w0 = log_wedge(rpi, cpi, origin)
    if w0 is None:
        return None
    deltas = []
    for k in range(1, years + 1):
        o = add_months(origin, -12 * k)
        t = add_months(o, h)
        if t > origin or o.year in exclude_years or t.year in exclude_years:
            continue
        a, b = log_wedge(rpi, cpi, o), log_wedge(rpi, cpi, t)
        if a is not None and b is not None:
            deltas.append(b - a)
    if not deltas:
        return w0
    return w0 + statistics.fmean(deltas)


def score_wedge(
    rpi: Mapping[dt.date, float], cpi: Mapping[dt.date, float],
    origins: Sequence[dt.date], horizons: Sequence[int], forecaster,
) -> dict[int, dict[str, float]]:
    out = {}
    for h in horizons:
        errs = []
        for o in origins:
            f = forecaster(rpi, cpi, o, h)
            a = log_wedge(rpi, cpi, add_months(o, h))
            if f is not None and a is not None:
                errs.append(f - a)
        out[h] = {
            "n": len(errs),
            "mae_pp": statistics.fmean(abs(e) for e in errs) if errs else math.nan,
            "bias_pp": statistics.fmean(errs) if errs else math.nan,
        }
    return out
