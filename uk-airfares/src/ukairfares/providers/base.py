"""Provider-agnostic fare search interface.

The pipeline is deliberately decoupled from any one fare source. The 2026
landscape is unstable -- Amadeus Self-Service was decommissioned on 17 July 2026,
Kiwi's Tequila API went invitation-only, and Skyscanner is partner-gated -- so
swapping sources needs to be a one-file change, not a rewrite.

A provider's only job is: given a route and dates, return candidate quotes.
All ONS-specific logic (which dates, which quote to pick, how to attribute it)
lives outside providers, in `onscal` and `selection`.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from typing import Any, Protocol


class ProviderError(RuntimeError):
    """A fare lookup failed. Carries whether a retry is worth attempting."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclasses.dataclass(frozen=True, slots=True)
class FareQuote:
    """One priced itinerary returned by a provider."""

    price: Decimal
    currency: str
    departure_at: dt.datetime | None
    return_at: dt.datetime | None
    airline: str | None = None
    flight_number: str | None = None
    transfers: int | None = None
    #: When the provider last actually observed this price. Critical for any
    #: cache-backed source: it is the difference between "the fare on index day"
    #: and "a fare somebody saw at some point recently".
    found_at: dt.datetime | None = None
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True, slots=True)
class SearchResult:
    """Everything one provider call produced, including the untouched payload."""

    quotes: tuple[FareQuote, ...]
    #: Full response, stored verbatim in `raw_response` for audit/reprocessing.
    raw_payload: dict[str, Any]
    source_api: str

    def __len__(self) -> int:
        return len(self.quotes)


class FareProvider(Protocol):
    """What the puller needs from a fare source."""

    name: str

    #: True if quotes may come from a cache rather than a live pricing call.
    #: Surfaced in the data and in validation, because a cached price is not the
    #: same measurement ONS make.
    is_cached_source: bool

    def search(
        self,
        *,
        origin: str,
        destination: str,
        departure_date: dt.date,
        return_date: dt.date | None,
        adults: int = 1,
        cabin: str = "economy",
        currency: str = "GBP",
    ) -> SearchResult:
        """Return candidate quotes for one route/date combination.

        Raise `ProviderError` on failure; the caller handles retry and
        continue-on-error so that one bad route cannot kill a run.
        """
        ...
