"""The RPI counterpart of the accommodation series, and where it actually sits.

WHY THIS EXISTS
---------------
RPI is still published monthly and is still what a lot of contracts, rail fares
and index-linked gilts are written against, so "what does RPI say about hotel
prices" is a reasonable question to ask of this pipeline. The answer is
structurally awkward, and the awkwardness is the point of this module.

RPI HAS NO HOTELS CLASS
-----------------------
CPI/CPIH carry a class whose entire contents are hotels: 11.2.0.1, "Hotels,
motels, inns and similar accommodation services", which is what this pipeline
reconstructs. RPI has nothing at that level. Its published hierarchy is 14
groups split into roughly 85 sections, and overnight accommodation is not one of
them. Hotel spending enters RPI inside a *holiday* section:

    Travel and leisure          (broad grouping)
      Leisure services          (RPI group)
        Television licences and rentals     (section)
        Entertainment and other recreation  (section)
        Foreign holidays                    (section)
        UK holidays                         (section)  <-- hotels live here

So the nearest published RPI analogue of a month-on-month hotel price change is
the month-on-month change in the UK holidays section. That section is NOT a
hotel index: it mixes the hotel stay in with self-catering lets, caravans and
holiday centres, and it is the hotel component only in part.

ONS do price a one-night hotel stay for RPI -- an FOI disclosure names RPI item
640406, "domestic holidays, hotel one-night price", and item 640305 for foreign
self-catering, which is consistent with 6404xx being the UK holidays section and
6403xx foreign holidays. But item-level RPI indices are not published, so 640406
is not available as a series. The section is the floor.

WHAT THAT MEANS FOR COMPARISON
------------------------------
The UK holidays section is a bad answer key and a usable context series, and the
difference matters. Treat a divergence between this and the reconstructed
11.2.0.1 series as uninformative until the composition difference is accounted
for: different scope (holidays vs hotels), different base (Jan 1994 = 100 vs
2015 = 100), and RPI's own population and formula differences (RPI excludes the
highest-income and pensioner households and uses a Carli-style elementary
aggregate where CPI uses Jevons, which alone puts a persistent wedge between
them).

WHY THIS PROBES RATHER THAN ASSERTS
-----------------------------------
Series identifiers were read off search results, not out of a published
catalogue, and a wrong four-letter code returns somebody else's series rather
than an error. So every ID is fetched with its ONS-published title and the title
is reported back. A mis-identified series is then visible in the output instead
of silently becoming a number in a table -- the same diagnostic-first move that
settled the star-rating and the regional-coverage questions.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys
from decimal import Decimal
from typing import Any, Iterable

import requests

from .backfill import USER_AGENT, _parse_ons_month, timeseries_url


class RpiFetchError(RuntimeError):
    """A series could not be fetched or held no monthly observations."""


#: (series_id, what we believe it is, role). `role` separates the series that
#: answer the question from the ones that only frame it.
#:
#: Identifiers are claims to be checked, not facts: the fetch reports the title
#: ONS return for each, and `title_matches_expectation` is computed per series.
SERIES: tuple[tuple[str, str, str], ...] = (
    # --- the answer to "the RPI equivalent" --------------------------------
    ("sgvm", "RPI: percentage change over 1 month - UK holidays", "rpi_answer"),
    ("doec", "RPI: percentage change over 12 months - UK holidays", "rpi_answer"),
    # --- the RPI context around it -----------------------------------------
    ("chmq", "RPI: Leisure services: foreign holidays (Jan 1993=100)", "rpi_context"),
    ("cdkn", "RPI: percentage change over 12 months - Foreign holidays", "rpi_context"),
    ("chbm", "RPI: Leisure services (Jan 1987=100)", "rpi_context"),
    ("czen", "RPI: percentage change over 12 months - Leisure services", "rpi_context"),
    ("czev", "RPI: percentage change over 1 month - Travel and leisure", "rpi_context"),
    ("dodf", "RPI: Leisure services: entertainment & other recreation", "rpi_context"),
    ("chaw", "RPI All Items Index", "rpi_context"),
    # --- unverified guesses at the UK holidays INDEX level ------------------
    # ONS publish the 1-month and 12-month changes for UK holidays (above) but
    # the index itself was not found by search. These are candidates only; the
    # returned title decides. A miss here costs one HTTP request.
    ("chms", "candidate: RPI UK holidays index (Jan 1994=100)", "rpi_candidate"),
    ("doee", "candidate: RPI UK holidays related series", "rpi_candidate"),
    # --- what this pipeline actually reconstructs, for scale ---------------
    ("l7ie", "CPI INDEX 11.2.0.1 Hotels, motels and similar accommodation", "cpi_target"),
    ("d7ex", "CPI INDEX 11.2 Accommodation services", "cpi_target"),
)


@dataclasses.dataclass(frozen=True, slots=True)
class Point:
    month: dt.date
    value: Decimal


@dataclasses.dataclass(frozen=True, slots=True)
class SeriesResult:
    series_id: str
    expected: str
    role: str
    ons_title: str
    unit: str
    points: tuple[Point, ...]

    @property
    def is_percentage_change(self) -> bool:
        """True when ONS already publish this as a rate, not a level.

        A rate must not be differenced again, and a level must be. Getting this
        backwards would produce a plausible-looking number, so it is derived
        from the ONS title rather than from the identifier.
        """
        title = self.ons_title.lower()
        return "percentage change" in title or "% change" in title


def _decimal(value: Any) -> Decimal | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


def parse_series(payload: dict[str, Any], series_id: str, expected: str, role: str) -> SeriesResult:
    months = payload.get("months")
    if not isinstance(months, list) or not months:
        raise RpiFetchError(
            f"{series_id}: no monthly observations (payload keys: {sorted(payload)[:12]})"
        )
    description = payload.get("description") or {}
    points: list[Point] = []
    for entry in months:
        if not isinstance(entry, dict):
            continue
        month = _parse_ons_month(entry.get("date"))
        value = _decimal(entry.get("value"))
        # A rate may legitimately be zero or negative, so unlike the index
        # backfill this must not filter on value > 0.
        if month is None or value is None:
            continue
        points.append(Point(month=month, value=value))
    if not points:
        raise RpiFetchError(f"{series_id}: {len(months)} month entries, none parseable")
    points.sort(key=lambda p: p.month)
    return SeriesResult(
        series_id=series_id,
        expected=expected,
        role=role,
        ons_title=str(description.get("title") or ""),
        unit=str(description.get("unit") or ""),
        points=tuple(points),
    )


def month_on_month(points: Iterable[Point]) -> list[tuple[dt.date, Decimal]]:
    """Percentage change on the previous month, from a level series.

    Consecutive months only. A gap in the series yields no change for the month
    after the gap rather than a 2-month change mislabelled as a 1-month one.
    """
    ordered = sorted(points, key=lambda p: p.month)
    out: list[tuple[dt.date, Decimal]] = []
    for previous, current in zip(ordered, ordered[1:]):
        expected_year = previous.month.year + (1 if previous.month.month == 12 else 0)
        expected_month = 1 if previous.month.month == 12 else previous.month.month + 1
        if (current.month.year, current.month.month) != (expected_year, expected_month):
            continue
        if previous.value == 0:
            continue
        change = (current.value - previous.value) / previous.value * Decimal(100)
        out.append((current.month, change.quantize(Decimal("0.01"))))
    return out


def fetch_one(
    session: requests.Session, series_id: str, expected: str, role: str, *, timeout: float = 60.0
) -> SeriesResult:
    url = timeseries_url(series_id)
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return parse_series(response.json(), series_id, expected, role)


def _title_matches(result: SeriesResult) -> bool:
    """Cheap sanity check that the ID returned the series we meant.

    Deliberately loose -- it only has to catch a four-letter code landing on an
    unrelated series, which it does by checking the family and the subject
    rather than the exact wording.
    """
    title = result.ons_title.lower()
    expected = result.expected.lower()
    if not title:
        return False
    family = "rpi" if expected.startswith("rpi") else "cpi"
    if family not in title:
        return False
    for token in ("holiday", "leisure", "accommodation", "hotel", "all items", "travel"):
        if token in expected and token not in title:
            return False
    return True


def run(*, months: int = 15, timeout: float = 60.0) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    series_out: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    for series_id, expected, role in SERIES:
        try:
            result = fetch_one(session, series_id, expected, role, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            errors[series_id] = f"{type(exc).__name__}: {exc}"
            continue

        if result.is_percentage_change:
            recent = [
                {"month": p.month.isoformat()[:7], "published_change_pct": str(p.value)}
                for p in result.points[-months:]
            ]
            derived = None
        else:
            changes = month_on_month(result.points)
            recent = [
                {"month": m.isoformat()[:7], "derived_change_pct": str(v)}
                for m, v in changes[-months:]
            ]
            derived = "computed from the index level; ONS publish no 1-month rate here"

        series_out.append(
            {
                "series_id": result.series_id,
                "role": result.role,
                "expected": result.expected,
                "ons_title": result.ons_title,
                "ons_unit": result.unit,
                "title_matches_expectation": _title_matches(result),
                "published_as_rate": result.is_percentage_change,
                "note": derived,
                "first_month": result.points[0].month.isoformat()[:7],
                "latest_month": result.points[-1].month.isoformat()[:7],
                "latest_value": str(result.points[-1].value),
                "n_months": len(result.points),
                "recent": recent,
            }
        )

    mismatched = [s["series_id"] for s in series_out if not s["title_matches_expectation"]]
    return {
        "generated_ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "question": (
            "RPI counterpart of month-on-month CPI 11.2.0.1 hotel price change"
        ),
        "rpi_placement": {
            "broad_grouping": "Travel and leisure",
            "group": "Leisure services",
            "section": "UK holidays",
            "rpi_item": "640406 domestic holidays, hotel one-night price",
            "item_index_published": False,
            "lowest_published_level": "section (UK holidays)",
            "caveat": (
                "RPI has no hotels class. UK holidays mixes the hotel one-night "
                "item with self-catering, caravans and holiday centres, so it is "
                "a context series, not an answer key for 11.2.0.1."
            ),
        },
        "series": series_out,
        "errors": errors,
        "series_with_unexpected_titles": mismatched,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", type=int, default=15, help="Recent months to report.")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)

    report = run(months=args.months, timeout=args.timeout)

    # Annotations to stderr. stdout is piped into a .json file by the workflow,
    # so anything printed here that is not JSON corrupts the artifact.
    if report["errors"]:
        print(f"::warning::{len(report['errors'])} series could not be fetched", file=sys.stderr)
    if report["series_with_unexpected_titles"]:
        print(
            "::warning::unexpected ONS titles for: "
            + ", ".join(report["series_with_unexpected_titles"]),
            file=sys.stderr,
        )

    print(json.dumps(report, indent=2))
    # Absence of some series is an expected outcome of probing, not a failure.
    # Only a total miss is worth a non-zero exit.
    return 0 if report["series"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
