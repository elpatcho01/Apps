import datetime as dt
import math

import pytest

from rpiclothing import nowcast, panel
from rpiclothing.panel import MockPanel, SegmentMapping


def test_predicted_index_day_is_tuesday_nearest_13th_and_a_2nd_or_3rd_tuesday():
    for y in range(2016, 2028):
        for m in range(1, 13):
            d = nowcast.predicted_index_day(y, m)
            assert d.weekday() == 1 and 10 <= d.day <= 16
            assert abs((d - dt.date(y, m, 13)).days) <= 3
            assert nowcast.is_second_or_third_tuesday(d)
    # July 2026 bulletin-confirmed collection day was 14 July.
    assert nowcast.predicted_index_day(2026, 7) == dt.date(2026, 7, 14)


def test_splice_takes_level_from_ons_and_change_from_panel():
    pub = {dt.date(2025, 9, 1): 300.0, dt.date(2026, 8, 1): 320.0}
    nc = nowcast.splice(pub, dt.date(2026, 9, 1), 2.0)
    assert nc.level == pytest.approx(326.4)
    assert nc.yoy_pct == pytest.approx(100 * (326.4 / 300 - 1))
    with pytest.raises(ValueError):
        nowcast.splice(pub, dt.date(2026, 11, 1), 1.0)


def test_log_wedge_reproduces_briefs_599():
    rpi = {dt.date(2025, 6, 1): 307.9, dt.date(2026, 6, 1): 325.0}
    cpi = {dt.date(2025, 6, 1): 119.4, dt.date(2026, 6, 1): 118.7}
    assert round(nowcast.log_wedge(rpi, cpi, dt.date(2026, 6, 1)), 2) == 5.99


def test_wedge_decomposition_adds_up():
    rpi = {dt.date(2025, 6, 1): 300.0, dt.date(2026, 1, 1): 290.0, dt.date(2026, 6, 1): 320.0}
    cpi = {dt.date(2025, 6, 1): 120.0, dt.date(2026, 1, 1): 115.0, dt.date(2026, 6, 1): 119.0}
    d = nowcast.wedge_decomposition(rpi, cpi, dt.date(2026, 6, 1))
    assert d["this_year_part"] + d["carried_in"] == pytest.approx(d["yoy"])


def _maps():
    return {c: SegmentMapping(c, f"CS_{c}", 0.9) for c in MockPanel().categories}


def test_mock_is_deterministic_and_guarded():
    p = MockPanel()
    a = list(p.observations_between(dt.date(2026, 1, 1), dt.date(2026, 1, 14)))
    b = list(p.observations_between(dt.date(2026, 1, 1), dt.date(2026, 1, 14)))
    assert a == b and a
    with pytest.raises(panel.MockDataReachedProduction):
        panel.assert_not_mock(a, production=True)
    panel.assert_not_mock(a, production=False)


def test_matched_quotes_drop_unmatched_skus_and_low_confidence_maps():
    p = MockPanel()
    base = list(p.observations_between(dt.date(2026, 1, 13), dt.date(2026, 1, 13)))
    cur = list(p.observations_between(dt.date(2026, 9, 15), dt.date(2026, 9, 15)))
    maps = _maps()
    maps["mens_jeans"] = SegmentMapping("mens_jeans", "CS_x", 0.4)
    qs = panel.panel_quotes(base, cur, maps)
    assert qs and all(q.item != "CS_x" for q in qs)
    base_keys = {(o.fascia, o.sku) for o in base}
    assert len(qs) <= sum(1 for o in cur if (o.fascia, o.sku) in base_keys)


def test_sale_onset_detected_from_markdown_share():
    p = MockPanel()
    obs = list(p.observations_between(dt.date(2026, 6, 1), dt.date(2026, 8, 31)))
    shares = panel.sale_shares(obs, _maps())
    onset = panel.sale_onset(shares, "CS_womens_dresses", (dt.date(2026, 6, 1), dt.date(2026, 8, 31)))
    true = p.sale_onset("womens_dresses", 2026, "summer")
    assert onset is not None and 0 <= (onset - true).days <= 7


def test_calibration_refuses_thin_overlap_and_recovers_slope():
    assert panel.fit_calibration([(1.0, 1.0)] * 5) is None
    pairs = [(x, 0.2 + 0.8 * x) for x in [i / 3 for i in range(-10, 10)]]
    c = panel.fit_calibration(pairs)
    assert c.alpha == pytest.approx(0.2) and c.beta == pytest.approx(0.8)


def test_index_day_week_picks_observation_in_the_right_window():
    obs = [dt.date(2026, 9, 1), dt.date(2026, 9, 8), dt.date(2026, 9, 15), dt.date(2026, 9, 22)]
    assert panel.index_day_week(obs, dt.date(2026, 9, 15)) == dt.date(2026, 9, 15)
    assert panel.index_day_week(obs, dt.date(2026, 9, 14)) == dt.date(2026, 9, 8)
    assert panel.index_day_week([dt.date(2026, 9, 30)], dt.date(2026, 9, 15)) is None


def test_panel_section_mom_runs_end_to_end_on_mock():
    p = MockPanel()
    idx = {m: nowcast.predicted_index_day(2026, m) for m in (1, 8, 9)}
    get = lambda d: list(p.observations_between(d, d))
    mom = panel.section_mom_from_panel(get(idx[1]), get(idx[8]), get(idx[9]), _maps(),
                                       {f"CS_{c}": 1.0 for c in p.categories})
    assert mom is not None and math.isfinite(mom)


def test_ws3_backtest_runs_on_mock_and_skips_uncalibrated_origins():
    import random
    from rpiclothing import ws3
    from rpiclothing.series import add_months
    random.seed(0)
    p = MockPanel(skus_per_cell=10)
    chbj, d, lvl = {}, dt.date(2022, 1, 1), 300.0
    while d <= dt.date(2025, 12, 1):
        chbj[d] = lvl
        lvl *= 1 + random.gauss(0.4, 1.5) / 100
        d = add_months(d, 1)
    targets = [add_months(dt.date(2023, 1, 1), k) for k in range(30)]
    res = ws3.backtest(chbj, p, _maps(), {f"CS_{c}": 1.0 for c in p.categories}, targets, min_calibration=12)
    assert res["scores"]["incumbent"]["n"] == 30
    assert res["scores"]["panel_raw"]["n"] > res["scores"]["panel_calibrated"]["n"]
