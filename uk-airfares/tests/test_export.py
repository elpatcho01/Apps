"""The export's job is to be readable outside BigQuery, and to always land.

Two properties are load-bearing:

  * **It never raises.** A section that fails becomes an `errors` entry. A
    partial export you can chart is worth more than a failed one you cannot.
  * **It is JSON-clean.** BigQuery hands back `datetime.date` and
    `decimal.Decimal`, both of which `json.dumps` refuses. A TypeError here
    would only surface in production, since the fake readers in every other test
    file hand back plain Python.
"""

import datetime as dt
import json
from decimal import Decimal

import pytest

from ukairfares.config import Config
from ukairfares.export import SCHEMA_VERSION, build_export, run_export


def _config(**over):
    defaults = dict(
        project="proj", dataset="ds", provider_name="mock", provider_credential=None,
        market="uk", currency="GBP", target_departure_time=dt.time(9, 0),
        failure_threshold=0.34, dry_run=True,
        scrapes_table="airfare_scrapes", index_table="reconstructed_index",
    )
    defaults.update(over)
    return Config(**defaults)


class FakeReader:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.queries = []

    def query(self, sql, params=None):
        self.queries.append(sql)
        for fragment, value in self.responses.items():
            if fragment in sql:
                if isinstance(value, Exception):
                    raise value
                return value
        return []


# Deliberately BigQuery-shaped: dates, Decimals and a timestamp, all of which
# json.dumps rejects untouched.
LIVE_SHAPED = {
    "panel_rows": [dict(
        panel_rows=176, panel_days=1,
        panel_first_day=dt.date(2026, 8, 17), panel_last_day=dt.date(2026, 8, 17),
        published_values=678,
        published_first=dt.date(2016, 1, 1), published_last=dt.date(2026, 2, 1),
    )],
    "WHERE is_current AND index_value IS NOT NULL": [
        dict(index_month=dt.date(2019, 6, 1), haul_category="european",
             months_ahead=1, index_value=224.92, basis="annual_january_100"),
    ],
    "GROUP BY 1, 2, 3": [
        dict(scrape_date=dt.date(2026, 8, 17), haul_category="domestic", months_ahead=1,
             ok=8, no_data=0, errors=0, mean_price_gbp=122.0, median_price_gbp=110.0,
             geomean_price_gbp=118.4, mean_cheapest_gbp=91.0, mean_mins_off_target=47.0,
             mean_quotes=7.6, mean_considered=3.1),
    ],
    "WHERE scrape_date = (SELECT d FROM latest)": [
        dict(scrape_date=dt.date(2026, 8, 17), route="LHR-EDI", haul_category="domestic",
             months_ahead=1, departure_date=dt.date(2026, 9, 8),
             return_date=dt.date(2026, 9, 15), days_out_actual=22, status="ok",
             price_gbp=Decimal("122.00"), price_cheapest_gbp=Decimal("91.00"),
             selected_airline="British Airways",
             selected_departure_ts=dt.datetime(2026, 9, 8, 8, 55),
             target_departure_time="09:00:00", ons_rule_time_delta_minutes=5,
             n_quotes=8, n_quotes_considered=3, candidate_basis="direct_only",
             error_message=None),
    ],
}


class TestAlwaysLands:
    def test_every_section_failing_still_produces_an_export(self):
        data = build_export(FakeReader({"": RuntimeError("bigquery is down")}), _config())
        assert data["schema_version"] == SCHEMA_VERSION
        assert set(data["errors"]) == {
            "coverage", "published_series", "daily_by_series",
            "latest_routes", "reconstructions",
        }
        assert data["published_series"] == []

    def test_one_section_failing_does_not_lose_the_others(self):
        responses = dict(LIVE_SHAPED, **{"GROUP BY 1, 2, 3": RuntimeError("nope")})
        data = build_export(FakeReader(responses), _config())
        assert "daily_by_series" in data["errors"]
        assert data["published_series"], "the healthy section should still be present"
        assert "published_series" not in data["errors"]

    def test_empty_warehouse_is_not_an_error(self):
        data = build_export(FakeReader(), _config())
        assert data["errors"] == {}
        assert data["coverage"] == {}


class TestJsonSafety:
    """BigQuery returns dates and Decimals; json.dumps refuses both."""

    def test_serialises_dates_and_decimals(self):
        data = build_export(FakeReader(LIVE_SHAPED), _config())
        text = json.dumps(data)  # must not raise
        assert '"2026-08-17"' in text
        assert "122.0" in text

    def test_decimal_becomes_a_number_not_a_string(self):
        data = build_export(FakeReader(LIVE_SHAPED), _config())
        price = data["latest_routes"][0]["price_gbp"]
        assert isinstance(price, float) and price == 122.0

    def test_timestamp_becomes_iso(self):
        data = build_export(FakeReader(LIVE_SHAPED), _config())
        assert data["latest_routes"][0]["selected_departure_ts"].startswith("2026-09-08T")


class TestShape:
    def test_coverage_is_unwrapped_to_an_object(self):
        """Consumers should not have to index into a one-element list."""
        data = build_export(FakeReader(LIVE_SHAPED), _config())
        assert isinstance(data["coverage"], dict)
        assert data["coverage"]["published_values"] == 678

    def test_panel_sections_read_the_view_not_the_raw_table(self):
        """Superseded runs must be excluded, exactly as reconciliation does."""
        reader = FakeReader(LIVE_SHAPED)
        build_export(reader, _config())
        panel = [q for q in reader.queries if "current_scrapes" in q or "airfare_scrapes" in q]
        assert panel
        for sql in panel:
            assert "proj.ds.current_scrapes" in sql
            assert "proj.ds.airfare_scrapes" not in sql

    def test_carries_provenance(self):
        data = build_export(FakeReader(LIVE_SHAPED), _config())
        for key in ("generated_ts", "pipeline_version", "project", "dataset"):
            assert data[key], f"{key} missing — a stale export must be recognisable"


class TestRunExport:
    def test_writes_valid_json(self, tmp_path):
        out = tmp_path / "nested" / "analytics.json"
        path = run_export(_config(), reader=FakeReader(LIVE_SHAPED), out_path=out)
        loaded = json.loads(path.read_text())
        assert loaded["coverage"]["panel_rows"] == 176

    def test_unchanged_data_produces_byte_identical_output(self, tmp_path):
        """Otherwise key ordering manufactures a commit every single month."""
        first = run_export(_config(), reader=FakeReader(LIVE_SHAPED),
                           out_path=tmp_path / "a.json").read_bytes()
        second = run_export(_config(), reader=FakeReader(LIVE_SHAPED),
                            out_path=tmp_path / "b.json").read_bytes()
        # generated_ts differs by design; everything else must match.
        strip = lambda b: [l for l in b.splitlines() if b"generated_ts" not in l]  # noqa: E731
        assert strip(first) == strip(second)

    def test_main_warns_but_succeeds_on_partial_export(self, tmp_path, monkeypatch, capsys):
        import ukairfares.export as export

        monkeypatch.setenv("GCP_PROJECT", "proj")
        monkeypatch.setenv("BQ_DATASET", "ds")
        monkeypatch.setenv("DRY_RUN", "1")
        monkeypatch.setattr(
            export, "run_export",
            lambda config, out_path=None: _write_partial(out_path or tmp_path / "x.json"),
        )
        assert export.main(["--out", str(tmp_path / "x.json")]) == 0
        assert "::warning::" in capsys.readouterr().out


def _write_partial(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"errors": {"daily_by_series": "NotFound: no view"}}))
    return path
