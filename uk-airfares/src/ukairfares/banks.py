"""Measure each route's departure bank, so the target time is observed not guessed.

WHY THIS EXISTS

The ONS rule takes the flight departing nearest a fixed target time. That is only
a rule if aircraft actually depart near the target. Where they do not, "nearest"
is a near-tie among candidates that are all hours away, and which one wins is
decided by whichever flights happen to be in the provider's result set that day.
The choice then moves between different aircraft carrying different fare buckets,
and the series acquires movement the market never made.

That failure was first blamed on the haul category and the long-haul target moved
from 09:00 to 12:00. Measuring it properly says otherwise. On 2026-09-11, mean
minutes from the 12:00 target:

    LHR-CPT  467      LGW-MCO   60
    LHR-DXB  115      LGW-JFK   20
    LHR-SIN   67      LHR-JFK   15

Excluding LHR-CPT, long-haul sits 52-62 minutes off target -- better than
domestic (63) and close to European (40). The target is fine for five routes of
six. The entire miss is one sector, because LHR-CPT is an ~11.5-hour overnight
timed to arrive in the morning: it departs 18:25 and 22:30 and there is no midday
service. No single clock time can serve CPT, JFK (11:55) and DXB (14:25) at once.

So the bank is a property of the SECTOR -- its length and its destination time
zone -- not of the haul bucket. This module reads the real candidate departure
times out of `raw_response` and proposes a target per route.

WHY IT PROPOSES RATHER THAN APPLIES

The output is printed for pasting into `TARGET_DEPARTURE_TIME_BY_ROUTE`, not
written back automatically, for the same reason the route panel and the ONS
weights are pinned constants: a target time that moved on its own would
reintroduce exactly the drift the fixed target exists to prevent. ONS hold theirs
constant month to month; so must we. This is a decision made once, on evidence,
and then frozen.

It also reads the CANDIDATE distribution, never the selected flight. The selected
flight is whatever was nearest the target already, so deriving a target from it
would just re-converge on the existing value -- a measurement that confirms
itself. `raw_response` retains every quote, which is what makes the honest
version possible over data already collected.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import logging
import statistics
import sys
from typing import Any

from . import bq
from .config import Config, ConfigError

log = logging.getLogger("ukairfares.banks")

#: Below this many observed departures a route's bank is not worth pinning --
#: one thin week would set a constant that outlives it.
MIN_OBSERVATIONS = 20

#: A bank wider than this is not a bank. Pinning a target into the middle of a
#: genuinely spread-out timetable buys nothing, and says so in the output rather
#: than silently emitting a number that looks as authoritative as the rest.
MAX_BANK_SPREAD_MINUTES = 240

CANDIDATES = """
SELECT route, haul_category, raw_response
FROM `{view}`
WHERE status = 'ok' AND raw_response IS NOT NULL
"""


def _minutes(value: str) -> int | None:
    """Minutes past midnight from an ISO timestamp or a bare HH:MM."""
    if not value:
        return None
    try:
        if "T" in value or " " in value:
            stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return stamp.hour * 60 + stamp.minute
        hh, _, mm = value.partition(":")
        return int(hh) * 60 + int(mm)
    except (ValueError, TypeError):
        return None


#: Leaf keys that carry a departure time on their own.
DEPARTURE_KEYS = {"departure_time", "departure_at", "departure", "departsat",
                  "depart_time"}

#: Leaf keys that carry a time only when the PATH says which time it is.
#: SerpApi is the case that matters: the departure sits at
#: `best_flights[].flights[].departure_airport.time`, where the leaf is just
#: `time` and every bit of meaning is in the parent.
AMBIGUOUS_TIME_KEYS = {"time", "at", "datetime"}


def _is_departure(key: str, path: str) -> bool:
    """Does this leaf carry the OUTBOUND departure time?

    Three exclusions, each for a time that would otherwise be averaged into the
    bank and move it: arrivals, return legs, and layover segments.
    """
    key, path = key.lower(), path.lower()
    if any(word in path for word in ("arrival", "return", "inbound", "layover")):
        return False
    if key in DEPARTURE_KEYS:
        return True
    return key in AMBIGUOUS_TIME_KEYS and "departure" in path


def departure_minutes(payload: Any) -> list[int]:
    """Every candidate departure time in one provider payload.

    Matched on the KEY PATH, not the leaf key, and that is the whole lesson of
    this function. It originally matched leaf keys only -- `departure_time`,
    `departure_at` and friends -- and found nothing in a single live payload,
    because SerpApi puts the departure at `departure_airport.time`: the leaf is
    `time` and the meaning is entirely in the parent. The first live run printed
    "no candidate departure times found in raw_response" against a panel where
    every row had one.

    The tests missed it because their fixtures were written from the same
    assumption as the code. They now use the provider's real shape.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return []
    found: list[int] = []
    stack: list[tuple[Any, str]] = [(payload, "")]
    while stack:
        node, path = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}" if path else str(key)
                if isinstance(value, (dict, list)):
                    stack.append((value, child))
                elif _is_departure(str(key), child):
                    minutes = _minutes(str(value))
                    if minutes is not None:
                        found.append(minutes)
        elif isinstance(node, list):
            for item in node:
                stack.append((item, f"{path}[]"))
    return found


def _circular_centre(minutes: list[int]) -> int:
    """Median departure, handling the wrap past midnight.

    A route with departures at 23:40 and 00:20 has a bank centred on midnight,
    not on midday, and a plain median would return the latter.
    """
    plain = statistics.median(minutes)
    shifted = [(m + 720) % 1440 for m in minutes]
    shifted_median = statistics.median(shifted)
    spread = lambda xs, c: sum(min(abs(x - c), 1440 - abs(x - c)) for x in xs)  # noqa: E731
    if spread(minutes, (shifted_median - 720) % 1440) < spread(minutes, plain):
        return int((shifted_median - 720) % 1440)
    return int(plain)


def banks(rows) -> dict[str, dict[str, Any]]:
    """Observed departure bank per route."""
    seen: dict[str, list[int]] = collections.defaultdict(list)
    hauls: dict[str, str] = {}
    for row in rows:
        route = row["route"]
        hauls[route] = row["haul_category"]
        seen[route].extend(departure_minutes(row["raw_response"]))

    out: dict[str, dict[str, Any]] = {}
    for route, minutes in sorted(seen.items()):
        if not minutes:
            continue
        centre = _circular_centre(minutes)
        deviations = [min(abs(m - centre), 1440 - abs(m - centre)) for m in minutes]
        # The interquartile spread, not the full range: one repositioned night
        # flight should not make a tight morning bank look diffuse.
        quartile = statistics.quantiles(deviations, n=4)[2] if len(deviations) > 3 \
            else max(deviations)
        out[route] = {
            "haul": hauls[route],
            "n": len(minutes),
            "centre_minutes": centre,
            "centre": f"{centre // 60:02d}:{centre % 60:02d}",
            "spread_minutes": int(quartile),
            "enough": len(minutes) >= MIN_OBSERVATIONS,
            "tight": quartile <= MAX_BANK_SPREAD_MINUTES,
        }
    return out


def render(measured: dict[str, dict[str, Any]]) -> str:
    """The block to paste into TARGET_DEPARTURE_TIME_BY_ROUTE, plus what was rejected."""
    lines = ["TARGET_DEPARTURE_TIME_BY_ROUTE: dict[str, dt.time] = {"]
    skipped: list[str] = []
    for route, bank in measured.items():
        if not bank["enough"]:
            skipped.append(f"{route}: only {bank['n']} observations "
                           f"(need {MIN_OBSERVATIONS})")
            continue
        if not bank["tight"]:
            skipped.append(f"{route}: bank spread {bank['spread_minutes']}min "
                           f"exceeds {MAX_BANK_SPREAD_MINUTES}; no usable centre")
            continue
        h, m = divmod(bank["centre_minutes"], 60)
        lines.append(f'    "{route}": dt.time({h}, {m}),'
                     f'  # n={bank["n"]}, +/-{bank["spread_minutes"]}min')
    lines.append("}")
    if skipped:
        lines += ["", "# Not pinned, and why:"] + [f"#   {s}" for s in skipped]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true",
                        help="emit the measurements rather than the paste block")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"::error::configuration error: {exc}", file=sys.stderr, flush=True)
        return 2

    reader = bq.BigQueryWriter(config.project)
    rows = reader.query(CANDIDATES.format(view=config.table_ref("current_scrapes")))
    measured = banks(rows)
    if not measured:
        print("::warning::no candidate departure times found in raw_response",
              file=sys.stderr, flush=True)
        return 1

    print(json.dumps(measured, indent=2, sort_keys=True) if args.json
          else render(measured))
    return 0


if __name__ == "__main__":
    sys.exit(main())
