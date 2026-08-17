"""Credential-redaction tests.

`raw_payload` is written verbatim to `raw_response` in an append-only table with
no delete path, and is uploaded as a workflow artifact by the provider smoke
test. A credential that reaches either is effectively unrotatable-in-place, so
these tests treat the scrub as load-bearing rather than defensive.
"""

from __future__ import annotations

import json

from ukhotels.providers.serpapi_hotels import redact

KEY = "sk_live_abc123DEADBEEF"


def test_credential_keys_are_dropped_at_any_depth():
    payload = {
        "api_key": KEY,
        "search_parameters": {"api_key": KEY, "q": "London, UK"},
        "properties": [{"name": "A", "meta": {"serpapi_api_key": KEY}}],
    }
    out = redact(payload, KEY)
    assert KEY not in json.dumps(out)
    assert out["search_parameters"]["q"] == "London, UK"


def test_the_key_is_scrubbed_out_of_urls_where_it_is_not_a_key_value_pair():
    # The case a key-name filter alone would miss entirely.
    payload = {"search_metadata": {
        "json_endpoint": f"https://serpapi.com/searches/x.json?api_key={KEY}&q=London"
    }}
    out = redact(payload, KEY)
    assert KEY not in json.dumps(out)
    assert "q=London" in out["search_metadata"]["json_endpoint"]


def test_ordinary_data_is_untouched():
    payload = {
        "properties": [
            {"name": "Hotel One", "hotel_class": 4.0,
             "rate_per_night": {"extracted_lowest": 120}}
        ]
    }
    assert redact(payload, KEY) == payload


def test_redaction_is_safe_when_no_secret_is_supplied():
    payload = {"api_key": "whatever", "name": "Hotel"}
    out = redact(payload, None)
    # The key-name rule still applies; only the substring rule needs a secret.
    assert out["api_key"] == "<redacted>"
    assert out["name"] == "Hotel"


def test_lists_and_scalars_round_trip():
    assert redact([1, "a", None, True], KEY) == [1, "a", None, True]
    assert redact(None, KEY) is None
    assert redact(42, KEY) == 42


def test_the_provider_redacts_before_the_payload_escapes(monkeypatch):
    # Guards the wiring, not just the helper: a correct `redact` that is never
    # called is exactly as leaky as no `redact` at all.
    import datetime as dt

    from ukhotels.providers.serpapi_hotels import SerpApiHotelsProvider

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "search_parameters": {"api_key": KEY},
                "properties": [{
                    "property_token": "t1", "name": "Hotel One", "type": "hotel",
                    "hotel_class": 4, "free_cancellation": True,
                    "rate_per_night": {"extracted_lowest": 120,
                                       "extracted_before_taxes_fees": 100},
                }],
            }

    class FakeSession:
        def get(self, *args, **kwargs):
            return FakeResponse()

    provider = SerpApiHotelsProvider(KEY, session=FakeSession())
    result = provider.search(
        query="London, UK",
        check_in=dt.date(2026, 10, 13),
        check_out=dt.date(2026, 10, 14),
    )
    assert KEY not in json.dumps(result.raw_payload)
    # And the observation itself still parsed.
    assert len(result.quotes) == 1
    assert result.quotes[0].property_token == "t1"
    # The per-quote `raw` is a slice of the redacted payload, so it is clean too.
    assert KEY not in json.dumps(result.quotes[0].raw)
