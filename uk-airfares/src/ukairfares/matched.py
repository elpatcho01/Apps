"""Would matched-model pricing be quieter than re-picking every month?

THE QUESTION

CPI prices the same item month to month, substituting only when it disappears.
Our rule re-picks from scratch each month: whichever flight departs nearest the
target wins, and the winner can change because the provider's result set changed
rather than because anything about fares did. Long-haul moves 2.9-8.4x more than
the price-blind control on identical queries; short-haul sits at 0.7-2.2x.

Matched-model would fix that by construction -- track BA 059 across months and it
cannot flip to a different aircraft. The catch is that it is a real methodology
bet: ONS have never published whether they re-pick or track, and a rule that is
stable because it ignores substitution is not automatically a rule that is right.

So this measures it instead of arguing about it. For every (route, window) it
replays both rules over the payloads already collected and reports which produces
less month-to-month movement, how often the tracked flight survives, and what
happens when it does not.

WHY IT REPLAYS RATHER THAN CHANGES ANYTHING

`raw_response` retains every quote, which is the whole reason it is stored: any
rule can be re-derived over data already collected without re-querying. So the
decision can be taken on numbers from the real panel before a line of the
collection path changes -- and if matched-model turns out no quieter, the answer
is to have measured it and not adopted it.

Nothing here writes. It prints.

A NOTE ON WHAT "THE SAME FLIGHT" MEANS

Index months price different dates -- September's departure is the 8th, October's
is the 13th -- so the matched item cannot be a specific departure. It is the
scheduled service: BA 059 on both dates is one item in CPI terms. That is why
`selected_flight_number` exists and why matching is on the number rather than on
the departure timestamp, which necessarily differs.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import logging
import statistics
import sys
from decimal import Decimal, InvalidOperation
from typing import Any

from . import bq
from .config import Config, ConfigError

log = logging.getLogger("ukairfares.matched")

#: Below this many index months a comparison says nothing. Two points give one
#: step per series, and one step cannot distinguish a quieter rule from a lucky
#: one.
MIN_INDEX_MONTHS = 3

PAYLOADS = """
SELECT index_month_departure, route, haul_category, months_ahead,
       scrape_date, target_departure_time, raw_response
FROM `{view}`
WHERE status = 'ok'
  AND raw_response IS NOT NULL
  AND index_month_departure IS NOT NULL
ORDER BY index_month_departure, route, months_ahead, scrape_date
"""


def _time_of(value: Any) -> int | None:
    if not value:
        return None
    try:
        text = str(value)
        if "T" in text or " " in text:
            stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
            return stamp.hour * 60 + stamp.minute
        hh, _, mm = text.partition(":")
        return int(hh) * 60 + int(mm)
    except (ValueError, TypeError):
        return None


def candidates(payload: Any) -> list[dict[str, Any]]:
    """Every (flight number, departure minute, price) a payload carries.

    Walks the structure rather than assuming a layout, for the same reason
    `banks.departure_minutes` does: a new provider should not require this
    rewritten before the question can be asked again.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return []
    out: list[dict[str, Any]] = []
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        price = node.get("price")
        segments = node.get("flights")
        if price is not None and isinstance(segments, list) and segments:
            first = segments[0] if isinstance(segments[0], dict) else {}
            departure = first.get("departure_airport") or {}
            try:
                value = Decimal(str(price))
            except (InvalidOperation, TypeError):
                value = None
            if value is not None and value > 0:
                out.append({
                    "flight_number": first.get("flight_number"),
                    "airline": first.get("airline"),
                    "minutes": _time_of(departure.get("time")),
                    "price": float(value),
                })
        for value in node.values():
            if isinstance(value, (dict, list)):
                stack.append(value)
    return out


def _nearest(cands: list[dict[str, Any]], target: int) -> dict[str, Any] | None:
    timed = [c for c in cands if c["minutes"] is not None]
    if not timed:
        return None
    return min(timed, key=lambda c: (
        min(abs(c["minutes"] - target), 1440 - abs(c["minutes"] - target)), c["price"]))


def replay(rows) -> dict[str, dict[str, Any]]:
    """Both rules, over the payloads already collected, per (route, window)."""
    # One payload per (series, index month): the earliest collection day, which
    # stands in for the index day. Using every day would mix index-day timing
    # noise into a comparison that is about the selection rule.
    chosen: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = (row["route"], row["months_ahead"], str(row["index_month_departure"]))
        if key not in chosen or str(row["scrape_date"]) < chosen[key]["scrape_date"]:
            chosen[key] = {
                "scrape_date": str(row["scrape_date"]),
                "target": _time_of(row["target_departure_time"]) or 9 * 60,
                "cands": candidates(row["raw_response"]),
                "haul": row["haul_category"],
            }

    by_series: dict[tuple, list[tuple]] = collections.defaultdict(list)
    for (route, window, month), data in chosen.items():
        by_series[(route, window)].append((month, data))

    out: dict[str, dict[str, Any]] = {}
    for (route, window), entries in sorted(by_series.items()):
        entries.sort(key=lambda e: e[0])
        target_prices, matched_prices = [], []
        tracked: str | None = None
        substitutions = 0
        for _month, data in entries:
            pick = _nearest(data["cands"], data["target"])
            if pick is None:
                continue
            target_prices.append(pick["price"])

            held = None
            if tracked is not None:
                held = next((c for c in data["cands"]
                             if c["flight_number"] == tracked), None)
                if held is None:
                    substitutions += 1
            if held is None:
                # Chain start, or the tracked service is gone: re-pick and
                # record the substitution rather than hiding it.
                held = pick
                tracked = pick["flight_number"]
            matched_prices.append(held["price"])

        if len(target_prices) < 2:
            continue

        def moves(series):
            return [abs(b / a - 1) * 100 for a, b in zip(series, series[1:]) if a]

        t_moves, m_moves = moves(target_prices), moves(matched_prices)
        out[f"{route}|{window}"] = {
            "haul": entries[0][1]["haul"],
            "index_months": len(target_prices),
            "target_rule_median_move": round(statistics.median(t_moves), 2) if t_moves else None,
            "matched_rule_median_move": round(statistics.median(m_moves), 2) if m_moves else None,
            "substitutions": substitutions,
            "tracked_flight": tracked,
            "enough": len(target_prices) >= MIN_INDEX_MONTHS,
        }
    return out


def report(measured: dict[str, dict[str, Any]]) -> str:
    lines = [
        "Matched-model vs re-picking, replayed over collected payloads.",
        "",
        f"{'series':22} {'months':>6} {'re-pick':>9} {'matched':>9} {'subs':>5}  verdict",
        "-" * 70,
    ]
    quieter = noisier = compared = thin = 0
    for name, r in sorted(measured.items()):
        if not r["enough"]:
            thin += 1
            continue
        t, m = r["target_rule_median_move"], r["matched_rule_median_move"]
        if t is None or m is None:
            continue
        verdict = "matched quieter" if m < t else ("same" if m == t else "re-pick quieter")
        compared += 1
        quieter += m < t
        noisier += m > t
        lines.append(f"{name:22} {r['index_months']:6} {t:8.1f}% {m:8.1f}% "
                     f"{r['substitutions']:5}  {verdict}")
    lines += ["", f"matched quieter in {quieter} series, noisier in {noisier}, "
                  f"tied in {compared - quieter - noisier}."]
    if thin:
        lines.append(f"{thin} series skipped: under {MIN_INDEX_MONTHS} index months, "
                     "which cannot distinguish a quieter rule from a lucky one.")
    # Keyed on whether anything could be compared at all, NOT on whether one rule
    # won: two rules that tie across three months have been compared and agreed,
    # which is a result. Conflating the two would report a real tie as an absence
    # of data and send someone looking for a problem that is not there.
    if compared == 0:
        lines.append("NO SERIES HAS ENOUGH HISTORY YET. This is the expected answer "
                     "until roughly February 2027; the comparison is not meaningful "
                     "before then and adopting matched-model on it would be a guess.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
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
    rows = reader.query(PAYLOADS.format(view=config.table_ref("current_scrapes")))
    measured = replay(rows)
    print(json.dumps(measured, indent=2, sort_keys=True) if args.json
          else report(measured))
    return 0


if __name__ == "__main__":
    sys.exit(main())
