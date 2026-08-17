"""SerpApi Google Hotels parsing, against a realistic payload shape.

WHY THIS FILE EXISTS

The mock provider constructs `PropertyQuote` objects directly, so it exercises
everything downstream of the provider and nothing inside it. That is why 165
tests passed while the live adapter returned 238 properties with every single
one unrated: the parsing bug lived in the one layer the mock cannot reach.

So these tests run the real parser over a fixture shaped like the real response,
with the field forms the live engine was observed to use -- `hotel_class` as the
display string "4-star hotel" alongside a numeric `extracted_hotel_class`, and
`type` as "vacation rental" with a space rather than an underscore.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from ukhotels import selection
from ukhotels.providers.serpapi_hotels import (
    SerpApiHotelsProvider,
    parse_free_cancellation,
    parse_hotel_class,
)


class TestHotelClass:
    """The bug that rejected the entire first live panel."""

    def test_display_string_and_numeric_field_together(self):
        # The exact shape that broke it. `hotel_class` is truthy, so the old
        # `a or b` never consulted `extracted_hotel_class`, and "4-star hotel"
        # does not float().
        assert parse_hotel_class(
            {"hotel_class": "4-star hotel", "extracted_hotel_class": 4}
        ) == 4.0

    def test_display_string_alone_is_still_parsed(self):
        assert parse_hotel_class({"hotel_class": "4-star hotel"}) == 4.0
        assert parse_hotel_class({"hotel_class": "3.5-star hotel"}) == 3.5

    def test_numeric_field_alone(self):
        assert parse_hotel_class({"extracted_hotel_class": 5}) == 5.0
        assert parse_hotel_class({"hotel_class": 4}) == 4.0

    @pytest.mark.parametrize(
        "item", [{}, {"hotel_class": None}, {"hotel_class": "boutique hotel"}]
    )
    def test_genuinely_unrated_stays_none(self, item):
        # None must remain possible: it is what keeps an unrated property out of
        # every tier, which is correct behaviour rather than a parse failure.
        assert parse_hotel_class(item) is None

    def test_booleans_are_not_star_ratings(self):
        # bool is a subclass of int in Python, so True would otherwise parse as
        # a 1-star rating.
        assert parse_hotel_class({"extracted_hotel_class": True}) is None


class TestFreeCancellation:
    @pytest.mark.parametrize(
        "item,expected",
        [
            ({"free_cancellation": True}, True),
            ({"free_cancellation": False}, False),
            ({"free_cancellation_until_date": "2026-10-01"}, True),
            ({"prices": [{"free_cancellation": True}]}, True),
            ({"prices": [{"source": "Booking.com"}]}, None),
            ({}, None),
        ],
    )
    def test_looked_for_in_every_plausible_place(self, item, expected):
        assert parse_free_cancellation(item) is expected

    def test_absent_means_unknown_not_false(self):
        # The distinction matters: `selection` excludes unknowns rather than
        # treating them as non-refundable, because guessing would silently blend
        # two rate bases that differ by 30-40%.
        assert parse_free_cancellation({}) is None


REALISTIC_PAYLOAD = {
    "search_metadata": {"status": "Success"},
    "properties": [
        {
            "type": "hotel",
            "name": "The Midland",
            "property_token": "ChkI1234",
            "hotel_class": "4-star hotel",
            "extracted_hotel_class": 4,
            "free_cancellation": True,
            "overall_rating": 4.3,
            "reviews": 5120,
            "rate_per_night": {
                "lowest": "£142",
                "extracted_lowest": 142,
                "before_taxes_fees": "£118",
                "extracted_before_taxes_fees": 118,
            },
            "total_rate": {"lowest": "£142", "extracted_lowest": 142},
        },
        {
            "type": "hotel",
            "name": "Britannia",
            "property_token": "ChkI5678",
            "hotel_class": "3-star hotel",
            "extracted_hotel_class": 3,
            "free_cancellation": False,
            "rate_per_night": {"extracted_lowest": 68},
        },
        {
            # No star rating at all: legitimately outside every tier.
            "type": "hotel",
            "name": "Unrated Guesthouse",
            "property_token": "ChkI9999",
            "rate_per_night": {"extracted_lowest": 55},
        },
        {
            # Space, not underscore -- the live value.
            "type": "vacation rental",
            "name": "Whole flat sleeping six",
            "property_token": "ChkIAAAA",
            "hotel_class": "4-star hotel",
            "extracted_hotel_class": 4,
            "free_cancellation": True,
            "rate_per_night": {"extracted_lowest": 210},
        },
        {
            # No token: cannot be matched across months, so it is dropped.
            "type": "hotel",
            "name": "Tokenless",
            "hotel_class": "4-star hotel",
            "rate_per_night": {"extracted_lowest": 130},
        },
    ],
}


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return _FakeResponse(self._payload)


def search(payload=REALISTIC_PAYLOAD):
    provider = SerpApiHotelsProvider("k", session=_FakeSession(payload))
    return provider.search(
        query="Manchester, UK",
        check_in=dt.date(2026, 10, 13),
        check_out=dt.date(2026, 10, 14),
    )


def test_every_star_rating_is_parsed_from_a_realistic_payload():
    quotes = {q.property_name: q for q in search().quotes}
    assert quotes["The Midland"].hotel_class == 4.0
    assert quotes["Britannia"].hotel_class == 3.0
    assert quotes["Unrated Guesthouse"].hotel_class is None


def test_a_tokenless_property_is_dropped():
    assert "Tokenless" not in {q.property_name for q in search().quotes}


def test_both_tax_bases_are_captured():
    midland = next(q for q in search().quotes if q.property_name == "The Midland")
    assert midland.price == Decimal("142")
    assert midland.price_before_taxes == Decimal("118")


def test_board_basis_and_room_type_stay_none():
    # Not a parsing gap -- the engine does not report them. Asserted so nobody
    # "fixes" it by inventing a value.
    for quote in search().quotes:
        assert quote.board_basis is None
        assert quote.room_type is None


def test_the_filter_keeps_real_hotels_and_rejects_the_rest():
    # The end-to-end assertion the first live run would have failed: a realistic
    # payload must yield a non-empty comparable set.
    sets = selection.comparable_sets(search().quotes, rate_basis="free_cancellation")
    upscale = sets["upscale"]
    assert [q.property_name for q in upscale.properties] == ["The Midland"]
    assert upscale.n_dropped_property_type == 1   # the vacation rental
    assert upscale.reconciles()

    # Britannia is 3-star and non-refundable, so it is in the other tier and
    # then excluded by the rate basis -- not silently pooled into upscale.
    assert sets["midscale"].properties == ()
    assert sets["midscale"].n_dropped_rate_basis == 1


def test_vacation_rentals_are_excluded_at_the_query_too():
    provider_session = _FakeSession(REALISTIC_PAYLOAD)
    SerpApiHotelsProvider("k", session=provider_session).search(
        query="Manchester, UK",
        check_in=dt.date(2026, 10, 13),
        check_out=dt.date(2026, 10, 14),
    )
    assert provider_session.calls[0]["vacation_rentals"] == "false"
    assert provider_session.calls[0]["no_cache"] == "true"
    assert provider_session.calls[0]["adults"] == 2
    assert provider_session.calls[0]["currency"] == "GBP"
