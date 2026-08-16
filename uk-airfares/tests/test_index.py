"""Index construction: matched samples, elementary aggregates, splicing."""

import datetime as dt
import math

import pytest

from ukairfares.index import (
    BASE_VALUE,
    IndexError_,
    build_chained_index,
    detect_basis,
    matched_pairs,
    price_relative,
    rebase_to_january,
    splice_nowcast,
    to_month_on_month,
)


def month(y, m):
    return dt.date(y, m, 1)


class TestMatchedPairs:
    def test_only_routes_in_both_periods(self):
        base = {"A": 100.0, "B": 200.0, "C": 300.0}
        current = {"A": 110.0, "B": 220.0, "D": 400.0}
        pairs, unmatched = matched_pairs(base, current)
        assert pairs == [(100.0, 110.0), (200.0, 220.0)]
        assert unmatched == 2  # C and D

    def test_non_positive_prices_treated_as_absent(self):
        base = {"A": 100.0, "B": 0.0, "C": -5.0}
        current = {"A": 110.0, "B": 220.0, "C": 300.0}
        pairs, _ = matched_pairs(base, current)
        assert pairs == [(100.0, 110.0)]

    def test_no_overlap(self):
        pairs, unmatched = matched_pairs({"A": 1.0}, {"B": 2.0})
        assert pairs == [] and unmatched == 2


class TestPriceRelative:
    def _uniform(self, factor):
        base = {c: 100.0 for c in "ABCD"}
        current = {c: 100.0 * factor for c in "ABCD"}
        return base, current

    @pytest.mark.parametrize("formula", ["jevons", "dutot", "carli"])
    def test_uniform_change_agrees_across_formulas(self, formula):
        base, current = self._uniform(1.10)
        rel = price_relative(base, current, formula)
        assert rel.value == pytest.approx(1.10)
        assert rel.pct_change == pytest.approx(10.0)

    def test_no_change_is_unity(self):
        base, current = self._uniform(1.0)
        assert price_relative(base, current).value == pytest.approx(1.0)

    def test_jevons_is_geometric(self):
        base = {"A": 100.0, "B": 100.0}
        current = {"A": 200.0, "B": 50.0}  # x2 and x0.5
        # Geometric mean of relatives = sqrt(2 * 0.5) = 1.0
        assert price_relative(base, current, "jevons", min_matched=2).value == pytest.approx(1.0)

    def test_carli_exceeds_jevons_on_dispersed_relatives(self):
        base = {"A": 100.0, "B": 100.0, "C": 100.0}
        current = {"A": 200.0, "B": 50.0, "C": 100.0}
        jevons = price_relative(base, current, "jevons").value
        carli = price_relative(base, current, "carli").value
        # Carli's known upward bias, the reason it is not used for CPI.
        assert carli > jevons

    def test_dutot_weights_by_price_level(self):
        # The expensive route doubles, the cheap one halves.
        base = {"cheap": 100.0, "dear": 1000.0}
        current = {"cheap": 50.0, "dear": 2000.0}
        dutot = price_relative(base, current, "dutot", min_matched=2).value
        jevons = price_relative(base, current, "jevons", min_matched=2).value
        assert dutot == pytest.approx(2050.0 / 1100.0)
        assert dutot > jevons  # dominated by the expensive route

    def test_counts_reported(self):
        base = {"A": 100.0, "B": 100.0, "C": 100.0, "X": 100.0}
        current = {"A": 110.0, "B": 110.0, "C": 110.0, "Y": 110.0}
        rel = price_relative(base, current)
        assert rel.n_matched == 3 and rel.n_unmatched == 2
        assert rel.match_rate == pytest.approx(3 / 5)

    def test_refuses_thin_samples(self):
        with pytest.raises(IndexError_, match="matched route"):
            price_relative({"A": 100.0}, {"A": 110.0})

    def test_min_matched_is_configurable(self):
        rel = price_relative({"A": 100.0}, {"A": 110.0}, min_matched=1)
        assert rel.value == pytest.approx(1.10)

    def test_unknown_formula_rejected(self):
        base, current = self._uniform(1.1)
        with pytest.raises(IndexError_, match="unknown formula"):
            price_relative(base, current, "laspeyres")


class TestMatchingPreventsPhantomMovement:
    """The reason matching exists, stated as a test."""

    def test_dropped_expensive_route_does_not_read_as_a_price_fall(self):
        # Long-haul route present in March, missing in April. Nothing about the
        # fares of the remaining routes changed.
        march = {"LHR-EDI": 100.0, "LHR-AMS": 200.0, "LHR-CPT": 900.0}
        april = {"LHR-EDI": 100.0, "LHR-AMS": 200.0}

        matched = price_relative(march, april, min_matched=2)
        assert matched.value == pytest.approx(1.0), "matched sample sees no change"

        # What an unmatched mean-of-levels comparison would have reported:
        naive = (sum(april.values()) / len(april)) / (sum(march.values()) / len(march))
        assert naive < 0.75, "unmatched comparison invents a >25% price collapse"


class TestSplice:
    def test_carries_published_level_forward(self):
        # ONS published 112.0 last month; we estimate +5%.
        assert splice_nowcast(112.0, 1.05) == pytest.approx(117.6)

    def test_accepts_a_price_relative(self):
        rel = price_relative({c: 100.0 for c in "ABCD"}, {c: 110.0 for c in "ABCD"})
        assert splice_nowcast(200.0, rel) == pytest.approx(220.0)

    def test_level_scale_is_irrelevant_to_the_estimate(self):
        # Our own fares are in pounds and ONS's index is ~100. Only the ratio
        # crosses over, so our level never needs to match theirs.
        our_prev, our_now = 350.0, 371.0
        published_prev = 118.4
        nowcast = splice_nowcast(published_prev, our_now / our_prev)
        assert nowcast == pytest.approx(118.4 * (371.0 / 350.0))

    def test_rejects_non_positive_published_level(self):
        with pytest.raises(IndexError_):
            splice_nowcast(0.0, 1.05)


class TestChainedIndex:
    def _series(self, factors):
        prices = [{c: 100.0 for c in "ABCD"}]
        for f in factors:
            prices.append({c: v * f for c, v in prices[-1].items()})
        return [(month(2026, i + 1), p) for i, p in enumerate(prices)]

    def test_first_month_is_the_base(self):
        points = build_chained_index(self._series([1.1, 1.1]))
        assert points[0].level == BASE_VALUE
        assert points[0].relative is None

    def test_compounds_relatives(self):
        points = build_chained_index(self._series([1.1, 1.1]))
        assert [p.level for p in points] == pytest.approx([100.0, 110.0, 121.0])

    def test_handles_unordered_input(self):
        series = self._series([1.1])
        points = build_chained_index(list(reversed(series)))
        assert [p.month for p in points] == [month(2026, 1), month(2026, 2)]

    def test_breaks_rather_than_fabricates_on_an_unchainable_gap(self):
        series = [
            (month(2026, 1), {c: 100.0 for c in "ABCD"}),
            (month(2026, 2), {c: 110.0 for c in "ABCD"}),
            (month(2026, 3), {"Z": 500.0}),  # no overlap with February
            (month(2026, 4), {c: 130.0 for c in "ABCD"}),
        ]
        points = build_chained_index(series)
        assert len(points) == 2, "must stop, not carry a level forward as if real"

    def test_empty(self):
        assert build_chained_index([]) == []


class TestRebaseToJanuary:
    def test_january_becomes_100(self):
        points = build_chained_index(
            [(month(2026, m), {c: 100.0 * (1 + 0.1 * m) for c in "ABCD"}) for m in range(1, 5)]
        )
        rebased = rebase_to_january(points)
        january = next(p for p in rebased if p.month.month == 1)
        assert january.level == pytest.approx(100.0)

    def test_drops_years_without_a_january(self):
        points = build_chained_index(
            [(month(2026, m), {c: 100.0 for c in "ABCD"}) for m in (6, 7, 8)]
        )
        assert rebase_to_january(points) == []

    def test_preserves_within_year_ratios(self):
        points = build_chained_index(
            [(month(2026, m), {c: 100.0 * (1.1 ** (m - 1)) for c in "ABCD"}) for m in range(1, 4)]
        )
        rebased = rebase_to_january(points)
        assert [p.level for p in rebased] == pytest.approx([100.0, 110.0, 121.0])


class TestDetectBasis:
    def test_recognises_annual_reset(self):
        series = [(month(2024, 1), 100.0), (month(2024, 6), 118.0),
                  (month(2025, 1), 100.0), (month(2025, 6), 121.0)]
        assert detect_basis(series) == "annual_january_100"

    def test_recognises_chained(self):
        series = [(month(2024, 1), 100.0), (month(2024, 6), 118.0),
                  (month(2025, 1), 106.0), (month(2025, 6), 128.0)]
        assert detect_basis(series) == "chained"

    def test_unknown_without_two_januaries(self):
        assert detect_basis([(month(2024, 1), 100.0), (month(2024, 6), 118.0)]) == "unknown"

    def test_tolerance_absorbs_rounding(self):
        series = [(month(2024, 1), 100.0), (month(2025, 1), 100.2)]
        assert detect_basis(series) == "annual_january_100"


class TestMonthOnMonth:
    def test_computes_percentage_change(self):
        series = [(month(2026, 1), 100.0), (month(2026, 2), 110.0), (month(2026, 3), 104.5)]
        changes = to_month_on_month(series)
        assert [c for _, c in changes] == pytest.approx([10.0, -5.0])

    def test_skips_non_consecutive_months(self):
        # A gap must not be reported as a single month's movement.
        series = [(month(2026, 1), 100.0), (month(2026, 4), 130.0), (month(2026, 5), 143.0)]
        changes = to_month_on_month(series)
        assert [m for m, _ in changes] == [month(2026, 5)]

    def test_empty_and_single(self):
        assert to_month_on_month([]) == []
        assert to_month_on_month([(month(2026, 1), 100.0)]) == []
