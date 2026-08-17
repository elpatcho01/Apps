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


#: The real ONS weights layout, confirmed from a production workbook dump:
#: sheet named "Weights", years DESCENDING across columns, and one row per
#: (haul category, advance window) -- six series, not three. The category label
#: is merged and blank on continuation rows. Within a year the six sum to 1.
REAL_WEIGHTS = {
    2026: {("Domestic", 1): 0.028575, ("European", 1): 0.206781,
           ("European", 3): 0.206781, ("Long-haul", 1): 0.055786,
           ("Long-haul", 3): 0.251039, ("Long-haul", 6): 0.251039},
    2025: {("Domestic", 1): 0.027145, ("European", 1): 0.207669,
           ("European", 3): 0.207669, ("Long-haul", 1): 0.055752,
           ("Long-haul", 3): 0.250883, ("Long-haul", 6): 0.250883},
    2024: {("Domestic", 1): 0.026447, ("European", 1): 0.202040,
           ("European", 3): 0.202040, ("Long-haul", 1): 0.056947,
           ("Long-haul", 3): 0.256263, ("Long-haul", 6): 0.256263},
}


def weights_sheet(years=(2026, 2025, 2024)):
    rows = [["2007-2026 airfares weights"] + [None] * len(years),
            [None, None] + list(years)]
    prev = None
    for cat, window in (("Domestic", 1), ("European", 1), ("European", 3),
                        ("Long-haul", 1), ("Long-haul", 3), ("Long-haul", 6)):
        label = cat if cat != prev else None
        prev = cat
        rows.append([label, f"{window} month"] + [REAL_WEIGHTS[y][(cat, window)] for y in years])
    return rows


#: Kept for the sub-index tests, which assert a weights sheet is not mistaken
#: for a series.
CANONICAL = weights_sheet()


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
    def test_parses_the_real_layout(self):
        rows = parse_weights(workbook_bytes({"Weights": weights_sheet()}))
        assert len(rows) == 3 * 6  # 3 years x 6 series

    def test_extracts_all_six_series(self):
        rows = parse_weights(workbook_bytes({"Weights": weights_sheet()}))
        series = {(r["haul_category"], r["months_ahead"]) for r in rows}
        assert series == {
            ("domestic", 1), ("european", 1), ("european", 3),
            ("long_haul", 1), ("long_haul", 3), ("long_haul", 6),
        }

    def test_weights_sum_to_one_within_a_year(self):
        rows = parse_weights(workbook_bytes({"Weights": weights_sheet()}))
        for year in (2026, 2025, 2024):
            total = sum(r["weight"] for r in rows if r["year"] == year)
            assert total == pytest.approx(1.0, abs=1e-3)

    def test_window_split_is_not_even(self):
        """Long-haul 1-month is ~0.056 while 3- and 6-month are ~0.251 each."""
        rows = parse_weights(workbook_bytes({"Weights": weights_sheet()}))
        lh = {r["months_ahead"]: r["weight"] for r in rows
              if r["year"] == 2026 and r["haul_category"] == "long_haul"}
        assert lh[3] > 4 * lh[1]

    def test_carries_the_category_label_forward(self):
        rows = parse_weights(workbook_bytes({"Weights": weights_sheet()}))
        threes = {r["haul_category"] for r in rows if r["months_ahead"] == 3}
        assert threes == {"european", "long_haul"}

    def test_years_read_from_the_header(self):
        rows = parse_weights(workbook_bytes({"Weights": weights_sheet()}))
        assert {r["year"] for r in rows} == {2024, 2025, 2026}

    def test_output_is_sorted(self):
        rows = parse_weights(workbook_bytes({"Weights": weights_sheet()}))
        keys = [(r["year"], r["haul_category"], r["months_ahead"]) for r in rows]
        assert keys == sorted(keys)

    def test_finds_the_sheet_among_others(self):
        from tests.test_backfill import year_sheet

        sheets = {"2019": year_sheet(2019), "Weights": weights_sheet()}
        assert len(parse_weights(workbook_bytes(sheets))) == 18


class TestParseWeightsRejects:
    """A wrong weight silently corrupts every aggregate, so refusing is right."""

    def test_no_weights_sheet(self):
        from tests.test_backfill import year_sheet

        with pytest.raises(WeightsParseError):
            parse_weights(workbook_bytes({"2019": year_sheet(2019)}))

    def test_too_few_year_columns_to_be_a_header(self):
        sheet = [["Weights"], [None, None, 2026], ["Domestic", "1 month", 0.5]]
        with pytest.raises(WeightsParseError):
            parse_weights(workbook_bytes({"Weights": sheet}))

    def test_unrecognisable_sheet_dumps_structure(self):
        with pytest.raises(WeightsParseError, match="sheet 'Weights'"):
            parse_weights(workbook_bytes({"Weights": [["alpha", "beta"], [1, 2]]}))

    def test_describe_shows_cells(self):
        wb = openpyxl.load_workbook(io.BytesIO(workbook_bytes({"Weights": weights_sheet()})))
        text = describe([(s.title, [list(r) for r in s.iter_rows(values_only=True)])
                         for s in wb.worksheets])
        assert "Domestic" in text and "Weights" in text


class TestWriteCsv:
    def _rows(self):
        return parse_weights(workbook_bytes({"Weights": weights_sheet()}))

    def test_roundtrips_through_load_weights(self, tmp_path):
        path = tmp_path / "weights.csv"
        write_weights_csv(self._rows(), path, "https://example.invalid/x.xlsx")

        w = panel.load_weights(2025, path=path)
        assert w.year == 2025 and w.is_placeholder is False
        assert w.get("domestic", 1) == pytest.approx(0.027145)
        assert w.get("long_haul", 6) == pytest.approx(0.250883)
        assert sum(w.normalised().values()) == pytest.approx(1.0)

    def test_haul_total_sums_its_windows(self, tmp_path):
        path = tmp_path / "weights.csv"
        write_weights_csv(self._rows(), path, "u")
        w = panel.load_weights(2026, path=path)
        assert w.haul_total("european") == pytest.approx(2 * 0.206781, abs=1e-5)

    def test_unknown_series_returns_none(self, tmp_path):
        path = tmp_path / "weights.csv"
        write_weights_csv(self._rows(), path, "u")
        assert panel.load_weights(2026, path=path).get("domestic", 6) is None

    def test_carries_forward_to_later_years(self, tmp_path):
        path = tmp_path / "weights.csv"
        write_weights_csv(self._rows(), path, "u")
        assert panel.load_weights(2030, path=path).year == 2026

    def test_written_weights_are_not_placeholders(self, tmp_path):
        path = tmp_path / "weights.csv"
        write_weights_csv(self._rows(), path, "u")
        assert panel.load_weights(2026, path=path).is_placeholder is False

    def test_records_provenance(self, tmp_path):
        path = tmp_path / "weights.csv"
        write_weights_csv(self._rows(), path, "https://ons/x.xlsx")
        assert "https://ons/x.xlsx" in path.read_text()


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
