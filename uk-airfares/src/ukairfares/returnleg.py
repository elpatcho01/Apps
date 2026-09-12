"""Is the return leg visible in what we already store, and does it move?

THE PROBLEM THIS SIZES

The ONS target-time rule is applied to the outbound and to nothing else. The
return leg is whatever the provider bundled -- in practice Google's cheapest
available return for the chosen outbound -- so half of every priced trip is
selected by a rule the README argues against for the other half: "a cheapest-of-
day rule silently migrates between a 06:10 departure one month and a 21:45 the
next, so much of the resulting price change is just the time-of-day fare curve
moving underneath you."

Worse, `return_at` on a quote is a placeholder: midnight of the date we asked
for, not a departure we observed. So the return is uncontrolled AND unrecorded,
and nobody has measured how much that costs.

THE LIVE HYPOTHESIS IT TESTS

European 1-month stepped **+18% on the price-blind cheapest measure** between the
September and October index months -- above its own 19-year maximum -- while the
rule-selected fare moved -1.7%. Nothing in the selection rule can do that, and
composition was ruled out (all nine routes priced on all twelve days).

The trips differ in a way that fits. September departs 8 Sep and returns 22 Sep,
both term-time. October departs 13 Oct and returns **27 Oct, inside UK autumn
half-term**. If cheap returns dry up in half-term, the cheapest TOTAL rises
sharply while the rule's already-dearer combination moves much less -- which is
the exact shape observed. If that is what happened, an uncontrolled return leg
explains an anomaly currently marked unexplained.

WHY THIS IS A CENSUS, NOT A PARSER

Nobody knows whether the round-trip payload carries return-leg detail at all.
SerpApi's documented flow selects the return in a SECOND call against a
`departure_token`, which implies the first response may carry none. So this does
not assume a shape and then fail to find one: it walks every payload, counts
every key path, and reports which keys exist and in what share of rows.

That is the method the sibling accommodation project used to settle the same kind
of question -- a raw-key census over 214 live properties found `free_cancellation`
in the key set of NONE of them, which turned an argument into a fact and stopped
a control being designed against a field that was never there.

The answer this produces is one of:

  * return detail IS present  -> measure the variation, no API spend needed
  * return detail is ABSENT   -> controlling it requires the second call, and
                                 the cost is real rather than hypothetical

Either way the spend decision stops being a judgement call. Nothing is written.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import logging
import re
import statistics
import sys
from typing import Any

from . import bq
from .config import Config, ConfigError

log = logging.getLogger("ukairfares.returnleg")

#: Key fragments that would plausibly carry a return leg. Matched case-insensitively
#: against the full key PATH, so a `return` nested under `flights` is caught even
#: where the leaf is named something else.
RETURN_HINTS = ("return", "inbound", "homeward", "back", "second_leg", "leg2")

#: Key fragments that carry a departure time, reused from the same walk.
TIME_HINTS = ("departure_time", "departure_at", "departs", "depart_time", "time")

#: The key SerpApi hands back to select a return leg in a second call. Whether it
#: is present decides what "buy the return" actually costs: with it, one extra
#: search per query; without it, the outbound has to be re-queried first.
TOKEN_HINT = "departure_token"

PAYLOADS = """
SELECT index_month_departure, route, haul_category, months_ahead,
       departure_date, return_date, raw_response
FROM `{view}`
WHERE status = 'ok' AND raw_response IS NOT NULL
"""


def key_paths(payload: Any, prefix: str = "", depth: int = 0) -> set[str]:
    """Every key path in a payload, e.g. `best_flights[].flights[].airline`.

    Lists collapse to `[]` so a hundred itineraries produce one path rather than
    a hundred, which is what makes the census readable.
    """
    if depth > 12:
        return set()
    out: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.add(path)
            out |= key_paths(value, path, depth + 1)
    elif isinstance(payload, list):
        for item in payload[:20]:
            out |= key_paths(item, f"{prefix}[]", depth + 1)
    return out


def _loads(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None
    return raw


def census(rows) -> dict[str, Any]:
    """How often each key path appears, and which of them mention a return."""
    counts: collections.Counter[str] = collections.Counter()
    total = 0
    for row in rows:
        payload = _loads(row["raw_response"])
        if payload is None:
            continue
        total += 1
        for path in key_paths(payload):
            counts[path] += 1
    return_paths = {
        p: n for p, n in counts.items()
        if any(h in p.lower() for h in RETURN_HINTS)
    }
    token_rows = max((n for p, n in counts.items() if TOKEN_HINT in p.lower()),
                     default=0)
    return {"rows": total, "paths": counts, "return_paths": return_paths,
            "token_rows": token_rows}


def return_times(payload: Any) -> list[int]:
    """Departure minutes found under any key path that mentions a return."""
    found: list[int] = []
    stack: list[tuple[Any, str]] = [(payload, "")]
    while stack:
        node, path = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}" if path else str(key)
                looks_return = any(h in child.lower() for h in RETURN_HINTS)
                is_time = any(h in str(key).lower() for h in TIME_HINTS)
                if looks_return and is_time and not isinstance(value, (dict, list)):
                    minutes = _to_minutes(str(value))
                    if minutes is not None:
                        found.append(minutes)
                if isinstance(value, (dict, list)):
                    stack.append((value, child))
        elif isinstance(node, list):
            for item in node[:50]:
                stack.append((item, f"{path}[]"))
    return found


#: A 24-hour clock time anywhere in a string. Anchored to both ends so "99:99"
#: and a duration like "1 hr 20 min" are rejected rather than half-read.
_CLOCK = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def _to_minutes(value: Any) -> int | None:
    """Minutes past midnight from whatever shape the provider used.

    Timestamps, bare clock times and clock times embedded in a longer string all
    read; durations and prices do not, because a number that is not a time of day
    would quietly become one.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return stamp.hour * 60 + stamp.minute
    except (ValueError, TypeError):
        pass
    match = _CLOCK.search(text)
    return int(match.group(1)) * 60 + int(match.group(2)) if match else None


def variation(rows) -> dict[str, Any]:
    """Observed return departure times per (series, index month), where present."""
    seen: dict[tuple, list[int]] = collections.defaultdict(list)
    for row in rows:
        payload = _loads(row["raw_response"])
        if payload is None:
            continue
        times = return_times(payload)
        if times:
            key = (row["haul_category"], row["months_ahead"],
                   str(row["index_month_departure"]))
            seen[key].extend(times)
    out: dict[str, Any] = {}
    for (haul, window, month), times in sorted(seen.items()):
        out[f"{haul}|{window}|{month}"] = {
            "n": len(times),
            "median": f"{int(statistics.median(times)) // 60:02d}:"
                      f"{int(statistics.median(times)) % 60:02d}",
            "spread_minutes": (max(times) - min(times)) if len(times) > 1 else 0,
        }
    return out


def report(cen: dict[str, Any], var: dict[str, Any]) -> str:
    lines = ["Return leg: is it in the payload we already store?", ""]
    if not cen["rows"]:
        return "\n".join(lines + ["No payloads readable. Nothing to say."])

    lines.append(f"Payloads examined: {cen['rows']}")
    if cen["return_paths"]:
        lines += ["", "Key paths mentioning a return leg:"]
        for path, n in sorted(cen["return_paths"].items(), key=lambda kv: -kv[1])[:20]:
            lines.append(f"  {n / cen['rows'] * 100:5.1f}% of rows   {path}")
    else:
        lines += ["", "NO KEY PATH MENTIONS A RETURN LEG, in any row.",
                  "The outbound response does not carry it, so controlling the return",
                  "requires the second departure_token call. The cost is real, not",
                  "hypothetical: +719 searches/month to cover every collection day."]

    share = cen["token_rows"] / cen["rows"] * 100
    if cen["token_rows"]:
        lines += ["", f"departure_token present in {share:.1f}% of rows, so the second",
                  "call can be made against payloads already stored: one extra search",
                  "per query, no re-querying of the outbound."]
    else:
        lines += ["", "NO departure_token in any row either. The second call would have",
                  "to re-query the outbound first, so controlling the return costs two",
                  "searches per query rather than one. Check the parser keeps the token",
                  "before costing the fix."]

    if var:
        lines += ["", "Observed return departure times, where present:",
                  f"  {'series / index month':34} {'n':>5} {'median':>7} {'spread':>8}"]
        for name, v in var.items():
            lines.append(f"  {name:34} {v['n']:5} {v['median']:>7} {v['spread_minutes']:7}m")
        lines += ["", "A large spread, or a median that moves between index months,",
                  "means the return leg is carrying price variation the outbound rule",
                  "never sees."]
    elif cen["return_paths"]:
        lines += ["", "Return keys exist but no parseable departure time was found in",
                  "them. Worth looking at the paths above by hand before spending."]

    lines += ["", "The live question: European 1m stepped +18% on the cheapest measure",
              "between Sep and Oct 2026 while the rule moved -1.7%. October's trip",
              "returns 27 Oct, inside half-term; September's returns 22 Sep, term-time.",
              "If the return medians above differ between those two months, that is",
              "the explanation."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--all-paths", action="store_true",
                        help="list every key path, not only return-related ones")
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
    rows = list(reader.query(PAYLOADS.format(view=config.table_ref("current_scrapes"))))
    cen, var = census(rows), variation(rows)

    if args.json:
        print(json.dumps({"rows": cen["rows"],
                          "return_paths": cen["return_paths"],
                          "all_paths": dict(cen["paths"]) if args.all_paths else None,
                          "variation": var}, indent=2, sort_keys=True))
    else:
        print(report(cen, var))
        if args.all_paths:
            print("\nEvery key path observed:")
            for path, n in sorted(cen["paths"].items()):
                print(f"  {n / max(cen['rows'], 1) * 100:5.1f}%  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
