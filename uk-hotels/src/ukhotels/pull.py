"""Daily collector.

For each location in the panel, for each stay night due today, price every
comparable property and append one row per property.

WHAT "DUE TODAY" MEANS, AND WHY IT IS NOT "TODAY IS INDEX DAY"
---------------------------------------------------------------
This is where the air-fares intuition breaks. There, the collector runs on index
day and prices flights departing on a future index day. Here the collector runs
*six weeks before* index week and prices a stay night inside it. So the schedule
sits a month and a half earlier in the calendar than the month it is measuring,
and on any given day the run may be serving one CPI month under one alignment
reading and a different one under the other. `onscal.index_months_in_scope`
answers that; nothing here reasons about it directly.

Because ONS withhold the index day until the following month's bulletin, both
the 2nd and 3rd Tuesday are live hypotheses at collection time, and both nights
hang off each. So a day that is due for any of those combinations is collected
for all of them. Over-collecting costs a few pence of quota; under-collecting
loses a night that can never be recollected, because a rate for a stay six weeks
out is a quote, not a record, and nobody retains it.

ERROR POLICY
------------
Two requirements pull in opposite directions -- "don't let one location kill the
run" and "fail loudly rather than skip silently" -- and they are reconciled the
same way as on the sibling project:

  * an individual location/night failure is retried once with backoff, then
    written as an `error` row and skipped;
  * if the failure *rate* crosses a threshold the run exits non-zero, because at
    that point the vintage is not trustworthy and a human should look;
  * a config error is fatal immediately, before any query is issued.

Failures are written as rows, not merely logged. An absent row and a failed row
are different facts, and only one of them is recoverable later.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib
import sys
import time
import uuid
from decimal import Decimal
from typing import Any

from . import bq, panel, selection
from .config import Config, ConfigError, PIPELINE_VERSION
from .onscal import (
    Stay,
    candidate_index_days,
    index_month_collection,
    index_month_stay,
    index_months_in_scope,
    stays_for_collection_day,
)
from .panel import Location
from .providers import AccommodationProvider, ProviderError, build_provider

log = logging.getLogger("ukhotels.pull")

RETRY_BACKOFF_SECONDS = 5.0


def _gha_notice(level: str, message: str) -> None:
    """Emit a GitHub Actions annotation so problems surface in the run UI."""
    print(f"::{level}::{message}", flush=True)


def stays_due(
    collection_day: dt.date,
    *,
    alignment: str = "per_night",
    advance_days: int = 42,
    tolerance_days: int = 0,
) -> list[Stay]:
    """Every stay due to be priced on `collection_day`, across both index-day
    hypotheses.

    De-duplicated on (check_in, alignment): the 2nd and 3rd Tuesday hypotheses
    can occasionally imply the same night, and pricing it twice in one run would
    inflate the observation count without adding information.
    """
    out: dict[tuple[dt.date, str], Stay] = {}
    for index_month in index_months_in_scope(collection_day, advance_days=advance_days):
        for index_day in candidate_index_days(index_month.year, index_month.month):
            for stay in stays_for_collection_day(
                collection_day,
                index_day=index_day,
                alignment=alignment,
                advance_days=advance_days,
                tolerance_days=tolerance_days,
            ):
                out.setdefault((stay.check_in, stay.alignment), stay)
    return sorted(out.values(), key=lambda s: (s.check_in, s.stay_night_kind))


def _base_row(
    *,
    run_id: str,
    scrape_ts: dt.datetime,
    scrape_date: dt.date,
    location: Location,
    stay: Stay,
    config: Config,
    source_api: str,
    is_cached_source: bool,
) -> dict[str, Any]:
    """The columns every row carries, whatever its status.

    Built once per (location, night) and copied per property, so an error row
    and an ok row are structurally identical apart from the property and price
    columns. That is what makes `status` sufficient to interpret a row.
    """
    return {
        "run_id": run_id,
        "scrape_ts": scrape_ts,
        "scrape_date": scrape_date,
        "location": location.code,
        "city": location.city,
        "check_in": stay.check_in,
        "check_out": stay.check_out,
        "stay_night_kind": stay.stay_night_kind,
        "advance_days": stay.advance_days,
        "advance_days_actual": stay.advance_days_actual,
        "collection_alignment": stay.alignment,
        "index_day": stay.index_day,
        "index_day_ordinal": stay.index_day_ordinal,
        "index_month_stay": index_month_stay(stay.check_in, stay.index_day),
        "index_month_collection": index_month_collection(scrape_date),
        "property_token": None,
        "property_name": None,
        "property_tier": None,
        "hotel_class": None,
        "property_type": None,
        "is_panel_property": False,
        "price_gbp": None,
        "price_before_taxes_gbp": None,
        "price_cheapest_gbp": None,
        "tax_basis": config.tax_basis,
        "rate_basis": config.rate_basis,
        "free_cancellation": None,
        # Not obtainable from any implemented provider. Written explicitly as
        # NULL on every row so the gap is in the data rather than only in the
        # documentation.
        "board_basis": None,
        "room_type": None,
        "adults": panel.ADULTS,
        "children": panel.CHILDREN,
        "currency_raw": None,
        "comparability_basis": None,
        "n_quotes": 0,
        "n_considered": 0,
        "n_dropped_rate_basis": 0,
        "n_dropped_tier": 0,
        "n_dropped_property_type": 0,
        "n_dropped_outlier": 0,
        "cell_price_spread_ratio": None,
        "source_api": source_api,
        "raw_response": None,
        "is_cached_source": is_cached_source,
        "status": "error",
        "error_message": None,
        "pipeline_version": PIPELINE_VERSION,
    }


def collect_one(
    provider: AccommodationProvider,
    location: Location,
    stay: Stay,
    *,
    config: Config,
    run_id: str,
    scrape_ts: dt.datetime,
    scrape_date: dt.date,
    pinned: frozenset[str],
    retries: int = 1,
    backoff: float = RETRY_BACKOFF_SECONDS,
) -> list[dict[str, Any]]:
    """Price one (location, night) and build its rows. Never raises.

    Returns one row per surviving comparable property, or a single row carrying
    `no_data` or `error` when there are none.
    """
    base = _base_row(
        run_id=run_id,
        scrape_ts=scrape_ts,
        scrape_date=scrape_date,
        location=location,
        stay=stay,
        config=config,
        source_api=getattr(provider, "name", "unknown"),
        is_cached_source=getattr(provider, "is_cached_source", False),
    )

    last_error: str | None = None
    for attempt in range(retries + 1):
        try:
            result = provider.search(
                query=location.query,
                check_in=stay.check_in,
                check_out=stay.check_out,
                adults=panel.ADULTS,
                children=panel.CHILDREN,
                currency=config.currency,
            )
        except ProviderError as exc:
            last_error = str(exc)
            if not exc.retryable or attempt >= retries:
                break
            wait = backoff * (2**attempt)
            log.warning(
                "%s %s: %s -- retrying in %.0fs",
                location.code, stay.check_in, exc, wait,
            )
            time.sleep(wait)
            continue
        except Exception as exc:  # noqa: BLE001 - a provider bug must not kill the run
            last_error = f"unexpected {type(exc).__name__}: {exc}"
            log.exception("unexpected error for %s", location.code)
            break
        else:
            return _rows_from_result(base, result, config=config, pinned=pinned)

    base["status"] = "error"
    base["error_message"] = (last_error or "unknown error")[:1000]
    log.error("%s %s FAILED: %s", location.code, stay.check_in, last_error)
    return [base]


def _rows_from_result(
    base: dict[str, Any],
    result,
    *,
    config: Config,
    pinned: frozenset[str],
) -> list[dict[str, Any]]:
    raw = json.dumps(result.raw_payload, default=str)
    base["source_api"] = result.source_api
    base["n_quotes"] = len(result.quotes)

    if not result.quotes:
        # A legitimate empty observation -- the location returned nothing for
        # these dates. Recorded as a first-class row so coverage gaps are
        # visible in the panel rather than silently absent.
        base["status"] = "no_data"
        base["raw_response"] = raw
        return [base]

    sets = selection.comparable_sets(
        result.quotes,
        rate_basis=config.rate_basis,
        max_price_ratio=config.max_price_ratio,
    )

    rows: list[dict[str, Any]] = []
    for tier, comparable in sets.items():
        if not comparable.properties:
            continue
        cheapest = comparable.cheapest
        spread = comparable.price_spread_ratio()
        for quote in comparable.properties:
            row = dict(base)
            row.update(
                {
                    "status": "ok",
                    "property_token": quote.property_token,
                    "property_name": quote.property_name,
                    "property_tier": tier,
                    "hotel_class": (
                        Decimal(str(quote.hotel_class))
                        if quote.hotel_class is not None
                        else None
                    ),
                    "property_type": quote.property_type,
                    "is_panel_property": quote.property_token in pinned,
                    "price_gbp": selection.headline_price(quote, config.tax_basis),
                    "price_before_taxes_gbp": quote.price_before_taxes,
                    "price_cheapest_gbp": cheapest.price if cheapest else None,
                    "free_cancellation": quote.free_cancellation,
                    "board_basis": quote.board_basis,
                    "room_type": quote.room_type,
                    "currency_raw": quote.currency,
                    "comparability_basis": comparable.basis,
                    "n_considered": comparable.n_considered,
                    "n_dropped_rate_basis": comparable.n_dropped_rate_basis,
                    "n_dropped_tier": comparable.n_dropped_tier,
                    "n_dropped_property_type": comparable.n_dropped_property_type,
                    "n_dropped_outlier": comparable.n_dropped_outlier,
                    "cell_price_spread_ratio": (
                        Decimal(f"{spread:.4f}") if spread is not None else None
                    ),
                    # The payload is per location call, so attaching it to every
                    # property row would multiply it by ~10. Attached to the
                    # first row of the first tier only; `run_id` plus
                    # `scrape_date` plus `location` recovers it for the rest.
                    "raw_response": raw if not rows else None,
                }
            )
            rows.append(row)

    if not rows:
        # The provider returned properties but none survived the comparability
        # filter -- every one was a vacation rental, unrated, or on the wrong
        # cancellation basis. That is emphatically not the same as "no data",
        # and conflating them would hide a filter that had become too strict.
        base["status"] = "no_data"
        base["raw_response"] = raw
        base["error_message"] = (
            f"{len(result.quotes)} properties returned, none comparable "
            f"(rate_basis={config.rate_basis})"
        )
        return [base]

    return rows


def run_pull(
    config: Config,
    *,
    scrape_date: dt.date | None = None,
    writer: bq.Writer | None = None,
    provider: AccommodationProvider | None = None,
    locations: tuple[Location, ...] | None = None,
    tolerance_days: int = 0,
    backoff: float = RETRY_BACKOFF_SECONDS,
) -> dict[str, Any]:
    """Execute one full collection run. Returns a summary dict."""
    scrape_date = scrape_date or dt.date.today()
    scrape_ts = dt.datetime.now(dt.timezone.utc)
    run_id = uuid.uuid4().hex
    locations = locations if locations is not None else panel.LOCATIONS

    stays = stays_due(
        scrape_date,
        alignment=config.collection_alignment,
        advance_days=config.advance_days,
        tolerance_days=tolerance_days,
    )

    if provider is None:
        # Fail before issuing any queries rather than after twelve failures.
        config.require_provider_credential()
        kwargs: dict[str, Any] = {}
        if config.provider_name == "serpapi":
            kwargs = {"api_key": config.provider_credential, "market": config.market}
        provider = build_provider(config.provider_name, **kwargs)

    writer = writer or bq.build_writer(config)

    # Synthetic rates must never reach a real table. `accommodation_scrapes` is
    # append-only by design, so a mock run against production leaves rows that
    # are not supposed to be deletable -- and they would silently corrupt every
    # index built afterwards. Easy to do by accident while setting up, hence an
    # explicit gate rather than a note in the docs.
    if getattr(provider, "name", "") == "mock" and not config.dry_run:
        import os

        if os.environ.get("ALLOW_MOCK_WRITES", "").strip().lower() not in {"1", "true", "yes"}:
            raise ConfigError(
                "refusing to write mock (synthetic) rates to "
                f"{config.scrapes_ref}. Set DRY_RUN=1 to test without writing, "
                "or ALLOW_MOCK_WRITES=1 if you genuinely intend to seed a "
                "throwaway dataset with fake data."
            )
        log.warning("ALLOW_MOCK_WRITES set -- writing SYNTHETIC rates to %s", config.scrapes_ref)

    pinned = panel.panel_tokens(panel.load_property_panel())

    log.info(
        "run %s | scrape_date=%s | %d stay night(s) due | %d locations | "
        "provider=%s | alignment=%s | rate_basis=%s | tax_basis=%s | pinned=%d",
        run_id, scrape_date, len(stays), len(locations), provider.name,
        config.collection_alignment, config.rate_basis, config.tax_basis, len(pinned),
    )
    for stay in stays:
        log.info(
            "  due: %s (%s) for index month %s, %d days ahead",
            stay.check_in, stay.stay_night_kind, stay.index_month, stay.advance_days_actual,
        )

    rows: list[dict[str, Any]] = []
    cells = 0
    cell_failures = 0
    for location in locations:
        for stay in stays:
            cells += 1
            produced = collect_one(
                provider,
                location,
                stay,
                config=config,
                run_id=run_id,
                scrape_ts=scrape_ts,
                scrape_date=scrape_date,
                pinned=pinned,
                backoff=backoff,
            )
            if produced and produced[0]["status"] == "error":
                cell_failures += 1
            rows.extend(produced)

    counts = {"ok": 0, "no_data": 0, "error": 0}
    for row in rows:
        counts[row.get("status", "error")] = counts.get(row.get("status", "error"), 0) + 1

    # The failure rate is measured over *cells* (location x night), not over
    # rows. A successful cell yields ten rows and a failed one yields a single
    # row, so a row-based rate would make a run that failed half its locations
    # look like a 9% failure. That arithmetic would have hidden exactly the kind
    # of partial outage the threshold exists to catch.
    failure_rate = cell_failures / cells if cells else 1.0

    written = writer.append(
        config.scrapes_ref if not config.dry_run else "accommodation_scrapes", rows
    )

    summary = {
        "run_id": run_id,
        "scrape_date": scrape_date.isoformat(),
        "stay_nights_due": [
            {
                "check_in": s.check_in.isoformat(),
                "kind": s.stay_night_kind,
                "index_month": s.index_month.isoformat(),
                "index_day": s.index_day.isoformat(),
                "advance_days_actual": s.advance_days_actual,
            }
            for s in stays
        ],
        "provider": provider.name,
        "collection_alignment": config.collection_alignment,
        "rate_basis": config.rate_basis,
        "tax_basis": config.tax_basis,
        "cells": cells,
        "cell_failures": cell_failures,
        "rows": len(rows),
        "written": written,
        "ok": counts["ok"],
        "no_data": counts["no_data"],
        "error": counts["error"],
        "failure_rate": round(failure_rate, 4),
        "properties": len({r["property_token"] for r in rows if r.get("property_token")}),
        "panel_properties_priced": sum(1 for r in rows if r.get("is_panel_property")),
    }
    log.info("summary: %s", json.dumps(summary))

    if counts["error"]:
        _gha_notice(
            "warning",
            f"{cell_failures}/{cells} location-nights failed on {scrape_date} "
            f"(rate {failure_rate:.0%})",
        )
    if counts["no_data"]:
        _gha_notice(
            "notice",
            f"{counts['no_data']} location-night(s) returned no comparable properties "
            f"on {scrape_date}. Check n_dropped_rate_basis before assuming a supply gap: "
            "a strict cancellation basis thins cells before availability does.",
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect one vintage of accommodation price observations."
    )
    parser.add_argument(
        "--scrape-date",
        type=dt.date.fromisoformat,
        default=None,
        help="Override the collection date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--dry-run-out",
        type=pathlib.Path,
        default=None,
        help="With DRY_RUN=1, write the would-be rows here as NDJSON.",
    )
    parser.add_argument(
        "--location",
        default=None,
        help="Restrict to one ONS region code (useful for debugging).",
    )
    parser.add_argument(
        "--tolerance-days",
        type=int,
        default=0,
        help=(
            "Price nights whose due date is within N days of the collection "
            "date. Use 1 to recover a run that slipped; the true lead is still "
            "recorded in advance_days_actual."
        ),
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
        # Fatal and immediate: a misconfigured run must not look like a quiet
        # no-op in the Actions UI.
        _gha_notice("error", f"configuration error: {exc}")
        log.error("configuration error: %s", exc)
        return 2

    locations = None
    if args.location:
        locations = tuple(l for l in panel.LOCATIONS if l.code == args.location)
        if not locations:
            _gha_notice("error", f"unknown location {args.location!r}")
            return 2

    writer = bq.build_writer(config, args.dry_run_out) if config.dry_run else None

    try:
        summary = run_pull(
            config,
            scrape_date=args.scrape_date,
            writer=writer,
            locations=locations,
            tolerance_days=args.tolerance_days,
        )
    except Exception as exc:  # noqa: BLE001
        _gha_notice("error", f"pull run failed: {exc}")
        log.exception("pull run failed")
        return 1

    if summary["cells"] == 0:
        # Nothing was due today. Not a failure -- the collection calendar is
        # sparse by construction (four to six days per CPI month), and a
        # scheduled run on a day with nothing due is the expected case, not a
        # broken one.
        _gha_notice(
            "notice",
            f"no stay nights due on {summary['scrape_date']}; nothing to collect",
        )
        print(json.dumps(summary, indent=2))
        return 0

    if summary["failure_rate"] > config.failure_threshold:
        _gha_notice(
            "error",
            f"failure rate {summary['failure_rate']:.0%} exceeds threshold "
            f"{config.failure_threshold:.0%} -- treating this vintage as unreliable",
        )
        return 1

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
