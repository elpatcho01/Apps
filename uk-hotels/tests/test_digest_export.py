"""Digest and export tests.

The digest's most important property is not what it says when everything works.
It is what it says when it cannot see the data -- see trap 5.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

import pytest

from ukhotels import digest, export
from tests.test_pipeline import make_config


class FailingReader:
    """Every query raises, as a missing table or view would."""

    def __init__(self, exc_name="NotFound"):
        self._exc = type(exc_name, (Exception,), {})

    def query(self, sql, params=None):
        raise self._exc("Not found: Table p:d.current_scrapes")


class EmptyReader:
    def query(self, sql, params=None):
        return []


class HealthyReader:
    def query(self, sql, params=None):
        if "days_collected" in sql:
            return [{
                "days_collected": 3, "runs": 3, "stay_nights": 2,
                "ok": 300, "no_data": 2, "errors": 0,
                "first_day": dt.date(2026, 6, 30), "last_day": dt.date(2026, 7, 9),
            }]
        if "dropped_rate_basis" in sql:
            return [{
                "location": "london", "property_tier": "upscale", "ok": 40,
                "properties": 8, "avg_rate_gbp": 210, "avg_cheapest_gbp": 180,
                "pct_above_cheapest": 16.7, "spread_ratio": 1.8,
                "returned": 20.0, "considered": 8.0, "dropped_rate_basis": 5.0,
            }]
        if "churn_status" in sql and "COUNT(*)" in sql:
            return [{"churn_status": "present", "properties": 90,
                     "avg_presence_rate": 0.95}]
        return []


def build(reader, config=None):
    return digest.build_digest(
        reader,
        config or make_config(),
        period_start=dt.date(2026, 7, 1),
        period_end=dt.date(2026, 7, 31),
        generated=dt.datetime(2026, 8, 2, tzinfo=dt.timezone.utc),
    )


def test_a_digest_is_produced_even_when_every_query_fails():
    # A digest that says "unavailable" is useful; a digest that failed to
    # generate is not -- and it would not reset the 60-day inactivity clock.
    text = build(FailingReader())
    assert "Accommodation digest" in text
    assert "unavailable" in text


def test_a_report_that_cannot_see_the_data_never_says_the_data_is_fine():
    # Trap 5, exactly. The first live digest on the sibling project printed
    # "Nothing flagged. Collection healthy." underneath two sections reading
    # "unavailable: NotFound", because the health checks sat inside the success
    # branch. An unanswered question is itself a concern.
    text = build(FailingReader())
    assert "Nothing flagged" not in text
    assert "Could not read collection health" in text


def test_a_missing_table_hints_at_the_self_healing_fix():
    text = build(FailingReader())
    assert "ensure_tables" in text


def test_no_data_at_all_flags_the_60_day_trap():
    text = build(EmptyReader())
    assert "No data collected this period" in text
    assert "60 days" in text


def test_missed_collection_days_are_flagged_as_unrecoverable():
    text = build(EmptyReader())
    assert "Needs attention" in text


def test_a_wide_comparable_spread_is_flagged():
    class WideReader(HealthyReader):
        def query(self, sql, params=None):
            rows = super().query(sql, params)
            if rows and "spread_ratio" in rows[0]:
                rows[0]["spread_ratio"] = 9.4
            return rows

    text = build(WideReader())
    assert "comparability filter may be letting a" in text


def test_standing_limitations_are_restated_every_month():
    # They do not resolve on their own, and a reader coming back after eight
    # weeks needs them next to the numbers rather than one click away.
    text = build(HealthyReader())
    assert "Board basis and room type are unknown" in text
    assert "Regional weights are placeholders" in text
    assert "two methodology breaks" in text


def test_the_collection_lag_is_explained_at_the_top():
    text = build(HealthyReader())
    assert "six weeks later" in text


def test_digest_writes_a_file_named_for_its_month(tmp_path):
    path = digest.run_digest(
        make_config(), month=dt.date(2026, 7, 1), reader=EmptyReader(), out_dir=tmp_path
    )
    assert path.name == "2026-07.md"
    assert path.read_text(encoding="utf-8")


# --- export -----------------------------------------------------------------


def test_export_degrades_per_section_and_records_the_failure():
    data = export.build_export(FailingReader(), make_config())
    assert set(data["errors"]) >= {"coverage", "published_series", "reconstructions"}
    assert data["reconstructions"] == []


def test_failed_sections_keep_the_type_their_success_path_produces():
    # A consumer doing coverage["panel_rows"] should get a KeyError, not a
    # TypeError -- the latter reads like a code bug rather than missing data.
    data = export.build_export(FailingReader(), make_config())
    assert isinstance(data["coverage"], dict)
    assert isinstance(data["daily_by_region"], list)


def test_export_records_the_methodology_it_was_collected_under():
    # Without these the numbers are uninterpretable: a free-cancellation
    # advertised-rate series is not the same series as a non-refundable
    # before-tax one, and nothing else in the file would say which this is.
    data = export.build_export(EmptyReader(), make_config(rate_basis="non_refundable"))
    assert data["methodology"]["rate_basis"] == "non_refundable"
    assert data["methodology"]["advance_days"] == 42
    assert "unknown" in data["methodology"]["board_basis"]


def test_export_is_byte_identical_when_nothing_changed(tmp_path):
    # Key ordering must not manufacture a diff, or the digest workflow commits
    # noise every month.
    config = make_config()
    generated = dt.datetime(2026, 8, 2, tzinfo=dt.timezone.utc)
    first = export.build_export(EmptyReader(), config, generated=generated)
    second = export.build_export(EmptyReader(), config, generated=generated)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_export_writes_valid_json(tmp_path):
    out = tmp_path / "analytics.json"
    export.run_export(make_config(), reader=EmptyReader(), out_path=out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == export.SCHEMA_VERSION
