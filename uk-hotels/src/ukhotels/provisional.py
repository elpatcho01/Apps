"""A provisional month-on-month relative, computed without a confirmed index day.

WHY THIS IS SEPARATE FROM reconcile.py, AND MUST STAY SEPARATE
--------------------------------------------------------------
`reconcile` refuses to attribute nights to a CPI month until ONS confirm the
index day for that month, and that refusal is correct: a mis-parsed index day
does not leave a visible gap, it files the right prices under the wrong month,
which is a plausible-looking number and therefore the worst kind of wrong.

But the refusal has a cost. As of 2026-11-04 the index-day parser cannot read
September 2026's bulletin, reconciliation has failed every scheduled run since
2026-10-21, and `reconstructions` is empty -- so there is no way to tell whether
the aggregation machinery produces a sane figure at all. This module answers
that one question and nothing else.

WHAT IT DOES INSTEAD OF WAITING
-------------------------------
The index day is withheld in advance, so collection prices the nights implied by
BOTH candidates -- the 2nd and the 3rd Tuesday. One of those candidate sets is
the real one and we do not yet know which. Rather than guess, this computes the
relative under each reading and reports all of them:

    candidate_1   nights implied by the 2nd Tuesday, both months
    candidate_2   nights implied by the 3rd Tuesday, both months
    pooled        every night in the month, both candidates together

If the three agree to within a few tenths of a point, the choice of index day
does not matter much for the month in question and the machinery is behaving. If
they disagree sharply, that disagreement is the finding, and it is exactly what
the confirmed index day is needed to resolve.

Candidates are paired by ORDINAL, not by proximity: the 2nd Tuesday of October
against the 2nd Tuesday of November. Pairing a 2nd Tuesday against a 3rd would
compare nights five weeks apart and call it a month.

WHAT THE FIRST RUN OF THIS FOUND
--------------------------------
Its first real query failed with "No matching signature for operator IN UNNEST
for argument types: DATE, ARRAY<STRING>". `bq._to_query_param` typed every list
as ARRAY<STRING>, so no `IN UNNEST(@param)` holding dates could run at all --
including `reconcile`'s own PANEL_QUERY, which passes scrape dates exactly that
way. Reconciliation would have hit the same error the first time it got that
far, and it never had: every run so far stops earlier, at index-day
confirmation. So fixing the index-day parser on its own would have bought a new
red run rather than a reconstruction. Worth recording, because that is the
entire argument for exercising a path before you need it.

WHAT THIS IS NOT
----------------
Not a nowcast, and not publishable. It is read-only -- it writes nothing to
BigQuery, so it cannot contaminate the append-only panel or be mistaken later
for a real reconstruction. Every row it emits carries `provisional: true` and
the reason. Validation remains INSUFFICIENT_DATA regardless of what this prints:
there is no overlap between anything collected here and the published answer key,
which ends 2025-07 in a different methodology era.

THE RPI LINE IS INDICATIVE AND IS LABELLED AS SUCH
--------------------------------------------------
RPI has no hotels class; the nearest published series is the UK holidays section
of Leisure services (SGVM), which carries the hotel one-night item alongside
self-catering, caravans and holiday centres. Over the 15 months to 2026-09 its
monthly change ran at 0.506x the CPI 11.2.0.1 monthly change with r = 0.995.
That is a fitted scaling on a short window, not an identity, and the two series
are not independent -- both are built from the same ONS collection. So the RPI
figure here is reported with its fit constants attached and the word INDICATIVE
in the field name, because a derived number that travels without its provenance
is a number that will eventually be quoted as fact.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import math
import statistics
import sys
from typing import Any, Iterable, Sequence

from .config import Config
from .index import FORMULAS, IndexError_, price_relative, spans_methodology_break
from .index import methodology_era

log = logging.getLogger(__name__)

#: Fitted from ONS published series over the 15 months to 2026-09; see the
#: module docstring. Recorded as data so the report carries its own provenance.
RPI_FIT = {
    "rpi_series": "sgvm (RPI: percentage change over 1 month - UK holidays)",
    "cpi_series": "l7ie (CPI INDEX 11.2.0.1 Hotels, motels and similar)",
    "slope_rpi_per_cpi": 0.506,
    "correlation_r": 0.995,
    "window_months": 15,
    "window_end": "2026-09",
    "independent": False,
    "caveat": (
        "Fitted scaling on a 15-month window, not an identity. Both series come "
        "from the same ONS price collection, so agreement is not corroboration."
    ),
}

PANEL_QUERY = """
WITH latest_run AS (
  SELECT scrape_date, run_id
  FROM (
    SELECT scrape_date, run_id,
           ROW_NUMBER() OVER (PARTITION BY scrape_date ORDER BY scrape_ts DESC) AS rn
    FROM (
      SELECT DISTINCT scrape_date, run_id, scrape_ts
      FROM `{table}`
      WHERE index_month_stay IN UNNEST(@months)
    )
  )
  WHERE rn = 1
)
SELECT
  s.index_month_stay, s.index_day, s.stay_night_kind, s.check_in,
  s.location, s.property_tier, s.property_token, s.price_gbp, s.rate_basis
FROM `{table}` AS s
JOIN latest_run AS l
  ON s.scrape_date = l.scrape_date AND s.run_id = l.run_id
WHERE s.status = 'ok'
  AND s.price_gbp IS NOT NULL
  AND s.property_token IS NOT NULL
  AND s.index_month_stay IN UNNEST(@months)
"""

MONTHS_QUERY = """
SELECT DISTINCT index_month_stay
FROM `{table}`
WHERE status = 'ok' AND price_gbp IS NOT NULL
ORDER BY index_month_stay
"""


def _as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and value[:10]:
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _geomean(values: Sequence[float]) -> float:
    return math.exp(statistics.fmean(math.log(v) for v in values))


@dataclasses.dataclass(frozen=True, slots=True)
class Cell:
    """Property-level rates for one month under one reading of the index day."""

    index_month: dt.date
    candidate: str
    scope: str
    #: property_token -> rate, geometrically averaged over that month's nights.
    rates: dict[str, float]
    n_nights: int


def candidate_ordinals(index_days: Iterable[dt.date]) -> dict[dt.date, str]:
    """Label each candidate index day by its ordinal within its own month.

    Ordinal, not calendar distance, is what makes two months comparable: ONS
    pick the 2nd or the 3rd Tuesday, and whichever they pick they pick in both
    months. Pairing a 2nd Tuesday against a 3rd would compare nights five weeks
    apart and report it as one month of inflation.
    """
    by_month: dict[tuple[int, int], list[dt.date]] = {}
    for day in sorted(set(index_days)):
        by_month.setdefault((day.year, day.month), []).append(day)
    out: dict[dt.date, str] = {}
    for days in by_month.values():
        for position, day in enumerate(sorted(days), start=1):
            out[day] = f"candidate_{position}"
    return out


def build_cells(rows: Sequence[dict[str, Any]]) -> list[Cell]:
    """Collapse panel rows into one rate per property per month-reading-scope.

    A property is normally priced on both of a month's nights. Those are two
    observations of the same monthly price, so they are combined with a
    geometric mean -- consistent with how the pipeline aggregates elsewhere, and
    it keeps the matched sample keyed on the property rather than on the night.
    """
    index_days = [d for d in (_as_date(r.get("index_day")) for r in rows) if d]
    ordinals = candidate_ordinals(index_days)

    buckets: dict[tuple[dt.date, str, str, str], list[float]] = {}
    nights: dict[tuple[dt.date, str, str], set[dt.date]] = {}

    for row in rows:
        month = _as_date(row.get("index_month_stay"))
        index_day = _as_date(row.get("index_day"))
        token = row.get("property_token")
        try:
            rate = float(row.get("price_gbp") or 0.0)
        except (TypeError, ValueError):
            continue
        if not month or not index_day or not token or rate <= 0:
            continue
        candidate = ordinals.get(index_day)
        if not candidate:
            continue
        tier = str(row.get("property_tier") or "unknown")
        night = _as_date(row.get("check_in"))

        for cand in (candidate, "pooled"):
            for scope in ("all", tier):
                buckets.setdefault((month, cand, scope, token), []).append(rate)
                if night:
                    nights.setdefault((month, cand, scope), set()).add(night)

    grouped: dict[tuple[dt.date, str, str], dict[str, float]] = {}
    for (month, cand, scope, token), values in buckets.items():
        grouped.setdefault((month, cand, scope), {})[token] = _geomean(values)

    return [
        Cell(
            index_month=month,
            candidate=cand,
            scope=scope,
            rates=rates,
            n_nights=len(nights.get((month, cand, scope), ())),
        )
        for (month, cand, scope), rates in sorted(
            grouped.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])
        )
    ]


def relatives(cells: Sequence[Cell], *, min_matched: int = 3) -> list[dict[str, Any]]:
    """Every consecutive-month relative available, per reading and scope."""
    by_key: dict[tuple[str, str], dict[dt.date, Cell]] = {}
    for cell in cells:
        by_key.setdefault((cell.candidate, cell.scope), {})[cell.index_month] = cell

    out: list[dict[str, Any]] = []
    for (candidate, scope), months in sorted(by_key.items()):
        ordered = sorted(months)
        for earlier, later in zip(ordered, ordered[1:]):
            # Consecutive calendar months only. A gap must not be reported as a
            # one-month change; that is the quiet error the whole design avoids.
            step = (later.year - earlier.year) * 12 + (later.month - earlier.month)
            if step != 1:
                continue
            if spans_methodology_break(earlier, later):
                out.append({
                    "from_month": earlier.isoformat()[:7],
                    "to_month": later.isoformat()[:7],
                    "candidate": candidate,
                    "scope": scope,
                    "skipped": "spans a methodology break; the two months are "
                               "not measuring the same thing",
                })
                continue

            base, current = months[earlier].rates, months[later].rates
            entry: dict[str, Any] = {
                "from_month": earlier.isoformat()[:7],
                "to_month": later.isoformat()[:7],
                "candidate": candidate,
                "scope": scope,
                "methodology_era": methodology_era(later),
                "n_properties_from": len(base),
                "n_properties_to": len(current),
                "n_nights_from": months[earlier].n_nights,
                "n_nights_to": months[later].n_nights,
                "provisional": True,
                "provisional_reason": (
                    "index day not confirmed by ONS; computed under this reading "
                    "of the candidate index days rather than the confirmed one"
                ),
            }
            for formula in FORMULAS:
                try:
                    rel = price_relative(base, current, formula, min_matched=min_matched)
                except IndexError_ as exc:
                    entry[formula] = {"error": str(exc)}
                    continue
                pct = rel.pct_change
                entry[formula] = {
                    "mom_pct": round(pct, 2),
                    "n_matched": rel.n_matched,
                    "n_unmatched": rel.n_unmatched,
                    "match_rate": round(rel.match_rate, 3),
                    "rpi_uk_holidays_equivalent_pct_INDICATIVE": round(
                        pct * float(RPI_FIT["slope_rpi_per_cpi"]), 2
                    ),
                }
            out.append(entry)
    return out


def run(config: Config, *, months: Sequence[dt.date] | None = None,
        min_matched: int = 3) -> dict[str, Any]:
    from .bq import BigQueryWriter  # lazy: dry runs need no dependency

    reader = BigQueryWriter(config.project)
    table = config.scrapes_ref

    if not months:
        rows = reader.query(MONTHS_QUERY.format(table=table))
        months = [d for d in (_as_date(r.get("index_month_stay")) for r in rows) if d]
    months = sorted(set(months))
    if not months:
        return {"error": "no stay months with usable rows in the panel"}

    rows = reader.query(PANEL_QUERY.format(table=table), {"months": list(months)})
    cells = build_cells(rows)
    computed = relatives(cells, min_matched=min_matched)

    return {
        "generated_ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "status": "PROVISIONAL_NOT_PUBLISHABLE",
        "what_this_is": (
            "Matched-sample month-on-month relatives computed WITHOUT a confirmed "
            "ONS index day, under each reading of the candidate index days. A "
            "machinery check, not a nowcast."
        ),
        "why": (
            "reconcile refuses to attribute nights to a CPI month until ONS "
            "confirm the index day. The parser cannot read September 2026's "
            "bulletin, so reconciliation has produced no rows at all and the "
            "aggregation path has never been exercised on real data."
        ),
        "standing_blockers": [
            "validation is INSUFFICIENT_DATA: no overlap with the published "
            "answer key, which ends 2025-07 in a different methodology era",
            "rate_basis is uncontrolled ('any'), mixing refundable and "
            "non-refundable rates that differ by 30-40% on an identical room",
            "regional weights are placeholders, so pooled figures are unweighted",
        ],
        "wrote_to_bigquery": False,
        "rpi_fit": RPI_FIT,
        "months_in_panel": [m.isoformat()[:7] for m in months],
        "panel_rows_read": len(rows),
        "cells": [
            {
                "index_month": c.index_month.isoformat()[:7],
                "candidate": c.candidate,
                "scope": c.scope,
                "n_properties": len(c.rates),
                "n_nights": c.n_nights,
            }
            for c in cells
        ],
        "relatives": computed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provisional month-on-month relative.")
    parser.add_argument(
        "--month", action="append", default=[],
        help="Stay month as YYYY-MM. Repeatable. Default: every month in the panel.",
    )
    parser.add_argument("--min-matched", type=int, default=3)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    months = [dt.date(int(m[:4]), int(m[5:7]), 1) for m in args.month]
    config = Config.from_env()
    report = run(config, months=months, min_matched=args.min_matched)

    # Annotations to stderr: stdout is piped into a .json file by the workflow.
    if report.get("error"):
        print(f"::error::{report['error']}", file=sys.stderr)
    else:
        usable = [r for r in report["relatives"] if not r.get("skipped")]
        print(
            f"::notice::{len(usable)} provisional relative(s) across "
            f"{len(report['months_in_panel'])} stay month(s). Not publishable.",
            file=sys.stderr,
        )

    print(json.dumps(report, indent=2, default=str))
    return 1 if report.get("error") else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
