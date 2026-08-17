"""SerpApi Google Flights adapter.

Note this adapter has never run against the live API (serpapi.com is blocked by
egress policy from the development sandbox), so these tests pin the *documented*
contract. If the real response shape differs, these are the tests to correct
first -- and `raw_response` on every row means past observations can be
reprocessed without re-querying.
"""

import datetime as dt
import json
from decimal import Decimal

import pytest
import requests

from ukairfares.providers import ProviderError, build_provider
from ukairfares.providers.serpapi import SerpApiProvider, _parse_dt


def flight_option(price, hour, minute=0, segments=1, airline="British Airways"):
    flights = []
    for i in range(segments):
        flights.append({
            "departure_airport": {
                "name": "Heathrow", "id": "LHR",
                "time": f"2026-09-08 {hour + i:02d}:{minute:02d}",
            },
            "arrival_airport": {"name": "JFK", "id": "JFK", "time": "2026-09-08 18:00"},
            "airline": airline,
            "flight_number": f"BA {100 + i}",
            "travel_class": "Economy",
        })
    return {"flights": flights, "price": price, "type": "Round trip"}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self._response = response
        self.last_params = None

    def get(self, url, params=None, timeout=None, headers=None):
        self.last_params = params
        return self._response


def provider_for(payload, status=200):
    session = FakeSession(FakeResponse(payload, status))
    return SerpApiProvider("key-123", session=session), session


class TestParseDt:
    @pytest.mark.parametrize("value,expected", [
        ("2026-09-08 09:15", dt.datetime(2026, 9, 8, 9, 15)),
        ("2026-09-08 09:15:30", dt.datetime(2026, 9, 8, 9, 15, 30)),
        ("2026-09-08T09:15:00", dt.datetime(2026, 9, 8, 9, 15)),
    ])
    def test_parses_known_forms(self, value, expected):
        assert _parse_dt(value) == expected

    @pytest.mark.parametrize("value", [None, "", "not a time", 42, {}])
    def test_rejects_junk(self, value):
        assert _parse_dt(value) is None


class TestRequestShape:
    def test_round_trip_parameters(self):
        provider, session = provider_for({"best_flights": []})
        provider.search(
            origin="LHR", destination="JFK",
            departure_date=dt.date(2026, 9, 8), return_date=dt.date(2026, 9, 29),
        )
        p = session.last_params
        assert p["engine"] == "google_flights"
        assert p["departure_id"] == "LHR" and p["arrival_id"] == "JFK"
        assert p["outbound_date"] == "2026-09-08"
        assert p["return_date"] == "2026-09-29"
        assert p["type"] == 1          # round trip
        assert p["travel_class"] == 1  # economy
        assert p["adults"] == 1
        assert p["currency"] == "GBP"
        assert p["gl"] == "uk"

    def test_one_way_when_no_return(self):
        provider, session = provider_for({"best_flights": []})
        provider.search(
            origin="LHR", destination="EDI",
            departure_date=dt.date(2026, 9, 8), return_date=None,
        )
        assert session.last_params["type"] == 2
        assert "return_date" not in session.last_params

    def test_no_cache_defaults_on(self):
        # "The price on index day" is the measurement; a cached copy would
        # reintroduce exactly the staleness this provider avoids.
        provider, session = provider_for({"best_flights": []})
        provider.search(
            origin="LHR", destination="EDI",
            departure_date=dt.date(2026, 9, 8), return_date=None,
        )
        assert session.last_params["no_cache"] == "true"

    def test_no_cache_can_be_disabled(self):
        session = FakeSession(FakeResponse({"best_flights": []}))
        provider = SerpApiProvider("k", session=session, no_cache=False)
        provider.search(
            origin="LHR", destination="EDI",
            departure_date=dt.date(2026, 9, 8), return_date=None,
        )
        assert "no_cache" not in session.last_params

    def test_api_key_is_sent(self):
        provider, session = provider_for({"best_flights": []})
        provider.search(
            origin="LHR", destination="EDI",
            departure_date=dt.date(2026, 9, 8), return_date=None,
        )
        assert session.last_params["api_key"] == "key-123"


class TestParsing:
    def test_parses_a_quote(self):
        payload = {"best_flights": [flight_option(431, 9, 15)]}
        provider, _ = provider_for(payload)
        result = provider.search(
            origin="LHR", destination="JFK",
            departure_date=dt.date(2026, 9, 8), return_date=dt.date(2026, 9, 29),
        )
        assert len(result) == 1
        q = result.quotes[0]
        assert q.price == Decimal("431")
        assert q.currency == "GBP"
        assert q.departure_at == dt.datetime(2026, 9, 8, 9, 15)
        assert q.airline == "British Airways"
        assert q.transfers == 0
        assert result.raw_payload == payload

    def test_combines_best_and_other_flights(self):
        # The ONS closest-to-target-time rule must choose from the real
        # timetable, not just Google's curated shortlist.
        payload = {
            "best_flights": [flight_option(431, 9)],
            "other_flights": [flight_option(380, 6), flight_option(520, 17)],
        }
        provider, _ = provider_for(payload)
        result = provider.search(
            origin="LHR", destination="JFK",
            departure_date=dt.date(2026, 9, 8), return_date=dt.date(2026, 9, 29),
        )
        assert len(result) == 3

    def test_missing_best_flights_key(self):
        provider, _ = provider_for({"other_flights": [flight_option(380, 6)]})
        result = provider.search(
            origin="LHR", destination="JFK",
            departure_date=dt.date(2026, 9, 8), return_date=None,
        )
        assert len(result) == 1

    def test_transfers_from_segment_count(self):
        payload = {"best_flights": [flight_option(431, 9, segments=2)]}
        provider, _ = provider_for(payload)
        result = provider.search(
            origin="LHR", destination="CPT",
            departure_date=dt.date(2026, 9, 8), return_date=None,
        )
        assert result.quotes[0].transfers == 1

    def test_return_date_reflects_request(self):
        provider, _ = provider_for({"best_flights": [flight_option(431, 9)]})
        result = provider.search(
            origin="LHR", destination="JFK",
            departure_date=dt.date(2026, 9, 8), return_date=dt.date(2026, 9, 29),
        )
        assert result.quotes[0].return_at.date() == dt.date(2026, 9, 29)

    def test_skips_malformed_options(self):
        payload = {"best_flights": [
            {"flights": [], "price": None},
            {"price": "not-a-number"},
            {"price": 0},
            {"price": -10},
            "not-a-dict",
            flight_option(299, 10),
        ]}
        provider, _ = provider_for(payload)
        result = provider.search(
            origin="LHR", destination="AMS",
            departure_date=dt.date(2026, 9, 8), return_date=None,
        )
        assert len(result) == 1
        assert result.quotes[0].price == Decimal("299")

    def test_option_without_segments_still_priced(self):
        provider, _ = provider_for({"best_flights": [{"price": 250}]})
        result = provider.search(
            origin="LHR", destination="AMS",
            departure_date=dt.date(2026, 9, 8), return_date=None,
        )
        assert len(result) == 1
        assert result.quotes[0].departure_at is None


class TestErrorHandling:
    @pytest.mark.parametrize("message", [
        "Google Flights hasn't returned any results for this query.",
        "Google Flights has not returned any results for this query.",
        "No results found for this search.",
    ])
    def test_no_results_is_empty_not_an_error(self, message):
        # A thin route/date legitimately has no flights; that is data, not failure.
        provider, _ = provider_for({"error": message})
        result = provider.search(
            origin="LHR", destination="CPT",
            departure_date=dt.date(2027, 3, 9), return_date=None,
        )
        assert len(result) == 0
        assert result.raw_payload["error"] == message

    def test_genuine_api_error_raises(self):
        provider, _ = provider_for({"error": "Invalid API key."})
        with pytest.raises(ProviderError, match="Invalid API key"):
            provider.search(
                origin="LHR", destination="JFK",
                departure_date=dt.date(2026, 9, 8), return_date=None,
            )

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_transient_statuses_are_retryable(self, status):
        provider, _ = provider_for({}, status=status)
        with pytest.raises(ProviderError) as exc:
            provider.search(
                origin="LHR", destination="JFK",
                departure_date=dt.date(2026, 9, 8), return_date=None,
            )
        assert exc.value.retryable is True

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_failures_are_not_retryable(self, status):
        provider, _ = provider_for({}, status=status)
        with pytest.raises(ProviderError) as exc:
            provider.search(
                origin="LHR", destination="JFK",
                departure_date=dt.date(2026, 9, 8), return_date=None,
            )
        assert exc.value.retryable is False
        assert "SERPAPI_KEY" in str(exc.value)

    def test_timeout_is_retryable(self):
        class TimeoutSession:
            def get(self, *a, **k):
                raise requests.Timeout("slow")

        provider = SerpApiProvider("k", session=TimeoutSession())
        with pytest.raises(ProviderError) as exc:
            provider.search(
                origin="LHR", destination="JFK",
                departure_date=dt.date(2026, 9, 8), return_date=None,
            )
        assert exc.value.retryable is True

    def test_rejects_unsupported_cabin(self):
        provider, _ = provider_for({"best_flights": []})
        with pytest.raises(ProviderError, match="cabin"):
            provider.search(
                origin="LHR", destination="JFK",
                departure_date=dt.date(2026, 9, 8), return_date=None, cabin="first_class",
            )

    def test_requires_api_key(self):
        with pytest.raises(ValueError):
            SerpApiProvider("")


class TestProviderRegistry:
    def test_build_by_name(self):
        assert build_provider("serpapi", api_key="k").name == "serpapi_google_flights"

    def test_serpapi_is_not_a_cached_source(self):
        # This flag drives validation's provenance downgrade.
        assert build_provider("serpapi", api_key="k").is_cached_source is False

    def test_unknown_provider_lists_options(self):
        with pytest.raises(ValueError, match="serpapi"):
            build_provider("nonesuch")


class TestConfigWiring:
    def test_missing_key_does_not_block_config_construction(self, monkeypatch):
        """Loading config must not require a fare credential.

        ensure_tables, reconcile, validate and backfill all build a Config but
        never query a fare provider; demanding a token from them blocks setup
        for no reason.
        """
        from ukairfares.config import Config

        monkeypatch.setenv("DRY_RUN", "1")
        monkeypatch.setenv("FARE_PROVIDER", "serpapi")
        monkeypatch.delenv("SERPAPI_KEY", raising=False)
        config = Config.from_env()
        assert config.provider_credential is None

    def test_pull_path_still_fails_fast_on_a_missing_key(self, monkeypatch):
        from ukairfares.config import Config, ConfigError

        monkeypatch.setenv("DRY_RUN", "1")
        monkeypatch.setenv("FARE_PROVIDER", "serpapi")
        monkeypatch.delenv("SERPAPI_KEY", raising=False)
        with pytest.raises(ConfigError, match="SERPAPI_KEY"):
            Config.from_env().require_provider_credential()

    def test_mock_requires_nothing(self, monkeypatch):
        from ukairfares.config import Config

        monkeypatch.setenv("DRY_RUN", "1")
        monkeypatch.setenv("FARE_PROVIDER", "mock")
        Config.from_env().require_provider_credential()  # must not raise

    def test_serpapi_key_is_picked_up(self, monkeypatch):
        from ukairfares.config import Config

        monkeypatch.setenv("DRY_RUN", "1")
        monkeypatch.setenv("FARE_PROVIDER", "serpapi")
        monkeypatch.setenv("SERPAPI_KEY", "abc")
        config = Config.from_env()
        assert config.provider_name == "serpapi"
        assert config.provider_credential == "abc"

    def test_unknown_provider_rejected(self, monkeypatch):
        from ukairfares.config import Config, ConfigError

        monkeypatch.setenv("DRY_RUN", "1")
        monkeypatch.setenv("FARE_PROVIDER", "nonesuch")
        with pytest.raises(ConfigError, match="unknown FARE_PROVIDER"):
            Config.from_env()

    def test_mock_needs_no_credential(self, monkeypatch):
        from ukairfares.config import Config

        monkeypatch.setenv("DRY_RUN", "1")
        monkeypatch.setenv("FARE_PROVIDER", "mock")
        assert Config.from_env().provider_credential is None


class TestEndToEndWithSerpApi:
    def test_pull_runs_against_a_stubbed_serpapi(self, monkeypatch):
        """The ONS selection rule applied to a realistic SerpApi payload."""
        from ukairfares import panel
        from ukairfares.bq import DryRunWriter
        from ukairfares.config import Config
        from ukairfares.pull import run_pull

        payload = {
            "best_flights": [flight_option(431, 9, 10)],   # nearest 09:00
            "other_flights": [flight_option(310, 6), flight_option(520, 17)],
        }
        session = FakeSession(FakeResponse(payload))
        provider = SerpApiProvider("k", session=session)

        config = Config(
            project="p", dataset="d", provider_name="serpapi", provider_credential="k",
            market="uk", currency="GBP", target_departure_time=dt.time(9, 0),
            failure_threshold=0.34, dry_run=True,
            scrapes_table="airfare_scrapes", index_table="reconstructed_index",
        )
        writer = DryRunWriter()
        summary = run_pull(
            config, scrape_date=dt.date(2026, 9, 8), writer=writer,
            provider=provider, routes=panel.routes_by_haul("domestic"),
        )
        assert summary["ok"] == 8
        assert summary["error"] == 0

        row = writer.written[0]
        assert row["source_api"] == "serpapi_google_flights"
        assert row["is_cached_source"] is False
        # ONS rule takes the 09:10 flight at 431, not the 06:00 one at 310.
        assert row["price_gbp"] == Decimal("431")
        assert row["price_cheapest_gbp"] == Decimal("310")
        assert row["ons_rule_time_delta_minutes"] == 10
