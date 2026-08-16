"""Fetch the ONS air fare sub-index weights and write them to weights.csv.

The weights live in an ad hoc release, "Domestic, European and long-haul
airfares consumer prices sub-indices", as an .xlsx with a weights sheet
alongside the sub-indices themselves.

WHY THIS IS WRITTEN DEFENSIVELY
-------------------------------
This parser was written without sight of the actual spreadsheet: the
development sandbox's egress policy blocks ons.gov.uk outright, so the layout
below is inferred from the release description rather than observed. The
consequences are designed in:

  * The sheet, header row and columns are located by *searching* for them, not
    by fixed offsets, so minor layout changes do not break it.
  * Every extracted row is validated (three positive weights, a plausible year,
    no duplicate years). Anything failing validation is rejected.
  * On any parse failure it dumps the workbook's actual structure and exits
    non-zero, so one look at the CI log tells you what to fix -- rather than
    silently writing plausible-looking garbage into the weights that every
    downstream aggregate depends on.
  * `--dump` skips parsing entirely and just prints what is in the file.

If the layout turns out to differ, fix `_find_weight_sheet` / `_find_header` and
nothing else needs to change.

Run it where egress to ons.gov.uk is permitted (a GitHub Actions runner is
fine); it is wired into the monthly reconciliation workflow.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import io
import logging
import pathlib
import re
import sys
from typing import Any, Iterable, Sequence

import requests

from .panel import WEIGHTS_PATH

log = logging.getLogger("ukairfares.onsweights")

ONS_BASE = "https://www.ons.gov.uk"

#: Most recent ad hoc release located at time of writing (Jan 2017 - Feb 2025).
#: Override with --release-url when ONS publish a newer vintage; --discover
#: attempts to find one automatically.
DEFAULT_RELEASE_URL = (
    f"{ONS_BASE}/economy/inflationandpriceindices/adhocs/"
    "2716domesticeuropeanandlonghaulairfaresconsumerpricessubindices"
    "january2017tofebruary2025"
)

SEARCH_URL = f"{ONS_BASE}/search"
SEARCH_TERM = "domestic European and long-haul airfares consumer prices sub-indices"

USER_AGENT = "uk-airfares-nowcasting/1.0 (research pipeline; contact via repository owner)"

#: Column header synonyms. Matched case-insensitively as substrings.
_CATEGORY_PATTERNS = {
    "domestic": (r"domestic",),
    "european": (r"europ", r"short[\s-]?haul"),
    "long_haul": (r"long[\s-]?haul", r"longhaul"),
}
_YEAR_PATTERNS = (r"^year$", r"^date$", r"^period$")
_MONTH_PATTERNS = (r"^month$", r"^date$", r"^period$", r"^index month$")

#: Sanity bounds. A weight outside this is a parse error, not a datum.
MIN_YEAR, MAX_YEAR = 2015, 2035

#: The sub-index series begins in January 2017 per the release title.
MIN_SERIES_YEAR = 2016

_MONTH_NAMES = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTH_NAMES.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})


class WeightsParseError(RuntimeError):
    """The workbook could not be parsed into defensible weights."""


class SubIndexParseError(RuntimeError):
    """The workbook could not be parsed into a defensible sub-index series."""


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _as_year(value: Any) -> int | None:
    if isinstance(value, dt.datetime):
        return value.year
    if isinstance(value, dt.date):
        return value.year
    if isinstance(value, (int, float)) and float(value).is_integer():
        year = int(value)
        return year if MIN_YEAR <= year <= MAX_YEAR else None
    match = re.search(r"\b(20\d{2})\b", str(value or ""))
    if match:
        year = int(match.group(1))
        return year if MIN_YEAR <= year <= MAX_YEAR else None
    return None


def _as_month(value: Any) -> dt.date | None:
    """Parse a monthly period label into the first of that month.

    Excel usually hands these over as datetimes, but ONS sheets also carry
    "Jan 2017", "January 2017" and "2017-01" forms depending on the vintage.
    """
    if isinstance(value, dt.datetime):
        return dt.date(value.year, value.month, 1)
    if isinstance(value, dt.date):
        return dt.date(value.year, value.month, 1)

    text = _norm(value)
    if not text:
        return None

    # "2017-01", "2017/01"
    match = re.match(r"^(20\d{2})[-/](\d{1,2})$", text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        return dt.date(year, month, 1) if 1 <= month <= 12 else None

    # "jan 2017", "january 2017"
    match = re.match(r"^([a-z]+)[\s-]+(20\d{2})$", text)
    if match and match.group(1) in _MONTH_NAMES:
        return dt.date(int(match.group(2)), _MONTH_NAMES[match.group(1)], 1)

    # "2017 jan"
    match = re.match(r"^(20\d{2})[\s-]+([a-z]+)$", text)
    if match and match.group(2) in _MONTH_NAMES:
        return dt.date(int(match.group(1)), _MONTH_NAMES[match.group(2)], 1)

    return None


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[,%\s£]", "", str(value or ""))
    try:
        return float(text)
    except ValueError:
        return None


def find_release_xlsx(release_url: str, session: requests.Session) -> str:
    """Locate the .xlsx download link on an ad hoc release page."""
    resp = session.get(release_url, timeout=60, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    links = re.findall(r'href="([^"]+\.xlsx[^"]*)"', resp.text, flags=re.IGNORECASE)
    if not links:
        raise WeightsParseError(
            f"no .xlsx link on {release_url}. The release layout may have changed; "
            "pass --xlsx-url with the direct file link."
        )
    href = links[0]
    return href if href.startswith("http") else f"{ONS_BASE}{href}"


def discover_latest_release(session: requests.Session) -> str | None:
    """Best-effort search for a newer ad hoc release than the pinned default."""
    try:
        resp = session.get(
            SEARCH_URL,
            params={"q": SEARCH_TERM},
            timeout=60,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("release discovery failed (%s); using the pinned default", exc)
        return None

    hrefs = re.findall(
        r'href="(/economy/inflationandpriceindices/adhocs/[^"]*'
        r'airfaresconsumerpricessubindices[^"]*)"',
        resp.text,
        flags=re.IGNORECASE,
    )
    if not hrefs:
        return None
    # Ad hoc URLs are prefixed with an incrementing reference number; the
    # highest is the most recent vintage.
    def ref(href: str) -> int:
        m = re.search(r"/adhocs/(\d+)", href)
        return int(m.group(1)) if m else -1

    best = max(hrefs, key=ref)
    return f"{ONS_BASE}{best}"


def _iter_sheets(workbook) -> Iterable[tuple[str, list[list[Any]]]]:
    for sheet in workbook.worksheets:
        yield sheet.title, [list(row) for row in sheet.iter_rows(values_only=True)]


def _find_weight_sheet(sheets: Sequence[tuple[str, list[list[Any]]]]):
    """Prefer a sheet named for weights; otherwise any sheet that parses."""
    named = [s for s in sheets if "weight" in _norm(s[0])]
    return named + [s for s in sheets if s not in named]


def _find_header(
    rows: list[list[Any]], key: str = "year"
) -> tuple[int, dict[str, int]] | None:
    """Locate the header row and map category -> column index.

    `key` selects whether the leading column holds years (weights sheet) or
    monthly periods (sub-index sheet).
    """
    key_patterns = _YEAR_PATTERNS if key == "year" else _MONTH_PATTERNS
    coerce = _as_year if key == "year" else _as_month

    for idx, row in enumerate(rows[:40]):
        cells = [_norm(c) for c in row]
        if not any(cells):
            continue
        columns: dict[str, int] = {}
        for category, patterns in _CATEGORY_PATTERNS.items():
            for col, cell in enumerate(cells):
                if cell and any(re.search(p, cell) for p in patterns):
                    columns.setdefault(category, col)
                    break
        if len(columns) != 3:
            continue
        key_col = next(
            (c for c, cell in enumerate(cells) if any(re.search(p, cell) for p in key_patterns)),
            None,
        )
        if key_col is None:
            # No explicit header for the key column: fall back to the first
            # column not claimed by a category, provided it actually holds
            # values of the expected kind.
            claimed = set(columns.values())
            for c in range(len(cells)):
                if c in claimed:
                    continue
                if any(coerce(r[c]) for r in rows[idx + 1 : idx + 15] if c < len(r)):
                    key_col = c
                    break
        if key_col is None:
            continue
        columns[key] = key_col
        return idx, columns
    return None


def parse_weights(xlsx_bytes: bytes) -> list[dict[str, Any]]:
    """Extract validated weight rows from the release workbook."""
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise WeightsParseError(
            "openpyxl is required to parse the ONS release; pip install openpyxl"
        ) from exc

    workbook = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True, read_only=True)
    sheets = list(_iter_sheets(workbook))

    for title, rows in _find_weight_sheet(sheets):
        found = _find_header(rows)
        if not found:
            continue
        header_idx, columns = found
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        for row in rows[header_idx + 1 :]:
            if not row or columns["year"] >= len(row):
                continue
            year = _as_year(row[columns["year"]])
            if year is None or year in seen:
                continue
            values = {}
            for category in ("domestic", "european", "long_haul"):
                col = columns[category]
                value = _as_number(row[col]) if col < len(row) else None
                if value is None or value <= 0:
                    values = {}
                    break
                values[category] = value
            if not values:
                continue
            seen.add(year)
            out.append({"year": year, **values})

        if len(out) >= 2:
            log.info("parsed %d weight rows from sheet %r", len(out), title)
            return sorted(out, key=lambda r: r["year"])

    raise WeightsParseError(
        "could not locate a weights table. Workbook structure:\n" + describe(sheets)
    )


def _find_subindex_sheet(sheets: Sequence[tuple[str, list[list[Any]]]]):
    """Prefer a sheet named for indices; explicitly deprioritise the weights sheet."""
    def rank(entry) -> int:
        title = _norm(entry[0])
        if "weight" in title:
            return 2
        if any(word in title for word in ("index", "indices", "sub-ind", "subind", "series")):
            return 0
        return 1

    return sorted(sheets, key=rank)


def parse_subindices(xlsx_bytes: bytes) -> list[dict[str, Any]]:
    """Extract ONS's published monthly sub-index series from the release.

    Same defensive posture as `parse_weights`: locate by searching, validate
    hard, and refuse rather than guess. This series is the validation *answer
    key*, so a mis-parse would not merely degrade the output -- it would make
    every accuracy figure wrong in a direction we could not detect.
    """
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise SubIndexParseError(
            "openpyxl is required to parse the ONS release; pip install openpyxl"
        ) from exc

    workbook = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True, read_only=True)
    sheets = list(_iter_sheets(workbook))

    for title, rows in _find_subindex_sheet(sheets):
        found = _find_header(rows, key="month")
        if not found:
            continue
        header_idx, columns = found
        out: list[dict[str, Any]] = []
        seen: set[dt.date] = set()
        for row in rows[header_idx + 1 :]:
            if not row or columns["month"] >= len(row):
                continue
            month = _as_month(row[columns["month"]])
            if month is None or month.year < MIN_SERIES_YEAR or month in seen:
                continue
            values: dict[str, float] = {}
            for category in ("domestic", "european", "long_haul"):
                col = columns[category]
                value = _as_number(row[col]) if col < len(row) else None
                if value is None or value <= 0:
                    values = {}
                    break
                values[category] = value
            if not values:
                continue
            seen.add(month)
            out.append({"index_month": month, **values})

        # A monthly series spanning years, not a handful of stray rows.
        if len(out) >= 12:
            log.info("parsed %d monthly sub-index rows from sheet %r", len(out), title)
            return sorted(out, key=lambda r: r["index_month"])

    raise SubIndexParseError(
        "could not locate a monthly sub-index table. Workbook structure:\n" + describe(sheets)
    )


def describe(sheets: Sequence[tuple[str, list[list[Any]]]], max_rows: int = 12) -> str:
    """Human-readable dump of the workbook, for diagnosing a failed parse."""
    lines: list[str] = []
    for title, rows in sheets:
        lines.append(f"\n=== sheet {title!r} ({len(rows)} rows) ===")
        for row in rows[:max_rows]:
            cells = [("" if c is None else str(c))[:24] for c in row[:10]]
            if any(cells):
                lines.append("  | " + " | ".join(cells))
    return "\n".join(lines)


def write_weights_csv(rows: list[dict[str, Any]], path: pathlib.Path, source_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(
            "# ONS air fares sub-index weights (CPI item 07.3.3).\n"
            f"# Fetched automatically from {source_url}\n"
            f"# on {dt.date.today().isoformat()} by ukairfares.onsweights.\n"
            "# Weights are normalised before use, so raw parts or percentages both work.\n"
        )
        writer = csv.DictWriter(
            fh, fieldnames=["year", "domestic", "european", "long_haul", "is_placeholder"]
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "is_placeholder": "false"})
    log.info("wrote %d rows to %s", len(rows), path)


def fetch(
    *,
    release_url: str | None = None,
    xlsx_url: str | None = None,
    discover: bool = False,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], str]:
    session = session or requests.Session()
    if not xlsx_url:
        url = release_url or DEFAULT_RELEASE_URL
        if discover and not release_url:
            url = discover_latest_release(session) or url
            log.info("using release %s", url)
        xlsx_url = find_release_xlsx(url, session)
    log.info("downloading %s", xlsx_url)
    resp = session.get(xlsx_url, timeout=120, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return parse_weights(resp.content), xlsx_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch ONS air fare sub-index weights into weights.csv."
    )
    parser.add_argument("--release-url", default=None, help="Ad hoc release page URL.")
    parser.add_argument("--xlsx-url", default=None, help="Direct .xlsx URL, skipping discovery.")
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Search ONS for a newer release than the pinned default.",
    )
    parser.add_argument("--out", type=pathlib.Path, default=WEIGHTS_PATH)
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Print the workbook's structure and exit without parsing.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        if args.dump:
            import openpyxl

            session = requests.Session()
            url = args.xlsx_url or find_release_xlsx(
                args.release_url or DEFAULT_RELEASE_URL, session
            )
            content = session.get(url, timeout=120, headers={"User-Agent": USER_AGENT}).content
            wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
            print(describe(list(_iter_sheets(wb)), max_rows=25))
            return 0

        rows, source = fetch(
            release_url=args.release_url, xlsx_url=args.xlsx_url, discover=args.discover
        )
    except WeightsParseError as exc:
        # Loud, with the evidence needed to fix it in one pass.
        print(f"::error::could not parse ONS weights: {exc}", flush=True)
        log.error("%s", exc)
        return 1
    except requests.RequestException as exc:
        print(f"::error::could not fetch ONS weights: {exc}", flush=True)
        log.error("%s", exc)
        return 1

    write_weights_csv(rows, args.out, source)
    print(
        f"Wrote {len(rows)} year(s) of weights ({rows[0]['year']}-{rows[-1]['year']}) "
        f"to {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
