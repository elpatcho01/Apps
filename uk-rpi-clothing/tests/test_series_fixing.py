import datetime as dt
import math
import random

import pytest

from rpiclothing import fixing
from rpiclothing.series import (
    SeasonalDriftSpec,
    add_months,
    contribution_bp,
    effective_weight_ppt,
    fit_seasonal,
    link_january,
    mom,
    month_range,
    seasonal_drift_path,
)


def _series(start, values):
    d, out = start, {}
    for v in values:
        out[d] = v
        d = add_months(d, 1)
    return out


def test_demeaned_factors_sum_to_zero_raw_carry_drift():
    random.seed(0)
    lvl, s, d = 100.0, {}, dt.date(2010, 1, 1)
    while d <= dt.date(2019, 12, 1):
        s[d] = lvl
        lvl *= 1.01  # 1% a month of pure drift
        d = add_months(d, 1)
    fit = fit_seasonal(mom(s), start=dt.date(2011, 1, 1), end=dt.date(2019, 12, 1),
                       complete_years_only=True)
    assert abs(fit.demeaned_sum) < 1e-9
    assert fit.raw_sum == pytest.approx(12.0, rel=1e-6)


def test_january_change_is_measured_on_last_years_chain():
    assert link_january(dt.date(2026, 1, 1)) == dt.date(2025, 1, 1)
    assert link_january(dt.date(2026, 2, 1)) == dt.date(2026, 1, 1)
    assert link_january(dt.date(2026, 12, 1)) == dt.date(2026, 1, 1)


def test_contribution_equals_naive_when_section_and_all_items_sit_at_base():
    # Section and all-items both at their January level in the prior month:
    # the exact formula collapses to w x MoM.
    sec = {dt.date(2026, 1, 1): 200.0, dt.date(2026, 2, 1): 200.0, dt.date(2026, 3, 1): 204.0}
    alli = {dt.date(2026, 1, 1): 400.0, dt.date(2026, 2, 1): 400.0}
    c = contribution_bp(sec, alli, {2026: 42.0}, dt.date(2026, 3, 1))
    assert c == pytest.approx(42 / 1000 * 2.0 * 100)


def test_contribution_scales_with_section_level_relative_to_january():
    # Section 10% above its January level: the same MoM contributes 10% more.
    sec = {dt.date(2026, 1, 1): 200.0, dt.date(2026, 8, 1): 220.0, dt.date(2026, 9, 1): 224.4}
    alli = {dt.date(2026, 1, 1): 400.0, dt.date(2026, 8, 1): 400.0}
    naive = 42 / 1000 * 2.0 * 100
    assert contribution_bp(sec, alli, {2026: 42.0}, dt.date(2026, 9, 1)) == pytest.approx(naive * 1.1)
    assert effective_weight_ppt(sec, alli, {2026: 42.0}, dt.date(2026, 9, 1)) == pytest.approx(46.2)


def test_contribution_missing_weight_is_none_not_a_guess():
    sec = {dt.date(2026, 1, 1): 1.0, dt.date(2026, 2, 1): 1.0, dt.date(2026, 3, 1): 1.0}
    assert contribution_bp(sec, sec, {2025: 42.0}, dt.date(2026, 3, 1)) is None


def test_seasonal_drift_uses_only_data_up_to_origin():
    random.seed(1)
    s = _series(dt.date(2000, 1, 1), [100 * 1.003 ** i for i in range(300)])
    o = dt.date(2015, 6, 1)
    spec = SeasonalDriftSpec(seasonal_years=5, drift_months=12)
    a = seasonal_drift_path(s, o, 6, spec)
    poisoned = dict(s)
    for d in poisoned:
        if d > o:
            poisoned[d] *= 5
    assert seasonal_drift_path(poisoned, o, 6, spec) == a


def test_shrinkage_lies_between_pooled_and_raw_and_keeps_mean():
    raw = {m: 0.49 for m in range(1, 13)}
    raw[2], raw[10] = 0.94, 0.30
    n = {m: 60 for m in range(1, 13)}
    pooled = sum(raw.values()) / 12
    sh = fixing.shrink_log_variances(raw, n, pooled)
    assert raw[10] < sh[10] < raw[2]
    assert sh[10] < sh[2] < raw[2]
    assert sum(sh.values()) / 12 == pytest.approx(pooled)


def test_feb_oct_gap_alone_is_not_distinguishable_from_noise_at_n9():
    # The brief's Feb 0.97 vs Oct 0.55 (variances 0.94 vs 0.30) with the other
    # ten months flat: at n=9 per month that spread is within sampling noise of
    # log variances (2/(n-1) = 0.25), so empirical Bayes pools completely.
    raw = {m: 0.49 for m in range(1, 13)}
    raw[2], raw[10] = 0.94, 0.30
    sh = fixing.shrink_log_variances(raw, {m: 9 for m in range(1, 13)}, sum(raw.values()) / 12)
    assert max(sh.values()) - min(sh.values()) < 1e-9


def test_shrinkage_goes_fully_to_pooled_when_spread_is_pure_noise():
    raw = {m: 0.49 for m in range(1, 13)}
    raw[2] = 0.52
    sh = fixing.shrink_log_variances(raw, {m: 9 for m in range(1, 13)}, 0.4925)
    assert max(sh.values()) - min(sh.values()) < 1e-9


def test_crps_normal_known_values():
    # CRPS of N(0,1) at 0 is (sqrt(2)-1)/sqrt(pi).
    assert fixing.crps_normal(0, 1, 0) == pytest.approx((math.sqrt(2) - 1) / math.sqrt(math.pi))
    assert fixing.crps_normal(0, 1, 3) > fixing.crps_normal(0, 1, 1)
    assert fixing.crps_normal(0, 0, 2) == 2


def test_select_simplest_prefers_earlier_within_tolerance():
    order = fixing.VARIANCE_CANDIDATES
    choice, why = fixing.select_simplest(
        {"unconditional": 1.03, "regime": 1.02, "shrunk": 1.00, "raw": 0.99}, order)
    assert choice == "unconditional"
    choice, _ = fixing.select_simplest(
        {"unconditional": 1.20, "regime": 1.10, "shrunk": 1.00, "raw": 0.99}, order)
    assert choice == "shrunk"


def test_select_respects_eligibility():
    choice, why = fixing.select_simplest(
        {"unconditional": 1.2, "regime": 1.0}, fixing.VARIANCE_CANDIDATES,
        eligible=lambda c: c != "regime")
    assert choice == "unconditional" and why["ineligible"] == ["regime"]


def test_fan_drift_share_grows_with_horizon():
    random.seed(3)
    lvl, s, d = 100.0, {}, dt.date(1995, 1, 1)
    drift = 0.3
    while d <= dt.date(2020, 1, 1):
        s[d] = lvl
        if d.month == 1:
            drift = random.gauss(0.3, 0.3)  # a new drift each year: unforecastable
        lvl *= 1 + (drift + random.gauss(0, 0.7)) / 100
        d = add_months(d, 1)
    pts = fixing.fan(s, dt.date(2019, 6, 1), 12,
                     fixing.FanSpec(SeasonalDriftSpec(8, None, 12, frozenset()), "unconditional"))
    assert pts[11].share_drift > pts[0].share_drift
    assert pts[11].sd_pct > pts[0].sd_pct


def test_default_origins_are_the_briefs_121():
    o = fixing.default_origins()
    assert len(o) == 121 and o[0] == dt.date(2015, 6, 1) and o[-1] == dt.date(2025, 6, 1)


def test_crps_ensemble_matches_normal_for_large_sample():
    import random as _r
    _r.seed(4)
    s = [_r.gauss(0, 1) for _ in range(20000)]
    assert fixing.crps_ensemble(s, 0.7) == pytest.approx(fixing.crps_normal(0, 1, 0.7), abs=0.01)


def test_empirical_quantile_interpolates():
    assert fixing.empirical_quantile([0, 1, 2, 3, 4], 0.5) == 2
    assert fixing.empirical_quantile([0, 10], 0.25) == 2.5
