"""Selection rule, panel, provider parsing and end-to-end pull behaviour."""

import datetime as dt
import json
from decimal import Decimal

import pytest
import requests

from ukairfares import panel, selection
from ukairfares.config import Config
from ukairfares.bq import DryRunWriter
from ukairfares.onscal import ADVANCE_MONTHS
from ukairfares.providers import MockProvider, ProviderError
from ukairfares.providers.base import FareQuote
from ukairfares.providers.travelpayouts import TravelpayoutsProvider
from ukairfares.pull import choose_ordinal, run_pull


def quote(price, hour, minute=0):
    return FareQuote(
        price=Decimal(str(price)),
        currency="GBP",
        departure_at=dt.datetime(2026, 9, 8, hour, minute),
        return_at=None,
    )


class TestSelection:
    def test_picks_closest_to_target_not_cheapest(self):
        quotes = [quote(60, 5, 30), quote(200, 9, 10), quote(150, 14)]
        sel = selection.select(quotes, target_time=dt.time(9, 0))
        assert sel.ons_rule.price == Decimal("200")  # nearest 09:00, though dearest
        assert sel.cheapest.price == Decimal("60")
        assert sel.ons_rule_time_delta_minutes == 10
        assert sel.spread == Decimal("140")

    def test_wraps_around_midnight(self):
        quotes = [quote(100, 23, 50), quote(120, 3, 0)]
        sel = selection.select(quotes, target_time=dt.time(0, 10))
        assert sel.ons_rule.price == Decimal("100")
        assert sel.ons_rule_time_delta_minutes == 20

    def test_ties_broken_by_price_for_determinism(self):
        quotes = [quote(300, 8, 0), quote(150, 10, 0)]
        sel = selection.select(quotes, target_time=dt.time(9, 0))
        assert sel.ons_rule.price == Decimal("150")

    def test_empty(self):
        sel = selection.select([])
        assert sel.ons_rule is None and sel.cheapest is None and sel.n_quotes == 0
        assert sel.spread is None

    def test_untimed_quotes_fall_back_and_flag(self):
        q = FareQuote(price=Decimal("99"), currency="GBP", departure_at=None, return_at=None)
        sel = selection.select([q])
        assert sel.ons_rule is q
        # None signals the ONS rule was not genuinely applied to this row.
        assert sel.ons_rule_time_delta_minutes is None

    def test_single_quote(self):
        sel = selection.select([quote(100, 12)])
        assert sel.ons_rule.price == sel.cheapest.price == Decimal("100")
        assert sel.spread == Decimal("0")


class TestPanel:
    def test_every_route_has_a_valid_haul(self):
        for route in panel.PANEL:
            assert route.haul in ("domestic", "european", "long_haul")

    def test_route_codes_unique(self):
        codes = [r.code for r in panel.PANEL]
        assert len(codes) == len(set(codes))

    def test_iata_codes_well_formed(self):
        for route in panel.PANEL:
            assert len(route.origin) == 3 and route.origin.isupper()
            assert len(route.destination) == 3 and route.destination.isupper()

    def test_all_origins_are_london(self):
        assert {r.origin for r in panel.PANEL} <= {"LHR", "LGW", "STN", "LTN", "LCY"}

    def test_every_route_has_a_rationale(self):
        for route in panel.PANEL:
            assert route.rationale.strip()

    def test_query_count_matches_advance_windows(self):
        expected = sum(len(ADVANCE_MONTHS[r.haul]) for r in panel.PANEL)
        assert panel.expected_queries_per_run() == expected
        assert expected == 8 * 1 + 9 * 2 + 6 * 3

    def test_all_three_hauls_populated(self):
        for haul in ("domestic", "european", "long_haul"):
            assert len(panel.routes_by_haul(haul)) >= 5


class TestWeights:
    def test_placeholder_refused_by_default(self):
        with pytest.raises(ValueError, match="placeholder"):
            panel.load_weights(2026)

    def test_placeholder_allowed_when_forced(self):
        w = panel.load_weights(2026, allow_placeholder=True)
        assert w.is_placeholder
        norm = w.normalised()
        assert pytest.approx(sum(norm.values())) == 1.0


class TestChooseOrdinal:
    def test_exact_index_days(self):
        assert choose_ordinal(dt.date(2026, 8, 11)) == 2
        assert choose_ordinal(dt.date(2026, 8, 18)) == 3

    def test_nearest_candidate_for_other_days(self):
        assert choose_ordinal(dt.date(2026, 8, 9)) == 2
        assert choose_ordinal(dt.date(2026, 8, 21)) == 3

    def test_always_returns_two_or_three(self):
        for day in range(1, 32):
            assert choose_ordinal(dt.date(2026, 8, day)) in (2, 3)


class TestTravelpayoutsParsing:
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

    def _provider(self, payload, status=200):
        session = self.FakeSession(self.FakeResponse(payload, status))
        return TravelpayoutsProvider("tok", session=session), session

    def test_parses_quotes(self):
        payload = {
            "success": True,
            "currency": "gbp",
            "data": [
                {
                    "price": 231,
                    "airline": "BA",
                    "flight_number": 1234,
                    "transfers": 0,
                    "departure_at": "2026-09-08T09:15:00+01:00",
                    "return_at": "2026-09-22T18:00:00+01:00",
                    "found_at": "2026-08-15T10:00:00Z",
                }
            ],
        }
        provider, _ = self._provider(payload)
        result = provider.search(
            origin="LHR",
            destination="AMS",
            departure_date=dt.date(2026, 9, 8),
            return_date=dt.date(2026, 9, 22),
        )
        assert len(result) == 1
        q = result.quotes[0]
        assert q.price == Decimal("231")
        assert q.currency == "GBP"
        assert q.airline == "BA"
        assert q.flight_number == "1234"
        assert q.found_at is not None
        assert result.raw_payload == payload

    def test_sends_return_leg_and_market(self):
        provider, session = self._provider({"success": True, "currency": "gbp", "data": []})
        provider.search(
            origin="LHR",
            destination="JFK",
            departure_date=dt.date(2027, 2, 9),
            return_date=dt.date(2027, 3, 2),
        )
        params = session.last_params
        assert params["one_way"] == "false"
        assert params["return_at"] == "2027-03-02"
        assert params["departure_at"] == "2027-02-09"
        assert params["market"] == "uk"
        assert params["currency"] == "gbp"

    def test_one_way_when_no_return(self):
        provider, session = self._provider({"success": True, "currency": "gbp", "data": []})
        provider.search(
            origin="LHR",
            destination="EDI",
            departure_date=dt.date(2026, 9, 8),
            return_date=None,
        )
        assert session.last_params["one_way"] == "true"
        assert "return_at" not in session.last_params

    def test_empty_data_is_not_an_error(self):
        provider, _ = self._provider({"success": True, "currency": "gbp", "data": []})
        result = provider.search(
            origin="LHR",
            destination="CPT",
            departure_date=dt.date(2027, 2, 9),
            return_date=dt.date(2027, 3, 2),
        )
        assert len(result) == 0

    def test_inband_error_raises(self):
        provider, _ = self._provider({"success": False, "error": "bad token", "data": None})
        with pytest.raises(ProviderError, match="bad token"):
            provider.search(
                origin="LHR", destination="EDI",
                departure_date=dt.date(2026, 9, 8), return_date=None,
            )

    def test_auth_failure_not_retryable(self):
        provider, _ = self._provider({}, status=401)
        with pytest.raises(ProviderError) as exc:
            provider.search(
                origin="LHR", destination="EDI",
                departure_date=dt.date(2026, 9, 8), return_date=None,
            )
        assert exc.value.retryable is False

    def test_rate_limit_is_retryable(self):
        provider, _ = self._provider({}, status=429)
        with pytest.raises(ProviderError) as exc:
            provider.search(
                origin="LHR", destination="EDI",
                departure_date=dt.date(2026, 9, 8), return_date=None,
            )
        assert exc.value.retryable is True

    def test_skips_malformed_and_nonpositive_prices(self):
        payload = {
            "success": True,
            "currency": "gbp",
            "data": [
                {"price": None},
                {"price": "not-a-number"},
                {"price": 0},
                {"price": -5},
                "not-a-dict",
                {"price": 199, "departure_at": "2026-09-08T09:00:00"},
            ],
        }
        provider, _ = self._provider(payload)
        result = provider.search(
            origin="LHR", destination="EDI",
            departure_date=dt.date(2026, 9, 8), return_date=None,
        )
        assert len(result) == 1
        assert result.quotes[0].price == Decimal("199")

    def test_rejects_unsupported_cabin(self):
        provider, _ = self._provider({"success": True, "data": []})
        with pytest.raises(ProviderError, match="cabin"):
            provider.search(
                origin="LHR", destination="JFK",
                departure_date=dt.date(2027, 2, 9), return_date=None,
                cabin="business",
            )

    def test_rejects_multiple_adults(self):
        provider, _ = self._provider({"success": True, "data": []})
        with pytest.raises(ProviderError, match="adults"):
            provider.search(
                origin="LHR", destination="JFK",
                departure_date=dt.date(2027, 2, 9), return_date=None,
                adults=2,
            )

    def test_timeout_is_retryable(self):
        class TimeoutSession:
            def get(self, *a, **k):
                raise requests.Timeout("too slow")

        provider = TravelpayoutsProvider("tok", session=TimeoutSession())
        with pytest.raises(ProviderError) as exc:
            provider.search(
                origin="LHR", destination="EDI",
                departure_date=dt.date(2026, 9, 8), return_date=None,
            )
        assert exc.value.retryable is True

    def test_requires_token(self):
        with pytest.raises(ValueError):
            TravelpayoutsProvider("")


def _dry_config(**over):
    defaults = dict(
        project="p", dataset="d", provider_name="mock", provider_credential=None,
        market="uk", currency="GBP", target_departure_time=dt.time(9, 0),
        failure_threshold=0.34, dry_run=True,
        scrapes_table="airfare_scrapes", index_table="reconstructed_index",
    )
    defaults.update(over)
    return Config(**defaults)


class TestRunPull:
    def test_row_count_matches_panel(self):
        writer = DryRunWriter()
        summary = run_pull(
            _dry_config(),
            scrape_date=dt.date(2026, 8, 11),
            writer=writer,
            provider=MockProvider(haul_of={r.code: r.haul for r in panel.PANEL}),
        )
        assert summary["rows"] == panel.expected_queries_per_run() == 44
        assert summary["ok"] == 44
        assert summary["error"] == 0

    def test_failures_are_recorded_as_rows_not_dropped(self):
        writer = DryRunWriter()
        provider = MockProvider(
            haul_of={r.code: r.haul for r in panel.PANEL},
            fail_routes=frozenset({"LHR-JFK"}),
        )
        summary = run_pull(
            _dry_config(), scrape_date=dt.date(2026, 8, 11), writer=writer,
            provider=provider, backoff=0.0,
        )
        # LHR-JFK is long-haul: 3 windows, so 3 error rows -- but still written.
        assert summary["error"] == 3
        assert summary["rows"] == 44
        errors = [r for r in writer.written if r["status"] == "error"]
        assert len(errors) == 3
        assert all(r["route"] == "LHR-JFK" for r in errors)
        assert all(r["error_message"] for r in errors)

    def test_one_route_failing_does_not_kill_the_run(self):
        writer = DryRunWriter()
        provider = MockProvider(
            haul_of={r.code: r.haul for r in panel.PANEL},
            fail_routes=frozenset({"LHR-EDI"}),
        )
        summary = run_pull(
            _dry_config(), scrape_date=dt.date(2026, 8, 11), writer=writer,
            provider=provider, backoff=0.0,
        )
        assert summary["ok"] == 43

    def test_no_data_recorded_distinctly_from_error(self):
        writer = DryRunWriter()
        provider = MockProvider(
            haul_of={r.code: r.haul for r in panel.PANEL},
            empty_routes=frozenset({"LGW-JER"}),
        )
        summary = run_pull(
            _dry_config(), scrape_date=dt.date(2026, 8, 11), writer=writer,
            provider=provider, backoff=0.0,
        )
        assert summary["no_data"] == 1
        assert summary["error"] == 0
        row = next(r for r in writer.written if r["route"] == "LGW-JER")
        assert row["status"] == "no_data"
        assert row["price_gbp"] is None
        assert row["n_quotes"] == 0

    def test_both_attributions_populated(self):
        writer = DryRunWriter()
        run_pull(
            _dry_config(),
            scrape_date=dt.date(2026, 8, 11),
            writer=writer,
            provider=MockProvider(haul_of={r.code: r.haul for r in panel.PANEL}),
        )
        for row in writer.written:
            assert row["index_month_collection"] == dt.date(2026, 8, 1)
            assert row["index_month_hyp"] == row["index_month_departure"]
            assert row["index_month_departure"] > row["index_month_collection"]

    def test_departures_are_tuesdays_at_correct_windows(self):
        writer = DryRunWriter()
        run_pull(
            _dry_config(),
            scrape_date=dt.date(2026, 8, 11),
            writer=writer,
            provider=MockProvider(haul_of={r.code: r.haul for r in panel.PANEL}),
        )
        for row in writer.written:
            assert row["departure_date"].weekday() == 1, row
            assert row["days_out"] in (30, 90, 180)
            assert row["months_ahead"] in (1, 3, 6)

    def test_return_legs_match_haul(self):
        writer = DryRunWriter()
        run_pull(
            _dry_config(),
            scrape_date=dt.date(2026, 8, 11),
            writer=writer,
            provider=MockProvider(haul_of={r.code: r.haul for r in panel.PANEL}),
        )
        expected = {"domestic": 7, "european": 14, "long_haul": 21}
        for row in writer.written:
            gap = (row["return_date"] - row["departure_date"]).days
            assert gap == expected[row["haul_category"]]

    def test_ons_rule_price_can_exceed_cheapest(self):
        writer = DryRunWriter()
        run_pull(
            _dry_config(),
            scrape_date=dt.date(2026, 8, 11),
            writer=writer,
            provider=MockProvider(haul_of={r.code: r.haul for r in panel.PANEL}),
        )
        priced = [r for r in writer.written if r["price_gbp"] is not None]
        assert priced
        assert all(r["price_gbp"] >= r["price_cheapest_gbp"] for r in priced)
        # The two rules must actually differ somewhere, or the distinction is moot.
        assert any(r["price_gbp"] > r["price_cheapest_gbp"] for r in priced)
