"""SerpApi Google Hotels provider.

WHY THIS SOURCE
---------------
It is the only self-serve option that does what the methodology needs without
requiring booking intent we do not have. The alternatives were researched and
rejected:

  * **Booking.com Demand API** -- application, manual review, partner approval.
  * **Expedia Rapid (EPS)** -- formal partnership, minimum performance
    commitments, a three-to-six month certification before going live.
  * **Hotelbeds APItude** -- certification and credential approval, bedbank
    terms written around booking volume.
  * **RateHawk / Emerging Travel** -- same partner-gated shape.

All three of the big ones are structured around a look-to-book expectation. A
research account that searches forever and books never is precisely the profile
those terms exist to stop, and building on one would mean building on an account
that gets capped or closed. That is a worse outcome than a slightly less clean
data source, so it was ruled out rather than risked.

SerpApi asks for no booking intent, takes exact check-in and check-out dates and
occupancy, and returns the two things the comparability filter needs most:
`hotel_class` and `free_cancellation`. It also returns both the advertised rate
and the before-taxes-and-fees rate, which is what lets this pipeline store both
rather than silently picking one.

It is also already in use on the sibling air-fares project, so the account, the
billing and the `SERPAPI_KEY` secret are shared.

CAVEATS, RECORDED HONESTLY
--------------------------
1. NOT VERIFIED AGAINST THE LIVE API. This adapter was written from SerpApi's
   documentation; serpapi.com is blocked by egress policy in the development
   sandbox, exactly as it was when the air-fares adapter was written. Field
   names and nesting below are inferred, not observed. Parsing is therefore
   defensive throughout and `raw_response` retains the full payload, so if the
   shape differs the observations can be reparsed without re-querying. The first
   live run should be checked against `n_quotes` before it is trusted.

2. ONE RATE PER PROPERTY. Google Hotels surfaces a property's lowest available
   rate across sources, not a rate card. So `free_cancellation` describes *that*
   rate, and there is no way to request the same property on the other basis.
   The filter therefore selects properties whose lowest rate happens to be on
   the configured basis, which thins the sample rather than biasing the level.

3. NO BOARD BASIS OR ROOM TYPE. Not returned at all. See `selection.py` -- these
   are recorded as unknown rather than assumed.

4. SCRAPING-AS-A-SERVICE. SerpApi fetches Google Hotels rather than holding a
   licence to it, and that model is under active litigation. We do not scrape
   any hotel or OTA site ourselves, but this is one intermediary away from it
   and should be understood as such rather than treated as a first-party feed.

Docs: https://serpapi.com/google-hotels-api
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from .base import AccommodationProvider, ProviderError, PropertyQuote, SearchResult

log = logging.getLogger(__name__)

BASE_URL = "https://serpapi.com/search"

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

#: SerpApi reports "no results" in-band as an error string. Not a failure -- a
#: legitimate empty observation for that location and date.
_NO_RESULTS_MARKERS = (
    "hasn't returned any results",
    "has not returned any results",
    "no results found",
    "didn't return any results",
)


#: Response keys that echo the request and could carry the credential. Dropped
#: outright rather than scrubbed, because their value is diagnostic at best.
_REDACT_KEYS = frozenset({"api_key", "serpapi_api_key", "secret_key"})

#: What a redacted value is replaced with, chosen to be obvious in a diff.
_REDACTED = "<redacted>"


def redact(obj: Any, secret: str | None) -> Any:
    """Strip the API key out of a payload before it is stored or uploaded.

    WHY THIS IS NOT PARANOIA

    `raw_payload` is written verbatim to `raw_response` in BigQuery, and that
    table is append-only by design -- there is no delete path, deliberately. So
    a credential that reaches it is there permanently, in a warehouse whose
    whole value proposition is that history cannot be rewritten. The same
    payload is also what gets uploaded as a workflow artifact when the provider
    smoke test runs, where anyone with read access to the repository can fetch
    it.

    SerpApi does not appear to echo `api_key` in its responses, but "does not
    appear to" is not a guarantee worth betting an unrotatable leak on, and the
    check costs one pass over a dict we are already serialising.

    Removes known credential keys anywhere in the structure, and replaces any
    string containing the key itself -- which covers the case of the key turning
    up inside a URL, where it would not be a key/value pair we could match on.
    """
    if isinstance(obj, dict):
        return {
            k: _REDACTED if k in _REDACT_KEYS else redact(v, secret)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(v, secret) for v in obj]
    if isinstance(obj, str) and secret and secret in obj:
        return obj.replace(secret, _REDACTED)
    return obj


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        log.debug("unparseable price %r", value)
        return None
    return price if price > 0 else None


def parse_hotel_class(item: dict[str, Any]) -> float | None:
    """Star rating, from whichever field actually carries it.

    THE BUG THIS REPLACES, because it is an easy one to reintroduce:

        _float(item.get("hotel_class") or item.get("extracted_hotel_class"))

    Google Hotels returns `hotel_class` as a *display string* -- "4-star hotel"
    -- and `extracted_hotel_class` as the number. The string is truthy, so `or`
    short-circuits on it and the numeric field is never read; `_float` then
    cannot parse "4-star hotel" and returns None. The first live run produced
    238 properties across twelve cities with every single one unrated, and since
    an unrated property is outside every tier, the entire panel was rejected.

    So: the numeric field first, and only then a number pulled out of the
    string. Both are tried because neither is guaranteed present.
    """
    extracted = item.get("extracted_hotel_class")
    if isinstance(extracted, (int, float)) and not isinstance(extracted, bool):
        return float(extracted)

    raw = item.get("hotel_class")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, str):
        match = re.search(r"(\d+(?:\.\d+)?)", raw)
        if match:
            return float(match.group(1))
    return None


def parse_free_cancellation(item: dict[str, Any]) -> bool | None:
    """Whether the returned rate is free-cancellation, from wherever it lives.

    Also None for all 238 properties on the first live run. Unlike the star
    rating that may not be a parsing bug -- the engine may simply not expose it
    on the properties list -- so this looks in every plausible place rather than
    assuming one, and still returns None when genuinely absent.

    None is not "no": it means unknown, and `selection` excludes unknowns rather
    than guessing, because a series that silently blends refundable and
    non-refundable rates carries a 30-40% contamination nobody can unpick later.
    """
    for key in ("free_cancellation", "free_cancellation_available"):
        value = item.get(key)
        if isinstance(value, bool):
            return value

    # A date being present is itself the signal: a rate cancellable until some
    # date is a free-cancellation rate.
    if item.get("free_cancellation_until_date"):
        return True

    # Per-source rates can carry it where the property summary does not.
    for price in item.get("prices") or []:
        if isinstance(price, dict) and isinstance(price.get("free_cancellation"), bool):
            return price["free_cancellation"]

    return None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class SerpApiHotelsProvider(AccommodationProvider):
    name = "serpapi_google_hotels"
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
        vacation_rentals: bool = False,
    ) -> None:
        """
        `no_cache` defaults to True: SerpApi will otherwise serve a recently
        cached copy of the same search, and "the rate on collection day" is the
        entire measurement. A cached result would quietly reintroduce the
        staleness this provider was chosen to avoid.

        `vacation_rentals` defaults to False: whole-home lettings are a
        different product from a hotel room and would be filtered out
        downstream anyway, so excluding them at the query keeps the payload and
        the quota smaller.
        """
        if not api_key:
            raise ValueError("SerpApi API key is required")
        self._api_key = api_key
        self._market = market
        self._language = language
        self._timeout = timeout
        self._session = session or requests.Session()
        self._no_cache = no_cache
        self._vacation_rentals = vacation_rentals

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
        if check_out <= check_in:
            raise ProviderError(
                f"check_out {check_out} must be after check_in {check_in}",
                retryable=False,
            )
        if adults < 1:
            raise ProviderError(f"adults must be >= 1, got {adults}", retryable=False)

        params: dict[str, Any] = {
            "engine": "google_hotels",
            "q": query,
            "check_in_date": check_in.isoformat(),
            "check_out_date": check_out.isoformat(),
            "adults": adults,
            "children": children,
            "currency": currency.upper(),
            "gl": self._market,
            "hl": self._language,
            "api_key": self._api_key,
        }
        if not self._vacation_rentals:
            params["vacation_rentals"] = "false"
        if self._no_cache:
            params["no_cache"] = "true"

        try:
            resp = self._session.get(BASE_URL, params=params, timeout=self._timeout)
        except requests.Timeout as exc:
            raise ProviderError(f"timeout for {query}: {exc}") from exc
        except requests.RequestException as exc:
            raise ProviderError(f"request failed for {query}: {exc}") from exc

        if resp.status_code in RETRYABLE_STATUS:
            raise ProviderError(f"HTTP {resp.status_code} for {query}", retryable=True)
        if resp.status_code in (401, 403):
            raise ProviderError(
                f"HTTP {resp.status_code} for {query}: check SERPAPI_KEY",
                retryable=False,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderError(f"non-JSON response for {query}: {exc}") from exc

        # Scrubbed immediately, before anything can hold a reference to the
        # unredacted form. See `redact`: this payload is stored verbatim in an
        # append-only table and uploaded as a workflow artifact.
        payload = redact(payload, self._api_key)

        if not isinstance(payload, dict):
            raise ProviderError(
                f"unexpected response shape for {query}", retryable=False
            )

        error = payload.get("error")
        if error:
            text = str(error)
            if any(marker in text.lower() for marker in _NO_RESULTS_MARKERS):
                log.info("no properties for %s on %s", query, check_in)
                return SearchResult(quotes=(), raw_payload=payload, source_api=self.name)
            raise ProviderError(
                f"SerpApi error for {query}: {text[:200]}",
                retryable=resp.status_code >= 500,
            )

        if resp.status_code >= 400:
            raise ProviderError(
                f"HTTP {resp.status_code} for {query}: {resp.text[:200]}",
                retryable=False,
            )

        # `properties` is the main list. `ads` carries paid placements, which are
        # a different product surface and are deliberately not read: their
        # composition varies with advertiser spend rather than with the hotel
        # market, so including them would put an auction into the index.
        items = payload.get("properties") or []
        quotes = tuple(
            q for q in (self._to_quote(item, currency.upper()) for item in items)
            if q is not None
        )
        return SearchResult(quotes=quotes, raw_payload=payload, source_api=self.name)

    def property_details(
        self, property_token: str, *, check_in: dt.date, check_out: dt.date,
        adults: int = 2, currency: str = "GBP",
    ) -> dict[str, Any]:
        """Fetch one property's detail payload, for probing what it carries.

        The properties list does not include `free_cancellation` -- confirmed
        from a raw-key census over 240 live properties, where the field appears
        on none of them. Every property does carry a
        `serpapi_property_details_link`, so the question is whether that second
        endpoint exposes cancellation terms and board basis, which would restore
        two comparability controls the list view cannot.

        This exists to answer that for the price of one call. It is NOT wired
        into collection: doing so would multiply the per-day quota by the number
        of pinned properties, which is a cost decision rather than a technical
        one.
        """
        params = {
            "engine": "google_hotels_property_details",
            "property_token": property_token,
            "check_in_date": check_in.isoformat(),
            "check_out_date": check_out.isoformat(),
            "adults": adults,
            "currency": currency.upper(),
            "gl": self._market,
            "hl": self._language,
            "api_key": self._api_key,
        }
        resp = self._session.get(BASE_URL, params=params, timeout=self._timeout)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderError(f"non-JSON detail response: {exc}") from exc
        return redact(payload, self._api_key)

    def _to_quote(self, item: Any, currency: str) -> PropertyQuote | None:
        if not isinstance(item, dict):
            return None

        token = item.get("property_token") or item.get("serpapi_property_details_link")
        if not token:
            # Without a stable identity the observation cannot be matched across
            # months, which is the whole basis of the index. Dropping it is
            # correct; the count shows up as n_quotes minus n_considered.
            log.debug("property with no token: %r", item.get("name"))
            return None

        rate = item.get("rate_per_night") or {}
        price = _decimal(rate.get("extracted_lowest"))
        if price is None:
            # `total_rate` is the whole-stay figure; for a one-night stay it
            # equals the nightly rate, so it is a sound fallback here and only
            # here. If NIGHTS ever stops being 1 this must be divided.
            total = item.get("total_rate") or {}
            price = _decimal(total.get("extracted_lowest"))
        if price is None:
            return None

        before_taxes = _decimal(rate.get("extracted_before_taxes_fees"))

        free_cancellation = parse_free_cancellation(item)

        return PropertyQuote(
            property_token=str(token),
            property_name=str(item.get("name") or "").strip(),
            price=price,
            price_before_taxes=before_taxes,
            currency=currency,
            hotel_class=parse_hotel_class(item),
            property_type=(item.get("type") or None),
            free_cancellation=free_cancellation,
            overall_rating=_float(item.get("overall_rating")),
            reviews=item.get("reviews") if isinstance(item.get("reviews"), int) else None,
            # Not returned by this engine. Explicit None rather than omitted, so
            # the comparability filter records that the control was unavailable
            # rather than appearing to have applied it.
            board_basis=None,
            room_type=None,
            raw=item,
        )
