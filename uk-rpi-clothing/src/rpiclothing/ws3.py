"""Workstream 3 backtest: index-day panel nowcast vs the incumbent at h1.

For each origin month m-1 (CHBJ published through m-1), nowcast month m from
the panel observed around m's index day, spliced onto published CHBJ(m-1),
and score against published CHBJ(m). Calibration is refitted at every
origin on months strictly before it (no look-ahead), and refused below a
minimum overlap, in which case the origin is skipped rather than guessed.

Candidates, pre-registered, simplest first:
    (i)   incumbent seasonal+drift h1
    (ii)  raw panel MoM
    (iii) calibrated panel MoM
The 5%/simplest rule on MAE selects. The result is reported next to the
quote-sampling floor from WS2a: no method can beat that floor on the
published number, so a target below it is infeasible, not ambitious.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Callable, Mapping, Sequence

from . import fixing, nowcast, panel
from .series import add_months, mae, seasonal_drift_path
from .ws1 import INCUMBENT

CANDIDATES = ("incumbent", "panel_raw", "panel_calibrated")


def backtest(
    chbj: Mapping[dt.date, float],
    provider: panel.PanelProvider,
    mappings: Mapping[str, panel.SegmentMapping],
    segment_weights: Mapping[str, float],
    targets: Sequence[dt.date],
    *,
    min_calibration: int = 12,
    index_day: Callable[[int, int], dt.date] = nowcast.predicted_index_day,
) -> dict:
    """Errors in pct of level per candidate, per target month."""
    obs_cache: dict[dt.date, list[panel.PanelObservation]] = {}

    def obs_for(month: dt.date) -> list[panel.PanelObservation] | None:
        day = index_day(month.year, month.month)
        if day not in obs_cache:
            window = list(provider.observations_between(day - dt.timedelta(days=6), day + dt.timedelta(days=3)))
            pick = panel.index_day_week((o.obs_date for o in window), day)
            obs_cache[day] = [o for o in window if o.obs_date == pick] if pick else []
        return obs_cache[day] or None

    def panel_mom(m: dt.date) -> float | None:
        # Feb..Dec: ratio of Carli-vs-January at m and m-1. January crosses the
        # chain link and is measured against the previous January instead.
        jan = dt.date(m.year if m.month > 1 else m.year - 1, 1, 1)
        base, prev, cur = obs_for(jan), obs_for(add_months(m, -1)), obs_for(m)
        if not (base and prev and cur):
            return None
        if m.month == 1:
            prev = base
        return panel.section_mom_from_panel(base, prev, cur, mappings, segment_weights)

    history: list[tuple[dt.date, float, float]] = []  # (month, panel mom, published mom)
    errs: dict[str, list[tuple[dt.date, float]]] = {c: [] for c in CANDIDATES}
    for m in sorted(targets):
        prev = add_months(m, -1)
        if prev not in chbj or m not in chbj:
            continue
        actual = chbj[m]
        inc = seasonal_drift_path(chbj, prev, 1, INCUMBENT)[1]
        errs["incumbent"].append((m, 100 * (inc / actual - 1)))
        pm = panel_mom(m)
        pub_mom = 100 * (actual / chbj[prev] - 1)
        if pm is not None:
            errs["panel_raw"].append((m, 100 * (chbj[prev] * (1 + pm / 100) / actual - 1)))
            cal = panel.fit_calibration([(x, y) for _, x, y in history], min_n=min_calibration)
            if cal is not None:
                lvl = chbj[prev] * (1 + cal.apply(pm) / 100)
                errs["panel_calibrated"].append((m, 100 * (lvl / actual - 1)))
            history.append((m, pm, pub_mom))
    scores = {c: {"n": len(e), "mae_pct": mae(e) if e else math.nan} for c, e in errs.items()}
    # Compare like with like: the rule is applied on months every candidate scored.
    common = set.intersection(*(set(d for d, _ in e) for e in errs.values())) if all(errs.values()) else set()
    common_mae = {c: mae([x for x in e if x[0] in common]) for c, e in errs.items()} if common else {}
    choice = fixing.select_simplest(common_mae, CANDIDATES)[1] if common_mae else None
    return {"scores": scores, "common_months": len(common), "common_mae_pct": common_mae,
            "selection": choice}
