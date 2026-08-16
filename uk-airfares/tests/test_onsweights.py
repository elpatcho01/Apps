"""Tests for the ONS weights fetcher and the weighted aggregate.

The parser was written without sight of the real spreadsheet (ons.gov.uk is
blocked from the development sandbox by egress policy), so these tests exercise
it against several plausible layouts rather than one assumed-correct one, and
assert hard that it *rejects* anything it cannot defensibly read.
"""

import datetime as dt
import io
from decimal import Decimal

import pytest

openpyxl = pytest.importorskip("openpyxl")

from ukairfares import panel
from ukairfares.onsweights import (
    WeightsParseError,
    _as_number,
    _as_year,
    describe,
    parse_weights,
    write_weights_csv,
)
from ukairfares.reconcile import aggregate
from tests.test_reconcile_validate import INDEX_DAY, panel_row


def workbook_bytes(sheets: dict[str, list[list]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title=title)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


CANONICAL = [
    ["Year", "Domestic", "European", "Long-haul"],
    [2023, 12.0, 48.0, 40.0],
    [2024, 11.0, 47.0, 42.0],
    [2025, 10.5, 46.5, 43.0],
]


class TestCoercion:
    @pytest.mark.parametrize("value,expected", [
        (2024, 2024), (2024.0, 2024), ("2024", 2024),
        ("Jan 2024", 2024), (dt.date(2024, 1, 1), 2024),
        (dt.datetime(2024, 6, 1), 2024),
    ])
    def test_year_parsing(self, value, expected):
        assert _as_year(value) == expected

    @pytest.mark.parametrize("value", [None, "", "not a year", 1200, 2999, 12.5])
    def test_year_rejection(self, value):
        assert _as_year(value) is None

    @pytest.mark.parametrize("value,expected", [
        (12.5, 12.5), ("12.5", 12.5), ("1,234", 1234.0), ("45%", 45.0), ("£10", 10.0),
    ])
    def test_number_parsing(self, value, expected):
        assert _as_number(value) == expected

    @pytest.mark.parametrize("value", [None, "", "n/a", True, False])
    def test_number_rejection(self, value):
        assert _as_number(value) is None


class TestParseWeights:
    def test_canonical_layout(self):
        rows = parse_weights(workbook_bytes({"Weights": CANONICAL}))
        assert len(rows) == 3
        assert rows[0] == {"year": 2023, "domestic": 12.0, "european": 48.0, "long_haul": 40.0}

    def test_prefers_sheet_named_weights(self):
        sheets = {
            "Sub-indices": [["Year", "Domestic", "European", "Long-haul"], [2023, 99, 99, 99]],
            "Weights": CANONICAL,
        }
        rows = parse_weights(workbook_bytes(sheets))
        assert rows[0]["domestic"] == 12.0

    def test_tolerates_title_rows_above_the_header(self):
        sheets = {"Weights": [
            ["Domestic, European and long-haul airfares"], [], ["Table 2: Weights"], [],
            *CANONICAL,
        ]}
        assert len(parse_weights(workbook_bytes(sheets))) == 3

    def test_tolerates_column_reordering(self):
        sheets = {"Weights": [
            ["Long haul", "Year", "Domestic", "European"],
            [40.0, 2023, 12.0, 48.0],
            [42.0, 2024, 11.0, 47.0],
        ]}
        rows = parse_weights(workbook_bytes(sheets))
        assert rows[0] == {"year": 2023, "domestic": 12.0, "european": 48.0, "long_haul": 40.0}

    def test_tolerates_short_haul_naming(self):
        sheets = {"Weights": [
            ["Year", "Domestic", "Short-haul", "Longhaul"],
            [2023, 12.0, 48.0, 40.0],
            [2024, 11.0, 47.0, 42.0],
        ]}
        assert len(parse_weights(workbook_bytes(sheets))) == 2

    def test_infers_year_column_without_a_year_header(self):
        sheets = {"Weights": [
            ["", "Domestic", "European", "Long-haul"],
            [2023, 12.0, 48.0, 40.0],
            [2024, 11.0, 47.0, 42.0],
        ]}
        rows = parse_weights(workbook_bytes(sheets))
        assert [r["year"] for r in rows] == [2023, 2024]

    def test_skips_footnote_rows(self):
        sheets = {"Weights": [
            *CANONICAL,
            ["Source: ONS", None, None, None],
            ["Note: weights are parts per 1000", None, None, None],
        ]}
        assert len(parse_weights(workbook_bytes(sheets))) == 3

    def test_output_is_year_sorted(self):
        sheets = {"Weights": [
            ["Year", "Domestic", "European", "Long-haul"],
            [2025, 10.5, 46.5, 43.0],
            [2023, 12.0, 48.0, 40.0],
            [2024, 11.0, 47.0, 42.0],
        ]}
        assert [r["year"] for r in parse_weights(workbook_bytes(sheets))] == [2023, 2024, 2025]

    def test_duplicate_years_take_the_first(self):
        sheets = {"Weights": [
            ["Year", "Domestic", "European", "Long-haul"],
            [2023, 12.0, 48.0, 40.0],
            [2023, 99.0, 99.0, 99.0],
            [2024, 11.0, 47.0, 42.0],
        ]}
        rows = parse_weights(workbook_bytes(sheets))
        assert len(rows) == 2 and rows[0]["domestic"] == 12.0


class TestParseWeightsRejects:
    """A wrong weight silently corrupts every aggregate, so refusing is correct."""

    def test_missing_a_category(self):
        sheets = {"Weights": [["Year", "Domestic", "European"], [2023, 12.0, 48.0]]}
        with pytest.raises(WeightsParseError):
            parse_weights(workbook_bytes(sheets))

    def test_negative_and_zero_weights_rejected(self):
        sheets = {"Weights": [
            ["Year", "Domestic", "European", "Long-haul"],
            [2023, -12.0, 48.0, 40.0],
            [2024, 0, 47.0, 42.0],
        ]}
        with pytest.raises(WeightsParseError):
            parse_weights(workbook_bytes(sheets))

    def test_single_row_is_not_enough(self):
        sheets = {"Weights": [["Year", "Domestic", "European", "Long-haul"], [2023, 12, 48, 40]]}
        with pytest.raises(WeightsParseError):
            parse_weights(workbook_bytes(sheets))

    def test_unrecognisable_workbook_dumps_structure(self):
        sheets = {"Data": [["alpha", "beta"], [1, 2]]}
        with pytest.raises(WeightsParseError, match="sheet 'Data'"):
            parse_weights(workbook_bytes(sheets))

    def test_describe_shows_cells(self):
        wb = openpyxl.load_workbook(io.BytesIO(workbook_bytes({"Weights": CANONICAL})))
        text = describe([(s.title, [list(r) for r in s.iter_rows(values_only=True)])
                         for s in wb.worksheets])
        assert "Domestic" in text and "Weights" in text


class TestWriteCsv:
    def test_roundtrips_through_load_weights(self, tmp_path):
        rows = parse_weights(workbook_bytes({"Weights": CANONICAL}))
        path = tmp_path / "weights.csv"
        write_weights_csv(rows, path, "https://example.invalid/release.xlsx")

        w = panel.load_weights(2024, path=path)
        assert w.year == 2024 and w.is_placeholder is False
        assert w.domestic == 11.0
        assert pytest.approx(sum(w.normalised().values())) == 1.0

    def test_written_weights_are_not_placeholders(self, tmp_path):
        rows = parse_weights(workbook_bytes({"Weights": CANONICAL}))
        path = tmp_path / "weights.csv"
        write_weights_csv(rows, path, "https://example.invalid/release.xlsx")
        # The default (strict) path must now accept them.
        assert panel.load_weights(2025, path=path).domestic == 10.5

    def test_carries_forward_to_later_years(self, tmp_path):
        rows = parse_weights(workbook_bytes({"Weights": CANONICAL}))
        path = tmp_path / "weights.csv"
        write_weights_csv(rows, path, "u")
        assert panel.load_weights(2030, path=path).year == 2025

    def test_records_provenance(self, tmp_path):
        rows = parse_weights(workbook_bytes({"Weights": CANONICAL}))
        path = tmp_path / "weights.csv"
        write_weights_csv(rows, path, "https://example.invalid/release.xlsx")
        assert "https://example.invalid/release.xlsx" in path.read_text()


def all_three_hauls():
    return [
        panel_row("LHR-EDI", "domestic", 100),
        panel_row("LGW-EDI", "domestic", 200),
        panel_row("LHR-AMS", "european", 300),
        panel_row("LHR-JFK", "long_haul", 600),
    ]


def run_aggregate(rows):
    return aggregate(
        rows, index_day=INDEX_DAY, scrape_date_used=dt.date(2026, 8, 11),
        offset_days=0, run_id="r", computed_ts=dt.datetime(2026, 9, 17),
    )


class TestWeightedAggregate:
    def test_emits_an_all_row(self):
        out = run_aggregate(all_three_hauls())
        assert any(r["haul_category"] == "all" for r in out)

    def test_all_row_is_the_weighted_mean_of_hauls(self):
        out = run_aggregate(all_three_hauls())
        row = next(
            r for r in out
            if r["haul_category"] == "all"
            and r["attribution_rule"] == "departure_month"
            and r["selection_rule"] == "ons_target_time"
            and r["agg_method"] == "mean"
        )
        # Placeholder weights are equal thirds: (150 + 300 + 600) / 3 = 350.
        assert float(row["reconstructed_value"]) == pytest.approx(350.0)

    def test_all_row_sums_observations_and_routes(self):
        out = run_aggregate(all_three_hauls())
        row = next(
            r for r in out
            if r["haul_category"] == "all" and r["agg_method"] == "mean"
            and r["attribution_rule"] == "departure_month"
            and r["selection_rule"] == "ons_target_time"
        )
        assert row["n_observations"] == 4
        assert row["n_routes"] == 4
        assert row["n_expected_routes"] == 8 + 9 + 6

    def test_all_row_flags_placeholder_weights(self):
        out = run_aggregate(all_three_hauls())
        assert all(
            r["weights_are_placeholder"] is True
            for r in out if r["haul_category"] == "all"
        )

    def test_haul_rows_also_carry_the_placeholder_flag(self):
        out = run_aggregate(all_three_hauls())
        assert all(r["weights_are_placeholder"] is True for r in out)

    def test_skipped_when_a_haul_is_missing(self):
        # Only domestic + long_haul: a two-thirds aggregate weighted as if whole
        # would misrepresent the panel, so no "all" row should appear.
        rows = [
            panel_row("LHR-EDI", "domestic", 100),
            panel_row("LHR-JFK", "long_haul", 600),
        ]
        out = run_aggregate(rows)
        assert out and not any(r["haul_category"] == "all" for r in out)

    def test_one_all_row_per_combination(self):
        out = run_aggregate(all_three_hauls())
        all_rows = [r for r in out if r["haul_category"] == "all"]
        keys = {
            (r["index_month"], r["attribution_rule"], r["selection_rule"], r["agg_method"])
            for r in all_rows
        }
        assert len(keys) == len(all_rows)
        # 2 attributions x 2 selection rules x 3 aggregations.
        assert len(all_rows) == 12

    def test_all_row_has_no_misleading_fare_columns(self):
        # mean/median/geomean describe a single haul's fares; on a cross-haul
        # weighted blend they would be meaningless, so they must be NULL.
        out = run_aggregate(all_three_hauls())
        for row in (r for r in out if r["haul_category"] == "all"):
            assert row["mean_fare_gbp"] is None
            assert row["median_fare_gbp"] is None
            assert row["geomean_fare_gbp"] is None
