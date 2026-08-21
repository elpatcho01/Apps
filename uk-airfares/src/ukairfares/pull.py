"""Daily puller.

For each route in the panel, at each advance window relevant to its haul
category, price the itinerary an ONS collector would price and append a row.

Error policy, reconciling two requirements that pull in opposite directions:

  * Task 3: "Log and continue on individual query failures -- do not let one
    route failing kill the run. Retry once with backoff."
  * Task 5: "Workflow should fail loudly (non-zero exit) rather than silently
    skip on error."

So: individual failures are retried once, then recorded as an `error` row and
skipped -- the run continues and keeps whatever it collected. But if the failure
*rate* crosses a threshold (default one third), the run exits non-zero, because
at that point the vintage is not trustworthy and a human should look. Config
errors are fatal immediately: there is no point querying 40 routes with a bad
token.

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
from typing import Any

from . import bq, panel, selection
from .config import Config, ConfigError, PIPELINE_VERSION
from .onscal import (
    Leg,
    candidate_index_days,
    in_collection_window,
    index_day_ordinal,
    index_month_collection,
    index_month_departure,
    legs_for,
    target_departure_time_for,
)
from .panel import Route
from .providers import FareProvider, ProviderError, build_provider

log = logging.getLogger("ukairfares.pull")

RETRY_BACKOFF_SECONDS = 5.0


def _gha_notice(level: str, message: str) -> None:
    """Emit a GitHub Actions annotation so problems surface in the run UI."""
    print(f"::{level}::{message}", file=sys.stderr, flush=True)


def choose_ordinal(scrape_date: dt.date) -> int:
    """Which index-day hypothesis this scrape stands in for.

    Before ONS confirm the month's index day, both the 2nd and 3rd Tuesday are
    live. We attribute each scrape to whichever candidate it is nearer, so that
    a scrape on the 9th targets 2nd-Tuesday departures and one on the 19th
    targets 3rd-Tuesday departures. Reconciliation later re-reads the panel by
    the confirmed day, so an imperfect guess here costs coverage, not accuracy.
    """
    exact = index_day_ordinal(scrape_date)
    if exact in (2, 3):
        return exact
    second, third = candidate_index_days(scrape_date.year, scrape_date.month)
    return 2 if abs((scrape_date - second).days) <= abs((scrape_date - third).days) else 3


def _base_row(
    *,
    run_id: str,
    scrape_ts: dt.datetime,
    scrape_date: dt.date,
    route: Route,
    leg: Leg,
    source_api: str,
    target_time: dt.time,
    is_cached_source: bool,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "scrape_ts": scrape_ts,
        "scrape_date": scrape_date,
        "route": route.code,
        "haul_category": route.haul,
        "days_out": leg.days_out,
        "days_out_actual": leg.days_out_actual,
        "months_ahead": leg.months_ahead,
        "index_day_ordinal": leg.index_day_ordinal,
        "departure_date": leg.departure_date,
        "return_date": leg.return_date,
        "index_month_hyp": index_month_departure(leg.departure_date),
        "index_month_departure": index_month_departure(leg.departure_date),
        "index_month_collection": index_month_collection(scrape_date),
        "source_api": source_api,
        "target_departure_time": target_time.strftime("%H:%M"),
        "is_cached_source": is_cached_source,
        "price_gbp": None,
        "price_cheapest_gbp": None,
        "currency_raw": None,
        "raw_response": None,
        "ons_rule_time_delta_minutes": None,
        "n_quotes": 0,
        "n_quotes_considered": 0,
        "candidate_basis": None,
        "selected_airline": None,
        "selected_departure_ts": None,
        "quote_found_at": None,
        "error_message": None,
    }


def collect_one(
    provider: FareProvider,
    route: Route,
    leg: Leg,
    *,
    run_id: str,
    scrape_ts: dt.datetime,
    scrape_date: dt.date,
    currency: str,
    target_time: dt.time,
    retries: int = 1,
    backoff: float = RETRY_BACKOFF_SECONDS,
) -> dict[str, Any]:
    """Price one (route, window) and build its row. Never raises."""
    row = _base_row(
        run_id=run_id,
        scrape_ts=scrape_ts,
        scrape_date=scrape_date,
        route=route,
        leg=leg,
        source_api=getattr(provider, "name", "unknown"),
        target_time=target_time,
        is_cached_source=getattr(provider, "is_cached_source", False),
    )

    last_error: str | None = None
    for attempt in range(retries + 1):
        try:
            result = provider.search(
                origin=route.origin,
                destination=route.destination,
                departure_date=leg.departure_date,
                return_date=leg.return_date,
                adults=panel.ADULTS,
                cabin=panel.CABIN_CLASS,
                currency=currency,
            )
        except ProviderError as exc:
            last_error = str(exc)
            if not exc.retryable or attempt >= retries:
                break
            wait = backoff * (2**attempt)
            log.warning(
                "%s %dm out: %s -- retrying in %.0fs", route.code, leg.months_ahead, exc, wait
            )
            time.sleep(wait)
            continue
        except Exception as exc:  # noqa: BLE001 - a provider bug must not kill the run
            last_error = f"unexpected {type(exc).__name__}: {exc}"
            log.exception("unexpected error for %s", route.code)
            break
        else:
            return _row_from_result(row, result, target_time)

    row["status"] = "error"
    row["error_message"] = (last_error or "unknown error")[:1000]
    log.error("%s %dm out FAILED: %s", route.code, leg.months_ahead, last_error)
    return row


def _row_from_result(row: dict[str, Any], result, target_time: dt.time) -> dict[str, Any]:
    row["source_api"] = result.source_api
    row["raw_response"] = json.dumps(result.raw_payload, default=str)
    row["n_quotes"] = len(result.quotes)

    if not result.quotes:
        # Expected for a demand-driven cache on a thin route/date. Recorded as a
        # first-class observation so coverage gaps are visible in the panel.
        row["status"] = "no_data"
        return row

    sel = selection.select(result.quotes, target_time=target_time)
    chosen = sel.ons_rule
    row["status"] = "ok"
    if chosen is not None:
        row["price_gbp"] = chosen.price
        row["currency_raw"] = chosen.currency
        row["selected_airline"] = chosen.airline
        row["selected_departure_ts"] = chosen.departure_at
        row["quote_found_at"] = chosen.found_at
    if sel.cheapest is not None:
        row["price_cheapest_gbp"] = sel.cheapest.price
    row["ons_rule_time_delta_minutes"] = sel.ons_rule_time_delta_minutes
    row["n_quotes_considered"] = sel.n_considered
    row["candidate_basis"] = sel.candidate_basis
    row["selection_margin_minutes"] = sel.selection_margin_minutes
    return row


def run_pull(
    config: Config,
    *,
    scrape_date: dt.date | None = None,
    writer: bq.Writer | None = None,
    provider: FareProvider | None = None,
    routes: tuple[Route, ...] | None = None,
    backoff: float = RETRY_BACKOFF_SECONDS,
) -> dict[str, Any]:
    """Execute one full collection run. Returns a summary dict."""
    scrape_date = scrape_date or dt.date.today()
    scrape_ts = dt.datetime.now(dt.timezone.utc)
    run_id = uuid.uuid4().hex
    ordinal = choose_ordinal(scrape_date)
    routes = routes if routes is not None else panel.PANEL

    if provider is None:
        # Fail before issuing any queries rather than after 40 failures.
        config.require_provider_credential()
        kwargs: dict[str, Any] = {}
        if config.provider_name == "travelpayouts":
            kwargs = {"token": config.provider_credential, "market": config.market}
        elif config.provider_name == "serpapi":
            kwargs = {"api_key": config.provider_credential, "market": config.market}
        elif config.provider_name == "mock":
            kwargs = {"haul_of": {r.code: r.haul for r in routes}}
        provider = build_provider(config.provider_name, **kwargs)

    writer = writer or bq.build_writer(config)

    # Synthetic fares must never reach a real table. `airfare_scrapes` is
    # append-only by design, so a mock run against production leaves rows that
    # are not supposed to be deletable -- and they would silently corrupt every
    # index built afterwards. Easy to do by accident while setting up, hence the
    # explicit gate rather than a comment in the docs.
    if getattr(provider, "name", "") == "mock" and not config.dry_run:
        import os

        if os.environ.get("ALLOW_MOCK_WRITES", "").strip().lower() not in {"1", "true", "yes"}:
            raise ConfigError(
                "refusing to write mock (synthetic) fares to "
                f"{config.scrapes_ref}. Set DRY_RUN=1 to test without writing, "
                "or ALLOW_MOCK_WRITES=1 if you genuinely intend to seed a "
                "throwaway dataset with fake data."
            )
        log.warning("ALLOW_MOCK_WRITES set -- writing SYNTHETIC fares to %s", config.scrapes_ref)

    log.info(
        "run %s | scrape_date=%s | index-day hypothesis: %s Tuesday | %d routes | provider=%s",
        run_id,
        scrape_date,
        {2: "2nd", 3: "3rd"}[ordinal],
        len(routes),
        provider.name,
    )

    rows: list[dict[str, Any]] = []
    for route in routes:
        for leg in legs_for(scrape_date, route.haul, ordinal):
            rows.append(
                collect_one(
                    provider,
                    route,
                    leg,
                    run_id=run_id,
                    scrape_ts=scrape_ts,
                    scrape_date=scrape_date,
                    currency=config.currency,
                    # Per haul, not one constant: see TARGET_DEPARTURE_TIME_BY_HAUL.
                    # An explicit TARGET_DEPARTURE_TIME in the environment still
                    # overrides for every haul, which is what makes the
                    # one-time-versus-several question testable.
                    target_time=target_departure_time_for(
                        route.haul, config.target_departure_time_override
                    ),
                    backoff=backoff,
                )
            )

    counts = {"ok": 0, "no_data": 0, "error": 0}
    for row in rows:
        counts[row.get("status", "error")] = counts.get(row.get("status", "error"), 0) + 1

    total = len(rows)
    failure_rate = counts["error"] / total if total else 1.0

    # Every row is written, including failures -- the panel should record that we
    # tried and could not get a price, which is itself information.
    written = writer.append(config.scrapes_ref if not config.dry_run else "airfare_scrapes", rows)

    summary = {
        "run_id": run_id,
        "scrape_date": scrape_date.isoformat(),
        "index_day_ordinal": ordinal,
        "provider": provider.name,
        "rows": total,
        "written": written,
        "ok": counts["ok"],
        "no_data": counts["no_data"],
        "error": counts["error"],
        "failure_rate": round(failure_rate, 4),
        "in_collection_window": in_collection_window(scrape_date),
    }
    log.info("summary: %s", json.dumps(summary))

    if counts["error"]:
        _gha_notice(
            "warning",
            f"{counts['error']}/{total} queries failed on {scrape_date} "
            f"(rate {failure_rate:.0%})",
        )
    if counts["no_data"]:
        _gha_notice(
            "notice",
            f"{counts['no_data']}/{total} queries returned no fares on {scrape_date}. "
            "Expected on a cache-backed provider for thin route/date combinations.",
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect one vintage of air fare observations.")
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
        "--haul",
        choices=("domestic", "european", "long_haul"),
        default=None,
        help="Restrict to one haul category (useful for debugging).",
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

    routes = panel.routes_by_haul(args.haul) if args.haul else None
    writer = bq.build_writer(config, args.dry_run_out) if config.dry_run else None

    try:
        summary = run_pull(config, scrape_date=args.scrape_date, writer=writer, routes=routes)
    except Exception as exc:  # noqa: BLE001
        _gha_notice("error", f"pull run failed: {exc}")
        log.exception("pull run failed")
        return 1

    if summary["rows"] == 0:
        _gha_notice("error", "no queries were attempted -- panel is empty?")
        return 1

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
