"""Answer-key loader tests. No network."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from ukhotels import backfill


# --- release ranking --------------------------------------------------------


def test_releases_are_ranked_by_coverage_period_not_reference_number():
    # The mistake that cost real time on the sibling project: ONS restarted
    # their ad hoc numbering, so the old five-digit series sorts numerically
    # above the newer four-digit one and a production run picked a release
    # years out of date.
    old = "/economy/inflationandpriceindices/adhocs/14287hotelsubindicesjanuary2019tojanuary2022"
    new = "/economy/inflationandpriceindices/adhocs/2993hotelsubindicesjanuary2025tojuly2025"
    assert backfill.coverage_end(new) > backfill.coverage_end(old)
    assert max([old, new], key=backfill.coverage_end) == new


def test_coverage_end_reads_year_and_month():
    assert backfill.coverage_end("/a/xjanuary2025tojuly2025") == (2025, 7)
    assert backfill.coverage_end("/a/xjanuary2025todecember2026") == (2026, 12)


def test_a_slug_with_no_readable_period_ranks_last():
    assert backfill.coverage_end("/economy/adhocs/2993somethingelse") == (0, 0)


# --- time series parsing ----------------------------------------------------


def payload(months):
    return {
        "description": {"title": "CPI INDEX 11.2.0.1"},
        "months": months,
        # Present in real payloads and deliberately ignored: including them
        # alongside the monthly figures would double-count.
        "years": [{"date": "2025", "value": "110.0"}],
        "quarters": [{"date": "2025 Q1", "value": "108.0"}],
    }


def test_monthly_observations_are_parsed_and_aggregates_ignored():
    obs = backfill.parse_timeseries(
        payload([
            {"date": "2026 JAN", "value": "112.3"},
            {"date": "2026 FEB", "value": "115.0"},
        ]),
        "l7ie", "CPI INDEX 11.2.0.1", "11.2.0.1", "http://x",
    )
    assert [o.index_month for o in obs] == [dt.date(2026, 1, 1), dt.date(2026, 2, 1)]
    assert obs[0].index_value == Decimal("112.3")
    assert obs[0].location == "uk"
    assert obs[0].basis == "2015_100"


@pytest.mark.parametrize(
    "label,expected",
    [
        ("2026 JAN", dt.date(2026, 1, 1)),
        ("2026 January", dt.date(2026, 1, 1)),
        ("2026-03", dt.date(2026, 3, 1)),
        ("2026 Q1", None),
        ("", None),
        ("nonsense", None),
    ],
)
def test_month_label_parsing(label, expected):
    assert backfill._parse_ons_month(label) == expected


def test_unparseable_values_are_skipped_not_written_as_zero():
    obs = backfill.parse_timeseries(
        payload([
            {"date": "2026 JAN", "value": ""},
            {"date": "2026 FEB", "value": "115.0"},
            {"date": "2026 MAR", "value": "-3"},
        ]),
        "l7ie", "x", "11.2.0.1", "http://x",
    )
    assert [o.index_month for o in obs] == [dt.date(2026, 2, 1)]


def test_a_payload_with_no_monthly_data_raises_rather_than_returning_nothing():
    with pytest.raises(backfill.BackfillError, match="no monthly observations"):
        backfill.parse_timeseries({"years": []}, "l7ie", "x", "11.2.0.1", "http://x")


# --- coverage reporting -----------------------------------------------------


def obs(month, source="adhoc_regional", location="london", value=100):
    return backfill.Observation(
        index_month=month,
        location=location,
        index_value=Decimal(str(value)),
        series_source=source,
        series_id=None,
        series_name="x",
        coicop_class="11.2.0.1",
        basis="january_2025_100",
        release_url="http://x",
        release_label="x",
    )


def test_coverage_is_counted_from_the_rows_not_quoted_from_the_title():
    loaded = [obs(dt.date(2025, m, 1)) for m in (1, 2, 3)]
    report = backfill.coverage_report(loaded)["adhoc_regional"]
    assert report["values"] == 3
    assert report["distinct_months"] == 3
    assert report["first_month"] == "2025-01-01"
    assert report["last_month"] == "2025-03-01"


def test_holes_are_visible_as_a_span_wider_than_the_month_count():
    # Rolling-origin validation needs to know a series has gaps, and the gap
    # count is the difference between these two numbers.
    loaded = [obs(dt.date(2025, m, 1)) for m in (1, 2, 6)]
    report = backfill.coverage_report(loaded)["adhoc_regional"]
    assert report["distinct_months"] == 3
    assert report["calendar_span_months"] == 6


def test_sources_are_reported_separately_because_they_are_not_comparable():
    loaded = [
        obs(dt.date(2025, 1, 1), source="adhoc_regional"),
        obs(dt.date(2015, 1, 1), source="timeseries", location="uk"),
    ]
    report = backfill.coverage_report(loaded)
    assert set(report) == {"adhoc_regional", "timeseries"}
    assert report["timeseries"]["locations"] == ["uk"]


def test_methodology_eras_present_in_the_load_are_reported():
    loaded = [obs(dt.date(2024, 6, 1)), obs(dt.date(2026, 6, 1))]
    report = backfill.coverage_report(loaded)["adhoc_regional"]
    assert report["methodology_eras"] == [
        "2026_six_weeks_two_nights", "pre_2025_one_day_ahead",
    ]


def test_rows_carry_the_methodology_era_of_their_own_month():
    rows = backfill.to_rows(
        [obs(dt.date(2024, 6, 1)), obs(dt.date(2026, 6, 1))],
        run_id="r",
        fetched_ts=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc),
    )
    assert rows[0]["methodology_era"] == "pre_2025_one_day_ahead"
    assert rows[1]["methodology_era"] == "2026_six_weeks_two_nights"


# --- region normalisation ---------------------------------------------------


@pytest.mark.parametrize(
    "label,expected",
    [
        ("London", "london"),
        ("Yorkshire and The Humber", "yorkshire_and_the_humber"),
        ("Yorkshire & the Humber", "yorkshire_and_the_humber"),
        ("NORTH WEST", "north_west"),
        ("United Kingdom", "uk"),
        ("Narnia", None),
    ],
)
def test_region_labels_normalise_to_our_codes(label, expected):
    assert backfill._region_code(label) == expected


def test_implausible_index_values_are_rejected():
    # Most likely a weight, a row number or a year that landed in a value column.
    assert backfill._plausible_index(Decimal("104.2"))
    assert not backfill._plausible_index(Decimal("2025"))
    assert not backfill._plausible_index(Decimal("0.31"))
    assert not backfill._plausible_index(None)
