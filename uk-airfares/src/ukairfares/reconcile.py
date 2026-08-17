"""Monthly reconciliation.

Once ONS confirm the index day, pull the panel rows collected on that day and
aggregate them into a reconstructed sub-index per haul category.

Two things are computed in parallel rather than decided in advance, because
neither is settled by public documentation:

  * **Attribution.** Does a fare belong to the month it *departs* or the month
    it was *collected*? The brief asserts departure-month; standard CPI practice
    would suggest collection-month. Both are computed, tagged in
    `attribution_rule`, and left for validation to settle empirically against
    ONS's published sub-indices.
  * **Aggregation.** Mean, median and geometric mean are all computed. ONS use a
    Jevons (geometric mean) elementary aggregate for most CPI items, so the
    geometric mean is the most likely match, but the mean and median are cheap
    to carry and let us check.

If no scrape exists on the confirmed index day, the nearest available date is
substituted and the substitution is recorded (`index_day_exact`,
`index_day_offset_days`) rather than hidden -- fares drift materially over a few
days, so a reader needs to know the reconstruction is standing on a proxy date.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import statistics
import sys
import uuid
from decimal import Decimal
from typing import Any, Iterable

from . import bq, panel
from .config import Config, ConfigError, PIPELINE_VERSION
from .onscal import HaulCategory, add_months
from .onsfetch import BulletinNotPublished, IndexDayNotFound, IndexDayResult, fetch_index_day

log = logging.getLogger("ukairfares.reconcile")

HAULS: tuple[HaulCategory, ...] = ("domestic", "european", "long_haul")
ATTRIBUTION_RULES = ("departure_month", "collection_month")
SELECTION_RULES = ("ons_target_time", "cheapest")


def _gha_notice(level: str, message: str) -> None:
    print(f"::{level}::{message}", flush=True)


PANEL_QUERY = """
SELECT
  route,
  haul_category,
  scrape_date,
  days_out,
  months_ahead,
  departure_date,
  index_month_departure,
  index_month_collection,
  price_gbp,
  price_cheapest_gbp,
  is_cached_source,
  status
FROM `{table}`
WHERE scrape_date = @scrape_date
  AND status = 'ok'
  AND price_gbp IS NOT NULL
"""

NEAREST_DATE_QUERY = """
SELECT scrape_date, COUNT(*) AS n
FROM `{table}`
WHERE scrape_date BETWEEN @lo AND @hi
  AND status = 'ok'
GROUP BY scrape_date
ORDER BY ABS(DATE_DIFF(scrape_date, @target, DAY)), scrape_date
LIMIT 1
"""


def _geometric_mean(values: Iterable[float]) -> float | None:
    vals = [v for v in values if v > 0]
    if not vals:
        return None
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


def _quantise(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(f"{value:.4f}")


def resolve_scrape_date(
    reader, table: str, index_day: dt.date, *, window_days: int = 7
) -> tuple[dt.date | None, int]:
    """Find the usable scrape date closest to the confirmed index day."""
    rows = reader.query(
        NEAREST_DATE_QUERY.format(table=table),
        {
            "lo": index_day - dt.timedelta(days=window_days),
            "hi": index_day + dt.timedelta(days=window_days),
            "target": index_day,
        },
    )
    if not rows:
        return None, 0
    chosen = rows[0]["scrape_date"]
    if isinstance(chosen, dt.datetime):
        chosen = chosen.date()
    return chosen, (chosen - index_day).days


def aggregate(
    rows: list[dict[str, Any]],
    *,
    index_day: IndexDayResult,
    scrape_date_used: dt.date,
    offset_days: int,
    run_id: str,
    computed_ts: dt.datetime,
) -> list[dict[str, Any]]:
    """Build every (attribution x selection x aggregation) reconstruction row.

    Also emits a weighted `haul_category = "all"` row per combination, using the
    ONS sub-index weights.

    A caveat on that aggregate, stated here because it is easy to misread: it is
    a weight-weighted mean of the three haul *levels*, and the month-on-month
    change of a weighted level is not the same as the weighted mean of the three
    changes -- long-haul's much larger absolute fares dominate the former
    regardless of its weight. The statistically correct aggregate needs two
    months in hand, so it is computed in `validate.py`, which has them. This row
    is a convenience level, not the headline.
    """
    out: list[dict[str, Any]] = []
    expected = {h: len(panel.routes_by_haul(h)) for h in HAULS}

    # Weights are keyed by year and carried forward, matching how CPI weights
    # are set annually. Placeholders are permitted here but the flag propagates
    # to every row, so validation can refuse to treat the result as evidence.
    weights_cache: dict[int, panel.Weights | None] = {}

    def weights_for(year: int) -> panel.Weights | None:
        if year not in weights_cache:
            try:
                weights_cache[year] = panel.load_weights(year, allow_placeholder=True)
            except (FileNotFoundError, ValueError) as exc:
                log.warning("no weights for %d (%s); skipping weighted aggregate", year, exc)
                weights_cache[year] = None
        return weights_cache[year]

    for attribution in ATTRIBUTION_RULES:
        key = (
            "index_month_departure"
            if attribution == "departure_month"
            else "index_month_collection"
        )
        months = sorted({r[key] for r in rows if r.get(key)})
        for index_month in months:
            month_rows = [r for r in rows if r.get(key) == index_month]
            for haul in HAULS:
                for window in sorted(
                    {r["months_ahead"] for r in month_rows
                     if r["haul_category"] == haul and r.get("months_ahead")}
                ):
                    haul_rows = [
                        r for r in month_rows
                        if r["haul_category"] == haul and r["months_ahead"] == window
                    ]
                    if not haul_rows:
                        continue
                    for selection_rule in SELECTION_RULES:
                        price_col = (
                            "price_gbp" if selection_rule == "ons_target_time" else "price_cheapest_gbp"
                        )
                        prices = [
                            float(r[price_col]) for r in haul_rows if r.get(price_col) is not None
                        ]
                        if not prices:
                            continue

                        mean = statistics.fmean(prices)
                        median = statistics.median(prices)
                        geomean = _geometric_mean(prices)

                        for agg_method, value in (
                            ("mean", mean),
                            ("median", median),
                            ("geometric_mean", geomean),
                        ):
                            if value is None:
                                continue
                            out.append(
                                {
                                    "index_month": index_month,
                                        "haul_category": haul,
                                    "months_ahead": window,
                                    "confirmed_index_day": index_day.index_day,
                                    "reconstructed_value": _quantise(value),
                                    "n_observations": len(prices),
                                    "published_ons_value": None,
                                    "computed_ts": computed_ts,
                                    "attribution_rule": attribution,
                                    "selection_rule": selection_rule,
                                    "agg_method": agg_method,
                                    "mean_fare_gbp": _quantise(mean),
                                    "median_fare_gbp": _quantise(median),
                                    "geomean_fare_gbp": _quantise(geomean),
                                    "index_day_exact": offset_days == 0,
                                    "scrape_date_used": scrape_date_used,
                                    "index_day_offset_days": offset_days,
                                    "n_routes": len({r["route"] for r in haul_rows}),
                                    "n_expected_routes": expected[haul],
                                    "weights_are_placeholder": (
                                        w.is_placeholder
                                        if (w := weights_for(index_month.year))
                                        else None
                                    ),
                                    "source_is_cached": any(
                                        bool(r.get("is_cached_source")) for r in haul_rows
                                    ),
                                    "pipeline_version": PIPELINE_VERSION,
                                    "is_current": True,
                                    "run_id": run_id,
                                }
                            )

    out.extend(_weighted_aggregates(out, weights_for, run_id=run_id, computed_ts=computed_ts))
    return out


def _weighted_aggregates(
    haul_rows: list[dict[str, Any]],
    weights_for,
    *,
    run_id: str,
    computed_ts: dt.datetime,
) -> list[dict[str, Any]]:
    """Combine the three haul rows into a weighted `haul_category = "all"` row.

    Requires all three hauls to be present for a given combination -- a
    two-thirds aggregate weighted as if it were whole would be misleading, so
    incomplete combinations are skipped rather than partially weighted.

    In practice this means only the 1-month window produces an "all" row: it is
    the sole advance window ONS collect for every haul category. Domestic has no
    3- or 6-month series to weight against.
    """
    grouped: dict[tuple, dict[str, dict[str, Any]]] = {}
    for row in haul_rows:
        key = (
            row["index_month"],
            row.get("months_ahead"),
            row["attribution_rule"],
            row["selection_rule"],
            row["agg_method"],
        )
        grouped.setdefault(key, {})[row["haul_category"]] = row

    out: list[dict[str, Any]] = []
    for (index_month, window, attribution, selection_rule, agg_method), by_haul in grouped.items():
        if set(by_haul) != set(HAULS):
            log.debug(
                "skipping weighted aggregate for %s/%s: only %s present",
                index_month,
                agg_method,
                sorted(by_haul),
            )
            continue
        weights = weights_for(index_month.year)
        if weights is None:
            continue
        shares = weights.normalised()
        value = sum(
            float(by_haul[h]["reconstructed_value"]) * shares[h] for h in HAULS
        )
        template = by_haul["long_haul"]
        out.append(
            {
                **template,
                "haul_category": "all",
                "months_ahead": window,
                "reconstructed_value": _quantise(value),
                "n_observations": sum(by_haul[h]["n_observations"] for h in HAULS),
                "mean_fare_gbp": None,
                "median_fare_gbp": None,
                "geomean_fare_gbp": None,
                "n_routes": sum(by_haul[h]["n_routes"] for h in HAULS),
                "n_expected_routes": sum(by_haul[h]["n_expected_routes"] for h in HAULS),
                "weights_are_placeholder": weights.is_placeholder,
                "source_is_cached": any(by_haul[h]["source_is_cached"] for h in HAULS),
                "computed_ts": computed_ts,
                "run_id": run_id,
            }
        )
    return out


def run_reconcile(
    config: Config,
    *,
    index_month: dt.date,
    index_day_override: dt.date | None = None,
    reader=None,
    writer: bq.Writer | None = None,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex
    computed_ts = dt.datetime.now(dt.timezone.utc)

    if index_day_override is not None:
        from .onscal import index_day_ordinal

        ordinal = index_day_ordinal(index_day_override)
        if ordinal not in (2, 3):
            _gha_notice(
                "warning",
                f"--index-day {index_day_override} is not the 2nd or 3rd Tuesday of "
                f"{index_month:%B %Y}; proceeding because it was given explicitly",
            )
        index_day = IndexDayResult(
            index_month=index_month.replace(day=1),
            index_day=index_day_override,
            ordinal=ordinal or 0,
            source_url="",
            evidence="supplied via --index-day",
        )
    else:
        index_day = fetch_index_day(index_month)
        log.info("confirmed index day: %s (%s)", index_day.index_day, index_day.source_url)
        log.info("evidence: %s", index_day.evidence)

    if reader is None:
        reader = bq.BigQueryWriter(config.project)
    writer = writer or bq.build_writer(config)

    scrape_date_used, offset = resolve_scrape_date(reader, config.scrapes_ref, index_day.index_day)
    if scrape_date_used is None:
        raise RuntimeError(
            f"no usable scrapes within a week of {index_day.index_day}. "
            "Did the daily puller run during the collection window?"
        )
    if offset != 0:
        _gha_notice(
            "warning",
            f"no scrape on the confirmed index day {index_day.index_day}; "
            f"using {scrape_date_used} ({offset:+d} days). Fare drift will bias this month.",
        )

    rows = reader.query(
        PANEL_QUERY.format(table=config.scrapes_ref), {"scrape_date": scrape_date_used}
    )
    log.info("%d panel rows on %s", len(rows), scrape_date_used)
    if not rows:
        raise RuntimeError(f"no successful panel rows on {scrape_date_used}")

    out = aggregate(
        rows,
        index_day=index_day,
        scrape_date_used=scrape_date_used,
        offset_days=offset,
        run_id=run_id,
        computed_ts=computed_ts,
    )
    written = writer.append(
        config.index_ref if not config.dry_run else "reconstructed_index", out
    )

    summary = {
        "run_id": run_id,
        "index_month": index_month.strftime("%Y-%m-01"),
        "confirmed_index_day": index_day.index_day.isoformat(),
        "index_day_ordinal": index_day.ordinal,
        "scrape_date_used": scrape_date_used.isoformat(),
        "index_day_offset_days": offset,
        "panel_rows": len(rows),
        "reconstruction_rows": written,
        "source_url": index_day.source_url,
    }
    log.info("summary: %s", json.dumps(summary))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconstruct a month's air fare sub-indices.")
    parser.add_argument(
        "--index-month",
        type=lambda s: dt.datetime.strptime(s, "%Y-%m").date().replace(day=1),
        default=None,
        help="Month to reconstruct, YYYY-MM. Defaults to last month.",
    )
    parser.add_argument(
        "--index-day",
        type=dt.date.fromisoformat,
        default=None,
        help="Skip the bulletin fetch and use this confirmed index day (YYYY-MM-DD).",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    index_month = args.index_month or add_months(dt.date.today().replace(day=1), -1)

    try:
        config = Config.from_env()
    except ConfigError as exc:
        _gha_notice("error", f"configuration error: {exc}")
        return 2

    try:
        summary = run_reconcile(
            config, index_month=index_month, index_day_override=args.index_day
        )
    except BulletinNotPublished as exc:
        # Not an error: we are simply early. Exit clean so the scheduled run is
        # a no-op until ONS publish.
        _gha_notice("notice", str(exc))
        log.info("%s -- nothing to do yet", exc)
        return 0
    except IndexDayNotFound as exc:
        # This IS an error: the bulletin exists but our parser could not read it.
        # Silence here would mean quietly skipping a month forever.
        _gha_notice("error", f"could not parse index day: {exc}")
        log.error("%s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        _gha_notice("error", f"reconciliation failed: {exc}")
        log.exception("reconciliation failed")
        return 1

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
