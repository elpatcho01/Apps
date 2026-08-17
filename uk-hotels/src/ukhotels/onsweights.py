"""Fetch ONS regional weights for the accommodation item, if they exist.

HONEST STATUS: THEY PROBABLY DO NOT, AND THAT IS THE POINT OF THIS MODULE
--------------------------------------------------------------------------
The air-fares project has a real weights source: the ad hoc release carries
per-series weights alongside the sub-indices, so `onsweights` there parses a
sheet and gets real numbers. No equivalent was found for accommodation. ONS
publish a CPI weight for class 11.2.0.1 as a whole (series `l8u9`), and
expenditure weights across the twelve UK regions in aggregate, but not the
cross-tabulation this pipeline would need to weight twelve regional
sub-indices into one national figure for this item specifically.

So this module does three things, in decreasing order of how much they help:

  1. Fetch the class-level weight from the ONS time series. That is a real ONS
     number and is worth having, even though it weights the item within CPI
     rather than weighting regions within the item.
  2. Look for a weights sheet on the ad hoc release, in case a newer vintage
     starts publishing one. Written defensively, like the air-fares parser.
  3. **Fail loudly and leave the placeholders in place** when neither yields
     regional weights.

Point 3 is the one that matters. Shipping invented numbers that look
authoritative is worse than shipping none: a wrong weight silently corrupts
every aggregate built on it, a loud failure costs one CI log. So `weights.csv`
keeps its `is_placeholder` flag, `load_weights` refuses to hand placeholders to
the validation path, and every weighted aggregate carries
`weights_are_placeholder` all the way into the report.

Per-region reconstructions do not use weights at all and are unaffected. Only
the national `location = "all"` roll-up depends on them, and it is explicitly a
convenience level rather than the headline.

Run where egress to ons.gov.uk is permitted -- a GitHub Actions runner is fine.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import logging
import pathlib
import re
import sys
from typing import Any

import requests

from .backfill import (
    DEFAULT_ADHOC_URL,
    REGION_ALIASES,
    USER_AGENT,
    discover_adhoc_release,
    find_spreadsheet_url,
)
from .panel import WEIGHTS_PATH

log = logging.getLogger("ukhotels.onsweights")

ONS_BASE = "https://www.ons.gov.uk"

#: CPI weight for class 11.2.0.1. A real ONS number, but a within-CPI weight
#: rather than a within-item regional one -- see the module docstring.
CLASS_WEIGHT_SERIES = "l8u9"

MIN_YEAR, MAX_YEAR = 2015, 2035


class WeightsParseError(RuntimeError):
    """No defensible regional weights could be obtained."""


def _session(session: requests.Session | None = None) -> requests.Session:
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    return session


def _region_code(label: Any) -> str | None:
    text = re.sub(r"\s+", " ", str(label or "")).strip().lower()
    text = re.sub(r"\s*\(.*\)$", "", text)
    return REGION_ALIASES.get(text)


def _as_year(value: Any) -> int | None:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.year
    if isinstance(value, (int, float)) and float(value).is_integer():
        year = int(value)
        return year if MIN_YEAR <= year <= MAX_YEAR else None
    match = re.search(r"\b(20\d{2})\b", str(value or ""))
    if match:
        year = int(match.group(1))
        return year if MIN_YEAR <= year <= MAX_YEAR else None
    return None


def parse_regional_weights(content: bytes) -> dict[int, dict[str, float]]:
    """Look for a year x region weights table in the ad hoc workbook.

    Written without sight of the file (ons.gov.uk is egress-blocked in the
    development sandbox), so it searches for its landmarks rather than assuming
    offsets, and validates hard: at least four regions, every weight positive,
    and the row summing to something close to 1 or to 100. A table that does not
    pass is rejected rather than half-read, because a plausible-looking wrong
    weight is the worst possible output here.
    """
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise WeightsParseError(f"openpyxl is required: {exc}")

    workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    for sheet in workbook.worksheets:
        if "weight" not in sheet.title.lower():
            continue
        rows = [list(r) for r in sheet.iter_rows(values_only=True)]
        for header_idx, row in enumerate(rows[:25]):
            region_cols = {
                col: code
                for col, cell in enumerate(row)
                if (code := _region_code(cell)) is not None and code != "uk"
            }
            if len(region_cols) < 4:
                continue
            out: dict[int, dict[str, float]] = {}
            for data_row in rows[header_idx + 1 :]:
                year = next((y for cell in data_row[:3] if (y := _as_year(cell))), None)
                if year is None:
                    continue
                weights: dict[str, float] = {}
                for col, region in region_cols.items():
                    if col >= len(data_row):
                        continue
                    try:
                        value = float(data_row[col])
                    except (TypeError, ValueError):
                        continue
                    if value > 0:
                        weights[region] = value
                total = sum(weights.values())
                if len(weights) >= 4 and (0.9 <= total <= 1.1 or 90 <= total <= 110):
                    out[year] = weights
            if out:
                return out
    raise WeightsParseError(
        "no year x region weights table found in the ad hoc workbook. "
        f"Sheets: {', '.join(s.title for s in workbook.worksheets)}"
    )


def fetch_class_weight(session: requests.Session | None = None) -> dict[int, float]:
    """The published CPI weight for class 11.2.0.1, by year.

    Not a regional weight, and not a substitute for one. Fetched because it is
    real, cheap, and useful context for how much of CPI this item moves.
    """
    session = _session(session)
    url = (
        f"{ONS_BASE}/economy/inflationandpriceindices/timeseries/"
        f"{CLASS_WEIGHT_SERIES}/mm23/data"
    )
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    out: dict[int, float] = {}
    for entry in payload.get("years") or []:
        if not isinstance(entry, dict):
            continue
        year = _as_year(entry.get("date"))
        try:
            value = float(entry.get("value"))
        except (TypeError, ValueError):
            continue
        if year is not None and value > 0:
            out[year] = value
    return out


def write_weights(
    by_year: dict[int, dict[str, float]],
    *,
    path: pathlib.Path | None = None,
    is_placeholder: bool = False,
) -> pathlib.Path:
    path = path or WEIGHTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        if is_placeholder:
            fh.write("# PLACEHOLDER REGIONAL WEIGHTS -- NOT ONS FIGURES.\n")
        else:
            fh.write(
                "# Regional weights parsed from the ONS ad hoc release.\n"
                f"# Written {dt.date.today().isoformat()} by ukhotels.onsweights.\n"
            )
        writer = csv.writer(fh)
        writer.writerow(["year", "region", "weight", "is_placeholder"])
        for year in sorted(by_year):
            for region in sorted(by_year[year]):
                writer.writerow(
                    [year, region, by_year[year][region], str(is_placeholder).lower()]
                )
    return path


def run(
    *,
    release_url: str | None = None,
    discover: bool = False,
    path: pathlib.Path | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    session = _session(session)
    summary: dict[str, Any] = {"regional_weights_found": False}

    try:
        summary["class_weight_by_year"] = fetch_class_weight(session)
    except Exception as exc:  # noqa: BLE001
        log.warning("class weight fetch failed: %s", exc)
        summary["class_weight_error"] = f"{type(exc).__name__}: {exc}"

    url = release_url or DEFAULT_ADHOC_URL
    if discover and not release_url:
        url = discover_adhoc_release(session) or url
    summary["release_url"] = url

    try:
        resp = session.get(url, timeout=90)
        resp.raise_for_status()
        xlsx_url = find_spreadsheet_url(resp.text, url)
        if not xlsx_url:
            raise WeightsParseError(f"no spreadsheet link on {url}")
        data = session.get(xlsx_url, timeout=90)
        data.raise_for_status()
        by_year = parse_regional_weights(data.content)
    except Exception as exc:  # noqa: BLE001
        summary["error"] = f"{type(exc).__name__}: {exc}"
        # Deliberately leaves the committed placeholders untouched. Overwriting
        # them with a guess would remove the one signal -- the is_placeholder
        # flag -- that stops a placeholder-based aggregate being reported as
        # validated.
        raise WeightsParseError(
            f"no regional weights obtained ({exc}). The committed placeholders are "
            "unchanged and every aggregate built on them stays flagged. This is the "
            "expected outcome unless ONS have started publishing regional weights "
            "for this item."
        ) from exc

    path = write_weights(by_year, path=path, is_placeholder=False)
    summary.update(
        {
            "regional_weights_found": True,
            "path": str(path),
            "years": sorted(by_year),
            "regions": sorted({r for w in by_year.values() for r in w}),
        }
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch ONS weights for the accommodation item."
    )
    parser.add_argument("--release-url", default=None)
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Search ONS for a newer ad hoc release first, ranked by coverage period.",
    )
    parser.add_argument("--out", type=pathlib.Path, default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        summary = run(
            release_url=args.release_url, discover=args.discover, path=args.out
        )
    except WeightsParseError as exc:
        print(f"::warning::{exc}", file=sys.stderr, flush=True)
        log.warning("%s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"::error::weights fetch failed: {exc}", file=sys.stderr, flush=True)
        log.exception("weights fetch failed")
        return 1

    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
