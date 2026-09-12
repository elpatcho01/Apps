"""Measuring whether matched-model pricing would be quieter, before adopting it.

Our rule re-picks the nearest-to-target flight every month, so the choice can
migrate between aircraft because the provider's result set changed rather than
because fares did. CPI practice is matched-model: price the same item month to
month, substitute only when it disappears. That would remove the flipping by
construction -- but it is a bet on how ONS operate, which they have not
published, and a rule that is stable because it ignores substitution is not
automatically a rule that is right.

So this module replays both over `raw_response` and reports which is quieter.
These tests pin the replay, the substitution accounting, and -- most importantly
-- that it refuses to answer while the panel is too short to have an answer.
"""

import json

from ukairfares.matched import MIN_INDEX_MONTHS, candidates, replay, report


def _payload(flights):
    return json.dumps({"best_flights": [
        {"price": price,
         "flights": [{"flight_number": num, "airline": "BA",
                      "departure_airport": {"time": f"2026-09-08 {time}"}}]}
        for num, time, price in flights
    ]})


def _row(month, route, window, flights, target="12:00", day="2026-08-18"):
    return {
        "index_month_departure": month, "route": route, "haul_category": "long_haul",
        "months_ahead": window, "scrape_date": day,
        "target_departure_time": target, "raw_response": _payload(flights),
    }


class TestReadingCandidates:
    def test_pulls_number_time_and_price(self):
        out = candidates(_payload([("BA 059", "11:55", 600)]))
        assert out == [{"flight_number": "BA 059", "airline": "BA",
                        "minutes": 11 * 60 + 55, "price": 600.0}]

    def test_skips_unpriced_and_unparseable(self):
        assert candidates("not json") == []
        assert candidates(json.dumps({"best_flights": [{"flights": []}]})) == []


class TestReplay:
    def test_matched_rule_holds_the_flight_when_it_survives(self):
        """The whole point: the tracked service cannot flip to another aircraft."""
        rows = [
            _row("2026-09-01", "LHR-JFK", 1, [("BA 059", "11:55", 600),
                                              ("VS 003", "12:05", 900)]),
            # BA 059 still there, but VS 003 is now marginally nearer the target.
            _row("2026-10-01", "LHR-JFK", 1, [("BA 059", "11:50", 620),
                                              ("VS 003", "12:00", 1400)]),
        ]
        out = replay(rows)["LHR-JFK|1"]
        assert out["tracked_flight"] == "BA 059"
        assert out["substitutions"] == 0
        # Re-picking jumps to the dearer aircraft; matched-model does not.
        assert out["matched_rule_median_move"] < out["target_rule_median_move"]

    def test_substitution_is_counted_when_the_flight_disappears(self):
        rows = [
            _row("2026-09-01", "LHR-JFK", 1, [("BA 059", "11:55", 600)]),
            _row("2026-10-01", "LHR-JFK", 1, [("BA 117", "12:10", 700)]),
        ]
        out = replay(rows)["LHR-JFK|1"]
        assert out["substitutions"] == 1
        assert out["tracked_flight"] == "BA 117"

    def test_one_payload_per_index_month_the_earliest_day(self):
        """Using every collection day would mix index-day timing noise in."""
        rows = [
            _row("2026-09-01", "LHR-JFK", 1, [("BA 059", "11:55", 600)], day="2026-08-18"),
            _row("2026-09-01", "LHR-JFK", 1, [("BA 059", "11:55", 999)], day="2026-08-20"),
            _row("2026-10-01", "LHR-JFK", 1, [("BA 059", "11:55", 600)], day="2026-09-08"),
        ]
        out = replay(rows)["LHR-JFK|1"]
        assert out["index_months"] == 2
        assert out["matched_rule_median_move"] == 0.0, "the 999 day should not be used"

    def test_a_single_index_month_yields_no_comparison(self):
        rows = [_row("2026-09-01", "LHR-JFK", 1, [("BA 059", "11:55", 600)])]
        assert replay(rows) == {}


class TestRefusesToAnswerTooEarly:
    """The failure this whole session kept finding: a confident number too soon."""

    def test_two_months_is_marked_not_enough(self):
        rows = [
            _row("2026-09-01", "LHR-JFK", 1, [("BA 059", "11:55", 600)]),
            _row("2026-10-01", "LHR-JFK", 1, [("BA 059", "11:55", 700)]),
        ]
        out = replay(rows)["LHR-JFK|1"]
        assert out["index_months"] == 2 < MIN_INDEX_MONTHS
        assert out["enough"] is False

    def test_the_report_says_so_rather_than_declaring_a_winner(self):
        rows = [
            _row("2026-09-01", "LHR-JFK", 1, [("BA 059", "11:55", 600)]),
            _row("2026-10-01", "LHR-JFK", 1, [("BA 059", "11:55", 700)]),
        ]
        text = report(replay(rows))
        assert "NO SERIES HAS ENOUGH HISTORY YET" in text
        assert "February 2027" in text

    def test_three_months_is_enough_to_be_counted(self):
        rows = [
            _row("2026-09-01", "LHR-JFK", 1, [("BA 059", "11:55", 600)]),
            _row("2026-10-01", "LHR-JFK", 1, [("BA 059", "11:55", 700)]),
            _row("2026-11-01", "LHR-JFK", 1, [("BA 059", "11:55", 650)]),
        ]
        out = replay(rows)["LHR-JFK|1"]
        assert out["enough"] is True
        assert "NO SERIES HAS ENOUGH HISTORY" not in report(replay(rows))


class TestNothingIsWritten:
    def test_module_exposes_no_writer(self):
        """It measures a methodology change; it must not make one."""
        import ukairfares.matched as m
        source = __import__("pathlib").Path(m.__file__).read_text(encoding="utf-8")
        for forbidden in ("INSERT", "UPDATE ", "load_table", "write_rows", "ALTER"):
            assert forbidden not in source, f"{forbidden} in a measurement module"
