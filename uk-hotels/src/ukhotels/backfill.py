"""Load ONS's published accommodation values -- the answer key.

Three sources, deliberately kept separate all the way into BigQuery because they
are on different bases and different geographies and comparing them with one
another would produce a confidently wrong number.

1. THE TIME SERIES (`--timeseries`)
   ONS publish every CPI item-class index as JSON at a stable URL:

       /economy/inflationandpriceindices/timeseries/<id>/mm23/data

   `l7ie` is CPI INDEX 11.2.0.1 (hotels, motels, inns and similar) -- the class
   this pipeline reconstructs. `l7ig` is 11.2.0.2 (holiday centres, camping
   sites, youth hostels), which is a *separate class* rather than a subdivision,
   and `cjvv`/`l8u9` carry the weights. National, 2015 = 100, decades of
   history, and no spreadsheet parsing at all.

   This is the most reliable of the three and the one to reach for first. Its
   limitation is that it is national and covers all of 11.2.0.1 including the
   items we do not replicate, so agreement with it is weaker evidence than
   agreement with the ad hoc release.

2. THE AD HOC RELEASE (`--adhoc`)
   "Hotel overnight stays booked in advance: consumer prices sub-indices" --
   regional sub-indices for the six-weeks-in-advance item specifically, on a
   January 2025 = 100 basis. This is the closest match to what we reconstruct
   and therefore the primary target. It is also the shortest: the item only
   began in 2025.

3. THE MICRODATA (`--quotes`)
   The "consumption segment indices and price quotes" dataset -- renamed from
   "item indices and price quotes" -- covering COICOP divisions 3 to 12 for
   locally collected items. Accommodation is division 11 and is part of the
   regional services collection, so quotes should be present. Divisions 1 and 2
   were dropped in March 2026 over scanner-data agreements, which does not touch
   us.

WHY THE COVERAGE IS COUNTED RATHER THAN TRUSTED
------------------------------------------------
Every loader reports what actually landed -- row count, distinct months, first
and last month, per source -- instead of repeating what the release says it
contains. On the sibling project a release titled as covering 2007 to 2026
actually loaded 2016-01 to 2026-02, and the difference was only found because
the loader counted. The stated coverage period is a title; the row count is a
fact.

WHAT THIS DOES NOT DO
---------------------
It does not let you reconstruct history. The rates needed for that are
unobservable in retrospect: an advertised nightly rate six weeks out is a quote,
not a record, and no provider retains them. What the backfill buys you is the
target series in BigQuery, so this item's real volatility can be sized before
any nowcast of it is trusted, and so the comparison is in place the moment live
reconstructions land.
"""

from __future__ import annotations

import argparse
import calendar
import dataclasses
import datetime as dt
import json
import logging
import re
import sys
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import requests

from . import bq
from .config import Config, ConfigError
from .index import detect_basis, methodology_era

log = logging.getLogger("ukhotels.backfill")

ONS_BASE = "https://www.ons.gov.uk"
SEARCH_URL = f"{ONS_BASE}/search"
USER_AGENT = "uk-hotel-nowcasting/0.1 (research pipeline; contact via repository owner)"

#: Published item-class series worth loading. `coicop` is carried through to the
#: table because 11.2.0.1 and 11.2.0.2 are different products and must never be
#: pooled: one is hotels, the other is campsites and hostels.
TIMESERIES: tuple[tuple[str, str, str], ...] = (
    ("l7ie", "CPI INDEX 11.2.0.1 Hotels, motels and similar accommodation services", "11.2.0.1"),
    ("l7ig", "CPI INDEX 11.2.0.2 Holiday centres, camping sites, youth hostels", "11.2.0.2"),
    ("d7ex", "CPI INDEX 11.2 Accommodation services", "11.2"),
)

#: Ad hoc release located at time of writing (January 2025 to July 2025).
#: Override with --release-url; --discover looks for a newer vintage.
DEFAULT_ADHOC_URL = (
    f"{ONS_BASE}/economy/inflationandpriceindices/adhocs/"
    "2993hotelovernightstaysbookedinadvanceconsumerpricessubindices"
    "january2025tojuly2025"
)
ADHOC_SEARCH_TERM = "hotel overnight stays booked in advance consumer prices sub-indices"

_MONTH_NAMES = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTH_NAMES.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})

#: Region label -> our region code. ONS label regions in prose; we key on codes.
REGION_ALIASES: dict[str, str] = {
    "north east": "north_east",
    "north west": "north_west",
    "yorkshire and the humber": "yorkshire_and_the_humber",
    "yorkshire & the humber": "yorkshire_and_the_humber",
    "yorkshire and humber": "yorkshire_and_the_humber",
    "east midlands": "east_midlands",
    "west midlands": "west_midlands",
    "east of england": "east_of_england",
    "east": "east_of_england",
    "london": "london",
    "south east": "south_east",
    "south west": "south_west",
    "wales": "wales",
    "scotland": "scotland",
    "northern ireland": "northern_ireland",
    "united kingdom": "uk",
    "uk": "uk",
    "all": "uk",
}


class BackfillError(RuntimeError):
    """A published series could not be parsed into defensible values."""


@dataclasses.dataclass(frozen=True, slots=True)
class Observation:
    index_month: dt.date
    location: str
    index_value: Decimal
    series_source: str
    series_id: str | None
    series_name: str | None
    coicop_class: str | None
    basis: str
    release_url: str
    release_label: str


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _session(session: requests.Session | None = None) -> requests.Session:
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    return session


# ---------------------------------------------------------------------------
# Source 1: the published time series
# ---------------------------------------------------------------------------


def timeseries_url(series_id: str, dataset: str = "mm23") -> str:
    return f"{ONS_BASE}/economy/inflationandpriceindices/timeseries/{series_id}/{dataset}/data"


def parse_timeseries(
    payload: dict[str, Any], series_id: str, series_name: str, coicop: str, url: str
) -> list[Observation]:
    """Read monthly observations out of an ONS time series JSON payload.

    ONS give months as `{"date": "2026 JAN", "value": "112.3"}`. The `months`
    key is what we want; `years` and `quarters` are aggregates of it and would
    double-count if included.
    """
    months = payload.get("months")
    if not isinstance(months, list) or not months:
        raise BackfillError(
            f"{series_id}: no monthly observations in the payload "
            f"(keys present: {sorted(payload)[:12]})"
        )

    description = payload.get("description") or {}
    label = str(description.get("title") or series_name)

    out: list[Observation] = []
    for entry in months:
        if not isinstance(entry, dict):
            continue
        month = _parse_ons_month(entry.get("date"))
        value = _decimal(entry.get("value"))
        if month is None or value is None or value <= 0:
            continue
        out.append(
            Observation(
                index_month=month,
                location="uk",
                index_value=value,
                series_source="timeseries",
                series_id=series_id,
                series_name=label,
                coicop_class=coicop,
                # These series are published on a 2015 = 100 reference year.
                # Stated rather than detected: unlike the ad hoc release there
                # is no ambiguity here, and the payload declares it.
                basis="2015_100",
                release_url=url,
                release_label=label,
            )
        )
    if not out:
        raise BackfillError(f"{series_id}: {len(months)} month entries, none parseable")
    return out


def _parse_ons_month(value: Any) -> dt.date | None:
    """Parse "2026 JAN", "2026 January" or "2026-01" into the first of the month."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    if not text:
        return None
    match = re.match(r"^(\d{4})[\s-]+([a-z]+)$", text)
    if match and match.group(2) in _MONTH_NAMES:
        return dt.date(int(match.group(1)), _MONTH_NAMES[match.group(2)], 1)
    match = re.match(r"^(\d{4})[-/](\d{1,2})$", text)
    if match:
        month = int(match.group(2))
        return dt.date(int(match.group(1)), month, 1) if 1 <= month <= 12 else None
    return None


def fetch_timeseries(
    session: requests.Session | None = None, *, timeout: float = 60.0
) -> list[Observation]:
    """Load every configured item-class series. One failure does not stop the rest.

    Partial success is the right behaviour here: `d7ex` in particular is a
    best-guess series id for the 11.2 aggregate, and losing the whole backfill
    because one of three identifiers is wrong would be a poor trade.
    """
    session = _session(session)
    out: list[Observation] = []
    for series_id, name, coicop in TIMESERIES:
        url = timeseries_url(series_id)
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("%s: fetch failed (%s)", series_id, exc)
            continue
        try:
            observations = parse_timeseries(payload, series_id, name, coicop, url)
        except BackfillError as exc:
            log.warning("%s: %s", series_id, exc)
            continue
        log.info("%s: %d monthly observations", series_id, len(observations))
        out.extend(observations)
    return out


# ---------------------------------------------------------------------------
# Source 2: the ad hoc regional release
# ---------------------------------------------------------------------------


def coverage_end(href: str) -> tuple[int, int]:
    """Latest month a release covers, read from its URL slug.

    Slugs end with the coverage period, e.g.
    ".../2993hotelovernightstaysbookedinadvanceconsumerpricessubindices
       january2025tojuly2025".

    Ranking by the coverage period rather than the ad hoc reference number is
    deliberate and was learned the hard way on the sibling project: ONS
    restarted their ad hoc numbering, so the old four- and five-digit series
    sorts numerically *above* the newer four-digit one. A production run there
    picked a release three years out of date because it ranked by reference
    number. The coverage period is the only field in the URL that means what we
    need it to mean.

    Returns (year, month), or (0, 0) if the slug carries no readable period.
    """
    match = re.search(r"to([a-z]+)(20\d{2})(?:/|$)", href, flags=re.IGNORECASE)
    if not match:
        return (0, 0)
    month = _MONTH_NAMES.get(match.group(1).lower())
    return (int(match.group(2)), month) if month else (0, 0)


def discover_adhoc_release(session: requests.Session | None = None) -> str | None:
    """Best-effort search for a newer ad hoc release than the pinned default."""
    session = _session(session)
    try:
        resp = session.get(SEARCH_URL, params={"q": ADHOC_SEARCH_TERM}, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("release discovery failed (%s); using the pinned default", exc)
        return None

    hrefs = re.findall(
        r'href="(/economy/inflationandpriceindices/adhocs/[^"]*'
        r'hotel[^"]*subindices[^"]*)"',
        resp.text,
        flags=re.IGNORECASE,
    )
    if not hrefs:
        log.warning("no candidate ad hoc releases found in search results")
        return None

    best = max(hrefs, key=coverage_end)
    if coverage_end(best) == (0, 0):
        log.warning("could not read a coverage period from any release slug")
        return None
    return f"{ONS_BASE}{best}"


def find_spreadsheet_url(release_html: str, release_url: str) -> str | None:
    """Locate the .xlsx attached to an ad hoc release page."""
    match = re.search(r'href="([^"]+\.xlsx?)"', release_html, flags=re.IGNORECASE)
    if not match:
        return None
    href = match.group(1)
    return href if href.startswith("http") else f"{ONS_BASE}{href}"


def parse_adhoc_workbook(
    content: bytes, *, release_url: str, release_label: str
) -> list[Observation]:
    """Read regional sub-indices out of the ad hoc workbook.

    WRITTEN WITHOUT SIGHT OF THE FILE. ons.gov.uk is blocked by egress policy in
    the development sandbox, exactly as it was for the air-fares weights parser,
    so the layout below is inferred from the release description rather than
    observed. The consequences are designed in rather than hoped away:

      * the header row and the month and region columns are located by
        *searching* for them, never by fixed offsets;
      * both orientations are handled -- months down rows with regions across
        columns, and the transpose -- because ONS ad hoc workbooks use both;
      * every value is validated (positive, plausible index range) and anything
        failing is rejected rather than written;
      * on total failure it raises with a dump of what it actually saw, so one
        CI log tells you what to fix.

    A wrong answer key is worse than no answer key: it would make validation
    report a number, and a number is believed.
    """
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise BackfillError(f"openpyxl is required to parse the ad hoc workbook: {exc}")

    import io

    workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    seen_shapes: list[str] = []

    for sheet in workbook.worksheets:
        rows = [list(r) for r in sheet.iter_rows(values_only=True)]
        seen_shapes.append(f"{sheet.title}: {len(rows)} rows")
        observations = _parse_sheet(
            rows, release_url=release_url, release_label=release_label
        )
        if observations:
            log.info("parsed %d values from sheet %r", len(observations), sheet.title)
            return observations

    raise BackfillError(
        "no sheet in the ad hoc workbook yielded regional sub-indices. "
        f"Sheets seen: {'; '.join(seen_shapes)}. "
        "Inspect the workbook and fix `_parse_sheet`."
    )


def _region_code(label: Any) -> str | None:
    text = re.sub(r"\s+", " ", str(label or "")).strip().lower()
    text = re.sub(r"\s*\(.*\)$", "", text)
    return REGION_ALIASES.get(text)


def _plausible_index(value: Decimal | None) -> bool:
    # An index on a January = 100 basis living outside this range is a parse
    # error, not a datum -- most likely a weight, a row number or a year that
    # landed in a value column.
    return value is not None and Decimal("10") <= value <= Decimal("500")


def _parse_sheet(
    rows: list[list[Any]], *, release_url: str, release_label: str
) -> list[Observation]:
    """Try both orientations; return whichever produces defensible values."""
    out = _parse_months_down(rows, release_url=release_url, release_label=release_label)
    if out:
        return out
    transposed = [list(col) for col in zip(*rows)] if rows else []
    return _parse_months_down(
        transposed, release_url=release_url, release_label=release_label
    )


def _parse_months_down(
    rows: list[list[Any]], *, release_url: str, release_label: str
) -> list[Observation]:
    """Months down the rows, regions across the columns."""
    for header_idx, row in enumerate(rows[:25]):
        region_cols = {
            col: code
            for col, cell in enumerate(row)
            if (code := _region_code(cell)) is not None
        }
        # Two regions is enough to be confident this is the header and not a
        # stray label; requiring all twelve would break on a release that
        # publishes a subset.
        if len(region_cols) < 2:
            continue

        out: list[Observation] = []
        for data_row in rows[header_idx + 1 :]:
            month = next(
                (m for cell in data_row[:3] if (m := _parse_month_cell(cell))), None
            )
            if month is None:
                continue
            for col, region in region_cols.items():
                if col >= len(data_row):
                    continue
                value = _decimal(data_row[col])
                if not _plausible_index(value):
                    continue
                out.append(
                    Observation(
                        index_month=month,
                        location=region,
                        index_value=value,
                        series_source="adhoc_regional",
                        series_id=None,
                        series_name="Hotel overnight stays booked in advance",
                        coicop_class="11.2.0.1",
                        basis="january_2025_100",
                        release_url=release_url,
                        release_label=release_label,
                    )
                )
        if out:
            return out
    return []


def _parse_month_cell(cell: Any) -> dt.date | None:
    if isinstance(cell, dt.datetime):
        return dt.date(cell.year, cell.month, 1)
    if isinstance(cell, dt.date):
        return dt.date(cell.year, cell.month, 1)
    text = re.sub(r"\s+", " ", str(cell or "")).strip().lower()
    if not text:
        return None
    for pattern in (
        r"^([a-z]+)[\s-]+(20\d{2})$",   # "january 2025"
        r"^(20\d{2})[\s-]+([a-z]+)$",   # "2025 january"
    ):
        match = re.match(pattern, text)
        if not match:
            continue
        a, b = match.groups()
        name, year = (a, b) if a in _MONTH_NAMES else (b, a)
        if name in _MONTH_NAMES and year.isdigit():
            return dt.date(int(year), _MONTH_NAMES[name], 1)
    match = re.match(r"^(20\d{2})[-/](\d{1,2})$", text)
    if match:
        month = int(match.group(2))
        return dt.date(int(match.group(1)), month, 1) if 1 <= month <= 12 else None
    return None


def fetch_adhoc(
    *,
    release_url: str | None = None,
    discover: bool = False,
    session: requests.Session | None = None,
    timeout: float = 90.0,
) -> list[Observation]:
    session = _session(session)
    url = release_url or DEFAULT_ADHOC_URL
    if discover and not release_url:
        url = discover_adhoc_release(session) or url
    log.info("ad hoc release: %s", url)

    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    xlsx_url = find_spreadsheet_url(resp.text, url)
    if not xlsx_url:
        raise BackfillError(f"no spreadsheet link found on {url}")

    log.info("spreadsheet: %s", xlsx_url)
    data = session.get(xlsx_url, timeout=timeout)
    data.raise_for_status()
    return parse_adhoc_workbook(
        data.content, release_url=url, release_label=url.rsplit("/", 1)[-1]
    )


# ---------------------------------------------------------------------------
# Writing, and reporting what actually landed
# ---------------------------------------------------------------------------


def to_rows(
    observations: Iterable[Observation], *, run_id: str, fetched_ts: dt.datetime
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for obs in observations:
        rows.append(
            {
                "index_month": obs.index_month,
                "location": obs.location,
                "series_id": obs.series_id,
                "series_name": obs.series_name,
                "series_source": obs.series_source,
                "coicop_class": obs.coicop_class,
                "index_value": obs.index_value,
                "basis": obs.basis,
                # Recorded per value, not per release: a single release spans
                # methodology regimes, and a validation run needs to know which
                # regime each value came from without re-deriving it.
                "methodology_era": methodology_era(obs.index_month),
                "release_url": obs.release_url,
                "release_label": obs.release_label,
                "fetched_ts": fetched_ts,
                "is_current": True,
                "run_id": run_id,
            }
        )
    return rows


def coverage_report(observations: list[Observation]) -> dict[str, Any]:
    """Count what actually loaded, per source. Never repeat the stated coverage.

    See the module docstring: a release title claiming 2007-2026 loaded
    2016-01 to 2026-02 on the sibling project, and only counting caught it.
    """
    by_source: dict[str, dict[str, Any]] = {}
    for obs in observations:
        entry = by_source.setdefault(
            obs.series_source,
            {
                "values": 0,
                "months": set(),
                "locations": set(),
                "eras": set(),
                "bases": set(),
            },
        )
        entry["values"] += 1
        entry["months"].add(obs.index_month)
        entry["locations"].add(obs.location)
        entry["eras"].add(methodology_era(obs.index_month))
        entry["bases"].add(obs.basis)

    out: dict[str, Any] = {}
    for source, entry in by_source.items():
        months = sorted(entry["months"])
        out[source] = {
            "values": entry["values"],
            "distinct_months": len(months),
            "first_month": months[0].isoformat() if months else None,
            "last_month": months[-1].isoformat() if months else None,
            # A published series with holes in it is the norm, not the
            # exception, and the gap count is what rolling-origin validation
            # needs in order to skip them.
            "calendar_span_months": (
                (months[-1].year - months[0].year) * 12
                + (months[-1].month - months[0].month)
                + 1
                if months
                else 0
            ),
            "locations": sorted(entry["locations"]),
            "methodology_eras": sorted(entry["eras"]),
            "bases": sorted(entry["bases"]),
        }
        if source == "adhoc_regional":
            series = sorted(
                (o.index_month, float(o.index_value))
                for o in observations
                if o.series_source == source and o.location == "uk"
            )
            if series:
                out[source]["detected_basis"] = detect_basis(series)
    return out


def run_backfill(
    config: Config,
    *,
    sources: tuple[str, ...] = ("timeseries", "adhoc"),
    release_url: str | None = None,
    discover: bool = False,
    writer: bq.Writer | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex
    fetched_ts = dt.datetime.now(dt.timezone.utc)
    observations: list[Observation] = []
    errors: dict[str, str] = {}

    if "timeseries" in sources:
        try:
            observations.extend(fetch_timeseries(session))
        except Exception as exc:  # noqa: BLE001
            log.warning("time series backfill failed: %s", exc)
            errors["timeseries"] = f"{type(exc).__name__}: {exc}"

    if "adhoc" in sources:
        try:
            observations.extend(
                fetch_adhoc(release_url=release_url, discover=discover, session=session)
            )
        except Exception as exc:  # noqa: BLE001
            # Not fatal on its own. The ad hoc release is the better target but
            # the time series is the one with history, and losing both because
            # one workbook changed layout would leave validation with nothing.
            log.warning("ad hoc backfill failed: %s", exc)
            errors["adhoc"] = f"{type(exc).__name__}: {exc}"

    if not observations:
        raise BackfillError(
            "no published values loaded from any source. "
            + json.dumps(errors)
        )

    writer = writer or bq.build_writer(config)
    rows = to_rows(observations, run_id=run_id, fetched_ts=fetched_ts)
    written = writer.append(
        config.table_ref("ons_published_index") if not config.dry_run
        else "ons_published_index",
        rows,
    )

    summary = {
        "run_id": run_id,
        "written": written,
        "errors": errors,
        # The point of the whole module: measured, not quoted.
        "coverage_as_loaded": coverage_report(observations),
    }
    log.info("summary: %s", json.dumps(summary, indent=2))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load ONS published accommodation indices (the answer key)."
    )
    parser.add_argument(
        "--sources",
        default="timeseries,adhoc",
        help="Comma-separated: timeseries, adhoc.",
    )
    parser.add_argument("--release-url", default=None, help="Ad hoc release page URL.")
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Search ONS for a newer ad hoc release first, ranked by coverage period.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"::error::configuration error: {exc}", flush=True)
        return 2

    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
    try:
        summary = run_backfill(
            config,
            sources=sources,
            release_url=args.release_url,
            discover=args.discover,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"::error::backfill failed: {exc}", flush=True)
        log.exception("backfill failed")
        return 1

    for source, err in summary["errors"].items():
        print(f"::warning::backfill source '{source}' failed: {err}", flush=True)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
