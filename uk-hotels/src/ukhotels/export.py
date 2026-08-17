"""Export analytics-ready JSON from BigQuery into the repository.

WHY THIS EXISTS

BigQuery cannot be queried from outside a GitHub Actions run. Service-account
JSON keys are blocked by the `iam.disableServiceAccountKeyCreation` org policy
-- Google's secure default for new projects, and not worth weakening for
convenience -- and the Workload Identity Federation path that replaced them
mints a short-lived token from GitHub's OIDC provider, which only exists inside
a running job. The network reaches `bigquery.googleapis.com` fine; the
credential is the wall, and it is deliberate.

So the data comes out the same way it went in: through a workflow. This module
runs where the credential already exists and commits the result as JSON that
anything can read -- a notebook, a dashboard, an assistant with no cloud access.

WHAT IS EXPORTED, AND WHAT IS NOT

Aggregates only, never raw observation rows. Two reasons:

  1. **Size.** The panel grows by several hundred rows per collection day
     forever and carries a `raw_response` blob. Git keeps every version of
     everything, so an export that grew with the panel would make every future
     clone pay for it.
  2. **`accommodation_scrapes` stays the single source of truth.** An export
     that could be mistaken for the panel would eventually be treated as the
     panel. `schema_version` and `generated_ts` are here so a stale copy is
     recognisable as one.

Panel sections read `current_scrapes`, not `accommodation_scrapes`, so
superseded runs are excluded exactly as they are in reconciliation. A number
here and the same number in the digest come from the same rows by construction.

Every query is independent and failure-tolerant: a section that cannot be read
becomes an `errors` entry and the rest of the export still lands. A partial
export is useful; a failed one leaves you with nothing to look at. On failure a
section is set to an empty value **of the same type its success path produces**,
because a consumer indexing into a dict should get a KeyError rather than a
TypeError -- the latter reads like a code bug rather than like missing data.
"""

from __future__ import annotations

import argparse
import datetime as dt
import decimal
import json
import logging
import pathlib
import sys
from typing import Any, Callable

from . import bq
from .config import Config, ConfigError, PIPELINE_VERSION

log = logging.getLogger("ukhotels.export")

#: Bump when the shape changes in a way a consumer would need to notice.
SCHEMA_VERSION = 1

DEFAULT_OUT = pathlib.Path("reports/data/analytics.json")

#: Sections exported as a single object rather than a list of rows.
SINGLE_ROW_SECTIONS = frozenset({"coverage"})

PUBLISHED_SERIES = """
SELECT index_month, location, series_source, series_id, coicop_class,
       CAST(index_value AS FLOAT64) AS index_value, basis, methodology_era
FROM `{published}`
WHERE is_current AND index_value IS NOT NULL
ORDER BY series_source, index_month, location
"""

DAILY_BY_REGION = """
SELECT
  scrape_date, check_in, stay_night_kind, location, property_tier,
  COUNTIF(status = 'ok')      AS ok,
  COUNTIF(status = 'no_data') AS no_data,
  COUNTIF(status = 'error')   AS errors,
  COUNT(DISTINCT property_token) AS properties,
  CAST(ROUND(AVG(price_gbp), 2) AS FLOAT64)  AS mean_rate_gbp,
  CAST(ROUND(APPROX_QUANTILES(price_gbp, 2)[OFFSET(1)], 2) AS FLOAT64)
                                             AS median_rate_gbp,
  CAST(ROUND(EXP(AVG(IF(price_gbp > 0, LN(price_gbp), NULL))), 2) AS FLOAT64)
                                             AS geomean_rate_gbp,
  CAST(ROUND(AVG(price_cheapest_gbp), 2) AS FLOAT64) AS mean_cheapest_gbp,
  CAST(ROUND(AVG(price_before_taxes_gbp), 2) AS FLOAT64) AS mean_before_taxes_gbp,
  ROUND(AVG(cell_price_spread_ratio), 3)     AS mean_spread_ratio,
  ROUND(AVG(n_quotes), 1)                    AS mean_returned,
  ROUND(AVG(n_considered), 1)                AS mean_considered
FROM `{view}`
GROUP BY 1, 2, 3, 4, 5
ORDER BY 1, 2, 4, 5
"""

#: Per-property detail for the most recent collection date only, so this stays a
#: bounded few hundred rows rather than growing with history.
LATEST_PROPERTIES = """
WITH latest AS (SELECT MAX(scrape_date) AS d FROM `{view}`)
SELECT
  scrape_date, check_in, stay_night_kind, location, property_tier,
  property_token, property_name,
  CAST(hotel_class AS FLOAT64)            AS hotel_class,
  CAST(price_gbp AS FLOAT64)              AS price_gbp,
  CAST(price_before_taxes_gbp AS FLOAT64) AS price_before_taxes_gbp,
  free_cancellation, is_panel_property, comparability_basis, status, error_message
FROM `{view}`
WHERE scrape_date = (SELECT d FROM latest)
ORDER BY location, property_tier, property_name
"""

CHURN = """
SELECT location, property_tier, property_token, property_name,
       is_panel_property, first_month, last_month, months_seen,
       months_absent, churn_status,
       ROUND(presence_rate, 3) AS presence_rate
FROM `{churn_view}`
ORDER BY churn_status, location, property_name
"""

RECONSTRUCTIONS = """
SELECT index_month, location, property_tier, stay_night_kind,
       attribution_rule, collection_alignment, sample_rule, agg_method,
       CAST(reconstructed_value AS FLOAT64) AS reconstructed_value,
       CAST(published_ons_value AS FLOAT64) AS published_ons_value,
       n_observations, n_properties, n_properties_churned,
       index_day_exact, index_day_offset_days,
       weights_are_placeholder, board_basis_known, rate_basis, tax_basis
FROM `{index_table}`
WHERE is_current
ORDER BY index_month, location, property_tier
"""

COVERAGE = """
SELECT
  (SELECT COUNT(*) FROM `{view}`)                              AS panel_rows,
  (SELECT COUNT(DISTINCT scrape_date) FROM `{view}`)           AS panel_days,
  (SELECT COUNT(DISTINCT check_in) FROM `{view}`)              AS stay_nights,
  (SELECT COUNT(DISTINCT property_token) FROM `{view}`)        AS properties,
  (SELECT MIN(scrape_date) FROM `{view}`)                      AS panel_first_day,
  (SELECT MAX(scrape_date) FROM `{view}`)                      AS panel_last_day,
  (SELECT COUNT(*) FROM `{published}` WHERE is_current)        AS published_values,
  (SELECT MIN(index_month) FROM `{published}` WHERE is_current) AS published_first,
  (SELECT MAX(index_month) FROM `{published}` WHERE is_current) AS published_last
"""


def _jsonable(value: Any) -> Any:
    """Dates as ISO strings, Decimals as floats -- both are JSON-hostile."""
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, dt.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def _rows(reader, sql: str) -> list[dict[str, Any]]:
    return [{k: _jsonable(v) for k, v in row.items()} for row in reader.query(sql)]


def build_export(
    reader, config: Config, *, generated: dt.datetime | None = None
) -> dict[str, Any]:
    """Assemble the export. Never raises for a query failure."""
    generated = generated or dt.datetime.now(dt.timezone.utc)
    view = config.table_ref("current_scrapes")
    churn_view = config.table_ref("property_churn")
    published = config.table_ref("ons_published_index")

    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "generated_ts": generated.isoformat(),
        "project": config.project,
        "dataset": config.dataset,
        # The methodology settings the panel was collected under. Without these
        # the numbers below are uninterpretable: a series collected on
        # free-cancellation advertised rates is a different series from one
        # collected on non-refundable before-tax rates, and nothing else in the
        # file would say which this is.
        "methodology": {
            "rate_basis": config.rate_basis,
            "tax_basis": config.tax_basis,
            "collection_alignment": config.collection_alignment,
            "advance_days": config.advance_days,
            "adults": 2,
            "nights": 1,
            "board_basis": "unknown -- not reported by any implemented provider",
        },
        "errors": {},
    }

    sections: dict[str, Callable[[], Any]] = {
        "coverage": lambda: _rows(reader, COVERAGE.format(view=view, published=published)),
        "published_series": lambda: _rows(reader, PUBLISHED_SERIES.format(published=published)),
        "daily_by_region": lambda: _rows(reader, DAILY_BY_REGION.format(view=view)),
        "latest_properties": lambda: _rows(reader, LATEST_PROPERTIES.format(view=view)),
        "property_churn": lambda: _rows(reader, CHURN.format(churn_view=churn_view)),
        "reconstructions": lambda: _rows(
            reader, RECONSTRUCTIONS.format(index_table=config.index_ref)
        ),
    }

    for name, fn in sections.items():
        try:
            rows = fn()
        except Exception as exc:  # noqa: BLE001 - a partial export beats none
            log.warning("%s failed: %s", name, exc)
            # Keep the message to one line: BigQuery's NotFound text carries the
            # job id and location over several lines, which turns a JSON diff
            # into noise and reads badly in a report.
            out["errors"][name] = " ".join(f"{type(exc).__name__}: {exc}".split())
            out[name] = {} if name in SINGLE_ROW_SECTIONS else []
            continue
        out[name] = (rows[0] if rows else {}) if name in SINGLE_ROW_SECTIONS else rows
        log.info("%s: %d rows", name, len(rows))

    return out


def run_export(
    config: Config, *, reader=None, out_path: pathlib.Path | None = None
) -> pathlib.Path:
    reader = reader or bq.BigQueryWriter(config.project)
    data = build_export(reader, config)

    path = out_path or DEFAULT_OUT
    path.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys so a run that changes no data produces a byte-identical file and
    # therefore no commit -- key ordering must not manufacture a diff.
    path.write_text(
        json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log.info("wrote %s (%d bytes)", path, path.stat().st_size)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export analytics JSON from BigQuery.")
    parser.add_argument("--out", type=pathlib.Path, default=None)
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

    try:
        path = run_export(config, out_path=args.out)
    except Exception as exc:  # noqa: BLE001
        print(f"::error::export failed: {exc}", flush=True)
        log.exception("export failed")
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    if data["errors"]:
        for name, err in data["errors"].items():
            print(f"::warning::export section '{name}' failed: {err}", flush=True)

    print(str(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
