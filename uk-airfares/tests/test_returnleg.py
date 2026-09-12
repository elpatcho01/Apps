"""Half of every priced trip is chosen by a rule we argue against for the other half.

The ONS target-time rule picks the outbound. The return is whatever the provider
bundled, and `return_at` on a quote is a placeholder -- midnight of the date we
asked for -- so it is uncontrolled AND unrecorded. Before spending API budget to
control it, the cheap question is whether the payloads already stored say
anything about it at all.

These tests pin a census that can answer that honestly in both directions: it has
to find return detail where it exists, and it has to say plainly that there is
none where there is none. The second half is the one that matters, because an
absence reported as a shrug is what lets a control get designed against a field
that was never there.
"""

import json

from ukairfares import returnleg
from ukairfares.returnleg import census, key_paths, report, return_times, variation


def _row(payload, *, haul="european", window=1, month="2026-10-01"):
    return {"index_month_departure": month, "route": "LGW-BCN",
            "haul_category": haul, "months_ahead": window,
            "departure_date": "2026-10-13", "return_date": "2026-10-27",
            "raw_response": json.dumps(payload)}


class TestKeyPaths:
    def test_lists_collapse_so_the_census_stays_readable(self):
        """A hundred itineraries must produce one path, not a hundred."""
        payload = {"best_flights": [{"airline": "BA"}, {"airline": "IB"}]}
        assert key_paths(payload) == {"best_flights", "best_flights[].airline"}

    def test_nesting_is_walked_to_the_leaf(self):
        payload = {"a": {"b": {"c": 1}}}
        assert "a.b.c" in key_paths(payload)

    def test_a_cyclic_depth_is_bounded_rather_than_recursing_forever(self):
        payload = {}
        node = payload
        for _ in range(40):
            node["next"] = {}
            node = node["next"]
        assert len(key_paths(payload)) <= 13


class TestCensus:
    def test_reports_the_share_of_rows_each_path_appears_in(self):
        rows = [_row({"best_flights": [{"airline": "BA"}]}),
                _row({"best_flights": [{"airline": "BA"}], "price_insights": {}})]
        out = census(rows)
        assert out["rows"] == 2
        assert out["paths"]["best_flights[].airline"] == 2
        assert out["paths"]["price_insights"] == 1

    def test_finds_a_return_leg_wherever_it_is_nested(self):
        rows = [_row({"itineraries": [{"return_flights": [
            {"departure_time": "2026-10-27 18:40"}]}]})]
        out = census(rows)
        assert any("return_flights" in p for p in out["return_paths"])

    def test_says_so_plainly_when_no_row_mentions_a_return(self):
        """The finding this exists for, and the one easiest to report as a shrug."""
        rows = [_row({"best_flights": [{"flights": [
            {"departure_time": "2026-10-13 11:55"}]}]}) for _ in range(5)]
        out = census(rows)
        assert out["return_paths"] == {}
        text = report(out, variation(rows))
        assert "NO KEY PATH MENTIONS A RETURN LEG" in text
        assert "departure_token" in text

    def test_an_unreadable_payload_is_skipped_rather_than_raising(self):
        rows = [{"raw_response": "not json at all", "haul_category": "european",
                 "months_ahead": 1, "index_month_departure": "2026-10-01"}]
        out = census(rows)
        assert out["rows"] == 0
        assert "Nothing to say" in report(out, {})


class TestTheToken:
    def test_counts_the_token_because_it_decides_what_the_fix_costs(self):
        rows = [_row({"best_flights": [{"departure_token": "abc"}]}),
                _row({"best_flights": [{"airline": "BA"}]})]
        out = census(rows)
        assert out["token_rows"] == 1
        assert "departure_token present in 50.0% of rows" in report(out, {})

    def test_its_absence_doubles_the_quoted_cost(self):
        rows = [_row({"best_flights": [{"airline": "BA"}]})]
        text = report(census(rows), {})
        assert "NO departure_token in any row" in text
        assert "two" in text and "searches per query" in text


class TestReadingReturnTimes:
    def test_reads_a_time_under_a_return_path(self):
        payload = {"return_flights": [{"departure_time": "2026-10-27 18:40"}]}
        assert return_times(payload) == [18 * 60 + 40]

    def test_ignores_the_outbound_sitting_beside_it(self):
        payload = {"flights": [{"departure_time": "2026-10-13 06:10"}],
                   "inbound": {"departure_time": "21:45"}}
        assert return_times(payload) == [21 * 60 + 45]

    def test_a_duration_is_not_mistaken_for_a_time(self):
        payload = {"return_flights": [{"total_duration_time": "1 hr 20 min"}]}
        assert return_times(payload) == []

    def test_an_impossible_clock_is_rejected(self):
        payload = {"inbound": {"departure_time": "99:99"}}
        assert return_times(payload) == []

    def test_a_clock_inside_a_longer_string_still_reads(self):
        payload = {"inbound": {"departure_time": "departs 18:40 local"}}
        assert return_times(payload) == [18 * 60 + 40]


class TestVariation:
    def test_reports_the_median_and_spread_per_series_and_index_month(self):
        rows = [_row({"inbound": {"departure_time": t}}, month="2026-10-01")
                for t in ("07:00", "12:00", "19:00")]
        out = variation(rows)
        entry = out["european|1|2026-10-01"]
        assert entry["n"] == 3
        assert entry["median"] == "12:00"
        assert entry["spread_minutes"] == 12 * 60

    def test_two_index_months_stay_separate_so_a_move_is_visible(self):
        """The live hypothesis is a median that MOVES between Sep and Oct."""
        rows = [_row({"inbound": {"departure_time": "09:00"}}, month="2026-09-01"),
                _row({"inbound": {"departure_time": "21:00"}}, month="2026-10-01")]
        out = variation(rows)
        assert out["european|1|2026-09-01"]["median"] == "09:00"
        assert out["european|1|2026-10-01"]["median"] == "21:00"
        assert "carrying price variation" in report(census(rows), out)


class TestItOnlyLooks:
    def test_the_module_never_writes(self):
        """A diagnostic that mutates the panel it measures is not a diagnostic."""
        source = (returnleg.__file__)
        text = open(source).read()
        for forbidden in ("INSERT", "UPDATE ", "DELETE", "MERGE", "load_table",
                          "insert_rows", "CREATE "):
            assert forbidden not in text, forbidden
