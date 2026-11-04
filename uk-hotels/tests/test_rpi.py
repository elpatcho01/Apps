"""RPI comparison module. No network -- payloads are fixtures."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from ukhotels import rpi


def payload(months, title, unit="Index"):
    return {
        "description": {"title": title, "unit": unit},
        "months": [{"date": d, "value": v} for d, v in months],
        "years": [{"date": "2025", "value": "999"}],
    }


def test_level_series_is_differenced_and_rate_series_is_not():
    level = rpi.parse_series(
        payload([("2026 JAN", "100.0"), ("2026 FEB", "110.0")], "RPI: Leisure services"),
        "chbm", "RPI: Leisure services", "rpi_context",
    )
    rate = rpi.parse_series(
        payload([("2026 JAN", "0.4"), ("2026 FEB", "-1.2")],
                "RPI: Percentage change over 1 month - UK holidays", "Percent"),
        "sgvm", "RPI: percentage change over 1 month - UK holidays", "rpi_answer",
    )
    assert not level.is_percentage_change
    assert rate.is_percentage_change


def test_month_on_month_is_a_percentage_of_the_previous_month():
    points = (
        rpi.Point(dt.date(2026, 1, 1), Decimal("200.0")),
        rpi.Point(dt.date(2026, 2, 1), Decimal("210.0")),
    )
    assert rpi.month_on_month(points) == [(dt.date(2026, 2, 1), Decimal("5.00"))]


def test_a_gap_does_not_become_a_two_month_change_labelled_as_one():
    """The quiet failure this guards against is a plausible number.

    March over January, reported as March's monthly change, looks entirely
    ordinary and is wrong by a whole month of inflation.
    """
    points = (
        rpi.Point(dt.date(2026, 1, 1), Decimal("100.0")),
        rpi.Point(dt.date(2026, 3, 1), Decimal("120.0")),
    )
    assert rpi.month_on_month(points) == []


def test_negative_and_zero_rates_survive_parsing():
    """Index levels are positive; rates are not.

    The backfill parser drops any value <= 0 because a zero index is a broken
    index. Reusing that rule here would silently delete every month in which
    holiday prices fell -- which, for a seasonal series, is about half of them.
    """
    result = rpi.parse_series(
        payload([("2026 JAN", "-3.4"), ("2026 FEB", "0.0"), ("2026 MAR", "2.1")],
                "RPI: Percentage change over 1 month - UK holidays", "Percent"),
        "sgvm", "RPI: percentage change over 1 month - UK holidays", "rpi_answer",
    )
    assert [str(p.value) for p in result.points] == ["-3.4", "0.0", "2.1"]


def test_a_wrong_identifier_is_reported_rather_than_silently_used():
    """A four-letter ONS code lands on somebody else's series, not on an error."""
    wrong = rpi.parse_series(
        payload([("2026 JAN", "100.0")], "RPI: Motoring expenditure: petrol & oil"),
        "sgvm", "RPI: percentage change over 1 month - UK holidays", "rpi_answer",
    )
    assert not rpi._title_matches(wrong)

    right = rpi.parse_series(
        payload([("2026 JAN", "0.4")],
                "RPI:Percentage change over 1 month - UK holidays", "Percent"),
        "sgvm", "RPI: percentage change over 1 month - UK holidays", "rpi_answer",
    )
    assert rpi._title_matches(right)


def test_empty_payload_raises_rather_than_reporting_an_empty_series():
    with pytest.raises(rpi.RpiFetchError):
        rpi.parse_series({"description": {"title": "x"}, "months": []}, "sgvm", "x", "rpi_answer")


def test_every_declared_series_has_a_role_the_report_understands():
    roles = {role for _, _, role in rpi.SERIES}
    assert roles <= {"rpi_answer", "rpi_context", "rpi_candidate", "cpi_target"}
    assert any(role == "rpi_answer" for _, _, role in rpi.SERIES)
    ids = [sid for sid, _, _ in rpi.SERIES]
    assert len(ids) == len(set(ids)), "duplicate series id"


def test_the_stated_placement_names_the_section_hotels_actually_sit_in():
    """The categorisation is part of the answer, so it is asserted, not implied."""
    placement = rpi.run.__doc__ or ""
    assert "UK holidays" in rpi.__doc__
    assert "Leisure services" in rpi.__doc__
    assert "640406" in rpi.__doc__
    del placement


def test_a_candidate_row_is_not_tested_as_though_it_were_a_cpi_series():
    """The first version keyed the family off startswith(), not membership.

    Both `rpi_candidate` expectations begin "candidate:", so they were checked
    for the string "cpi" and reported as mismatches although the identifier was
    right -- chms really is the RPI UK holidays index. A false alarm here is
    expensive: it teaches you to skim the mismatch list, which in the same run
    also contained d7ex returning canteens.
    """
    candidate = rpi.parse_series(
        payload([("2026 JAN", "400.1")], "RPI: Leisure services: UK Holidays (Jan 1994=100)"),
        "chms", "candidate: RPI UK holidays index (Jan 1994=100)", "rpi_candidate",
    )
    assert rpi._title_matches(candidate)


def test_the_corrected_accommodation_aggregate_id_is_used_here_too():
    ids = {series_id for series_id, _, _ in rpi.SERIES}
    assert "d7cx" in ids and "d7ex" not in ids
