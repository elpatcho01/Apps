"""The digest's contract is that it always produces a report.

Every test here is really the same test from a different angle: given a broken
BigQuery, an empty BigQuery, or a healthy one, `build_digest` returns Markdown.
It never raises. That matters because the digest's second job is to be committed
-- and a digest that fails to generate does not get committed, which means the
60-day inactivity clock keeps running and the schedules get disabled.
"""

import datetime as dt
import pathlib

import pytest

from ukairfares.config import Config
from ukairfares.digest import build_digest, run_digest

AUGUST = dt.date(2026, 8, 1)
AUGUST_END = dt.date(2026, 8, 31)


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
    """Answers queries by matching on a distinctive fragment of each SQL body."""

    def __init__(self, responses: dict[str, object] | None = None):
        self.responses = responses or {}
        self.queries: list[str] = []

    def query(self, sql, params=None):
        self.queries.append(sql)
        for fragment, value in self.responses.items():
            if fragment in sql:
                if isinstance(value, Exception):
                    raise value
                return value
        return []


def _health(**over):
    row = dict(
        days_collected=14, runs=14, ok=600, no_data=10, errors=6,
        first_day=dt.date(2026, 8, 8), last_day=dt.date(2026, 8, 21),
    )
    row.update(over)
    return [row]


def _series(**over):
    row = dict(
        haul_category="domestic", months_ahead=1, ok=100, no_data=2, errors=0,
        flights_seen=7.6, considered=3.1, mins_off_target=47,
        avg_price_gbp=122, pct_above_cheapest=34.0,
    )
    row.update(over)
    return [row]


# Fragments are matched in insertion order, and several of these queries share
# vocabulary -- `latest_recon` must be tried before the looser reconstruction
# fragment, or validate's scoring query gets answered with digest rows.
HEALTHY = {
    "latest_recon": [],
    "days_collected": _health(),
    "GROUP BY 1, 2\nORDER BY 1, 2": _series(),
    "WHERE is_current": [
        dict(index_month=dt.date(2026, 8, 1), haul_category="domestic", months_ahead=1,
             reconstructed_value=104.2, published_ons_value=None, n_observations=8,
             index_day_exact=True, index_day_offset_days=0),
    ],
    # last_month must cover the reported period, or the no-overlap concern fires
    # and this stops being a clean fixture. In reality it does not cover it yet
    # (ONS publish the ad hoc series with a long lag) -- see TestConcerns.
    "COUNT(DISTINCT index_month) AS months": [
        dict(values_=678, months=113, first_month=dt.date(2016, 1, 1),
             last_month=dt.date(2026, 8, 1), basis="annual_january_100"),
    ],
}


class TestAlwaysProducesAReport:
    def test_every_query_failing_still_renders(self):
        reader = FakeReader({"": RuntimeError("bigquery is on fire")})
        text = build_digest(reader, _config(), period_start=AUGUST, period_end=AUGUST_END)
        assert text.startswith("# Air fares digest — August 2026")
        assert "unavailable" in text
        assert "## Needs attention" in text

    def test_empty_warehouse_renders_and_flags(self):
        text = build_digest(FakeReader(), _config(),
                            period_start=AUGUST, period_end=AUGUST_END)
        assert "_No observations in this period._" in text
        assert "No data collected this period" in text
        assert "Published ONS series not loaded" in text

    def test_healthy_warehouse_flags_nothing(self):
        text = build_digest(FakeReader(HEALTHY), _config(),
                            period_start=AUGUST, period_end=AUGUST_END)
        assert "Nothing flagged" in text
        assert "14 days** collected" in text

    def test_a_report_that_cannot_see_the_data_never_says_healthy(self):
        """The bug the first live digest shipped with.

        `current_scrapes` did not exist yet, so every panel query raised NotFound
        and the health checks never got the chance to notice anything wrong. The
        report printed "Nothing flagged. Collection healthy." under two sections
        reading "unavailable: NotFound" -- the precise misreading the digest is
        supposed to prevent.
        """
        reader = FakeReader({"": RuntimeError("NotFound")})
        text = build_digest(reader, _config(),
                            period_start=AUGUST, period_end=AUGUST_END)
        assert "Nothing flagged" not in text
        assert "Collection healthy" not in text
        assert "Could not read collection health" in text

    def test_notfound_carries_the_self_healing_hint(self):
        class NotFound(Exception):
            pass

        reader = FakeReader({"days_collected": NotFound("no such view")})
        text = build_digest(reader, _config(),
                            period_start=AUGUST, period_end=AUGUST_END)
        assert "clears itself on the next collection run" in text

    def test_reports_no_overlap_honestly(self):
        """With no backfilled published values, the verdict must not be a pass."""
        text = build_digest(FakeReader(HEALTHY), _config(),
                            period_start=AUGUST, period_end=AUGUST_END)
        assert "**Verdict: INSUFFICIENT_DATA**" in text

    def test_validation_failure_is_contained(self):
        # run_validate issues its own query through the same reader; a failure
        # there must not take the report with it.
        reader = FakeReader(dict(HEALTHY, **{"latest_recon": RuntimeError("no")}))
        text = build_digest(reader, _config(), period_start=AUGUST, period_end=AUGUST_END)
        assert "_Validation unavailable: RuntimeError_" in text
        assert "## Needs attention" in text


class TestConcerns:
    def _concerns(self, text):
        after = text.split("## Needs attention", 1)[1]
        return [l for l in after.splitlines() if l.startswith("- ")]

    def test_high_error_rate_flagged(self):
        responses = dict(HEALTHY, **{"days_collected": _health(ok=50, errors=50)})
        text = build_digest(FakeReader(responses), _config(),
                            period_start=AUGUST, period_end=AUGUST_END)
        assert any("Error rate" in c for c in self._concerns(text))

    def test_few_collection_days_flagged(self):
        responses = dict(HEALTHY, **{"days_collected": _health(days_collected=3)})
        text = build_digest(FakeReader(responses), _config(),
                            period_start=AUGUST, period_end=AUGUST_END)
        assert any("collection days" in c for c in self._concerns(text))

    def test_selection_drift_flagged(self):
        """The £1,244 domestic fare regression, caught automatically this time."""
        responses = dict(HEALTHY, **{
            "GROUP BY 1, 2\nORDER BY 1, 2": _series(pct_above_cheapest=882.3),
        })
        text = build_digest(FakeReader(responses), _config(),
                            period_start=AUGUST, period_end=AUGUST_END)
        concerns = self._concerns(text)
        assert any("above cheapest" in c for c in concerns)

    def test_target_time_drift_flagged(self):
        responses = dict(HEALTHY, **{
            "GROUP BY 1, 2\nORDER BY 1, 2": _series(mins_off_target=400),
        })
        text = build_digest(FakeReader(responses), _config(),
                            period_start=AUGUST, period_end=AUGUST_END)
        assert any("minutes from" in c for c in self._concerns(text))

    def _published(self, last_month):
        return dict(HEALTHY, **{
            "COUNT(DISTINCT index_month) AS months": [
                dict(values_=678, months=113, first_month=dt.date(2016, 1, 1),
                     last_month=last_month, basis="annual_january_100"),
            ],
        })

    def test_six_month_gap_is_flagged(self):
        """The live case: published ends 2026-02, we are reporting on 2026-08.

        A `> 6` threshold let exactly this through on the first real digest.
        """
        text = build_digest(FakeReader(self._published(dt.date(2026, 2, 1))), _config(),
                            period_start=AUGUST, period_end=AUGUST_END)
        concerns = self._concerns(text)
        assert any("ends 2026-02, 6 month(s)" in c for c in concerns)

    def test_any_gap_is_flagged(self):
        text = build_digest(FakeReader(self._published(dt.date(2026, 7, 1))), _config(),
                            period_start=AUGUST, period_end=AUGUST_END)
        assert any("1 month(s) before this period" in c for c in self._concerns(text))

    def test_gap_explains_that_no_action_is_needed(self):
        """It resolves when ONS publish, not through anything in this repo."""
        text = build_digest(FakeReader(self._published(dt.date(2026, 2, 1))), _config(),
                            period_start=AUGUST, period_end=AUGUST_END)
        assert "no action is needed here" in text

    def test_published_series_covering_the_period_is_not_flagged(self):
        text = build_digest(FakeReader(self._published(AUGUST)), _config(),
                            period_start=AUGUST, period_end=AUGUST_END)
        assert not any("Published ONS series ends" in c for c in self._concerns(text))


class TestQueriesUseTheView:
    def test_collection_reads_current_scrapes_not_raw_table(self):
        """Reading airfare_scrapes directly averages across superseded runs."""
        reader = FakeReader(HEALTHY)
        build_digest(reader, _config(), period_start=AUGUST, period_end=AUGUST_END)
        panel_queries = [q for q in reader.queries if "scrape_date BETWEEN" in q]
        assert panel_queries
        for sql in panel_queries:
            assert "proj.ds.current_scrapes" in sql
            assert "proj.ds.airfare_scrapes" not in sql


class TestRunDigest:
    def test_writes_previous_month_by_default(self, tmp_path, monkeypatch):
        import ukairfares.digest as digest

        class FixedDate(dt.date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 3)

        monkeypatch.setattr(digest.dt, "date", FixedDate)
        path = run_digest(_config(), reader=FakeReader(HEALTHY), out_dir=tmp_path)
        assert path == tmp_path / "2026-08.md"
        assert "August 2026" in path.read_text()

    def test_explicit_month_and_window(self, tmp_path):
        reader = FakeReader(HEALTHY)
        path = run_digest(_config(), month=dt.date(2026, 2, 14),
                          reader=reader, out_dir=tmp_path)
        assert path == tmp_path / "2026-02.md"
        text = path.read_text()
        # February 2026 is not a leap year; the window must end on the 28th.
        assert "`2026-02-01` to `2026-02-28`" in text

    def test_creates_missing_directory(self, tmp_path):
        out = tmp_path / "nested" / "reports"
        path = run_digest(_config(), month=AUGUST, reader=FakeReader(), out_dir=out)
        assert path.exists()

    def test_rerun_overwrites_rather_than_appending(self, tmp_path):
        for _ in range(2):
            path = run_digest(_config(), month=AUGUST,
                              reader=FakeReader(HEALTHY), out_dir=tmp_path)
        assert path.read_text().count("# Air fares digest") == 1
