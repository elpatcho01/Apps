"""Calendar arithmetic tests.

The calendar is the part of this pipeline that is hardest to notice being wrong.
A wrong price is visible; a stay night collected on the wrong day looks fine and
quietly measures something else. So these tests are deliberately picky about
weekdays and leads rather than just about types.
"""

from __future__ import annotations

import datetime as dt

import pytest

from ukhotels import onscal


def test_second_and_third_tuesday():
    second, third = onscal.candidate_index_days(2026, 8)
    assert second == dt.date(2026, 8, 11)
    assert third == dt.date(2026, 8, 18)
    assert second.weekday() == onscal.TUESDAY


@pytest.mark.parametrize("year", [2025, 2026, 2027])
def test_every_candidate_index_day_is_a_tuesday_between_the_8th_and_21st(year):
    for month in range(1, 13):
        for day in onscal.candidate_index_days(year, month):
            assert day.weekday() == onscal.TUESDAY
            assert 8 <= day.day <= 21


def test_thursday_after_index_week_is_nine_days_out_and_a_thursday():
    index_day = dt.date(2026, 8, 11)
    night = onscal.stay_night(index_day, "thursday_after")
    assert night == dt.date(2026, 8, 20)
    assert night.weekday() == onscal.THURSDAY


def test_thursday_after_rejects_a_non_tuesday_anchor():
    # Guards the case where a mis-parsed bulletin date reaches the calendar. The
    # +9 arithmetic silently produces *a* date for any input; only the weekday
    # check catches that it is the wrong one.
    with pytest.raises(ValueError, match="not a Thursday"):
        onscal.stay_night(dt.date(2026, 8, 12), "thursday_after")


def test_both_sampled_nights_are_weeknights():
    # Worth asserting because it is the finding most likely to be "corrected"
    # later by someone assuming a weekend leg must exist. It does not.
    for _, night in onscal.stay_nights(dt.date(2026, 8, 11)):
        assert night.weekday() in (onscal.TUESDAY, onscal.THURSDAY)


def test_per_night_alignment_gives_both_nights_a_42_day_lead():
    index_day = dt.date(2026, 8, 11)
    for kind, night in onscal.stay_nights(index_day):
        day = onscal.collection_date_for(night, index_day=index_day, alignment="per_night")
        assert (night - day).days == 42, kind


def test_single_day_alignment_gives_the_thursday_a_longer_lead():
    index_day = dt.date(2026, 8, 11)
    day = onscal.collection_date_for(
        onscal.stay_night(index_day, "thursday_after"),
        index_day=index_day,
        alignment="single_day",
    )
    assert day == index_day - dt.timedelta(days=42)
    # 51, not 42 -- nine extra days of price drift, which is precisely why the
    # two readings are stored separately rather than collapsed.
    assert (onscal.stay_night(index_day, "thursday_after") - day).days == 51


def test_stays_for_collection_day_prices_only_what_is_due():
    index_day = dt.date(2026, 8, 11)
    due = index_day - dt.timedelta(days=42)
    stays = list(onscal.stays_for_collection_day(due, index_day=index_day))
    kinds = {s.stay_night_kind for s in stays}
    # Under per_night only the index-week night is due 42 days before index day.
    assert kinds == {"index_week"}
    assert stays[0].check_in == index_day
    assert stays[0].check_out == index_day + dt.timedelta(days=1)
    assert stays[0].nights == 1
    assert stays[0].advance_days_actual == 42


def test_stay_is_exactly_one_night():
    index_day = dt.date(2026, 8, 18)
    for alignment in onscal.COLLECTION_ALIGNMENTS:
        for kind, night in onscal.stay_nights(index_day):
            due = onscal.collection_date_for(
                night, index_day=index_day, alignment=alignment
            )
            (stay,) = [
                s
                for s in onscal.stays_for_collection_day(
                    due, index_day=index_day, alignment=alignment
                )
                if s.stay_night_kind == kind
            ]
            assert stay.nights == 1


def test_collection_days_cover_both_index_day_hypotheses():
    days = onscal.collection_days_for_index_month(dt.date(2026, 8, 1))
    second, third = onscal.candidate_index_days(2026, 8)
    # Whichever Tuesday ONS later confirm, and whichever alignment turns out to
    # be right, a collection day for it is in the set.
    for index_day in (second, third):
        for _, night in onscal.stay_nights(index_day):
            for alignment in onscal.COLLECTION_ALIGNMENTS:
                assert (
                    onscal.collection_date_for(
                        night, index_day=index_day, alignment=alignment
                    )
                    in days
                )


def test_collection_days_precede_the_index_month():
    # The whole schedule shift versus air fares in one assertion: for a six-week
    # lead, every collection day for August lands before August starts.
    for month in range(1, 13):
        index_month = dt.date(2026, month, 1)
        for day in onscal.collection_days_for_index_month(index_month):
            assert day < index_month


def test_index_months_in_scope_round_trips():
    for month in range(1, 13):
        index_month = dt.date(2027, month, 1)
        for day in onscal.collection_days_for_index_month(index_month):
            assert index_month in onscal.index_months_in_scope(day)


def test_attribution_rules_disagree_by_more_than_a_boundary_case():
    # For air fares the two attribution rules mostly agree. Here a six-week lead
    # puts them one or two months apart nearly always, so picking the wrong one
    # is not a rounding error.
    index_day = dt.date(2026, 8, 11)
    collection_day = index_day - dt.timedelta(days=42)
    stay_month = onscal.index_month_stay(index_day, index_day)
    collection_month = onscal.index_month_collection(collection_day)
    assert stay_month == dt.date(2026, 8, 1)
    assert collection_month == dt.date(2026, 6, 1)
    assert stay_month != collection_month
