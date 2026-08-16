"""Travelpayouts (Aviasales) Data API provider.

Endpoint: GET /aviasales/v3/prices_for_dates

Known limitations, recorded here because they bear directly on how much the
resulting index can be trusted:

1. NOT LIVE PRICING. This endpoint returns "the cheapest tickets for specific
   dates found by Aviasales users in the last 48 hours". It is a cache of other
   people's searches, not a fresh quote. ONS price collectors read the fare
   advertised *on index day*; this returns a fare somebody was shown up to 48
   hours earlier, and only for routes/dates somebody actually searched. Every
   row is therefore stamped with the provider's `found_at` and a
   `is_cached_source` flag so this never gets quietly forgotten downstream.

2. THIN OR ABSENT COVERAGE ON UNPOPULAR DATE COMBINATIONS. Because the cache is
   demand-driven, a specific Tuesday six months out on a thin route may return
   nothing at all. Empty results are a normal outcome here, not an error, and
   are recorded as such.

3. NO CABIN CLASS PARAMETER. The endpoint has no cabin selector; results are
   economy. That happens to match what we want, but it is not something we can
   assert via the API, so `cabin` is validated and rejected if anything other
   than economy is requested.

4. NO PASSENGER COUNT. Prices are per-adult one-passenger fares. `adults` is
   likewise validated rather than passed through.

Docs: https://support.travelpayouts.com/hc/en-us/articles/203956163-Aviasales-Data-API
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from .base import FareQuote, FareProvider, ProviderError, SearchResult

log = logging.getLogger(__name__)

BASE_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

#: Travelpayouts returns prices for a given market's storefront. "uk" gives the
#: fares a UK consumer would be shown, which is what CPI measures.
DEFAULT_MARKET = "uk"

#: HTTP statuses worth a retry: transient server-side or rate-limit conditions.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _parse_dt(value: Any) -> dt.datetime | None:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z' and bare dates."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            return dt.datetime.combine(dt.date.fromisoformat(text[:10]), dt.time.min)
        except ValueError:
            log.debug("unparseable timestamp %r", value)
            return None


class TravelpayoutsProvider(FareProvider):
    name = "travelpayouts_aviasales_v3"
    is_cached_source = True

    def __init__(
        self,
        token: str,
        *,
        market: str = DEFAULT_MARKET,
        timeout: float = 30.0,
        session: requests.Session | None = None,
        limit: int = 30,
    ) -> None:
        if not token:
            raise ValueError("Travelpayouts API token is required")
        self._token = token
        self._market = market
        self._timeout = timeout
        self._session = session or requests.Session()
        self._limit = limit

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
        if cabin != "economy":
            raise ProviderError(
                f"Travelpayouts prices_for_dates has no cabin selector; "
                f"cannot honour cabin={cabin!r}",
                retryable=False,
            )
        if adults != 1:
            raise ProviderError(
                f"Travelpayouts prices_for_dates returns single-adult fares; "
                f"cannot honour adults={adults}",
                retryable=False,
            )

        params: dict[str, Any] = {
            "origin": origin,
            "destination": destination,
            "departure_at": departure_date.isoformat(),
            "currency": currency.lower(),
            "market": self._market,
            "limit": self._limit,
            "sorting": "price",
            "one_way": "false" if return_date else "true",
            "token": self._token,
        }
        if return_date:
            params["return_at"] = return_date.isoformat()

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
        if resp.status_code == 401 or resp.status_code == 403:
            raise ProviderError(
                f"HTTP {resp.status_code} for {origin}-{destination}: check "
                "TRAVELPAYOUTS_TOKEN",
                retryable=False,
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"HTTP {resp.status_code} for {origin}-{destination}: {resp.text[:200]}",
                retryable=False,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderError(f"non-JSON response for {origin}-{destination}: {exc}") from exc

        if not isinstance(payload, dict):
            raise ProviderError(
                f"unexpected response shape for {origin}-{destination}: {type(payload)}",
                retryable=False,
            )

        if not payload.get("success", False):
            # The API reports logical errors in-band with HTTP 200.
            raise ProviderError(
                f"API error for {origin}-{destination}: {payload.get('error')!r}",
                retryable=False,
            )

        response_currency = str(payload.get("currency", currency)).upper()
        quotes = tuple(
            q
            for q in (
                self._to_quote(item, response_currency)
                for item in payload.get("data") or []
            )
            if q is not None
        )

        # Zero quotes is an expected outcome for a demand-driven cache, not a
        # failure. The caller records it as a no-data observation.
        return SearchResult(quotes=quotes, raw_payload=payload, source_api=self.name)

    def _to_quote(self, item: Any, currency: str) -> FareQuote | None:
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

        return FareQuote(
            price=price,
            currency=currency,
            departure_at=_parse_dt(item.get("departure_at")),
            return_at=_parse_dt(item.get("return_at")),
            airline=item.get("airline"),
            flight_number=(
                str(item["flight_number"]) if item.get("flight_number") is not None else None
            ),
            transfers=item.get("transfers"),
            found_at=_parse_dt(item.get("found_at")),
            raw=item,
        )
