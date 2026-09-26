"""Workstream 1: calendar-month-conditional fixing exposure.

The point forecast is the incumbent seasonal + drift model, unchanged. What
this module changes is the *spread* around it. The incumbent fan uses one
residual variance for every month; the candidates below let it depend on the
calendar month, with increasing freedom:

    (i)   unconditional   one pooled variance                      [incumbent]
    (ii)  regime          sale-transition months vs the rest
    (iii) shrunk          twelve variances, log-shrunk to the pooled one
    (iv)  raw             twelve free variances

That order is the pre-registered complexity order used by the 5%/simplest
selection rule. It is fixed here, in code, before any backtest is run.

Level fan at horizon h, in percent of the level (a normal approximation):

    var_h = sum_{k=1..h} sigma^2_{month(k)}  +  h^2 * sigma^2_drift

The second term is the drift error, persistent across the horizon, so its
standard deviation grows with h, not sqrt(h). sigma^2_drift is estimated at
each origin from that origin's own past 12-month forecast errors, net of the
residual part. This is why conditional monthly variances matter at h1-h3 and
are swamped at h12. The fan reports the two parts separately so that is
visible rather than argued.

Everything is re-estimated at every origin from data <= origin. Nothing
full-sample leaks into the backtest (trap 2).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
import statistics
from typing import Callable, Mapping, Sequence

from .series import (
    DEFAULT_EXCLUDE_YEARS,
    SeasonalDriftSpec,
    add_months,
    fit_seasonal,
    mom,
    month_range,
    seasonal_drift_path,
)

#: Calendar months treated as the sale-transition regime in candidate (ii):
#: the January and summer sales going on (Jan, Jul) and coming off (Feb, Aug,
#: Sep). Fixed in advance from retail practice, not fitted.
SALE_TRANSITION_MONTHS: frozenset[int] = frozenset({1, 2, 7, 8, 9})

VARIANCE_CANDIDATES: tuple[str, ...] = ("unconditional", "regime", "shrunk", "raw")

#: Added AFTER the first live backtest (research run 2, 2026-09-26), which
#: showed every residual-based candidate under-covering at h1 (nominal 90%
#: band covering 76-79% of outcomes): in-sample residual variance understates
#: out-of-sample error, because the seasonal factors and drift are themselves
#: estimated. These size the fan from the origin's own past *out-of-sample*
#: errors instead. They were chosen after seeing results, so they sit last in
#: the complexity order and any win they record is labelled as such.
EMPIRICAL_CANDIDATES: tuple[str, ...] = ("empirical", "empirical_month")

#: Added AFTER research run 3: the empirical normal fans lifted h1 90%
#: coverage only from 78.5% to 83%, with 50% coverage already about right --
#: the signature of fat tails, which no normal fan can fix. This candidate
#: uses the empirical distribution of past out-of-sample errors directly
#: (shifted onto the point forecast), scored with the ensemble CRPS.
QUANTILE_CANDIDATES: tuple[str, ...] = ("empirical_quantile",)

ALL_CANDIDATES: tuple[str, ...] = VARIANCE_CANDIDATES + EMPIRICAL_CANDIDATES + QUANTILE_CANDIDATES

NOMINAL_COVERAGE: tuple[float, ...] = (0.5, 0.8, 0.9)

_Z = {0.5: 0.6744897501960817, 0.8: 1.2815515655446004, 0.9: 1.6448536269514722}
_Z_P10 = 1.2815515655446004


# --- variance estimators -----------------------------------------------------


def monthly_residuals(
    changes: Mapping[dt.date, float],
    *,
    start: dt.date | None,
    end: dt.date,
    exclude_years: frozenset[int] = DEFAULT_EXCLUDE_YEARS,
) -> dict[int, list[float]]:
    """MoM minus the raw seasonal mean, by calendar month, within the window."""
    fit = fit_seasonal(changes, start=start, end=end, exclude_years=exclude_years)
    out: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    for d, v in changes.items():
        if d > end or d.year in exclude_years or (start is not None and d < start):
            continue
        out[d.month].append(v - fit.raw[d.month])
    return out


def _var(xs: Sequence[float], ddof: int = 1) -> float:
    return statistics.variance(xs) if len(xs) > ddof else math.nan


def monthly_variances(resid: Mapping[int, Sequence[float]], candidate: str) -> dict[int, float]:
    """Residual variance per calendar month under one candidate."""
    pooled_vals = [v for vs in resid.values() for v in vs]
    pooled = _var(pooled_vals)
    if candidate == "unconditional":
        return {m: pooled for m in range(1, 13)}
    if candidate == "regime":
        a = [v for m, vs in resid.items() if m in SALE_TRANSITION_MONTHS for v in vs]
        b = [v for m, vs in resid.items() if m not in SALE_TRANSITION_MONTHS for v in vs]
        va, vb = _var(a), _var(b)
        return {m: (va if m in SALE_TRANSITION_MONTHS else vb) for m in range(1, 13)}
    raw = {m: _var(vs) for m, vs in resid.items()}
    if candidate == "raw":
        return {m: (v if not math.isnan(v) else pooled) for m, v in raw.items()}
    if candidate == "shrunk":
        return shrink_log_variances(raw, {m: len(vs) for m, vs in resid.items()}, pooled)
    raise ValueError(f"unknown variance candidate {candidate!r}")


def shrink_log_variances(
    raw: Mapping[int, float], n: Mapping[int, int], pooled: float
) -> dict[int, float]:
    """Empirical-Bayes shrinkage of log variances toward their mean.

    For normal data, log(s^2) has sampling variance ~ 2/(n-1). The spread of the
    twelve log variances beyond that is the genuine between-month variation
    tau^2. Each month is pulled toward the mean by
    lambda = (2/(n-1)) / (2/(n-1) + tau^2). With n~9 and a Feb/Oct ratio of ~3,
    this lands well short of either extreme, which is the point: the data, not
    us, decide how much of the apparent pattern is real.

    Estimated wholly from the training window, so it is re-chosen at every
    origin with no look-ahead.
    """
    months = [m for m in range(1, 13) if raw.get(m) and raw[m] > 0 and n.get(m, 0) > 2]
    if len(months) < 3:
        return {m: pooled for m in range(1, 13)}
    logs = {m: math.log(raw[m]) for m in months}
    samp = {m: 2.0 / (n[m] - 1) for m in months}
    mean_log = statistics.fmean(logs.values())
    spread = statistics.variance(logs.values())
    tau2 = max(0.0, spread - statistics.fmean(samp.values()))
    out = {}
    for m in range(1, 13):
        if m not in logs:
            out[m] = pooled
            continue
        lam = samp[m] / (samp[m] + tau2) if (samp[m] + tau2) > 0 else 1.0
        out[m] = math.exp(lam * mean_log + (1 - lam) * logs[m])
    # Log-shrinkage biases the average variance down (Jensen); rescale so the
    # mean variance matches the pooled one and only the *shape* is shrunk.
    scale = pooled / statistics.fmean(out.values())
    return {m: v * scale for m, v in out.items()}


# --- empirical (out-of-sample) variances ----------------------------------------


def past_errors(
    level: Mapping[dt.date, float],
    changes: Mapping[dt.date, float],
    origin: dt.date,
    point: SeasonalDriftSpec,
    horizons: int,
    *,
    lookback_years: int = 10,
    cache: dict | None = None,
) -> dict[int, list[tuple[dt.date, float]]]:
    """Out-of-sample errors (pct of level) of the point model, known at ``origin``.

    For every past origin o' within ``lookback_years`` and every h with
    o'+h <= origin, the error the model actually made. Targets in excluded
    years are skipped. ``cache`` memoises paths across origins.
    """
    out: dict[int, list[tuple[dt.date, float]]] = {h: [] for h in range(1, horizons + 1)}
    first = add_months(dt.date(origin.year - lookback_years, origin.month, 1), 0)
    o = first
    while o < origin:
        if o in level:
            key = (o, horizons)
            path = cache.get(key) if cache is not None else None
            if path is None:
                path = seasonal_drift_path(level, o, horizons, point, changes=changes)
                if cache is not None:
                    cache[key] = path
            for h in range(1, horizons + 1):
                tgt = add_months(o, h)
                if tgt > origin or tgt not in level or tgt.year in point.exclude_years:
                    continue
                out[h].append((tgt, 100.0 * (level[tgt] / path[h] - 1.0)))
        o = add_months(o, 1)
    return out


def empirical_variances(
    errs: Mapping[int, Sequence[tuple[dt.date, float]]], horizons: int, by_month: bool
) -> dict[int, dict[int, float]]:
    """{h: {target_month: variance}} from past errors (mean square, bias kept in).

    ``by_month`` conditions on the target's calendar month, log-shrunk toward
    the horizon's pooled value exactly as for residual variances.
    """
    out: dict[int, dict[int, float]] = {}
    for h in range(1, horizons + 1):
        es = errs.get(h, [])
        if len(es) < 12:
            out[h] = {m: math.nan for m in range(1, 13)}
            continue
        pooled = statistics.fmean(e * e for _, e in es)
        if not by_month:
            out[h] = {m: pooled for m in range(1, 13)}
            continue
        per = {m: [e for d, e in es if d.month == m] for m in range(1, 13)}
        raw = {m: statistics.fmean(x * x for x in v) if len(v) > 1 else math.nan for m, v in per.items()}
        out[h] = shrink_log_variances(raw, {m: len(v) for m, v in per.items()}, pooled)
    return out


# --- the fan ------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class FanPoint:
    horizon: int
    target: dt.date
    mean_level: float
    sd_pct: float
    #: Variance shares, which sum to 1.
    share_residual: float
    share_drift: float
    #: This month's own residual variance and the per-month drift variance,
    #: pct^2. The MoM for the target month has variance month_var + drift_var.
    month_var: float = 0.0
    drift_var: float = 0.0


@dataclasses.dataclass(frozen=True)
class FanSpec:
    point: SeasonalDriftSpec
    variance: str = "unconditional"
    #: Calendar years of residual history feeding the variances; None = all
    #: from ``variance_start``.
    variance_years: int | None = 10
    variance_start: dt.date | None = None


def _drift_variance(
    level: Mapping[dt.date, float],
    changes: Mapping[dt.date, float],
    origin: dt.date,
    point: SeasonalDriftSpec,
    month_var: Mapping[int, float],
    *,
    lookback_origins: int = 120,
) -> float:
    """sigma^2_drift per month^2, from past 12-month errors known at ``origin``.

    Past origins o' with o'+12 <= origin give realised 12-month errors e12. Their
    mean square, minus the residual part (sum of the twelve monthly variances),
    divided by 144, is the drift variance. Floored at zero.
    """
    past = [add_months(origin, -12 - k) for k in range(lookback_origins)]
    sq = []
    for o in past:
        tgt = add_months(o, 12)
        if o not in level or tgt not in level or tgt > origin:
            continue
        if o.year in DEFAULT_EXCLUDE_YEARS or tgt.year in DEFAULT_EXCLUDE_YEARS:
            continue
        path = seasonal_drift_path(level, o, 12, point, changes=changes)
        sq.append((100.0 * (path[12] / level[tgt] - 1.0)) ** 2)
    if len(sq) < 12:
        return 0.0
    resid12 = sum(month_var[m] for m in range(1, 13))
    return max(0.0, (statistics.fmean(sq) - resid12) / 144.0)


def fan(
    level: Mapping[dt.date, float],
    origin: dt.date,
    horizons: int,
    spec: FanSpec,
    *,
    changes: Mapping[dt.date, float] | None = None,
    cache: dict | None = None,
) -> list[FanPoint]:
    changes = changes if changes is not None else mom(level)
    if spec.variance in EMPIRICAL_CANDIDATES:
        return _empirical_fan(level, changes, origin, horizons, spec, cache=cache)
    known = {d: v for d, v in changes.items() if d <= origin}
    start = spec.variance_start
    if spec.variance_years is not None:
        start = add_months(dt.date(origin.year - spec.variance_years, origin.month, 1), 1)
    resid = monthly_residuals(known, start=start, end=origin)
    mv = monthly_variances(resid, spec.variance)
    dvar = _drift_variance(level, changes, origin, spec.point, mv)
    path = seasonal_drift_path(level, origin, horizons, spec.point, changes=changes)
    out = []
    acc = 0.0
    for h in range(1, horizons + 1):
        tgt = add_months(origin, h)
        acc += mv[tgt.month]
        drift_part = h * h * dvar
        total = acc + drift_part
        out.append(
            FanPoint(
                horizon=h,
                target=tgt,
                mean_level=path[h],
                sd_pct=math.sqrt(total),
                share_residual=acc / total if total else 1.0,
                share_drift=drift_part / total if total else 0.0,
                month_var=mv[tgt.month],
                drift_var=dvar,
            )
        )
    return out


def _empirical_fan(
    level: Mapping[dt.date, float],
    changes: Mapping[dt.date, float],
    origin: dt.date,
    horizons: int,
    spec: FanSpec,
    *,
    cache: dict | None,
) -> list[FanPoint]:
    """Fan whose h-step variance is the empirical mean-square past h-step error.

    No residual/drift decomposition is imposed; for reporting, the drift share
    is backed out against the pooled residual-based h-step variance (floored at
    0). The one-month MoM variance for the exposure table uses the h=1
    empirical variance for the target's month.
    """
    errs = past_errors(level, changes, origin, spec.point, horizons,
                       lookback_years=spec.variance_years or 10, cache=cache)
    ev = empirical_variances(errs, horizons, by_month=(spec.variance == "empirical_month"))
    path = seasonal_drift_path(level, origin, horizons, spec.point, changes=changes)
    known = {d: v for d, v in changes.items() if d <= origin}
    start = add_months(dt.date(origin.year - (spec.variance_years or 10), origin.month, 1), 1)
    mv = monthly_variances(monthly_residuals(known, start=start, end=origin), "unconditional")
    out, acc = [], 0.0
    for h in range(1, horizons + 1):
        tgt = add_months(origin, h)
        var = ev[h][tgt.month]
        if math.isnan(var):
            var = ev[h][1] if not math.isnan(ev[h][1]) else mv[1] * h
        acc += mv[tgt.month]
        share_d = max(0.0, 1.0 - acc / var) if var > 0 else 0.0
        m1 = ev[1][tgt.month] if not math.isnan(ev[1][tgt.month]) else mv[tgt.month]
        out.append(FanPoint(h, tgt, path[h], math.sqrt(var), 1.0 - share_d, share_d,
                            month_var=m1, drift_var=0.0))
    return out


# --- scoring ------------------------------------------------------------------


def _phi(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def _Phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def crps_normal(mu: float, sigma: float, x: float) -> float:
    """Closed-form CRPS of N(mu, sigma^2) at x. Same units as x; lower is better."""
    if sigma <= 0:
        return abs(x - mu)
    z = (x - mu) / sigma
    return sigma * (z * (2 * _Phi(z) - 1) + 2 * _phi(z) - 1 / math.sqrt(math.pi))


def crps_ensemble(sample: Sequence[float], x: float) -> float:
    """CRPS of an empirical distribution: E|X - x| - 0.5 E|X - X'|.

    Uses the sorted-sample identity for E|X - X'| so it is O(n log n)."""
    xs = sorted(sample)
    n = len(xs)
    if n == 0:
        return math.nan
    t1 = sum(abs(v - x) for v in xs) / n
    t2 = sum((2 * (i + 1) - n - 1) * v for i, v in enumerate(xs)) * 2 / (n * n)
    return t1 - 0.5 * t2


def empirical_quantile(sample: Sequence[float], q: float) -> float:
    xs = sorted(sample)
    if not xs:
        return math.nan
    pos = q * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


@dataclasses.dataclass
class FanScore:
    candidate: str
    horizon: int
    n: int
    crps: float
    coverage: dict[float, float]
    mean_sd: float

    @property
    def coverage_error(self) -> float:
        return statistics.fmean(abs(self.coverage[c] - c) for c in NOMINAL_COVERAGE)


def backtest_fans(
    level: Mapping[dt.date, float],
    origins: Sequence[dt.date],
    horizons: Sequence[int],
    point: SeasonalDriftSpec,
    candidates: Sequence[str] = ALL_CANDIDATES,
    *,
    variance_years: int | None = 10,
    by_month: bool = False,
) -> dict[str, dict]:
    """Score every candidate's fan at every origin. Errors in pp of level.

    Returns {candidate: {horizon: FanScore, 'by_month': {h: {month: FanScore}}}}.
    """
    changes = mom(level)
    hmax = max(horizons)
    cache: dict = {}
    raw: dict[str, dict[int, list[tuple[dt.date, float, float, float]]]] = {
        c: {h: [] for h in horizons} for c in candidates
    }
    samples: dict[int, list[tuple[dt.date, float, list[float]]]] = {h: [] for h in horizons}
    for o in origins:
        if o not in level:
            continue
        if "empirical_quantile" in candidates:
            errs = past_errors(level, changes, o, point, hmax,
                               lookback_years=variance_years or 10, cache=cache)
            path = seasonal_drift_path(level, o, hmax, point, changes=changes)
            for h in horizons:
                tgt = add_months(o, h)
                if tgt in level and len(errs[h]) >= 12:
                    samples[h].append((tgt, 100.0 * (level[tgt] / path[h] - 1.0), [e for _, e in errs[h]]))
        for c in candidates:
            if c in QUANTILE_CANDIDATES:
                continue
            pts = fan(level, o, hmax, FanSpec(point, c, variance_years), changes=changes, cache=cache)
            for h in horizons:
                p = pts[h - 1]
                if p.target not in level:
                    continue
                err = 100.0 * (level[p.target] / p.mean_level - 1.0)  # actual vs mean, pct
                raw[c][h].append((p.target, err, p.sd_pct, 0.0))
    out: dict[str, dict] = {}
    for c in candidates:
        out[c] = {}
        if c in QUANTILE_CANDIDATES:
            for h in horizons:
                out[c][h] = _score_quantile(c, h, samples[h])
                if by_month:
                    out[c].setdefault("by_month", {})[h] = {
                        m: _score_quantile(c, h, [r for r in samples[h] if r[0].month == m])
                        for m in range(1, 13)
                    }
            continue
        for h in horizons:
            out[c][h] = _score(c, h, raw[c][h])
            if by_month:
                out[c].setdefault("by_month", {})[h] = {
                    m: _score(c, h, [r for r in raw[c][h] if r[0].month == m])
                    for m in range(1, 13)
                }
    return out


def _score(c: str, h: int, rows: list[tuple[dt.date, float, float, float]]) -> FanScore:
    if not rows:
        return FanScore(c, h, 0, math.nan, {k: math.nan for k in NOMINAL_COVERAGE}, math.nan)
    crps = statistics.fmean(crps_normal(0.0, sd, e) for _, e, sd, _ in rows)
    cov = {k: statistics.fmean(1.0 if abs(e) <= _Z[k] * sd else 0.0 for _, e, sd, _ in rows)
           for k in NOMINAL_COVERAGE}
    return FanScore(c, h, len(rows), crps, cov, statistics.fmean(sd for *_, sd, _ in rows))


def _score_quantile(c: str, h: int, rows: list[tuple[dt.date, float, list[float]]]) -> FanScore:
    if not rows:
        return FanScore(c, h, 0, math.nan, {k: math.nan for k in NOMINAL_COVERAGE}, math.nan)
    crps = statistics.fmean(crps_ensemble(s, e) for _, e, s in rows)
    cov = {}
    for k in NOMINAL_COVERAGE:
        lo, hi = (1 - k) / 2, 1 - (1 - k) / 2
        cov[k] = statistics.fmean(
            1.0 if empirical_quantile(s, lo) <= e <= empirical_quantile(s, hi) else 0.0
            for _, e, s in rows)
    sd = statistics.fmean(statistics.pstdev(s) for _, _, s in rows)
    return FanScore(c, h, len(rows), crps, cov, sd)


def select_simplest(
    scores: Mapping[str, float],
    order: Sequence[str],
    *,
    tolerance: float = 0.05,
    eligible: Callable[[str], bool] = lambda _c: True,
) -> tuple[str, dict]:
    """The 5%/simplest rule: among eligible candidates within ``tolerance`` of the
    best score (lower is better), take the earliest in the pre-registered order.

    Returns (choice, explanation). Ineligible candidates are reported, not
    silently dropped.
    """
    valid = {c: s for c, s in scores.items() if not math.isnan(s)}
    elig = {c: s for c, s in valid.items() if eligible(c)}
    if not elig:
        raise ValueError("no eligible candidate")
    best = min(elig.values())
    within = [c for c in order if c in elig and elig[c] <= best * (1 + tolerance)]
    choice = within[0]
    return choice, {
        "best_score": best,
        "best_candidate": min(elig, key=elig.get),
        "within_tolerance": within,
        "ineligible": sorted(set(valid) - set(elig)),
        "chosen": choice,
        "tolerance": tolerance,
    }


def select_fan_candidate(
    scores: Mapping[str, Mapping[int, FanScore]],
    horizon: int = 1,
    *,
    coverage_slack: float = 0.025,
) -> tuple[str, dict]:
    """Apply the rule to CRPS at ``horizon``. A candidate is eligible only if its
    mean coverage error is no worse than the incumbent's plus ``coverage_slack``
    (about three origins in 121) -- 'without worse coverage'."""
    inc = scores["unconditional"][horizon].coverage_error
    return select_simplest(
        {c: s[horizon].crps for c, s in scores.items()},
        ALL_CANDIDATES,
        eligible=lambda c: scores[c][horizon].coverage_error <= inc + coverage_slack,
    )


# --- the output table -----------------------------------------------------------


def exposure_table(
    section: Mapping[dt.date, float],
    all_items: Mapping[dt.date, float],
    weights_ppt: Mapping[int, float],
    origin: dt.date,
    spec: FanSpec,
) -> list[dict]:
    """Next-12-month clothing contribution to the RPI all-items MoM, in bp.

    Uses the exact chain-linked contribution with the forecast section path.
    The all-items ratio I_all,t-1/I_all,base beyond the last published month is
    held at its last known value: all-items is not forecast here, and the ratio
    moves by well under 1% within a year, which is second order for a bp
    contribution.
    """
    changes = mom(section)
    pts = fan(section, origin, 12, spec, changes=changes)
    path = {origin: section[origin]}
    path.update({p.target: p.mean_level for p in pts})
    known_section = dict(section)
    rows = []
    last_all = max(d for d in all_items if d <= origin)
    for p in pts:
        d = p.target
        prev = add_months(d, -1)
        base = dt.date(d.year if d.month > 1 else d.year - 1, 1, 1)
        wy = base.year
        w = weights_ppt.get(wy)
        if w is None:
            w = weights_ppt[max(weights_ppt)]  # next year's weight unpublished: carry the latest
            weight_carried = True
        else:
            weight_carried = False
        c_prev = known_section.get(prev, path.get(prev))
        c_base = known_section.get(base, path.get(base))
        a_prev = all_items.get(prev, all_items[last_all])
        a_base = all_items.get(base, all_items[last_all])
        eff_w = w * (c_prev / c_base) / (a_prev / a_base)
        mom_mean = 100.0 * (p.mean_level / c_prev - 1.0)
        # Spread of this month's MoM: its own residual variance plus one month's
        # worth of drift error.
        sd_mom = math.sqrt(p.month_var + p.drift_var)
        k = eff_w / 1000.0 * 100.0  # bp of headline per pp of section MoM
        rows.append(
            {
                "target_month": d.isoformat(),
                "horizon": p.horizon,
                "expected_bp": round(mom_mean * k, 2),
                "sd_bp": round(sd_mom * k, 2),
                "p10_bp": round((mom_mean - _Z_P10 * sd_mom) * k, 2),
                "p50_bp": round(mom_mean * k, 2),
                "p90_bp": round((mom_mean + _Z_P10 * sd_mom) * k, 2),
                "effective_weight_ppt": round(eff_w, 3),
                "nominal_weight_ppt": w,
                "weight_carried_forward": weight_carried,
                "variance_candidate": spec.variance,
            }
        )
    return rows


def default_origins() -> list[dt.date]:
    """The brief's 121 rolling origins, Jun 2015 to Jun 2025 inclusive."""
    return month_range(dt.date(2015, 6, 1), dt.date(2025, 6, 1))
