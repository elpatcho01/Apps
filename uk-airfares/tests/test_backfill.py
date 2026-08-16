"""Sub-index parsing and the published-series backfill."""

import datetime as dt
from decimal import Decimal

import pytest

openpyxl = pytest.importorskip("openpyxl")

from ukairfares.backfill import build_rows
from ukairfares.onsweights import SubIndexParseError, _as_month, parse_subindices
from tests.test_onsweights import CANONICAL, workbook_bytes


def monthly_series(n=30, start=(2017, 1), base=100.0, step=1.0):
    """A plausible sub-index sheet: header plus n monthly rows."""
    rows = [["Month", "Domestic", "European", "Long-haul"]]
    year, month = start
    for i in range(n):
        rows.append([
            dt.datetime(year, month, 1),
            base + i * step,
            base + i * step * 1.5,
            base + i * step * 2.0,
        ])
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return rows


class TestAsMonth:
    @pytest.mark.parametrize("value,expected", [
        (dt.datetime(2017, 3, 15), dt.date(2017, 3, 1)),
        (dt.date(2017, 3, 15), dt.date(2017, 3, 1)),
        ("2017-03", dt.date(2017, 3, 1)),
        ("2017/03", dt.date(2017, 3, 1)),
        ("Mar 2017", dt.date(2017, 3, 1)),
        ("March 2017", dt.date(2017, 3, 1)),
        ("2017 Mar", dt.date(2017, 3, 1)),
        ("  january  2020 ", dt.date(2020, 1, 1)),
    ])
    def test_parses_known_forms(self, value, expected):
        assert _as_month(value) == expected

    @pytest.mark.parametrize("value", [None, "", "not a month", "2017-13", "Smarch 2017", 42])
    def test_rejects_junk(self, value):
        assert _as_month(value) is None


class TestParseSubindices:
    def test_canonical_sheet(self):
        rows = parse_subindices(workbook_bytes({"Sub-indices": monthly_series()}))
        assert len(rows) == 30
        assert rows[0]["index_month"] == dt.date(2017, 1, 1)
        assert rows[0]["domestic"] == pytest.approx(100.0)
        assert rows[0]["long_haul"] == pytest.approx(100.0)

    def test_output_is_month_sorted(self):
        rows = parse_subindices(workbook_bytes({"Indices": monthly_series()}))
        months = [r["index_month"] for r in rows]
        assert months == sorted(months)

    def test_prefers_index_sheet_over_weights_sheet(self):
        sheets = {"Weights": CANONICAL, "Sub-indices": monthly_series()}
        rows = parse_subindices(workbook_bytes(sheets))
        # The weights sheet is annual with 3 rows; the index sheet is monthly.
        assert len(rows) == 30
        assert rows[0]["index_month"] == dt.date(2017, 1, 1)

    def test_does_not_mistake_the_weights_sheet_for_a_series(self):
        # Weights alone must not parse as a sub-index series.
        with pytest.raises(SubIndexParseError):
            parse_subindices(workbook_bytes({"Weights": CANONICAL}))

    def test_tolerates_string_months(self):
        rows = [["Period", "Domestic", "European", "Long-haul"]]
        for i in range(14):
            rows.append([f"{2017 + i // 12}-{i % 12 + 1:02d}", 100 + i, 100 + i, 100 + i])
        parsed = parse_subindices(workbook_bytes({"Indices": rows}))
        assert len(parsed) == 14

    def test_tolerates_title_rows(self):
        sheets = {"Indices": [
            ["Domestic, European and long-haul airfares sub-indices"], [],
            ["January 2017 = 100"], [],
            *monthly_series(),
        ]}
        assert len(parse_subindices(workbook_bytes(sheets))) == 30

    def test_skips_footnotes_and_blanks(self):
        sheets = {"Indices": [*monthly_series(), [], ["Source: ONS"], ["Note: provisional"]]}
        assert len(parse_subindices(workbook_bytes(sheets))) == 30

    def test_requires_a_year_of_data(self):
        # A handful of stray rows is not a monthly series.
        with pytest.raises(SubIndexParseError):
            parse_subindices(workbook_bytes({"Indices": monthly_series(n=5)}))

    def test_rejects_missing_category(self):
        rows = [["Month", "Domestic", "European"]]
        for i in range(20):
            rows.append([dt.datetime(2017, 1, 1) + dt.timedelta(days=31 * i), 100, 100])
        with pytest.raises(SubIndexParseError):
            parse_subindices(workbook_bytes({"Indices": rows}))

    def test_dumps_structure_on_failure(self):
        with pytest.raises(SubIndexParseError, match="sheet 'Mystery'"):
            parse_subindices(workbook_bytes({"Mystery": [["a", "b"], [1, 2]]}))

    def test_ignores_pre_series_years(self):
        sheets = {"Indices": [
            ["Month", "Domestic", "European", "Long-haul"],
            [dt.datetime(1999, 1, 1), 50, 50, 50],
            *monthly_series()[1:],
        ]}
        rows = parse_subindices(workbook_bytes(sheets))
        assert all(r["index_month"].year >= 2016 for r in rows)


class TestBuildRows:
    def _series(self):
        return parse_subindices(workbook_bytes({"Indices": monthly_series()}))

    def test_one_row_per_month_per_haul(self):
        series = self._series()
        rows = build_rows(
            series, release_url="u", release_label="rel",
            run_id="r", fetched_ts=dt.datetime(2026, 8, 16),
        )
        assert len(rows) == len(series) * 3
        assert {r["haul_category"] for r in rows} == {"domestic", "european", "long_haul"}

    def test_values_are_decimal(self):
        rows = build_rows(
            self._series(), release_url="u", release_label="rel",
            run_id="r", fetched_ts=dt.datetime(2026, 8, 16),
        )
        assert all(isinstance(r["index_value"], Decimal) for r in rows)

    def test_detects_chained_basis(self):
        # monthly_series climbs continuously, so Januaries are not all 100.
        rows = build_rows(
            self._series(), release_url="u", release_label="rel",
            run_id="r", fetched_ts=dt.datetime(2026, 8, 16),
        )
        assert {r["basis"] for r in rows} == {"chained"}

    def test_detects_annual_january_100_basis(self):
        sheet = [["Month", "Domestic", "European", "Long-haul"]]
        for year in (2017, 2018, 2019):
            for m in range(1, 13):
                v = 100.0 if m == 1 else 100.0 + m
                sheet.append([dt.datetime(year, m, 1), v, v, v])
        series = parse_subindices(workbook_bytes({"Indices": sheet}))
        rows = build_rows(
            series, release_url="u", release_label="rel",
            run_id="r", fetched_ts=dt.datetime(2026, 8, 16),
        )
        assert {r["basis"] for r in rows} == {"annual_january_100"}

    def test_carries_provenance(self):
        rows = build_rows(
            self._series(), release_url="https://ons/x.xlsx", release_label="2716",
            run_id="run-1", fetched_ts=dt.datetime(2026, 8, 16),
        )
        assert all(r["release_url"] == "https://ons/x.xlsx" for r in rows)
        assert all(r["release_label"] == "2716" for r in rows)
        assert all(r["run_id"] == "run-1" for r in rows)
        assert all(r["is_current"] is True for r in rows)
