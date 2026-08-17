"""Index-construction tests.

The two things worth being paranoid about here are property churn (which is the
normal monthly condition, not an edge case) and the methodology breaks (which
make some month pairs incomparable no matter how good the data is).
"""

from __future__ import annotations

import datetime as dt

import pytest

from ukhotels import index


def test_dropped_property_does_not_read_as_a_price_fall():
    # The core reason relatives are matched. An expensive property leaving the
    # sample drags an unmatched average down, and nothing about the market moved.
    base = {"cheap": 100.0, "mid": 150.0, "expensive": 400.0}
    current = {"cheap": 100.0, "mid": 150.0}  # expensive closed for refurbishment

    naive = (sum(current.values()) / len(current)) / (sum(base.values()) / len(base))
    assert naive < 0.75  # a fabricated 25%+ "price collapse"

    rel = index.price_relative(base, current, "jevons", min_matched=2)
    assert rel.value == pytest.approx(1.0)
    assert rel.n_matched == 2
    assert rel.n_unmatched == 1


def test_a_new_property_arriving_does_not_read_as_a_price_rise():
    base = {"a": 100.0, "b": 110.0, "c": 120.0}
    current = {"a": 100.0, "b": 110.0, "c": 120.0, "new_luxury": 900.0}
    rel = index.price_relative(base, current, "jevons")
    assert rel.value == pytest.approx(1.0)
    assert rel.n_unmatched == 1


def test_relative_is_refused_below_the_minimum_matched_sample():
    with pytest.raises(index.IndexError_, match="matched"):
        index.price_relative({"a": 100.0}, {"a": 120.0}, min_matched=3)


def test_zero_and_negative_rates_are_treated_as_absent():
    # A zero rate is a data error, and letting one into a geometric mean would
    # take the log of zero.
    pairs, unmatched = index.matched_pairs({"a": 0.0, "b": 100.0}, {"a": 90.0, "b": 110.0})
    assert pairs == [(100.0, 110.0)]
    assert unmatched == 1


@pytest.mark.parametrize(
    "formula,expected",
    [("jevons", 1.2), ("dutot", 1.2), ("carli", 1.2)],
)
def test_all_three_formulas_agree_on_a_uniform_move(formula, expected):
    base = {"a": 100.0, "b": 200.0, "c": 300.0}
    current = {k: v * 1.2 for k, v in base.items()}
    assert index.price_relative(base, current, formula).value == pytest.approx(expected)


def test_carli_exceeds_jevons_on_a_dispersed_move():
    # Carli's known upward bias, asserted so nobody "simplifies" the three
    # formulas down to whichever is easiest.
    base = {"a": 100.0, "b": 100.0}
    current = {"a": 50.0, "b": 200.0}
    jevons = index.price_relative(base, current, "jevons", min_matched=2).value
    carli = index.price_relative(base, current, "carli", min_matched=2).value
    assert carli > jevons
    assert jevons == pytest.approx(1.0)


def test_methodology_eras_are_assigned_at_the_right_boundaries():
    assert index.methodology_era(dt.date(2024, 6, 1)) == "pre_2025_one_day_ahead"
    assert index.methodology_era(dt.date(2025, 1, 1)) == "2025_split_weight"
    assert index.methodology_era(dt.date(2026, 1, 1)) == "2025_split_weight"
    assert index.methodology_era(dt.date(2026, 2, 1)) == "2026_six_weeks_two_nights"


def test_a_pair_spanning_a_methodology_break_is_not_usable():
    # January to February 2026 is when the one-day-ahead item was removed and
    # the second night added. A "change" across it is a change of measurement.
    assert index.spans_methodology_break(dt.date(2026, 1, 1), dt.date(2026, 2, 1))
    pairs = index.consecutive_pairs([dt.date(2026, 1, 1), dt.date(2026, 2, 1)])
    assert pairs == []


def test_consecutive_pairs_skips_gaps():
    months = [dt.date(2026, 2, 1), dt.date(2026, 3, 1), dt.date(2026, 5, 1)]
    assert index.consecutive_pairs(months) == [(dt.date(2026, 2, 1), dt.date(2026, 3, 1))]


def test_chained_index_breaks_rather_than_carrying_a_level_forward():
    # A fabricated level that looks like real data is worse than a visible gap.
    months = [
        (dt.date(2026, 2, 1), {"a": 100.0, "b": 100.0, "c": 100.0}),
        (dt.date(2026, 3, 1), {"a": 110.0, "b": 110.0, "c": 110.0}),
        (dt.date(2026, 5, 1), {"a": 120.0, "b": 120.0, "c": 120.0}),  # April missing
    ]
    points = index.build_chained_index(months)
    assert [p.month for p in points] == [dt.date(2026, 2, 1), dt.date(2026, 3, 1)]


def test_splice_projects_the_published_level_by_our_change():
    base = {"a": 100.0, "b": 100.0, "c": 100.0}
    current = {"a": 105.0, "b": 105.0, "c": 105.0}
    rel = index.price_relative(base, current)
    assert index.splice_nowcast(112.0, rel) == pytest.approx(117.6)


def test_month_on_month_never_spans_a_break_or_a_gap():
    series = [
        (dt.date(2026, 1, 1), 100.0),
        (dt.date(2026, 2, 1), 110.0),  # crosses the February 2026 break
        (dt.date(2026, 3, 1), 121.0),
    ]
    changes = index.to_month_on_month(series)
    assert [m for m, _ in changes] == [dt.date(2026, 3, 1)]


def test_detect_basis_reads_the_answer_off_the_data():
    resets = [(dt.date(y, 1, 1), 100.0) for y in (2025, 2026)]
    assert index.detect_basis(resets) == "annual_january_100"
    running = [(dt.date(2025, 1, 1), 100.0), (dt.date(2026, 1, 1), 108.0)]
    assert index.detect_basis(running) == "single_base"
    assert index.detect_basis([(dt.date(2025, 1, 1), 100.0)]) == "unknown"
