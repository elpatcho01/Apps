"""Monthly reconciliation.

Once ONS confirm the index day, work out which collection dates should have
priced that month's nights, pull those rows, and aggregate them into a
reconstructed sub-index per region.

WHY THIS IS HARDER THAN THE AIR-FARES EQUIVALENT
-------------------------------------------------
There, the confirmed index day *is* the scrape date: find the rows collected
that day and aggregate. Here the confirmed index day is a stay night six weeks
after the collection that priced it, and the collection dates implied depend on
which alignment reading is correct. So confirming August's index day tells us to
go looking for rows collected in late June and early July, and to look in two
places if we are unsure which reading holds.

FIVE VARIANTS, ALL COMPUTED
---------------------------
Nothing about this item's methodology is settled enough to bake in, so every
combination is computed and tagged:

  attribution_rule      stay_month vs collection_month
  collection_alignment  per_night vs single_day
  sample_rule           pinned_panel vs matched_census
  agg_method            mean vs median vs geometric_mean
  stay_night_kind       index_week, thursday_after, and both pooled

Validation scores them side by side. That is a lot of rows for one month, and it
is still the right trade: a reconstruction that cannot say which reading of the
methodology produced it is not evidence of anything.

ABSENCE VERSUS FAILURE
----------------------
Two situations look identical from inside a failing reconciliation and mean
opposite things: "we had not started collecting yet" and "the collector broke".
`NoCollectionYet` separates them by checking the earliest collection date before
deciding, because six red runs in the first fortnight for an absence nobody can
fix is exactly how someone learns to ignore Actions email -- and this pipeline's
survival depends on those being read.
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
from .onscal import (
    COLLECTION_ALIGNMENTS,
    STAY_NIGHT_KINDS,
    add_months,
    collection_date_for,
    stay_nights,
)
from .onsfetch import (
    BulletinNotPublished,
    IndexDayNotFound,
    IndexDayResult,
    fetch_index_day,
)
from .panel import STAR_TIERS

log = logging.getLogger("ukhotels.reconcile")

ATTRIBUTION_RULES = ("stay_month", "collection_month")
SAMPLE_RULES = ("pinned_panel", "matched_census")
AGG_METHODS = ("mean", "median", "geometric_mean")

#: Rows for the collection dates that priced one month's nights, restricted to
#: the latest run per date.
#:
#: The panel is append-only and a date can legitimately carry several runs -- a
#: retry, a manual re-run, a double-clicked dispatch. Without the restriction
#: every property on that date is counted once per run: the mean barely moves
#: but n_observations inflates, which misrepresents coverage and lets a
#: duplicated day outvote a clean one.
PANEL_QUERY = """
WITH latest_run AS (
  SELECT scrape_date, run_id
  FROM (
    SELECT scrape_date, run_id,
           ROW_NUMBER() OVER (PARTITION BY scrape_date ORDER BY scrape_ts DESC) AS rn
    FROM (
      SELECT DISTINCT scrape_date, run_id, scrape_ts
      FROM `{table}`
      WHERE scrape_date IN UNNEST(@scrape_dates)
    )
  )
  WHERE rn = 1
)
SELECT
  s.scrape_date, s.location, s.property_token, s.property_name, s.property_tier,
  s.stay_night_kind, s.collection_alignment, s.check_in, s.index_day,
  s.index_month_stay, s.index_month_collection,
  s.price_gbp, s.price_before_taxes_gbp, s.price_cheapest_gbp,
  s.is_panel_property, s.is_cached_source, s.board_basis,
  s.rate_basis, s.tax_basis, s.status
FROM `{table}` AS s
JOIN latest_run AS l
  ON s.scrape_date = l.scrape_date AND s.run_id = l.run_id
WHERE s.status = 'ok'
  AND s.price_gbp IS NOT NULL
  AND s.index_day = @index_day
"""

#: The collection dates we actually hold, near the ones the confirmed index day
#: implies. Used only when an exact date is missing, so a substitution is
#: possible but always recorded.
NEARBY_DATES_QUERY = """
SELECT scrape_date, COUNT(*) AS n
FROM `{table}`
WHERE scrape_date BETWEEN @lo AND @hi
  AND status = 'ok'
GROUP BY scrape_date
ORDER BY scrape_date
"""

COLLECTION_START_QUERY = """
SELECT MIN(scrape_date) AS first_day
FROM `{table}`
WHERE status = 'ok'
"""


class NoCollectionYet(Exception):
    """Asked to reconstruct a month from before collection started.

    Kept out of the error path deliberately. Reconcile attempts the previous
    month daily across the middle of each month, so in the pipeline's first
    weeks it is repeatedly asked for a month whose collection dates predate the
    very first scrape. There is no data and there never will be -- you cannot go
    back and collect a rate quoted six weeks before a night that has already
    passed -- so failing is both useless and actively harmful.

    The distinction that matters:

      * collection dates predate the panel -> exit 0, notice   (expected)
      * panel covered them but no rows      -> exit 1, error    (collector broke)

    Collapsing those two would make a genuinely broken collector look identical
    to being new, and a month would be skipped in silence.
    """


def _gha_notice(level: str, message: str) -> None:
    print(f"::{level}::{message}", file=sys.stderr, flush=True)


def _geometric_mean(values: Iterable[float]) -> float | None:
    vals = [v for v in values if v > 0]
    if not vals:
        return None
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


def _quantise(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(f"{value:.4f}")


def implied_collection_dates(index_day: dt.date, *, advance_days: int = 42) -> list[dt.date]:
    """Every collection date that should have priced this month's nights.

    Both nights, both alignment readings. Three or four distinct dates -- the
    single-day alignment shares one date across both nights, so the set is
    smaller than nights x alignments.
    """
    dates: set[dt.date] = set()
    for _, night in stay_nights(index_day):
        for alignment in COLLECTION_ALIGNMENTS:
            dates.add(
                collection_date_for(
                    night,
                    index_day=index_day,
                    alignment=alignment,
                    advance_days=advance_days,
                )
            )
    return sorted(dates)


def resolve_collection_dates(
    reader,
    table: str,
    index_day: dt.date,
    *,
    advance_days: int = 42,
    window_days: int = 3,
) -> tuple[list[dt.date], int]:
    """Which collection dates we actually hold, and how far they are off.

    Returns the usable dates and the largest offset from an implied date. A
    non-zero offset means a run slipped and rate drift has crept in; it is
    recorded on every reconstruction row rather than silently absorbed.
    """
    implied = implied_collection_dates(index_day, advance_days=advance_days)
    rows = reader.query(
        NEARBY_DATES_QUERY.format(table=table),
        {
            "lo": min(implied) - dt.timedelta(days=window_days),
            "hi": max(implied) + dt.timedelta(days=window_days),
        },
    )
    held = sorted(
        r["scrape_date"].date() if isinstance(r["scrape_date"], dt.datetime)
        else r["scrape_date"]
        for r in rows
    )
    if not held:
        return [], 0

    exact = [d for d in held if d in implied]
    if exact:
        return exact, 0

    # Nothing on an implied date. Take the nearest held date to each implied one
    # rather than dropping the month: a rate from one day either side is a
    # degraded observation, an absent month is no observation at all, and the
    # offset makes the degradation visible.
    chosen: list[dt.date] = []
    worst = 0
    for target in implied:
        nearest = min(held, key=lambda d: (abs((d - target).days), d))
        chosen.append(nearest)
        worst = max(worst, abs((nearest - target).days))
    return sorted(set(chosen)), worst


def collection_start(reader, table: str) -> dt.date | None:
    rows = reader.query(COLLECTION_START_QUERY.format(table=table))
    if not rows:
        return None
    first = rows[0].get("first_day")
    if isinstance(first, dt.datetime):
        return first.date()
    return first


def _cell_key(row: dict[str, Any], night_scope: str, tier_scope: str) -> tuple:
    return (
        row["location"],
        row["property_tier"] if tier_scope == "per_tier" else "all",
        row["stay_night_kind"] if night_scope == "per_night_kind" else "both",
    )


def aggregate(
    rows: list[dict[str, Any]],
    *,
    index_day: IndexDayResult,
    scrape_dates_used: list[dt.date],
    offset_days: int,
    config: Config,
    run_id: str,
    computed_ts: dt.datetime,
    pinned_expected: dict[tuple[str, str], int] | None = None,
) -> list[dict[str, Any]]:
    """Build every reconstruction row: one per cell per methodology variant.

    Also emits a weight-weighted `location = "all"` row per variant.

    A caveat on that aggregate, stated here because it is easy to misread: it is
    a weighted mean of regional *levels*, and the month-on-month change of a
    weighted level is not the same as the weighted mean of the regional changes
    -- London's much higher absolute rates dominate the former regardless of its
    weight. The statistically correct aggregate needs two months in hand, so it
    lives in validation. This row is a convenience level, not the headline.
    """
    pinned_expected = pinned_expected or {}
    weights_cache: dict[int, panel.Weights | None] = {}

    def weights_for(year: int) -> panel.Weights | None:
        if year not in weights_cache:
            try:
                weights_cache[year] = panel.load_weights(year, allow_placeholder=True)
            except (FileNotFoundError, ValueError) as exc:
                log.warning("no weights for %d (%s); skipping weighted aggregate", year, exc)
                weights_cache[year] = None
        return weights_cache[year]

    out: list[dict[str, Any]] = []

    for attribution in ATTRIBUTION_RULES:
        month_key = (
            "index_month_stay" if attribution == "stay_month" else "index_month_collection"
        )
        for index_month in sorted({r[month_key] for r in rows if r.get(month_key)}):
            month_rows = [r for r in rows if r.get(month_key) == index_month]

            for alignment in COLLECTION_ALIGNMENTS:
                aligned = [
                    r for r in month_rows if r.get("collection_alignment") == alignment
                ]
                if not aligned:
                    continue

                for sample_rule in SAMPLE_RULES:
                    sampled = (
                        [r for r in aligned if r.get("is_panel_property")]
                        if sample_rule == "pinned_panel"
                        else aligned
                    )
                    if not sampled:
                        continue

                    # Two scopes on each of two dimensions: per-tier and pooled,
                    # per-night-kind and pooled. ONS publish one item covering
                    # both nights, but the nights are separate measurements and
                    # are worth reconstructing separately as well.
                    for night_scope in ("per_night_kind", "both"):
                        for tier_scope in ("per_tier", "all"):
                            cells: dict[tuple, list[dict[str, Any]]] = {}
                            for row in sampled:
                                cells.setdefault(
                                    _cell_key(row, night_scope, tier_scope), []
                                ).append(row)

                            for (location, tier, night_kind), cell_rows in cells.items():
                                rates = [
                                    float(r["price_gbp"])
                                    for r in cell_rows
                                    if r.get("price_gbp") is not None
                                ]
                                if not rates:
                                    continue

                                mean = statistics.fmean(rates)
                                median = statistics.median(rates)
                                geomean = _geometric_mean(rates)
                                tokens = {
                                    r["property_token"]
                                    for r in cell_rows
                                    if r.get("property_token")
                                }
                                expected = pinned_expected.get(
                                    (location, tier), len(tokens)
                                )

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
                                            "location": location,
                                            "property_tier": tier,
                                            "stay_night_kind": night_kind,
                                            "confirmed_index_day": index_day.index_day,
                                            "reconstructed_value": _quantise(value),
                                            "n_observations": len(rates),
                                            "n_properties": len(tokens),
                                            "published_ons_value": None,
                                            "computed_ts": computed_ts,
                                            "attribution_rule": attribution,
                                            "collection_alignment": alignment,
                                            "sample_rule": sample_rule,
                                            "agg_method": agg_method,
                                            "mean_rate_gbp": _quantise(mean),
                                            "median_rate_gbp": _quantise(median),
                                            "geomean_rate_gbp": _quantise(geomean),
                                            "index_day_exact": offset_days == 0,
                                            "scrape_dates_used": ",".join(
                                                d.isoformat() for d in scrape_dates_used
                                            ),
                                            "index_day_offset_days": offset_days,
                                            "n_properties_expected": expected,
                                            "n_properties_churned": max(
                                                expected - len(tokens), 0
                                            ),
                                            "weights_are_placeholder": (
                                                w.is_placeholder
                                                if (w := weights_for(index_month.year))
                                                else None
                                            ),
                                            "source_is_cached": any(
                                                bool(r.get("is_cached_source"))
                                                for r in cell_rows
                                            ),
                                            # False today for every row: no
                                            # implemented provider reports board
                                            # basis. Carried as data rather than
                                            # as a README footnote so the gap
                                            # travels with the numbers.
                                            "board_basis_known": all(
                                                r.get("board_basis") is not None
                                                for r in cell_rows
                                            ),
                                            "rate_basis": config.rate_basis,
                                            "tax_basis": config.tax_basis,
                                            "pipeline_version": PIPELINE_VERSION,
                                            "is_current": True,
                                            "run_id": run_id,
                                        }
                                    )

    out.extend(
        _weighted_aggregates(out, weights_for, run_id=run_id, computed_ts=computed_ts)
    )
    return out


def _weighted_aggregates(
    region_rows: list[dict[str, Any]],
    weights_for,
    *,
    run_id: str,
    computed_ts: dt.datetime,
) -> list[dict[str, Any]]:
    """Combine regional rows into a weight-weighted `location = "all"` row.

    Requires every weighted region to be present for a combination. A
    partial aggregate weighted as if it were whole would be misleading in a
    direction nobody could later detect, so incomplete combinations are skipped
    rather than partially weighted -- and skipping is visible as a missing row,
    where a partial weighting would not be.
    """
    grouped: dict[tuple, dict[str, dict[str, Any]]] = {}
    for row in region_rows:
        if row["location"] == "all":
            continue
        key = (
            row["index_month"],
            row["property_tier"],
            row["stay_night_kind"],
            row["attribution_rule"],
            row["collection_alignment"],
            row["sample_rule"],
            row["agg_method"],
        )
        grouped.setdefault(key, {})[row["location"]] = row

    out: list[dict[str, Any]] = []
    for key, by_region in grouped.items():
        index_month = key[0]
        weights = weights_for(index_month.year)
        if weights is None:
            continue
        shares = weights.normalised()
        missing = set(shares) - set(by_region)
        if missing:
            log.debug(
                "skipping weighted aggregate for %s: %d region(s) missing (%s)",
                key, len(missing), ", ".join(sorted(missing)[:4]),
            )
            continue

        total = sum(shares[r] for r in by_region if r in shares)
        if total <= 0:
            continue
        value = (
            sum(float(by_region[r]["reconstructed_value"]) * shares[r]
                for r in by_region if r in shares)
            / total
        )
        template = by_region[max(by_region)]
        out.append(
            {
                **template,
                "location": "all",
                "reconstructed_value": _quantise(value),
                "n_observations": sum(r["n_observations"] for r in by_region.values()),
                "n_properties": sum(r["n_properties"] for r in by_region.values()),
                "mean_rate_gbp": None,
                "median_rate_gbp": None,
                "geomean_rate_gbp": None,
                "n_properties_expected": sum(
                    r["n_properties_expected"] or 0 for r in by_region.values()
                ),
                "n_properties_churned": sum(
                    r["n_properties_churned"] or 0 for r in by_region.values()
                ),
                "weights_are_placeholder": weights.is_placeholder,
                "source_is_cached": any(
                    r["source_is_cached"] for r in by_region.values()
                ),
                "board_basis_known": all(
                    r["board_basis_known"] for r in by_region.values()
                ),
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

    implied = implied_collection_dates(index_day.index_day, advance_days=config.advance_days)
    log.info(
        "index day %s implies collection on %s",
        index_day.index_day, ", ".join(d.isoformat() for d in implied),
    )

    dates_used, offset = resolve_collection_dates(
        reader, config.scrapes_ref, index_day.index_day, advance_days=config.advance_days
    )
    if not dates_used:
        # Before calling this a failure, establish whether collection had even
        # started. See NoCollectionYet for why the two cases must not be merged.
        first_day = collection_start(reader, config.scrapes_ref)
        if first_day is None:
            raise NoCollectionYet(
                "the panel holds no successful scrapes at all, so "
                f"{index_day.index_month:%B %Y} cannot be reconstructed. Expected "
                "until the collector has run at least once."
            )
        if first_day > min(implied):
            raise NoCollectionYet(
                f"collection started {first_day}, after the {index_day.index_month:%B %Y} "
                f"nights would have been priced ({min(implied)} to {max(implied)}). "
                "This month predates the panel and always will -- a rate quoted six "
                "weeks before a night that has passed cannot be recovered."
            )
        raise RuntimeError(
            f"no usable scrapes near {implied[0]}..{implied[-1]}, though collection has "
            f"been running since {first_day}. Did the collector run on those dates? "
            "They are six weeks before the index month, not inside it."
        )
    if offset != 0:
        _gha_notice(
            "warning",
            f"no scrape on the implied collection dates for {index_day.index_day}; "
            f"using {', '.join(d.isoformat() for d in dates_used)} (up to {offset} days "
            "off). Rate drift will bias this month.",
        )

    rows = reader.query(
        PANEL_QUERY.format(table=config.scrapes_ref),
        {"scrape_dates": dates_used, "index_day": index_day.index_day},
    )
    log.info("%d panel rows across %d collection date(s)", len(rows), len(dates_used))
    if not rows:
        raise RuntimeError(
            f"no successful panel rows for index day {index_day.index_day} on "
            f"{', '.join(d.isoformat() for d in dates_used)}. The collector ran but "
            "priced a different index-day hypothesis, or every cell failed."
        )

    pinned = panel.load_property_panel()
    pinned_expected = {key: len(props) for key, props in pinned.items()}
    # Pooled-tier cells expect the union across tiers, so they are counted
    # separately rather than derived -- summing per-tier counts would double the
    # expectation for a cell that pools them.
    for (location, _tier), props in pinned.items():
        pinned_expected[(location, "all")] = pinned_expected.get(
            (location, "all"), 0
        ) + len(props)

    out = aggregate(
        rows,
        index_day=index_day,
        scrape_dates_used=dates_used,
        offset_days=offset,
        config=config,
        run_id=run_id,
        computed_ts=computed_ts,
        pinned_expected=pinned_expected,
    )
    written = writer.append(
        config.index_ref if not config.dry_run else "accommodation_reconstructed_index", out
    )

    summary = {
        "run_id": run_id,
        "index_month": index_month.strftime("%Y-%m-01"),
        "confirmed_index_day": index_day.index_day.isoformat(),
        "index_day_ordinal": index_day.ordinal,
        "implied_collection_dates": [d.isoformat() for d in implied],
        "collection_dates_used": [d.isoformat() for d in dates_used],
        "index_day_offset_days": offset,
        "panel_rows": len(rows),
        "reconstruction_rows": written,
        "distinct_properties": len(
            {r["property_token"] for r in rows if r.get("property_token")}
        ),
        "source_url": index_day.source_url,
    }
    log.info("summary: %s", json.dumps(summary))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconstruct a month's accommodation sub-indices."
    )
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
    except NoCollectionYet as exc:
        # Also not an error: the month is older than the panel. Exit clean so
        # the pipeline's first weeks are not a wall of red runs.
        _gha_notice("notice", f"nothing to reconstruct: {exc}")
        log.info("%s", exc)
        return 0
    except IndexDayNotFound as exc:
        # This IS an error: the bulletin exists but our parser could not read
        # it. Silence would mean quietly skipping a month forever.
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
