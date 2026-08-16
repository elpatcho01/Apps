"""Deterministic fare provider for tests, dry runs and CI.

Generates plausible-shaped fares without touching the network, so the whole
pipeline -- panel, calendar, selection, BigQuery row construction -- can be
exercised end to end without an API token or spending quota.

The numbers are synthetic. They are seeded off the route and dates so a given
query always returns the same thing, which makes tests deterministic, but they
carry no information about real fares and must never reach a production table.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from typing import Any

from .base import FareQuote, FareProvider, ProviderError, SearchResult

#: Rough per-haul fare anchors, purely so synthetic output is not absurd.
_BASE_PRICE = {
    "domestic": 120,
    "european": 210,
    "long_haul": 650,
}

_DEPARTURE_HOURS = (6, 8, 11, 14, 17, 21)


def _seed(*parts: Any) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big")


class MockProvider(FareProvider):
    name = "mock"
    is_cached_source = False

    def __init__(
        self,
        *,
        haul_of: dict[str, str] | None = None,
        fail_routes: frozenset[str] = frozenset(),
        empty_routes: frozenset[str] = frozenset(),
    ) -> None:
        """
        `fail_routes` and `empty_routes` take "ORG-DST" codes and let tests
        exercise the failure and no-data paths without monkeypatching.
        """
        self._haul_of = haul_of or {}
        self._fail_routes = fail_routes
        self._empty_routes = empty_routes

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
        code = f"{origin}-{destination}"
        if code in self._fail_routes:
            raise ProviderError(f"mock failure for {code}", retryable=True)

        payload: dict[str, Any] = {
            "success": True,
            "currency": currency.lower(),
            "data": [],
        }
        if code in self._empty_routes:
            return SearchResult(quotes=(), raw_payload=payload, source_api=self.name)

        haul = self._haul_of.get(code, "european")
        base = _BASE_PRICE.get(haul, 200)
        rng = _seed(code, departure_date, return_date)

        quotes: list[FareQuote] = []
        for i, hour in enumerate(_DEPARTURE_HOURS):
            bump = (rng >> (i * 5)) % 90
            # Mid-morning and evening departures priced above the red-eye, so the
            # ONS-rule vs cheapest distinction is actually visible in tests.
            shape = 1.0 + 0.18 * (1 - abs(hour - 12) / 9)
            price = Decimal(int((base + bump) * shape))
            dep = dt.datetime.combine(departure_date, dt.time(hour, (rng >> i) % 60))
            ret = (
                dt.datetime.combine(return_date, dt.time((hour + 2) % 24, 30))
                if return_date
                else None
            )
            item = {
                "origin": origin,
                "destination": destination,
                "price": int(price),
                "airline": f"M{i}",
                "flight_number": 100 + i,
                "transfers": 0,
                "departure_at": dep.isoformat(),
                "return_at": ret.isoformat() if ret else None,
                "found_at": dt.datetime.combine(
                    departure_date - dt.timedelta(days=1), dt.time(12, 0)
                ).isoformat(),
            }
            payload["data"].append(item)
            quotes.append(
                FareQuote(
                    price=price,
                    currency=currency.upper(),
                    departure_at=dep,
                    return_at=ret,
                    airline=item["airline"],
                    flight_number=str(item["flight_number"]),
                    transfers=0,
                    found_at=dt.datetime.fromisoformat(item["found_at"]),
                    raw=item,
                )
            )

        return SearchResult(
            quotes=tuple(quotes), raw_payload=payload, source_api=self.name
        )
