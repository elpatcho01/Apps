import datetime as dt

import pytest

from ukairfares import onscal
from ukairfares.onscal import (
    Leg,
    add_months,
    candidate_index_days,
    in_collection_window,
    index_day_ordinal,
    index_month_collection,
    index_month_departure,
    legs_for,
    nth_tuesday,
    return_date,
    target_departure_date,
)


class TestNthTuesday:
    def test_known_dates(self):
        # August 2026 starts on a Saturday; Tuesdays are 4, 11, 18, 25.
        assert nth_tuesday(2026, 8, 1) == dt.date(2026, 8, 4)
        assert nth_tuesday(2026, 8, 2) == dt.date(2026, 8, 11)
        assert nth_tuesday(2026, 8, 3) == dt.date(2026, 8, 18)
        assert nth_tuesday(2026, 8, 4) == dt.date(2026, 8, 25)

    def test_month_starting_on_tuesday(self):
        # September 2025 starts Monday, so 1st Tuesday is the 2nd.
        assert nth_tuesday(2025, 9, 1) == dt.date(2025, 9, 2)
        # December 2025 starts Monday too.
        assert nth_tuesday(2025, 12, 2) == dt.date(2025, 12, 9)

    def test_all_results_are_tuesdays(self):
        for year in (2024, 2025, 2026, 2027):
            for month in range(1, 13):
                for n in (1, 2, 3, 4):
                    assert nth_tuesday(year, month, n).weekday() == onscal.TUESDAY

    def test_fifth_tuesday_may_not_exist(self):
        # February 2027 (28 days, starts Monday) has exactly 4 Tuesdays.
        with pytest.raises(ValueError):
            nth_tuesday(2027, 2, 5)

    def test_rejects_zero(self):
        with pytest.raises(ValueError):
            nth_tuesday(2026, 8, 0)


class TestIndexDays:
    def test_candidates_are_second_and_third_tuesday(self):
        second, third = candidate_index_days(2026, 8)
        assert second == dt.date(2026, 8, 11)
        assert third == dt.date(2026, 8, 18)
        assert (third - second).days == 7

    def test_ordinal_roundtrip(self):
        for year in (2025, 2026):
            for month in range(1, 13):
                for n in (1, 2, 3, 4):
                    assert index_day_ordinal(nth_tuesday(year, month, n)) == n

    def test_ordinal_none_for_non_tuesday(self):
        assert index_day_ordinal(dt.date(2026, 8, 12)) is None  # a Wednesday


class TestAddMonths:
    def test_simple(self):
        assert add_months(dt.date(2026, 8, 11), 1) == dt.date(2026, 9, 11)
        assert add_months(dt.date(2026, 8, 11), 6) == dt.date(2027, 2, 11)

    def test_year_rollover(self):
        assert add_months(dt.date(2026, 11, 10), 3) == dt.date(2027, 2, 10)

    def test_clamps_short_month(self):
        assert add_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)


class TestTargetDeparture:
    def test_departure_is_same_ordinal_tuesday(self):
        collection = dt.date(2026, 8, 11)  # 2nd Tuesday
        dep = target_departure_date(collection, 1)
        assert dep == nth_tuesday(2026, 9, 2)
        assert dep.weekday() == onscal.TUESDAY

    def test_all_windows_land_on_tuesdays(self):
        collection = dt.date(2026, 8, 18)  # 3rd Tuesday
        for months in (1, 3, 6):
            dep = target_departure_date(collection, months)
            assert dep.weekday() == onscal.TUESDAY
            assert index_day_ordinal(dep) == 3

    def test_requires_ordinal_when_collection_not_tuesday(self):
        with pytest.raises(ValueError, match="not a Tuesday"):
            target_departure_date(dt.date(2026, 8, 12), 1)

    def test_explicit_ordinal_on_non_tuesday_collection(self):
        # A Thursday scrape standing in for the 2nd-Tuesday hypothesis.
        dep = target_departure_date(dt.date(2026, 8, 13), 1, ordinal=2)
        assert dep == nth_tuesday(2026, 9, 2)

    def test_actual_gap_differs_from_nominal(self):
        # This is the whole point of tracking days_out_actual separately:
        # a "1 month" window is not 30 days.
        collection = dt.date(2026, 8, 11)
        gap = (target_departure_date(collection, 1) - collection).days
        assert gap == 28
        assert gap != 30


class TestReturnLeg:
    def test_durations_match_ons(self):
        dep = dt.date(2026, 9, 8)
        assert (return_date(dep, "domestic") - dep).days == 7
        assert (return_date(dep, "european") - dep).days == 14
        assert (return_date(dep, "long_haul") - dep).days == 21


class TestLegsFor:
    def test_leg_counts_by_haul(self):
        collection = dt.date(2026, 8, 11)
        assert len(list(legs_for(collection, "domestic", 2))) == 1
        assert len(list(legs_for(collection, "european", 2))) == 2
        assert len(list(legs_for(collection, "long_haul", 2))) == 3

    def test_nominal_buckets(self):
        collection = dt.date(2026, 8, 11)
        legs = list(legs_for(collection, "long_haul", 2))
        assert [leg.days_out for leg in legs] == [30, 90, 180]

    def test_leg_internal_consistency(self):
        collection = dt.date(2026, 8, 11)
        for haul in ("domestic", "european", "long_haul"):
            for leg in legs_for(collection, haul, 2):
                assert isinstance(leg, Leg)
                assert leg.departure_date > collection
                assert leg.return_date > leg.departure_date
                assert leg.days_out_actual == (leg.departure_date - collection).days
                assert leg.departure_date.weekday() == onscal.TUESDAY

    def test_domestic_only_one_month_out(self):
        collection = dt.date(2026, 8, 11)
        (leg,) = list(legs_for(collection, "domestic", 2))
        assert leg.months_ahead == 1
        assert leg.days_out == 30


class TestAttribution:
    def test_departure_and_collection_months_differ(self):
        collection = dt.date(2026, 8, 11)
        (leg,) = list(legs_for(collection, "domestic", 2))
        assert index_month_collection(collection) == dt.date(2026, 8, 1)
        assert index_month_departure(leg.departure_date) == dt.date(2026, 9, 1)


class TestCollectionWindow:
    def test_window_covers_both_candidate_index_days(self):
        for year in (2025, 2026, 2027):
            for month in range(1, 13):
                for day in candidate_index_days(year, month):
                    assert in_collection_window(day), f"{day} outside window"

    def test_window_bounds(self):
        assert not in_collection_window(dt.date(2026, 8, 7))
        assert in_collection_window(dt.date(2026, 8, 8))
        assert in_collection_window(dt.date(2026, 8, 21))
        assert not in_collection_window(dt.date(2026, 8, 22))
