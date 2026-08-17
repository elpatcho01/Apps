"""Provider-agnostic accommodation search interface.

Same shape as the air-fares `FareProvider`, and for the same reason: the 2026
travel-data landscape is unstable and swapping sources should be a one-file
change. What differs is the unit of return.

ONE CALL RETURNS MANY PROPERTIES
---------------------------------
A fare provider is asked about a route and returns itineraries on that route. An
accommodation provider is asked about a *place* and returns every property in
it. That inverts the cost model -- properties are free once the location call is
made -- and it is why this panel can cover twelve regions on a hobby-tier quota
where the air-fares panel needed forty-four calls a day for twenty-three routes.

It also means the comparability problem lands here rather than in selection: the
returned set mixes hostels, five-star suites, serviced apartments and whole-home
vacation rentals, all priced per night and all superficially interchangeable.
`selection.py` filters that down; this module's job is only to report faithfully
what came back, including the fields needed to do the filtering.

WHAT WE CANNOT GET, STATED HERE SO IT IS NOT REDISCOVERED
----------------------------------------------------------
`board_basis` and `room_type` are on `PropertyQuote` and will be `None` from
every provider currently implemented. Google Hotels surfaces a property's lowest
available rate without saying whether it includes breakfast or which room it is
for. That is a real hole in the comparability controls -- two of the four the
methodology calls for -- and it is represented as an explicit `None` rather than
quietly omitted, so `comparability_basis` can record that the control could not
be applied and the digest can count how often.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from typing import Any, Protocol


class ProviderError(RuntimeError):
    """A lookup failed. Carries whether a retry is worth attempting."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclasses.dataclass(frozen=True, slots=True)
class PropertyQuote:
    """One priced property for one location and one set of dates."""

    #: Provider-stable identifier. The join key across months. Never use the
    #: name: a rebrand changes the name and keeps the token, and treating a
    #: rebrand as a new property is exactly how a matched sample silently
    #: shrinks.
    property_token: str
    property_name: str
    #: Advertised nightly rate as displayed to a consumer.
    price: Decimal
    #: Same rate excluding taxes and fees, where the provider separates them.
    #: Stored alongside rather than instead of, because which one ONS record is
    #: not established and mixing the two silently is an unfixable bias.
    price_before_taxes: Decimal | None
    currency: str
    #: Star rating, e.g. 4.0. None when the provider does not classify it --
    #: which is itself informative, and excludes the property from the panel.
    hotel_class: float | None
    #: "hotel", "vacation_rental", ... Vacation rentals are a different product
    #: and are filtered out; keeping the raw value makes that auditable.
    property_type: str | None
    #: True when the returned rate is free-cancellation. The single biggest
    #: contamination risk in this dataset -- refundable and non-refundable rates
    #: for an identical room routinely differ by 30-40%.
    free_cancellation: bool | None
    overall_rating: float | None = None
    reviews: int | None = None
    #: Not obtainable from any provider implemented here. See the module
    #: docstring; present so the schema does not have to change when one is.
    board_basis: str | None = None
    room_type: str | None = None
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True, slots=True)
class SearchResult:
    """Everything one provider call produced, including the untouched payload."""

    quotes: tuple[PropertyQuote, ...]
    #: Full response, stored verbatim in `raw_response` for audit and
    #: reprocessing. This has already paid for itself once on the sibling
    #: project: a selection bug was diagnosed and re-scored from stored payloads
    #: without re-querying anything.
    raw_payload: dict[str, Any]
    source_api: str

    def __len__(self) -> int:
        return len(self.quotes)


class AccommodationProvider(Protocol):
    """What the puller needs from an accommodation source."""

    name: str

    #: True if rates may come from a cache rather than a live availability call.
    #: Surfaced in the data and in validation: a cached rate is not the
    #: measurement ONS make on collection day.
    is_cached_source: bool

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
        """Return every priced property for one location and date pair.

        Raise `ProviderError` on failure; the caller handles retry and
        continue-on-error so one bad location cannot kill a run.
        """
        ...
