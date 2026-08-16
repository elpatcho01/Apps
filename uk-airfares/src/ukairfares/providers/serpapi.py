"""SerpApi Google Flights provider.

The closest available analogue to what an ONS price collector actually does:
read the fare advertised to a consumer, on a public site, on index day. Google
Flights is metasearch rather than an airline or OTA, and SerpApi fetches it live
on request rather than serving a cache of other people's searches.

Why this is the preferred source
--------------------------------
Unlike a cache-backed provider, this returns the *full itinerary list* for the
route and date, each with its departure time. That is what the ONS selection
rule needs -- "the flight departing closest to a pre-specified time" -- and it
cannot be applied honestly to a list that has already been filtered down to the
cheapest few options.

It also supports every dimension the methodology requires: specific outbound and
return dates, cabin class, passenger count, and UK market pricing in GBP.

Caveats, recorded honestly
--------------------------
1. NOT VERIFIED AGAINST THE LIVE API. This adapter was written from SerpApi's
   documentation; the development sandbox blocks serpapi.com by egress policy,
   so the response-shape assumptions below are inferred, not observed. Parsing
   is defensive and tolerant of missing fields, and `raw_response` retains the
   full payload so any observation can be reprocessed without re-querying.

2. ROUND-TRIP PRICING IS A TOTAL. For a return search Google Flights returns
   outbound itineraries priced at the *total* round-trip fare, with the return
   leg selected in a subsequent step. That total is exactly what ONS record, so
   it is what we store -- but `return_at` on a quote reflects the return date we
   requested rather than a specific returning flight we saw.

3. NO RESULTS IS NOT AN ERROR. Google Flights legitimately returns nothing for
   some route/date combinations. SerpApi reports that in-band as an `error`
   string; we detect the no-results wording and record it as a `no_data`
   observation rather than a failure.

Docs: https://serpapi.com/google-flights-api
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from .base import FareQuote, FareProvider, ProviderError, SearchResult

log = logging.getLogger(__name__)

BASE_URL = "https://serpapi.com/search"

#: Google Flights trip types.
TRIP_ROUND = 1
TRIP_ONE_WAY = 2

#: Cabin classes. We only ever use economy -- ONS price the advertised economy
#: fare -- but the mapping is here so an unsupported request fails loudly rather
#: than silently pricing the wrong cabin.
TRAVEL_CLASS = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

#: SerpApi reports "no results" in-band as an error string. These are not
#: failures -- they are a legitimate empty observation for that route/date.
_NO_RESULTS_MARKERS = (
    "hasn't returned any results",
    "has not returned any results",
    "no results found",
    "didn't return any results",
)


def _parse_dt(value: Any) -> dt.datetime | None:
    """Parse Google Flights' "YYYY-MM-DD HH:MM" timestamps."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        log.debug("unparseable timestamp %r", value)
        return None


class SerpApiProvider(FareProvider):
    name = "serpapi_google_flights"
    is_cached_source = False

    def __init__(
        self,
        api_key: str,
        *,
        market: str = "uk",
        language: str = "en",
        timeout: float = 60.0,
        session: requests.Session | None = None,
        no_cache: bool = True,
    ) -> None:
        """
        `no_cache` defaults to True: SerpApi will otherwise serve a recently
        cached copy of the same search, and "the price on index day" is the
        whole measurement. A cached result would quietly reintroduce exactly the
        staleness this provider was chosen to avoid.
        """
        if not api_key:
            raise ValueError("SerpApi API key is required")
        self._api_key = api_key
        self._market = market
        self._language = language
        self._timeout = timeout
        self._session = session or requests.Session()
        self._no_cache = no_cache

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
        if cabin not in TRAVEL_CLASS:
            raise ProviderError(f"unsupported cabin {cabin!r}", retryable=False)
        if adults < 1:
            raise ProviderError(f"adults must be >= 1, got {adults}", retryable=False)

        params: dict[str, Any] = {
            "engine": "google_flights",
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": departure_date.isoformat(),
            "type": TRIP_ROUND if return_date else TRIP_ONE_WAY,
            "travel_class": TRAVEL_CLASS[cabin],
            "adults": adults,
            "currency": currency.upper(),
            "gl": self._market,
            "hl": self._language,
            "api_key": self._api_key,
        }
        if return_date:
            params["return_date"] = return_date.isoformat()
        if self._no_cache:
            params["no_cache"] = "true"

        try:
            resp = self._session.get(BASE_URL, params=params, timeout=self._timeout)
        except requests.Timeout as exc:
            raise ProviderError(f"timeout for {origin}-{destination}: {exc}") from exc
        except requests.RequestException as exc:
            raise ProviderError(f"request failed for {origin}-{destination}: {exc}") from exc

        if resp.status_code in RETRYABLE_STATUS:
            raise ProviderError(
                f"HTTP {resp.status_code} for {origin}-{destination}", retryable=True
            )
        if resp.status_code in (401, 403):
            raise ProviderError(
                f"HTTP {resp.status_code} for {origin}-{destination}: check SERPAPI_KEY",
                retryable=False,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderError(
                f"non-JSON response for {origin}-{destination}: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise ProviderError(
                f"unexpected response shape for {origin}-{destination}", retryable=False
            )

        error = payload.get("error")
        if error:
            text = str(error)
            if any(marker in text.lower() for marker in _NO_RESULTS_MARKERS):
                # Legitimate empty result, not a failure.
                log.info("no flights for %s-%s on %s", origin, destination, departure_date)
                return SearchResult(quotes=(), raw_payload=payload, source_api=self.name)
            raise ProviderError(
                f"SerpApi error for {origin}-{destination}: {text[:200]}",
                retryable=resp.status_code >= 500,
            )

        if resp.status_code >= 400:
            raise ProviderError(
                f"HTTP {resp.status_code} for {origin}-{destination}: {resp.text[:200]}",
                retryable=False,
            )

        quotes = tuple(
            q
            for q in (
                self._to_quote(item, currency.upper(), return_date)
                # `best_flights` is Google's curated shortlist and is not always
                # present; `other_flights` carries the rest of the timetable.
                # Both are needed for the ONS closest-to-target-time rule to be
                # choosing from the real set of departures.
                for item in (payload.get("best_flights") or [])
                + (payload.get("other_flights") or [])
            )
            if q is not None
        )

        return SearchResult(quotes=quotes, raw_payload=payload, source_api=self.name)

    def _to_quote(
        self, item: Any, currency: str, return_date: dt.date | None
    ) -> FareQuote | None:
        if not isinstance(item, dict):
            return None
        raw_price = item.get("price")
        if raw_price is None:
            return None
        try:
            price = Decimal(str(raw_price))
        except (InvalidOperation, TypeError):
            log.warning("unparseable price %r", raw_price)
            return None
        if price <= 0:
            return None

        segments = item.get("flights") or []
        first = segments[0] if segments and isinstance(segments[0], dict) else {}
        departure = first.get("departure_airport") or {}

        return FareQuote(
            price=price,
            currency=currency,
            departure_at=_parse_dt(departure.get("time")),
            # The returning flight is chosen in a later step; the date we asked
            # for is the honest value to record here.
            return_at=(
                dt.datetime.combine(return_date, dt.time.min) if return_date else None
            ),
            airline=first.get("airline"),
            flight_number=first.get("flight_number"),
            # Segment count minus one: a two-segment itinerary is one stop.
            transfers=max(len(segments) - 1, 0) if segments else None,
            # Fetched live, so the observation time is the query time. Left None
            # rather than invented; the row's scrape_ts already records it.
            found_at=None,
            raw=item,
        )
