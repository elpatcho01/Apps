"""Measuring the departure bank, and targeting it per route rather than per haul.

The long-haul target was moved from 09:00 to 12:00 on the theory that long-haul
shares one departure bank. Measurement says it does not. On 2026-09-11 five of
the six long-haul routes sat 15-115 minutes from the 12:00 target -- as good as
short-haul -- while LHR-CPT sat 467 minutes away, because it is an overnight
sector that departs 18:25 and 22:30 and has no midday service at all.

So the bank belongs to the sector, not the haul, and the target has to be
measured rather than reasoned about. These tests pin the mechanism that makes
that possible, and the guards that stop it pinning a number it should not.
"""

import datetime as dt
import json

from ukairfares import onscal
from ukairfares.banks import (
    MAX_BANK_SPREAD_MINUTES,
    MIN_OBSERVATIONS,
    banks,
    departure_minutes,
    render,
)


class TestTargetResolution:
    def test_falls_back_to_haul_when_the_route_is_unmeasured(self):
        assert onscal.target_departure_time_for("long_haul") == dt.time(12, 0)
        assert onscal.target_departure_time_for("domestic") == dt.time(9, 0)

    def test_route_target_wins_over_the_haul(self, monkeypatch):
        monkeypatch.setitem(onscal.TARGET_DEPARTURE_TIME_BY_ROUTE,
                            "LHR-CPT", dt.time(18, 30))
        assert onscal.target_departure_time_for("long_haul", route="LHR-CPT") \
            == dt.time(18, 30)
        # A sibling long-haul route is untouched by it.
        assert onscal.target_departure_time_for("long_haul", route="LHR-JFK") \
            == dt.time(12, 0)

    def test_override_still_beats_everything(self, monkeypatch):
        """TARGET_DEPARTURE_TIME in the environment must stay a global escape."""
        monkeypatch.setitem(onscal.TARGET_DEPARTURE_TIME_BY_ROUTE,
                            "LHR-CPT", dt.time(18, 30))
        assert onscal.target_departure_time_for(
            "long_haul", dt.time(9, 0), route="LHR-CPT") == dt.time(9, 0)

    def test_the_old_two_argument_call_still_works(self):
        """Callers that do not know the route must keep haul behaviour."""
        assert onscal.target_departure_time_for("long_haul", None) == dt.time(12, 0)

    def test_ships_empty_so_behaviour_is_unchanged_until_measured(self):
        """A guessed constant is what this exists to replace, not to become.

        Shipping provisional times derived from one day's selected flights would
        repeat the mistake it corrects -- the selected flight is already biased
        toward the current target, so a target derived from it confirms itself.
        """
        assert onscal.TARGET_DEPARTURE_TIME_BY_ROUTE == {}


def _serpapi(*times, arrival="2026-10-13 15:30"):
    """One payload shaped the way the live provider actually shapes it.

    The departure is at `best_flights[].flights[].departure_airport.time`. The
    leaf key is `time`; all the meaning is in the parent. Fixtures that invent a
    flat `departure_time` key are why this module shipped unable to read a single
    real payload.
    """
    return {"best_flights": [
        {"price": 214, "flights": [{
            "departure_airport": {"id": "LHR", "time": t},
            "arrival_airport": {"id": "JFK", "time": arrival},
            "airline": "BA", "flight_number": "BA 117",
        }]}
        for t in times
    ]}


class TestReadingCandidateTimes:
    def test_reads_the_shape_the_provider_actually_returns(self):
        """The regression. This payload is SerpApi's, not an invented one."""
        payload = _serpapi("2026-10-13 11:55", "2026-10-13 14:25")
        assert sorted(departure_minutes(payload)) == [11 * 60 + 55, 14 * 60 + 25]

    def test_the_arrival_beside_it_is_not_counted(self):
        """Averaging arrivals into the bank moves the target hours late."""
        payload = _serpapi("06:10", arrival="09:45")
        assert departure_minutes(payload) == [6 * 60 + 10]

    def test_a_return_leg_is_not_counted(self):
        """The bank being measured is the outbound's."""
        payload = {"return_flights": [
            {"departure_airport": {"time": "21:45"}}]}
        assert departure_minutes(payload) == []

    def test_a_layover_is_not_counted(self):
        """A connection departs too, and it is not a candidate."""
        payload = {"flights": [{"departure_airport": {"time": "07:00"}}],
                   "layovers": [{"departure_airport": {"time": "12:30"}}]}
        assert departure_minutes(payload) == [7 * 60]

    def test_a_flat_departure_key_still_reads(self):
        """Other providers nest it differently; none should need a rewrite."""
        payload = {"best_flights": [{"flights": [
            {"departure_time": "2026-10-13 11:55"}]}]}
        assert departure_minutes(payload) == [11 * 60 + 55]

    def test_accepts_a_json_string(self):
        raw = json.dumps(_serpapi("08:40"))
        assert departure_minutes(raw) == [8 * 60 + 40]

    def test_unparseable_payload_yields_nothing_rather_than_raising(self):
        assert departure_minutes("not json at all") == []
        assert departure_minutes(_serpapi("elevenish")) == []


def _rows(route, haul, times, n=1):
    """Panel rows carrying the provider's real payload shape, not a stand-in."""
    return [{"route": route, "haul_category": haul,
             "raw_response": json.dumps(_serpapi(*times))}
            for _ in range(n)]


class TestBankMeasurement:
    def test_centres_on_the_observed_cluster(self):
        out = banks(_rows("LHR-JFK", "long_haul", ["11:30", "11:55", "12:20"], n=10))
        assert out["LHR-JFK"]["centre"] == "11:55"

    def test_an_evening_sector_centres_in_the_evening(self):
        """The case the haul-level target cannot serve."""
        out = banks(_rows("LHR-CPT", "long_haul", ["18:25", "19:00", "22:30"], n=10))
        centre = out["LHR-CPT"]["centre_minutes"]
        assert 18 * 60 <= centre <= 23 * 60, f"centred at {out['LHR-CPT']['centre']}"

    def test_a_bank_spanning_midnight_does_not_centre_on_midday(self):
        """23:40 and 00:20 are 40 minutes apart, not 23 hours."""
        out = banks(_rows("LHR-XXX", "long_haul", ["23:40", "00:20"], n=12))
        centre = out["LHR-XXX"]["centre_minutes"]
        assert centre > 23 * 60 or centre < 60, f"centred at {out['LHR-XXX']['centre']}"


class TestGuards:
    def test_a_thin_route_is_not_pinned(self):
        out = banks(_rows("LHR-XXX", "long_haul", ["11:00"], n=2))
        assert out["LHR-XXX"]["n"] < MIN_OBSERVATIONS
        block = render(out)
        assert "LHR-XXX" not in block.split("# Not pinned")[0]
        assert "only 2 observations" in block

    def test_a_diffuse_timetable_is_not_pinned(self):
        """Pinning a target into the middle of an all-day timetable buys nothing."""
        spread = [f"{h:02d}:00" for h in range(5, 23)]
        out = banks(_rows("LHR-YYY", "long_haul", spread, n=4))
        assert out["LHR-YYY"]["spread_minutes"] > MAX_BANK_SPREAD_MINUTES
        block = render(out)
        assert "LHR-YYY" not in block.split("# Not pinned")[0]
        assert "no usable centre" in block

    def test_the_rendered_block_is_valid_python(self):
        out = banks(_rows("LHR-JFK", "long_haul", ["11:30", "11:55", "12:20"], n=10))
        block = render(out).split("# Not pinned")[0]
        namespace: dict = {"dt": dt}
        exec(block, namespace)  # noqa: S102 - the point is that it parses
        assert namespace["TARGET_DEPARTURE_TIME_BY_ROUTE"]["LHR-JFK"] == dt.time(11, 55)

    def test_the_block_records_the_evidence_behind_each_number(self):
        """A pinned constant with no provenance is the thing being replaced."""
        out = banks(_rows("LHR-JFK", "long_haul", ["11:30", "11:55"], n=15))
        assert "n=30" in render(out)
