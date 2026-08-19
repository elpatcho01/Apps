"""Sub-index parsing and the published-series backfill."""

import datetime as dt
from decimal import Decimal

import pytest

openpyxl = pytest.importorskip("openpyxl")

from ukairfares.backfill import build_rows
from ukairfares.onsweights import SubIndexParseError, _as_month, parse_subindices
from tests.test_onsweights import CANONICAL, workbook_bytes


MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

#: The real ONS layout, confirmed from a production run's workbook dump:
#: one sheet per year, months across columns, and each haul category broken
#: out by advance window -- six series, not three.
def year_sheet(year, base=100.0, step=2.0, missing=()):
    rows = [
        [f"{year} Airfares sub-indices"] + [None] * 12,
        [None, None] + MONTHS,
    ]
    for cat, windows in (("Domestic", [1]), ("European", [1, 3]), ("Long-haul", [1, 3, 6])):
        for j, w in enumerate(windows):
            vals = []
            for m in range(1, 13):
                if (cat, w, m) in missing:
                    vals.append("..")
                else:
                    vals.append(100.0 if m == 1 else base + m * step + w)
            # Category label appears only on the first row of each group
            # (merged in the original), blank on continuation rows.
            rows.append([cat if j == 0 else None, f"{w}-month"] + vals)
    return rows


def workbook_years(years=(2017, 2018, 2019)):
    return workbook_bytes({str(y): year_sheet(y) for y in years})


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
    def test_parses_the_real_layout(self):
        rows = parse_subindices(workbook_years())
        # 3 years x 12 months x 6 series
        assert len(rows) == 3 * 12 * 6

    def test_extracts_all_six_series(self):
        rows = parse_subindices(workbook_years())
        series = {(r["haul_category"], r["months_ahead"]) for r in rows}
        assert series == {
            ("domestic", 1),
            ("european", 1), ("european", 3),
            ("long_haul", 1), ("long_haul", 3), ("long_haul", 6),
        }

    def test_year_comes_from_the_sheet_name(self):
        rows = parse_subindices(workbook_years((2021, 2022)))
        assert {r["index_month"].year for r in rows} == {2021, 2022}

    def test_carries_the_category_label_forward(self):
        # "European" is blank on its 3-month continuation row.
        rows = parse_subindices(workbook_years((2019,)))
        threes = [r for r in rows if r["months_ahead"] == 3]
        assert {r["haul_category"] for r in threes} == {"european", "long_haul"}

    def test_january_is_the_base(self):
        rows = parse_subindices(workbook_years((2019,)))
        jans = [r for r in rows if r["index_month"].month == 1]
        assert len(jans) == 6
        assert all(r["index_value"] == pytest.approx(100.0) for r in jans)

    def test_skips_missing_values(self):
        sheet = year_sheet(2019, missing={("Domestic", 1, 5), ("Domestic", 1, 6)})
        rows = parse_subindices(workbook_bytes({"2019": sheet}))
        dom = [r for r in rows if r["haul_category"] == "domestic"]
        assert len(dom) == 10
        assert {r["index_month"].month for r in dom} == set(range(1, 13)) - {5, 6}

    def test_output_is_sorted(self):
        rows = parse_subindices(workbook_years())
        keys = [(r["index_month"], r["haul_category"], r["months_ahead"]) for r in rows]
        assert keys == sorted(keys)

    def test_ignores_non_year_sheets(self):
        sheets = {"Notes": [["some", "preamble"]], "2019": year_sheet(2019)}
        rows = parse_subindices(workbook_bytes(sheets))
        assert {r["index_month"].year for r in rows} == {2019}

    def test_does_not_mistake_the_weights_sheet_for_a_series(self):
        with pytest.raises(SubIndexParseError):
            parse_subindices(workbook_bytes({"Weights": CANONICAL}))

    def test_dumps_structure_on_failure(self):
        with pytest.raises(SubIndexParseError, match="sheet 'Mystery'"):
            parse_subindices(workbook_bytes({"Mystery": [["a", "b"], [1, 2]]}))

    def test_rejects_a_sheet_with_too_few_month_columns(self):
        sheet = [["2019 Airfares"], [None, None, "Jan", "Feb"],
                 ["Domestic", "1-month", 100, 101]]
        with pytest.raises(SubIndexParseError):
            parse_subindices(workbook_bytes({"2019": sheet}))


class TestBuildRows:
    def _series(self):
        return parse_subindices(workbook_years())

    def test_one_row_per_parsed_value(self):
        series = self._series()
        rows = build_rows(
            series, release_url="u", release_label="rel",
            run_id="r", fetched_ts=dt.datetime(2026, 8, 16),
        )
        assert len(rows) == len(series)
        assert {r["haul_category"] for r in rows} == {"domestic", "european", "long_haul"}
        assert {r["months_ahead"] for r in rows} == {1, 3, 6}

    def test_values_are_decimal(self):
        rows = build_rows(
            self._series(), release_url="u", release_label="rel",
            run_id="r", fetched_ts=dt.datetime(2026, 8, 16),
        )
        assert all(isinstance(r["index_value"], Decimal) for r in rows)

    def test_detects_the_annual_january_100_basis(self):
        # Confirmed against the real workbook: every January is exactly 100
        # and each year's sheet restarts from there.
        rows = build_rows(
            self._series(), release_url="u", release_label="rel",
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


class TestReleaseDiscovery:
    """Ranking releases correctly is not cosmetic.

    A production run picked ad hoc 14287 (coverage ending January 2022) over
    2716 (ending February 2025) because the old reference series sorts above
    the new one numerically. Three years of the target series were silently
    lost. Rank by the coverage period in the slug instead.
    """

    SLUGS = {
        "13727": "/economy/inflationandpriceindices/adhocs/13727domesticeuropeanandlonghaulairfaresconsumerpricessubindicesjanuary2017toaugust2021",
        "14097": "/economy/inflationandpriceindices/adhocs/14097domesticeuropeanandlonghaulairfaresconsumerpricessubindicesjanuary2017tonovember2021",
        "14287": "/economy/inflationandpriceindices/adhocs/14287domesticeuropeanandlonghaulairfaresconsumerpricessubindicesjanuary2017tojanuary2022",
        "2032": "/economy/inflationandpriceindices/adhocs/2032domesticeuropeanandlonghaulairfaresconsumerpricessubindicesjanuary2022tomarch2024",
        "2362": "/economy/inflationandpriceindices/adhocs/2362domesticeuropeanandlonghaulairfaresconsumerpricessubindicesjanuary2017tojuly2024",
        "2716": "/economy/inflationandpriceindices/adhocs/2716domesticeuropeanandlonghaulairfaresconsumerpricessubindicesjanuary2017tofebruary2025",
    }

    def test_reads_coverage_end_from_the_slug(self):
        from ukairfares.onsweights import _coverage_end

        assert _coverage_end(self.SLUGS["2716"]) == (2025, 2)
        assert _coverage_end(self.SLUGS["14287"]) == (2022, 1)
        assert _coverage_end(self.SLUGS["2032"]) == (2024, 3)

    def test_newest_coverage_wins_despite_a_lower_reference_number(self):
        from ukairfares.onsweights import _coverage_end

        best = max(self.SLUGS.values(), key=_coverage_end)
        assert "2716" in best
        assert "february2025" in best

    def test_old_five_digit_refs_do_not_win(self):
        from ukairfares.onsweights import _coverage_end

        # The exact failure seen in production.
        assert _coverage_end(self.SLUGS["2716"]) > _coverage_end(self.SLUGS["14287"])

    def test_unreadable_slug_sorts_last(self):
        from ukairfares.onsweights import _coverage_end

        assert _coverage_end("/economy/.../adhocs/999someotherrelease") == (0, 0)

    def test_discovery_picks_the_widest_coverage(self):
        import requests
        from ukairfares.onsweights import discover_latest_release

        html = "".join(f'<a href="{h}">x</a>' for h in self.SLUGS.values())

        class FakeResp:
            status_code = 200
            text = html

            def raise_for_status(self):
                pass

        class FakeSession:
            def get(self, *a, **k):
                return FakeResp()

        assert "2716" in discover_latest_release(FakeSession())


class TestPreTwentySixteenHistory:
    """The floor that silently dropped nine years of the published series.

    `MIN_YEAR` was 2015, chosen when the pinned release was titled "January 2017
    to February 2025". Discovery now finds one spanning **January 2007**, and the
    floor was throwing every earlier sheet away without a word: `_as_year`
    returned None, so the loop skipped the sheet. The live table held 678 values
    starting 2016 while the workbook offered roughly nine more years.

    The failure mode is what makes it worth a test. Nothing errored. A bound
    written against one release's title quietly became a filter when the release
    changed, and the only symptom was history that was never there.
    """

    def test_parses_a_2007_sheet(self):
        rows = parse_subindices(workbook_years((2007,)))
        assert len(rows) == 12 * 6
        assert min(r["index_month"] for r in rows).year == 2007

    def test_parses_the_full_2007_to_2026_span(self):
        years = (2007, 2010, 2014, 2015, 2016, 2020, 2026)
        rows = parse_subindices(workbook_years(years))
        assert {r["index_month"].year for r in rows} == set(years)
        assert len(rows) == len(years) * 12 * 6

    def test_still_rejects_a_year_that_is_not_one(self):
        """Widening the floor must not turn stray numbers into sheets."""
        from ukairfares.onsweights import _as_year

        assert _as_year(1999) is None
        assert _as_year(2099) is None
        assert _as_year(2007) == 2007

    def test_a_sheet_that_is_not_year_shaped_is_still_skipped(self):
        """Admitting older years must not admit junk sheets."""
        sheets = {"2007": year_sheet(2007), "Notes": [["Some", "commentary"]]}
        rows = parse_subindices(workbook_bytes(sheets))
        assert len(rows) == 12 * 6
