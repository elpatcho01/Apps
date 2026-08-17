"""End-to-end collection tests against the mock provider. No network."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json

import pytest

from ukhotels import bq, onscal, panel, pull
from ukhotels.config import Config, ConfigError
from ukhotels.providers.mock import MockProvider


def make_config(**overrides):
    base = dict(
        project="p",
        dataset="d",
        provider_name="mock",
        provider_credential=None,
        market="uk",
        currency="GBP",
        rate_basis="free_cancellation",
        tax_basis="advertised",
        collection_alignment="per_night",
        advance_days=42,
        max_price_ratio=5.0,
        min_properties_per_cell=3,
        failure_threshold=0.34,
        dry_run=True,
        scrapes_table="accommodation_scrapes",
        index_table="reconstructed_index",
    )
    base.update(overrides)
    return Config(**base)


DUE_DAY = dt.date(2026, 8, 11) - dt.timedelta(days=42)  # 2026-06-30


def run(provider=None, **config_overrides):
    writer = bq.DryRunWriter()
    summary = pull.run_pull(
        make_config(**config_overrides),
        scrape_date=DUE_DAY,
        writer=writer,
        provider=provider or MockProvider(),
        backoff=0,
    )
    return summary, writer.written


def test_a_due_day_prices_every_location():
    summary, rows = run()
    assert summary["cells"] == len(panel.LOCATIONS)
    assert {r["location"] for r in rows} == {loc.code for loc in panel.LOCATIONS}
    assert summary["error"] == 0


def test_a_day_with_nothing_due_collects_nothing_and_is_not_an_error():
    # The collection calendar is sparse by construction -- four to six days per
    # CPI month -- so an idle scheduled run is the expected case.
    writer = bq.DryRunWriter()
    summary = pull.run_pull(
        make_config(),
        scrape_date=dt.date(2026, 6, 15),
        writer=writer,
        provider=MockProvider(),
        backoff=0,
    )
    assert summary["cells"] == 0
    assert writer.written == []


def test_stay_night_is_six_weeks_ahead_and_one_night_long():
    _, rows = run()
    for row in rows:
        assert (row["check_in"] - row["scrape_date"]).days == 42
        assert (row["check_out"] - row["check_in"]).days == 1


def test_collection_precedes_the_cpi_month_it_measures():
    # The single most confusing property of this panel, asserted so it cannot
    # regress into the air-fares shape.
    _, rows = run()
    for row in rows:
        assert row["scrape_date"] < row["index_month_stay"]


def test_attribution_hypotheses_are_both_stored_and_differ():
    _, rows = run()
    for row in rows:
        assert row["index_month_stay"] == dt.date(2026, 8, 1)
        assert row["index_month_collection"] == dt.date(2026, 6, 1)


def test_only_comparable_properties_are_written():
    # The mock returns vacation rentals, unrated and five-star properties
    # deliberately. None of them may reach the panel.
    _, rows = run()
    ok = [r for r in rows if r["status"] == "ok"]
    assert ok
    for row in ok:
        assert row["property_type"] == "hotel"
        assert row["property_tier"] in panel.STAR_TIERS
        assert row["free_cancellation"] is True


def test_every_row_records_what_could_not_be_controlled_for():
    _, rows = run()
    for row in (r for r in rows if r["status"] == "ok"):
        assert row["board_basis"] is None
        assert row["room_type"] is None
        assert "board=unknown" in row["comparability_basis"]


def test_both_tax_bases_are_stored_regardless_of_which_is_headline():
    _, advertised = run(tax_basis="advertised")
    _, before = run(tax_basis="before_taxes")
    a = next(r for r in advertised if r["status"] == "ok")
    b = next(
        r for r in before
        if r["status"] == "ok" and r["property_token"] == a["property_token"]
        and r["location"] == a["location"]
    )
    assert a["price_gbp"] > b["price_gbp"]
    # The other basis is on the row either way, so a series collected under one
    # can be recomputed under the other without re-collecting.
    assert a["price_before_taxes_gbp"] == b["price_gbp"]
    assert a["tax_basis"] == "advertised" and b["tax_basis"] == "before_taxes"


def test_a_failed_location_is_written_as_a_row_not_omitted():
    # An absent row and a failed row are different facts, and only one of them
    # is recoverable later.
    provider = MockProvider(fail_locations=frozenset({"Manchester, UK"}))
    summary, rows = run(provider)
    errors = [r for r in rows if r["status"] == "error"]
    assert len(errors) == 1
    assert errors[0]["location"] == "north_west"
    assert errors[0]["error_message"]
    assert errors[0]["price_gbp"] is None
    assert summary["cell_failures"] == 1


def test_an_empty_location_is_no_data_not_an_error():
    provider = MockProvider(empty_locations=frozenset({"Cardiff, UK"}))
    _, rows = run(provider)
    empty = [r for r in rows if r["status"] == "no_data"]
    assert len(empty) == 1
    assert empty[0]["location"] == "wales"


def test_failure_rate_is_measured_over_cells_not_rows():
    # A successful cell yields several rows and a failed one yields exactly one,
    # so a row-based rate would report a half-failed run as a ~10% failure and
    # slip under the threshold.
    failing = frozenset({loc.query for loc in panel.LOCATIONS[:6]})
    summary, rows = run(MockProvider(fail_locations=failing))
    assert summary["cell_failures"] == 6
    assert summary["failure_rate"] == pytest.approx(0.5)
    row_rate = summary["error"] / summary["rows"]
    assert row_rate < 0.34 < summary["failure_rate"]


def test_all_comparable_but_filtered_out_is_no_data_with_an_explanation():
    # "Properties returned but none comparable" is not the same as "no
    # properties", and conflating them would hide a filter that had become too
    # strict.
    _, rows = run(rate_basis="non_refundable", max_price_ratio=1.0)
    no_data = [r for r in rows if r["status"] == "no_data"]
    for row in no_data:
        assert row["n_quotes"] > 0
        assert "none comparable" in (row["error_message"] or "")


def test_mock_data_cannot_reach_a_real_table():
    config = make_config(dry_run=False)
    with pytest.raises(ConfigError, match="refusing to write mock"):
        pull.run_pull(
            config,
            scrape_date=DUE_DAY,
            writer=bq.DryRunWriter(),
            provider=MockProvider(),
            backoff=0,
        )


def test_rows_are_json_serialisable_as_bigquery_would_receive_them():
    _, rows = run()
    payload = json.dumps(rows, default=bq._json_default)
    assert json.loads(payload)


def test_raw_payload_is_retained_once_per_location_call():
    # Retained so observations can be re-scored under a different comparability
    # rule without re-querying, but not repeated on every property row.
    _, rows = run()
    with_raw = [r for r in rows if r["raw_response"]]
    assert len(with_raw) == len(panel.LOCATIONS)


def test_property_churn_shows_up_as_a_missing_property_not_a_price_move():
    _, before = run()
    token = next(r["property_token"] for r in before if r["status"] == "ok")
    _, after = run(MockProvider(drop_tokens=frozenset({token})))
    assert token not in {r["property_token"] for r in after}
    # The remaining properties are untouched -- churn must not perturb them.
    kept = {
        (r["location"], r["property_token"]): r["price_gbp"]
        for r in after if r["status"] == "ok"
    }
    for row in before:
        key = (row["location"], row["property_token"])
        if row["status"] == "ok" and key in kept:
            assert kept[key] == row["price_gbp"]
