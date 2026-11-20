"""The nowcast: a fixed, backtested method instead of numbers from a conversation.

Each test pins one decision the method depends on, most of them learned the
hard way: an index day assumed rather than checked, comparison years from when
the fares group moved less with air fares, a scraped date a week off the date
ONS priced, and a backtest that must never see the year it forecasts.
"""

import copy
import datetime as dt
import json

import pytest

from ukairfares import nowcast as n
from ukairfares.onscal import target_departure_date
from ukairfares.pull import choose_ordinal


# --- Synthetic history --------------------------------------------------------
AIR = {1: -30.0, 2: 6.0, 3: 3.0, 4: 5.0, 5: 4.0, 6: 5.0,
       7: 15.0, 8: 8.0, 9: -25.0, 10: 1.0, 11: -15.0, 12: 30.0}


def _beta(y):
    """A sensitivity that rises over time, as the real one has."""
    return 0.30 + 0.01 * (y - 2002)


def _mm(last=(2026, 9)):
    mm = {c: {} for c in n.NEEDED}
    level = 100.0
    for y in range(2002, last[0] + 1):
        for m in range(1, 13):
            if (y, m) > last:
                break
            a = AIR[m] + (25.0 if m == 12 and n.late_christmas(y) else 0.0)
            a += ((y * 7 + m * 3) % 5 - 2) * 0.5          # deterministic wiggle
            r = -1.0 + _beta(y) * a
            level *= 1 + r / 100
            mm["D7MB"][(y, m)] = a
            mm["CZFN"][(y, m)] = r
            mm["CHBR"][(y, m)] = level
    return mm


def _row(scrape, haul, k, price, cheapest=None, departure=None):
    r = {"scrape_date": scrape, "haul_category": haul, "months_ahead": k,
         "geomean_price_gbp": price, "mean_cheapest_gbp": cheapest if cheapest else price}
    if departure:
        r["departure_date"] = departure
    return r


# --- Calendar -----------------------------------------------------------------
class TestIndexDay:
    def test_the_rule_reproduces_every_date_ons_have_stated(self):
        """If any stated date broke the rule, the rule would be a guess."""
        for (y, m), d in n.CONFIRMED_INDEX_DAYS.items():
            assert n.rule_index_day(y, m) == d, (y, m)

    def test_the_release_comes_36_days_later_wherever_both_are_known(self):
        """The link that lets an announced release date fix the index day."""
        both = set(n.CONFIRMED_INDEX_DAYS) & set(n.RELEASE_DATES)
        assert len(both) >= 6
        off = {ym: (n.RELEASE_DATES[ym] - n.CONFIRMED_INDEX_DAYS[ym]).days for ym in both}
        assert {ym for ym, d in off.items() if d != 36} == {(2025, 12)}

    def test_a_stated_day_beats_the_release_arithmetic(self):
        """December 2025 was released 43 days on; its index day was the 9th."""
        assert n.index_day(2025, 12) == dt.date(2025, 12, 9)

    def test_the_fallback_is_a_tuesday_on_the_9th_to_15th_except_november(self):
        for y in range(2000, 2031):
            for m in range(1, 13):
                d = n.rule_index_day(y, m)
                assert d.weekday() == 1
                assert (8 if m == 11 else 9) <= d.day <= (14 if m == 11 else 15)

    def test_moving_off_the_8th_and_the_one_time_it_did_not(self):
        assert n.index_day(2015, 12) == dt.date(2015, 12, 15)
        assert n.index_day(2020, 12) == dt.date(2020, 12, 15)
        assert n.index_day(2026, 9) == dt.date(2026, 9, 15)
        assert n.index_day(2022, 11) == dt.date(2022, 11, 8)

    def test_november_and_december_2026_follow_their_announced_releases(self):
        """16 December 2026 and 20 January 2027, from ONS's release calendar."""
        assert n.release_index_day(2026, 11) == dt.date(2026, 11, 10)
        assert n.release_index_day(2026, 12) == dt.date(2026, 12, 15)
        assert n.index_day(2026, 11) == n.rule_index_day(2026, 11)
        assert n.index_day(2026, 12) == n.rule_index_day(2026, 12)

    def test_december_2026_is_the_15th_like_december_2020(self):
        """The 2nd Tuesday is the 8th in both years; ONS used the 15th in 2020."""
        assert n.index_day(2026, 12) == dt.date(2026, 12, 15)
        assert n.index_day(2020, 12) == dt.date(2020, 12, 15)

    def test_an_override_wins(self):
        o = {(2026, 11): dt.date(2026, 11, 17)}
        assert n.index_day(2026, 11, o) == dt.date(2026, 11, 17)
        assert n.index_day(2026, 11) == dt.date(2026, 11, 10)

    def test_christmas_alignment_turns_on_the_european_return(self):
        """Return = index day + 14. Christmas Eve and Day are cheap travel days."""
        assert not n.late_christmas(2024)   # 10 Dec -> back 24 Dec
        assert not n.late_christmas(2025)   # 9 Dec  -> back 23 Dec
        assert not n.late_christmas(2018)   # 11 Dec -> back 25 Dec
        assert n.late_christmas(2023)       # 12 Dec -> back 26 Dec
        assert n.late_christmas(2022)       # 13 Dec -> back 27 Dec
        assert n.late_christmas(2026)       # 15 Dec -> back 29 Dec


class TestEaster:
    @pytest.mark.parametrize("year,date", [
        (2016, dt.date(2016, 3, 27)), (2024, dt.date(2024, 3, 31)),
        (2025, dt.date(2025, 4, 20)), (2026, dt.date(2026, 4, 5)),
        (2027, dt.date(2027, 3, 28)), (2028, dt.date(2028, 4, 16)),
    ])
    def test_dates(self, year, date):
        assert n.easter(year) == date

    def test_buckets(self):
        assert n.easter_bucket(2027) == "early"
        assert n.easter_bucket(2026) == "late"
        assert n.easter_bucket(2025) == "very_late"


class TestAnalogues:
    YEARS = range(2002, 2026)

    def test_december_uses_only_years_with_the_same_christmas_alignment(self):
        assert n.select_analogues(12, 2026, self.YEARS) == [2023, 2022, 2017]

    def test_january_follows_the_december_before_it(self):
        """2022 is out: the December before it (2021) is a COVID year."""
        assert n.select_analogues(1, 2027, range(2002, 2027)) == [2024, 2023, 2018]

    def test_march_and_april_use_the_same_easter_timing(self):
        assert n.select_analogues(3, 2027, range(2002, 2027)) == [2024, 2016, 2013, 2008, 2005]

    def test_other_months_use_the_last_three_years(self):
        assert n.select_analogues(11, 2026, self.YEARS) == [2025, 2024, 2023]

    def test_covid_years_are_never_used(self):
        assert n.select_analogues(11, 2022, self.YEARS) == [2019, 2018, 2017]

    def test_only_earlier_years(self):
        assert all(y < 2015 for y in n.select_analogues(6, 2015, self.YEARS))


# --- Statistical forecast -----------------------------------------------------
class TestSensitivity:
    def test_recovers_the_slope(self):
        mm = _mm()
        assert n.sensitivity(mm, 2024) == pytest.approx(_beta(2024))

    def test_too_few_months_is_no_answer(self):
        mm = _mm(last=(2026, 4))
        assert n.sensitivity(mm, 2026, range(1, 10)) is None


class TestBaseline:
    def test_translates_air_with_this_years_sensitivity(self):
        mm = _mm()
        b = n.baseline(mm, 2026, 12)
        assert b.beta == pytest.approx(_beta(2026))
        assert b.rpi == pytest.approx(b.nonair + b.beta * b.air)
        assert b.analogues == (2023, 2022, 2017)

    def test_copying_rpi_moves_lags_a_rising_sensitivity(self):
        """The defect that made December read +22% instead of +28%."""
        mm = _mm()
        b = n.baseline(mm, 2026, 12)
        assert b.copy_rpi < b.rpi


class TestBacktest:
    def test_never_sees_the_year_it_forecasts(self):
        mm = _mm()
        clean = n.backtest(mm, 12)
        poisoned = copy.deepcopy(mm)
        for series in poisoned.values():
            for key in list(series):
                if key >= (2024, 1):
                    series[key] *= 50
        dirty = n.backtest(poisoned, 12)
        before = lambda bt: [(y, e) for y, e in bt.air_errors if y < 2024]
        assert before(clean) == before(dirty)

    def test_the_route_is_whichever_did_better(self):
        bt = n.Backtest(5, air_errors=((2020, 3.0),), copy_errors=((2020, 1.0),))
        assert bt.route == "copy" and bt.rmse == 1.0
        bt = n.Backtest(5, air_errors=((2020, 1.0),), copy_errors=((2020, 3.0),))
        assert bt.route == "air" and bt.rmse == 1.0

    def test_the_air_route_wins_when_sensitivity_rises(self):
        assert n.backtest(_mm(), 12).route == "air"


# --- Scraping -----------------------------------------------------------------
class TestDeparture:
    def test_rederived_with_the_pipelines_own_ordinal_rule(self):
        """Schema 1-3 exports carry no departure date; this is the pull's logic."""
        for scrape, want in (("2026-09-10", dt.date(2026, 12, 8)),
                             ("2026-09-14", dt.date(2026, 12, 15))):
            d = dt.date.fromisoformat(scrape)
            assert n.departure_of(_row(scrape, "european", 3, 100)) == want
            assert want == target_departure_date(d, 3, choose_ordinal(d))

    def test_a_stated_departure_wins(self):
        r = _row("2026-09-10", "european", 3, 100, departure="2026-12-15")
        assert n.departure_of(r) == dt.date(2026, 12, 15)


class TestPanelLevel:
    DEC15 = dt.date(2026, 12, 15)

    def _rows(self, days, **kw):
        return [_row(f"2026-09-{d:02d}", "european", 3, 100 + d, **kw) for d in days]

    def test_only_the_index_day_departure_counts(self):
        """12-21 Sep priced 15 Dec; 8-11 Sep priced 8 Dec and must not blend in."""
        level, days = n.panel_level(self._rows(range(8, 22)), "european", 3, self.DEC15)
        assert days == 10

    def test_days_outside_the_collection_window_are_ignored(self):
        rows = self._rows(range(12, 16)) + [_row("2026-09-25", "european", 3, 9999)]
        level, days = n.panel_level(rows, "european", 3, self.DEC15)
        assert days == 4 and level < 200

    def test_too_few_days_is_no_measurement(self):
        assert n.panel_level(self._rows([12, 13]), "european", 3, self.DEC15) is None

    def test_long_haul_reads_the_price_blind_fare(self):
        rows = [_row(f"2026-09-{d}", "long_haul", 3, 1000, cheapest=500) for d in (14, 15, 16)]
        level, _ = n.panel_level(rows, "long_haul", 3, self.DEC15)
        assert level == pytest.approx(500)


def _sub(step_pct, month=10, years=range(2007, 2026)):
    """Sub-indices where every year moves `step_pct` into `month`."""
    out = {}
    for haul, k in n.CELLS:
        s = {}
        for y in years:
            s[(y, month - 1)] = 100.0
            s[(y, month)] = 100.0 * (1 + step_pct / 100)
        out[(haul, k)] = s
    return out


class TestSignal:
    def test_scraped_move_against_the_cells_norm(self):
        """Oct 13 departures from 8-11 September, Nov 10 from 8-13 October."""
        rows = ([_row(f"2026-09-{d:02d}", "european", 1, 100) for d in (8, 9, 10, 11)]
                + [_row(f"2026-10-{d:02d}", "european", 1, 120) for d in (8, 9, 10)])
        sig = n.scrape_signal(rows, _sub(10.0, month=11), 2026, 11)
        (cell,) = sig.cells
        assert cell.scraped_pct == pytest.approx(20.0)
        assert cell.surprise == pytest.approx(10.0)
        assert sig.coverage == pytest.approx(0.337 * 0.710)
        assert sig.air_surprise == pytest.approx(0.337 * 0.710 * 10.0)

    def test_january_is_never_scored_against_a_norm(self):
        """The sub-indices restart at 100 each January: no Dec->Jan step exists."""
        assert n.cell_norm(_sub(5.0, month=1), "european", 3, 2027, 1) is None

    def test_one_side_missing_is_no_signal(self):
        rows = [_row(f"2026-09-{d:02d}", "european", 1, 120) for d in (8, 9, 10, 11)]
        assert n.scrape_signal(rows, _sub(10.0, month=11), 2026, 11).cells == ()

    def test_a_step_whose_base_was_never_priced_is_no_signal(self):
        """Override September to the 8th: August only priced the 15th."""
        rows = ([_row(f"2026-08-{d}", "european", 1, 100) for d in (17, 18, 19)]
                + [_row(f"2026-09-{d:02d}", "european", 1, 120) for d in (8, 9, 10, 11)])
        o = {(2026, 9): dt.date(2026, 9, 8)}
        assert n.scrape_signal(rows, _sub(10.0), 2026, 10, o).cells == ()
        assert n.scrape_signal(rows, _sub(10.0), 2026, 10).cells != ()


class TestReliability:
    def _score(self, x, err):
        return n.Score(2026, 10, actual=err, baseline=0.0, scrape_rpi_pp=x, coverage=0.3)

    def test_the_prior_holds_until_there_is_a_record(self):
        rel, basis = n.reliability([self._score(1.0, 1.0)] * (n.MIN_SCORED - 1))
        assert rel == n.PRIOR_RELIABILITY and "prior" in basis

    def test_then_the_record_decides(self):
        rel, _ = n.reliability([self._score(x, 0.3 * x) for x in (1, -2, 3, 1, 2, -1)])
        assert rel == pytest.approx(0.3)

    def test_it_is_bounded(self):
        assert n.reliability([self._score(x, -x) for x in (1, 2, 3, 1, 2, 1)])[0] == 0.0
        assert n.reliability([self._score(x, 3 * x) for x in (1, 2, 3, 1, 2, 1)])[0] == 1.0

    def test_months_without_a_scraped_surprise_do_not_count(self):
        scores = [self._score(0.0, 5.0)] * 10
        assert n.reliability(scores)[0] == n.PRIOR_RELIABILITY


# --- Assembly -----------------------------------------------------------------
class TestRun:
    def test_a_policy_adjustment_moves_its_month_and_widens_its_range(self):
        mm = _mm()
        adj = n.Adjustment(2027, 1, -2.0, 1.0, "test", "test")
        with_ = n.run(mm, {}, [], horizon=4, policy=(adj,))
        without = n.run(mm, {}, [], horizon=4, policy=())
        a = {(f.year, f.month): f for f in with_.months}
        b = {(f.year, f.month): f for f in without.months}
        assert a[(2027, 1)].mom == pytest.approx(b[(2027, 1)].mom - 2.0)
        assert (a[(2027, 1)].high - a[(2027, 1)].low) > (b[(2027, 1)].high - b[(2027, 1)].low)
        assert a[(2026, 12)].mom == pytest.approx(b[(2026, 12)].mom)

    def test_levels_chain_from_a_hand_supplied_month(self):
        mm = _mm()
        mm["CZFN"][(2026, 10)] = 1.6        # published, but not yet in the data
        nc = n.run(mm, {}, [], horizon=2, policy=())
        assert nc.last_published == (2026, 10)
        assert nc.last_level == pytest.approx(mm["CHBR"][(2026, 9)] * 1.016)
        first = nc.months[0]
        assert (first.year, first.month) == (2026, 11)
        assert first.level == pytest.approx(nc.last_level * (1 + first.mom / 100))

    def test_output_is_strict_json_and_deterministic(self):
        nc = n.run(_mm(), {}, [], horizon=6)
        a = json.dumps(n.to_json(nc), sort_keys=True, allow_nan=False)
        b = json.dumps(n.to_json(n.run(_mm(), {}, [], horizon=6)), sort_keys=True, allow_nan=False)
        assert a == b

    def test_the_report_names_every_month_and_its_route(self):
        text = n.render(n.run(_mm(), {}, [], horizon=6))
        for label in ("Oct 2026", "Dec 2026", "Mar 2027"):
            assert label in text
        assert "Route used" in text


class TestCli:
    def test_parse_known_and_index_days(self):
        assert n.parse_known(["CZFN:2026-10=1.6"]) == [("CZFN", (2026, 10), 1.6)]
        assert n.parse_index_days(["2026-11=17"]) == {(2026, 11): dt.date(2026, 11, 17)}

    def _export(self, tmp_path, mm23=True):
        rows = []
        if mm23:
            for cdid, series in _mm().items():
                for (y, m), v in series.items():
                    rows.append({"period": f"{y}-{m:02d}-01", "cdid": cdid, "value": v})
        path = tmp_path / "analytics.json"
        path.write_text(json.dumps({"mm23_series": rows, "published_series": [],
                                    "daily_by_series": [], "coverage": {}}))
        return path

    def test_writes_the_report_and_the_json(self, tmp_path):
        md, js = tmp_path / "nowcast.md", tmp_path / "nowcast.json"
        rc = n.main(["--export", str(self._export(tmp_path)), "--known", "CZFN:2026-10=1.6",
                     "--out-md", str(md), "--out-json", str(js)])
        assert rc == 0
        assert "Nov 2026" in md.read_text()
        assert json.loads(js.read_text())["last_published"] == [2026, 10]

    def test_no_mm23_is_a_clear_error(self, tmp_path, capsys):
        rc = n.main(["--export", str(self._export(tmp_path, mm23=False))])
        assert rc == 2
        assert "no mm23 data" in capsys.readouterr().err
