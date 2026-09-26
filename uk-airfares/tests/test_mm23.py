"""Reading ONS's published monthly series rather than quoting them at second hand.

This project spent three analyses quoting air fare figures out of bulletin prose
-- +6.2%, +22.2%, +2.1%, -34.8%, -28.8% -- none of which it could verify, because
the only ONS data it loaded was an annual ad hoc release, rebased to January=100
every year and lagged to February 2026. The rebase severs year-on-year by
construction, so those series can describe seasonality and nothing else.

These tests pin the fetcher that closes that gap, and in particular pin the
failure behaviour: when the payload is not the shape we expect it must SAY SO.
The bank measurement searched live payloads for a key the provider does not use,
found nothing, reported nothing, and went green -- wrong rather than empty. A
fetcher that returns zero rows on an unrecognised payload repeats that.
"""

import datetime as dt
import json

import pytest
import requests

from ukairfares import bq, mm23
from ukairfares.mm23 import BY_CDID, SERIES, Mm23Error, _period, build_rows, parse_series


def _payload(*pairs, **extra):
    months = [{"date": d, "value": v, "month": d.split()[1], "year": d.split()[0]}
              for d, v in pairs]
    return {"description": {"title": "CPI INDEX 07.3.3"}, "months": months, **extra}


class TestTheRegistry:
    def test_every_series_is_a_valid_four_character_cdid(self):
        for s in SERIES:
            assert len(s.cdid) == 4 and s.cdid.isalnum() and s.cdid.isupper()

    def test_the_url_is_the_mm23_data_endpoint(self):
        assert BY_CDID["D7EH"].url == (
            "https://www.ons.gov.uk/economy/inflationandpriceindices/"
            "timeseries/d7eh/mm23/data")

    def test_kinds_are_declared_so_an_index_cannot_be_averaged_with_a_rate(self):
        """D7EH is a level, D7MB a percentage. Nothing in the numbers says which."""
        assert {s.kind for s in SERIES} <= {"index", "monthly_rate", "annual_rate", "weight"}
        assert BY_CDID["D7EH"].kind == "index"
        assert BY_CDID["D7MB"].kind == "monthly_rate"
        assert BY_CDID["CJXW"].kind == "weight"

    def test_both_measures_are_covered_and_labelled(self):
        assert BY_CDID["D7EH"].measure == "cpi"
        assert BY_CDID["CHBR"].measure == "rpi"

    def test_the_rpi_subgroups_needed_to_isolate_air_are_present(self):
        """Air sits inside 'other travel costs'; rail and bus are what must come out."""
        for cdid in ("CHBR", "DOCW", "DOCX", "DOCY", "CZHM"):
            assert cdid in BY_CDID, cdid

    def test_every_series_records_why_it_is_fetched(self):
        for s in SERIES:
            assert len(s.why) > 30, s.cdid


class TestDateParsing:
    @pytest.mark.parametrize("raw,expected", [
        ("2026 AUG", dt.date(2026, 8, 1)),
        ("2026 August", dt.date(2026, 8, 1)),
        ("AUG 2026", dt.date(2026, 8, 1)),
        ("2026-08-01", dt.date(2026, 8, 1)),
        ("2026-08-16", dt.date(2026, 8, 1)),
    ])
    def test_the_spellings_ons_actually_use(self, raw, expected):
        assert _period(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "2026", "Q3 2026", "nonsense"])
    def test_an_unreadable_date_is_skipped_not_guessed(self, raw):
        assert _period(raw) is None


class TestParsing:
    def test_reads_the_monthly_block(self):
        rows = parse_series(_payload(("2026 JUL", "118.4"), ("2026 AUG", "125.8")),
                            BY_CDID["D7EH"])
        assert [(r["period"].isoformat(), str(r["value"])) for r in rows] == [
            ("2026-07-01", "118.4"), ("2026-08-01", "125.8")]

    def test_accepts_a_json_string(self):
        rows = parse_series(json.dumps(_payload(("2026 AUG", "6.2"))), BY_CDID["D7MB"])
        assert str(rows[0]["value"]) == "6.2"

    def test_negative_and_comma_formatted_values_survive(self):
        rows = parse_series(_payload(("2025 SEP", "-28.8"), ("2025 OCT", "1,024.5")),
                            BY_CDID["D7MB"])
        assert [str(r["value"]) for r in rows] == ["-28.8", "1024.5"]

    def test_output_is_ordered_by_period(self):
        rows = parse_series(_payload(("2026 AUG", "2"), ("2026 JAN", "1")), BY_CDID["D7EH"])
        assert [r["period"].month for r in rows] == [1, 8]

    def test_placeholder_values_are_dropped(self):
        rows = parse_series(_payload(("2026 JUL", ".."), ("2026 AUG", "125.8")),
                            BY_CDID["D7EH"])
        assert len(rows) == 1 and rows[0]["period"].month == 8


class TestItRefusesToBeSilentlyWrong:
    """The lesson from the bank measurement, encoded as tests."""

    def test_a_missing_months_block_raises_and_names_what_was_there(self):
        with pytest.raises(Mm23Error) as exc:
            parse_series({"description": {}, "quarters": [], "years": []}, BY_CDID["D7EH"])
        message = str(exc.value)
        assert "no 'months' list" in message
        # The keys that WERE present must be in the message, or the next person
        # has to go and fetch the payload by hand to find out.
        assert "description" in message and "quarters" in message

    def test_months_present_but_none_readable_raises(self):
        with pytest.raises(Mm23Error, match="none were readable"):
            parse_series({"months": [{"date": "Q3 2026", "value": "1"}]}, BY_CDID["D7EH"])

    def test_non_json_raises(self):
        with pytest.raises(Mm23Error, match="not JSON"):
            parse_series("<html>404</html>", BY_CDID["D7EH"])

    def test_a_partially_unreadable_series_still_loads(self, caplog):
        rows = parse_series({"months": [{"date": "Q3 2026", "value": "1"},
                                        {"date": "2026 AUG", "value": "125.8"}]},
                            BY_CDID["D7EH"])
        assert len(rows) == 1
        assert "unreadable" in caplog.text


class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code, self.text = status_code, text


class FakeSession:
    def __init__(self, by_url):
        self.by_url = by_url
        self.requested: list[str] = []

    def get(self, url, timeout=None, headers=None):
        self.requested.append(url)
        outcome = self.by_url.get(url, FakeResponse(404, "not found"))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeConfig:
    project = "proj"
    dataset = "ds"
    dry_run = False

    def table_ref(self, table):
        return f"proj.ds.{table}"


class RecordingWriter(bq.DryRunWriter):
    """DryRunWriter, but it remembers the table name it was handed.

    DryRunWriter accepts any string as a table and discards it, which is exactly
    why the first live run failed: every test passed while the code passed a bare
    'ons_mm23_series' that the BigQuery client refuses.
    """

    def __init__(self):
        super().__init__()
        self.tables: list[str] = []

    def append(self, table, rows):
        self.tables.append(table)
        return super().append(table, rows)


class TestRunFetch:
    def _session(self, cdids):
        return FakeSession({
            BY_CDID[c].url: FakeResponse(200, json.dumps(_payload(("2026 AUG", "125.8"))))
            for c in cdids
        })

    def test_writes_one_row_per_observation_with_its_provenance(self):
        writer = bq.DryRunWriter()
        mm23.run_fetch(FakeConfig(), writer=writer,
                       session=self._session(["D7EH"]), cdids=["D7EH"])
        assert len(writer.written) == 1
        row = writer.written[0]
        assert row["cdid"] == "D7EH" and row["kind"] == "index" and row["measure"] == "cpi"
        assert row["basis"] == "2015=100" and row["is_current"] is True
        assert row["source_url"].endswith("/d7eh/mm23/data")
        assert row["run_id"] and row["fetched_ts"].tzinfo is not None

    def test_the_table_is_fully_qualified(self):
        """'project.dataset.table' or the client raises ValueError on write.

        The regression: eleven series fetched and parsed correctly, then the
        write died on a bare table name, and the step reported green because it
        is continue-on-error.
        """
        writer = RecordingWriter()
        mm23.run_fetch(FakeConfig(), writer=writer,
                       session=self._session(["D7EH"]), cdids=["D7EH"])
        assert writer.tables == ["proj.ds.ons_mm23_series"]

    def test_a_dry_run_uses_the_bare_name_the_dry_writer_expects(self):
        config = FakeConfig()
        config.dry_run = True
        writer = RecordingWriter()
        mm23.run_fetch(config, writer=writer,
                       session=self._session(["D7EH"]), cdids=["D7EH"])
        assert writer.tables == ["ons_mm23_series"]

    def test_one_dead_series_does_not_cost_the_others(self):
        """The CPI index is load-bearing; an RPI subgroup going missing is not."""
        session = self._session(["D7EH"])  # CHBR will 404
        writer = bq.DryRunWriter()
        out = mm23.run_fetch(FakeConfig(), writer=writer, session=session,
                             cdids=["D7EH", "CHBR"])
        assert set(out) == {"D7EH"}
        assert len(writer.written) == 1

    def test_a_network_error_on_one_series_is_also_survived(self):
        session = FakeSession({BY_CDID["CHBR"].url: requests.ConnectionError("reset"),
                               BY_CDID["D7EH"].url: FakeResponse(
                                   200, json.dumps(_payload(("2026 AUG", "1"))))})
        out = mm23.run_fetch(FakeConfig(), writer=bq.DryRunWriter(), session=session,
                             cdids=["CHBR", "D7EH"])
        assert set(out) == {"D7EH"}

    def test_everything_failing_raises_rather_than_reporting_success(self):
        with pytest.raises(Mm23Error, match="every series failed"):
            mm23.run_fetch(FakeConfig(), writer=bq.DryRunWriter(),
                           session=FakeSession({}), cdids=["D7EH"])

    def test_a_dry_run_writes_nothing_to_bigquery(self):
        writer = bq.DryRunWriter()
        mm23.run_fetch(FakeConfig(), writer=writer,
                       session=self._session(["D7EH"]), cdids=["D7EH"])
        assert writer.path is None  # nothing persisted anywhere


class TestRender:
    def test_shows_the_range_and_the_recent_months_with_units(self):
        rows = [{"period": dt.date(2026, 7, 1), "value": 6.2},
                {"period": dt.date(2026, 8, 1), "value": -28.8}]
        text = mm23.render({"D7MB": rows})
        assert "D7MB" in text and "Jul26 6.2%" in text and "Aug26 -28.8%" in text
        assert "2 months" in text

    def test_an_index_is_not_given_a_percent_sign(self):
        rows = [{"period": dt.date(2026, 8, 1), "value": 125.8}]
        assert "125.8%" not in mm23.render({"D7EH": rows})


class TestBuildRows:
    def test_every_row_carries_the_vintage_that_orders_revisions(self):
        ts = dt.datetime(2026, 9, 26, tzinfo=dt.timezone.utc)
        rows = build_rows(BY_CDID["CHBR"], [{"period": dt.date(1987, 1, 1), "value": 100}],
                          run_id="r1", fetched_ts=ts)
        assert rows[0]["fetched_ts"] == ts and rows[0]["run_id"] == "r1"
        assert rows[0]["basis"] == "Jan 1987=100"


class TestAnnualSeries:
    """Weights are annual, so mm23 leaves `months` empty and fills `years`.

    The first live run said so in as many words -- "CJXW: 'months' had 0 entries
    and none were readable" -- which is the strict-failure design paying for
    itself: an empty months list is a true fact about an annual series, and a
    fetcher that returned zero rows quietly would have been read as "ONS publish
    no weight".
    """

    def test_a_weight_series_reads_the_years_block(self):
        payload = {"months": [], "years": [{"date": "2025", "value": "5.1"},
                                           {"date": "2026", "value": "4.8"}]}
        rows = parse_series(payload, BY_CDID["CJXW"])
        assert [(r["period"].isoformat(), str(r["value"])) for r in rows] == [
            ("2025-01-01", "5.1"), ("2026-01-01", "4.8")]

    def test_an_annual_value_is_anchored_to_january(self):
        """A weight applies to the whole year, and the table partitions by date."""
        rows = parse_series({"years": [{"date": "2026", "value": "4.8"}]},
                            BY_CDID["CZHM"])
        assert rows[0]["period"] == dt.date(2026, 1, 1)

    def test_a_monthly_series_still_ignores_the_years_block(self):
        payload = {"months": [{"date": "2026 AUG", "value": "125.8"}],
                   "years": [{"date": "2026", "value": "999"}]}
        rows = parse_series(payload, BY_CDID["D7EH"])
        assert len(rows) == 1 and str(rows[0]["value"]) == "125.8"

    def test_the_error_names_the_block_it_looked_in(self):
        with pytest.raises(Mm23Error, match="'years' had 0 entries"):
            parse_series({"years": []}, BY_CDID["CJXW"])

    def test_declared_frequency_matches_what_the_series_is(self):
        assert BY_CDID["CJXW"].frequency == "annual"
        assert BY_CDID["CZHM"].frequency == "annual"
        assert all(s.frequency == "monthly" for s in SERIES if s.kind != "weight")
