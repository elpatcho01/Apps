"""Backfill ONS's published sub-indices -- the validation answer key.

The ONS ad hoc release carries the actual domestic / European / long-haul
sub-indices from January 2017, in the same workbook as the weights. Loading
them means the comparison is already sitting in BigQuery the moment our own
reconstructions start landing, rather than needing a scramble at scoring time.

Note what this does and does not buy. It does NOT let us reconstruct history --
the fares needed for that are unobservable in retrospect (see README). It gives
us the target series: something to size each haul category's real volatility
against before trusting any nowcast of it, and the values to score against once
live months accumulate.

The series' basis is *detected* rather than assumed. The release describes a
"January (of each year) = 100 basis", but whether the published series resets
every January or is chain-linked into a continuous one is not clear from that
description. Once the data is loaded the question answers itself -- if every
January is exactly 100, it resets -- so we read it off rather than guess, and
record the answer on every row.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import uuid
from decimal import Decimal
from typing import Any

import requests

from . import bq
from .config import Config, ConfigError
from .index import detect_basis
from .onsweights import (
    DEFAULT_RELEASE_URL,
    SubIndexParseError,
    USER_AGENT,
    discover_latest_release,
    find_release_xlsx,
    parse_subindices,
)

log = logging.getLogger("ukairfares.backfill")

HAULS = ("domestic", "european", "long_haul")


def _gha_notice(level: str, message: str) -> None:
    print(f"::{level}::{message}", flush=True)


def build_rows(
    series: list[dict[str, Any]],
    *,
    release_url: str,
    release_label: str,
    run_id: str,
    fetched_ts: dt.datetime,
) -> list[dict[str, Any]]:
    """Flatten the parsed series into one row per (month, haul category)."""
    basis_by_haul = {
        haul: detect_basis([(r["index_month"], r[haul]) for r in series]) for haul in HAULS
    }
    rows: list[dict[str, Any]] = []
    for record in series:
        for haul in HAULS:
            rows.append(
                {
                    "index_month": record["index_month"],
                    "haul_category": haul,
                    "index_value": Decimal(f"{record[haul]:.4f}"),
                    "basis": basis_by_haul[haul],
                    "release_url": release_url,
                    "release_label": release_label,
                    "fetched_ts": fetched_ts,
                    "is_current": True,
                    "run_id": run_id,
                }
            )
    return rows


def run_backfill(
    config: Config,
    *,
    release_url: str | None = None,
    xlsx_url: str | None = None,
    discover: bool = False,
    writer: bq.Writer | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex
    fetched_ts = dt.datetime.now(dt.timezone.utc)
    session = session or requests.Session()

    source_page = release_url or DEFAULT_RELEASE_URL
    if discover and not release_url:
        source_page = discover_latest_release(session) or source_page
    if not xlsx_url:
        xlsx_url = find_release_xlsx(source_page, session)

    log.info("downloading %s", xlsx_url)
    resp = session.get(xlsx_url, timeout=120, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()

    series = parse_subindices(resp.content)
    rows = build_rows(
        series,
        release_url=xlsx_url,
        release_label=source_page.rsplit("/", 1)[-1],
        run_id=run_id,
        fetched_ts=fetched_ts,
    )

    writer = writer or bq.build_writer(config)
    written = writer.append(
        config.table_ref("ons_published_index") if not config.dry_run else "ons_published_index",
        rows,
    )

    months = [r["index_month"] for r in series]
    bases = sorted({r["basis"] for r in rows})
    summary = {
        "run_id": run_id,
        "release_url": xlsx_url,
        "months": len(series),
        "rows_written": written,
        "first_month": months[0].isoformat(),
        "last_month": months[-1].isoformat(),
        "basis_detected": bases,
    }
    log.info("summary: %s", json.dumps(summary))

    if "unknown" in bases:
        _gha_notice(
            "warning",
            "could not determine the published series' basis from the data; "
            "index comparisons may not be like-for-like",
        )
    else:
        _gha_notice(
            "notice",
            f"published series basis detected as {', '.join(bases)} "
            f"({len(series)} months, {months[0]:%b %Y} to {months[-1]:%b %Y})",
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill ONS published air fare sub-indices into BigQuery."
    )
    parser.add_argument("--release-url", default=None, help="Ad hoc release page URL.")
    parser.add_argument("--xlsx-url", default=None, help="Direct .xlsx URL.")
    parser.add_argument(
        "--discover", action="store_true", help="Search ONS for a newer release."
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
        _gha_notice("error", f"configuration error: {exc}")
        return 2

    try:
        summary = run_backfill(
            config,
            release_url=args.release_url,
            xlsx_url=args.xlsx_url,
            discover=args.discover,
        )
    except SubIndexParseError as exc:
        # The answer key must never be guessed at.
        _gha_notice("error", f"could not parse ONS sub-indices: {exc}")
        log.error("%s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        _gha_notice("error", f"backfill failed: {exc}")
        log.exception("backfill failed")
        return 1

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
