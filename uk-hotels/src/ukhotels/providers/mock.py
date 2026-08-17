"""Deterministic accommodation provider for tests, dry runs and CI.

Generates a plausible-shaped mix of properties without touching the network, so
the whole pipeline -- calendar, panel, comparability filter, row construction --
runs end to end with no API key and no quota.

The mix is deliberately *contaminated*: every location returns vacation rentals,
unrated properties, five-star outliers and a spread of cancellation policies
alongside the comparable hotels. A mock that returned only clean data would let
the comparability filter regress without any test noticing, and that filter is
the single most consequential piece of logic here.

The numbers are synthetic. They are seeded off location and dates so a given
query always returns the same thing, but they carry no information about real
rates and must never reach a production table -- `pull` enforces that.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from typing import Any

from .base import AccommodationProvider, ProviderError, PropertyQuote, SearchResult

#: Rough nightly anchors by tier, purely so synthetic output is not absurd.
_BASE_RATE = {3.0: 85, 3.5: 100, 4.0: 130, 4.5: 165, 5.0: 260, None: 70}

#: What each location returns, as (star class, property type) pairs. The
#: unrated, the vacation rentals and the five-star are there to be filtered out;
#: if a change lets them through, `test_selection` fails.
_SHAPE: tuple[tuple[float | None, str], ...] = (
    (3.0, "hotel"),
    (3.0, "hotel"),
    (3.5, "hotel"),
    (4.0, "hotel"),
    (4.0, "hotel"),
    (4.5, "hotel"),
    (5.0, "hotel"),           # outside both tiers
    (None, "hotel"),          # unrated
    (4.0, "vacation rental"), # right class, wrong product
    (2.0, "hotel"),           # below the tiers
)


def _seed(*parts: Any) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big")


class MockProvider(AccommodationProvider):
    name = "mock"
    is_cached_source = False

    def __init__(
        self,
        *,
        fail_locations: frozenset[str] = frozenset(),
        empty_locations: frozenset[str] = frozenset(),
        drop_tokens: frozenset[str] = frozenset(),
    ) -> None:
        """
        `fail_locations` and `empty_locations` exercise the error and no-data
        paths without monkeypatching. `drop_tokens` simulates a property leaving
        the aggregator -- the churn case that matched-sample logic exists for,
        and which is otherwise impossible to test deterministically.
        """
        self._fail = fail_locations
        self._empty = empty_locations
        self._drop = drop_tokens

    def search(
        self,
        *,
        query: str,
        check_in: dt.date,
        check_out: dt.date,
        adults: int = 2,
        children: int = 0,
        currency: str = "GBP",
    ) -> SearchResult:
        if query in self._fail:
            raise ProviderError(f"mock failure for {query}", retryable=True)
        if check_out <= check_in:
            raise ProviderError(
                f"check_out {check_out} must be after check_in {check_in}",
                retryable=False,
            )

        payload: dict[str, Any] = {"search_query": query, "properties": []}
        if query in self._empty:
            return SearchResult(quotes=(), raw_payload=payload, source_api=self.name)

        nights = (check_out - check_in).days
        rng = _seed(query, check_in, adults)
        quotes: list[PropertyQuote] = []

        for i, (hotel_class, property_type) in enumerate(_SHAPE):
            token = f"mock_{_seed(query, i) % 10**10:010d}"
            if token in self._drop:
                continue
            base = _BASE_RATE.get(hotel_class, 90)
            bump = (rng >> (i * 5)) % 45
            # A weekend-ish seasonal shape so month-on-month movement exists to
            # be measured, and a Thursday differs from a Tuesday.
            shape = 1.0 + 0.06 * (check_in.weekday() >= 3)
            price = Decimal(int((base + bump) * shape))
            # Alternating so both rate bases are represented and the filter has
            # something to reject.
            free_cancellation = (i % 3) != 0
            before_taxes = (price * Decimal("0.83")).quantize(Decimal("1"))

            item = {
                "property_token": token,
                "name": f"Mock {property_type.title()} {i} {query.split(',')[0]}",
                "type": property_type,
                "hotel_class": hotel_class,
                "free_cancellation": free_cancellation,
                "rate_per_night": {
                    "extracted_lowest": int(price),
                    "extracted_before_taxes_fees": int(before_taxes),
                },
                "nights": nights,
            }
            payload["properties"].append(item)
            quotes.append(
                PropertyQuote(
                    property_token=token,
                    property_name=item["name"],
                    price=price,
                    price_before_taxes=before_taxes,
                    currency=currency.upper(),
                    hotel_class=hotel_class,
                    property_type=property_type,
                    free_cancellation=free_cancellation,
                    overall_rating=4.0,
                    reviews=100 + i,
                    raw=item,
                )
            )

        return SearchResult(
            quotes=tuple(quotes), raw_payload=payload, source_api=self.name
        )
