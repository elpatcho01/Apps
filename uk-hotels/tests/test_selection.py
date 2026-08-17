"""Comparability-filter tests.

This is the trap-1 module: the ONS-style selection rule is price-blind, so it is
only safe over a genuinely comparable candidate set. These tests are written
against the specific ways a hotel search result set is *not* comparable.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ukhotels import selection
from ukhotels.providers.base import PropertyQuote


def quote(
    token: str,
    price: float,
    *,
    hotel_class: float | None = 4.0,
    property_type: str = "hotel",
    free_cancellation: bool | None = True,
    before_taxes: float | None = None,
) -> PropertyQuote:
    return PropertyQuote(
        property_token=token,
        property_name=f"Hotel {token}",
        price=Decimal(str(price)),
        price_before_taxes=Decimal(str(before_taxes)) if before_taxes is not None else None,
        currency="GBP",
        hotel_class=hotel_class,
        property_type=property_type,
        free_cancellation=free_cancellation,
    )


def test_vacation_rentals_are_excluded():
    # A whole flat sleeping six is not a comparable to a double room, and ONS's
    # item is explicitly hotels, motels, inns and similar.
    sets = selection.comparable_sets(
        [quote("a", 100), quote("b", 900, property_type="vacation_rental")]
    )
    tokens = {p.property_token for p in sets["upscale"].properties}
    assert tokens == {"a"}
    assert sets["upscale"].n_dropped_property_type == 1


def test_unrated_and_five_star_properties_fall_outside_every_tier():
    sets = selection.comparable_sets(
        [
            quote("mid", 80, hotel_class=3.0),
            quote("up", 140, hotel_class=4.0),
            quote("unrated", 40, hotel_class=None),
            quote("luxury", 900, hotel_class=5.0),
        ]
    )
    assert {p.property_token for p in sets["midscale"].properties} == {"mid"}
    assert {p.property_token for p in sets["upscale"].properties} == {"up"}


def test_a_five_star_outlier_cannot_reach_a_four_star_cell():
    # The concrete shape of the air-fares failure, transposed: a price-blind
    # rule over an unfiltered set picks the absurd product. Here the £900
    # five-star must not be able to set, or join, the four-star cell.
    sets = selection.comparable_sets([quote("up", 140), quote("lux", 900, hotel_class=5.0)])
    prices = [float(p.price) for p in sets["upscale"].properties]
    assert prices == [140.0]


def test_rate_basis_is_enforced_and_counted():
    # The single biggest contamination risk: refundable versus non-refundable
    # for an identical room is routinely a 30-40% gap.
    quotes = [
        quote("flex", 150, free_cancellation=True),
        quote("saver", 100, free_cancellation=False),
    ]
    flex = selection.comparable_sets(quotes, rate_basis="free_cancellation")["upscale"]
    assert {p.property_token for p in flex.properties} == {"flex"}
    assert flex.n_dropped_rate_basis == 1

    saver = selection.comparable_sets(quotes, rate_basis="non_refundable")["upscale"]
    assert {p.property_token for p in saver.properties} == {"saver"}


def test_unknown_cancellation_policy_is_excluded_not_assumed():
    # A thinner honest sample beats a fuller contaminated one. Letting unknowns
    # through would make the series a silent blend of both bases.
    sets = selection.comparable_sets(
        [quote("known", 150, free_cancellation=True), quote("unknown", 90, free_cancellation=None)],
        rate_basis="free_cancellation",
    )
    assert {p.property_token for p in sets["upscale"].properties} == {"known"}


def test_rate_basis_any_keeps_everything_and_is_therefore_contaminated():
    # Exists only so the effect of NOT controlling for cancellation policy can
    # be measured. If this ever becomes the default, that is a bug.
    sets = selection.comparable_sets(
        [quote("a", 150, free_cancellation=True), quote("b", 90, free_cancellation=False)],
        rate_basis="any",
    )
    assert len(sets["upscale"].properties) == 2


def test_outlier_cap_applies_within_a_tier_not_across_the_whole_set():
    # Capping before tiering would let a cheap three-star set the floor for the
    # four-star cell and drag legitimate four-star rates out with it.
    sets = selection.comparable_sets(
        [
            quote("budget", 40, hotel_class=3.0),
            quote("up1", 140),
            quote("up2", 180),
        ]
    )
    assert {p.property_token for p in sets["upscale"].properties} == {"up1", "up2"}
    assert sets["upscale"].n_dropped_outlier == 0


def test_outlier_cap_drops_an_absurd_in_tier_rate():
    sets = selection.comparable_sets(
        [quote("a", 100), quote("b", 120), quote("penthouse", 5000)]
    )
    assert {p.property_token for p in sets["upscale"].properties} == {"a", "b"}
    assert sets["upscale"].n_dropped_outlier == 1


def test_one_absurdly_cheap_listing_does_not_evict_the_legitimate_properties():
    # The reason the cap anchors on the median rather than the minimum. With a
    # minimum anchor a single £1 listing puts the ceiling at £5 and throws out
    # every real four-star rate in the cell, leaving the anomaly as the only
    # survivor. Hotel result sets fail low as well as high, unlike fare sets.
    sets = selection.comparable_sets(
        [quote("junk", 1), quote("a", 130), quote("b", 150), quote("c", 170)]
    )
    tokens = {p.property_token for p in sets["upscale"].properties}
    assert tokens == {"a", "b", "c"}
    assert sets["upscale"].n_dropped_outlier == 1


def test_the_median_property_always_survives_its_own_bounds():
    # Guarantees a cell can never be emptied by the cap alone.
    for prices in ([10, 20, 30], [1, 1000], [5], [100, 100, 100]):
        sets = selection.comparable_sets(
            [quote(f"t{i}", p) for i, p in enumerate(prices)]
        )
        assert sets["upscale"].properties


def test_comparability_basis_records_the_unavailable_controls():
    # The board-basis and room-type gap has to be visible in the data, not only
    # in the README. Every row says so.
    sets = selection.comparable_sets([quote("a", 100)])
    basis = sets["upscale"].basis
    assert "board=unknown" in basis
    assert "room=unknown" in basis
    assert "rate=free_cancellation" in basis


def test_spread_ratio_is_the_diagnostic_that_catches_a_broken_filter():
    tight = selection.comparable_sets([quote("a", 100), quote("b", 130)])["upscale"]
    assert tight.price_spread_ratio() == pytest.approx(1.3)
    assert selection.comparable_sets([quote("a", 100)])["upscale"].price_spread_ratio() is None


def test_headline_price_follows_the_tax_basis_and_never_silently_mixes():
    q = quote("a", 120, before_taxes=100)
    assert selection.headline_price(q, "advertised") == Decimal("120")
    assert selection.headline_price(q, "before_taxes") == Decimal("100")


def test_before_taxes_falls_back_visibly_rather_than_dropping_the_row():
    # The fallback is detectable downstream because price_before_taxes_gbp is
    # NULL on exactly these rows.
    q = quote("a", 120, before_taxes=None)
    assert selection.headline_price(q, "before_taxes") == Decimal("120")
    assert q.price_before_taxes is None


def test_panel_candidates_are_drawn_price_blind():
    # Drawing by price would make every later month a comparison against a base
    # chosen for being cheap. Token order is arbitrary and price-independent.
    quotes = [quote("zzz", 50), quote("aaa", 500), quote("mmm", 200)]
    picked = selection.pick_panel_candidates(
        selection.comparable_sets(quotes)["upscale"], n=2
    )
    assert [p.property_token for p in picked] == ["aaa", "mmm"]


def test_empty_input_yields_empty_cells_not_an_exception():
    sets = selection.comparable_sets([])
    assert all(not s.properties for s in sets.values())
