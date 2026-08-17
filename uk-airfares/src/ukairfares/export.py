"""Export analytics-ready JSON from BigQuery into the repository.

WHY THIS EXISTS

BigQuery cannot be queried from outside a GitHub Actions run. Service-account
JSON keys are blocked by the `iam.disableServiceAccountKeyCreation` org policy
(the right policy -- it is Google's secure default and should not be weakened for
convenience), and the Workload Identity Federation path that replaced them mints
a short-lived token from GitHub's OIDC provider, which only exists inside a
running workflow. The network reaches `bigquery.googleapis.com` fine; the
credential is the wall, and it is deliberate.

So the data comes out the same way it went in: through a workflow. This module
runs where the credential already exists, and commits the result as JSON that
anything can read -- a notebook, a dashboard, an assistant with no cloud access.

WHAT IS EXPORTED, AND WHAT IS NOT

Aggregates only, never raw observation rows. Two reasons:

  1. **Size.** The panel grows by ~44 rows a day forever and carries a
     `raw_response` blob per row. Committing that to git would be a slow-motion
     mistake -- git keeps every version of everything, so an export that doubles
     in size each year makes every future clone pay for it. Per-day and
     per-series aggregates stay in the low hundreds of kilobytes over years.
  2. **`airfare_scrapes` stays the single source of truth.** An export that
     could be mistaken for the panel would eventually be treated as the panel.
     This is a derived view for reading, and `schema_version` plus `generated_ts`
     are here so a stale copy is recognisable as one.

Panel queries read `current_scrapes`, not `airfare_scrapes`, so superseded runs
are excluded exactly as they are in reconciliation. A number here and the same
number in the digest come from the same rows by construction.

Every query is independent and failure-tolerant: a section that cannot be read
becomes an `errors` entry and the rest of the export still lands. A partial
export is useful; a failed one leaves you with nothing to look at.
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

log = logging.getLogger("ukairfares.export")

#: Bump when the shape changes in a way a consumer would need to notice.
SCHEMA_VERSION = 1

DEFAULT_OUT = pathlib.Path("reports/data/analytics.json")

#: Sections exported as a single object rather than a list of rows.
SINGLE_ROW_SECTIONS = frozenset({"coverage"})

# --- The ONS published series: the richest thing in the warehouse today -------
# 678 values, 113 months, six series, and it does not depend on collection
# having run. This is what makes a dashboard worth looking at before the panel
# has any depth.
PUBLISHED_SERIES = """
SELECT index_month, haul_category, months_ahead,
       CAST(index_value AS FLOAT64) AS index_value, basis
FROM `{published}`
WHERE is_current AND index_value IS NOT NULL
ORDER BY index_month, haul_category, months_ahead
"""

# --- Collection: one row per day, per series ---------------------------------
DAILY_BY_SERIES = """
SELECT
  scrape_date, haul_category, months_ahead,
  COUNTIF(status = 'ok')      AS ok,
  COUNTIF(status = 'no_data') AS no_data,
  COUNTIF(status = 'error')   AS errors,
  CAST(ROUND(AVG(price_gbp), 2) AS FLOAT64)           AS mean_price_gbp,
  CAST(ROUND(APPROX_QUANTILES(price_gbp, 2)[OFFSET(1)], 2) AS FLOAT64)
                                                      AS median_price_gbp,
  CAST(ROUND(EXP(AVG(IF(price_gbp > 0, LN(price_gbp), NULL))), 2) AS FLOAT64)
                                                      AS geomean_price_gbp,
  CAST(ROUND(AVG(price_cheapest_gbp), 2) AS FLOAT64)  AS mean_cheapest_gbp,
  ROUND(AVG(ons_rule_time_delta_minutes))             AS mean_mins_off_target,
  ROUND(AVG(n_quotes), 1)                             AS mean_quotes,
  ROUND(AVG(n_quotes_considered), 1)                  AS mean_considered
FROM `{view}`
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
"""

# --- Per-route latest snapshot ------------------------------------------------
# Restricted to the most recent collection date so this stays a fixed ~44 rows
# rather than growing with history.
LATEST_ROUTES = """
WITH latest AS (SELECT MAX(scrape_date) AS d FROM `{view}`)
SELECT
  scrape_date, route, haul_category, months_ahead,
  departure_date, return_date, days_out_actual, status,
  CAST(price_gbp AS FLOAT64)          AS price_gbp,
  CAST(price_cheapest_gbp AS FLOAT64) AS price_cheapest_gbp,
  selected_airline, selected_departure_ts, target_departure_time,
  ons_rule_time_delta_minutes,
  n_quotes, n_quotes_considered, candidate_basis, error_message
FROM `{view}`
WHERE scrape_date = (SELECT d FROM latest)
ORDER BY haul_category, route, months_ahead
"""

RECONSTRUCTIONS = """
SELECT index_month, haul_category, months_ahead,
       attribution_rule, selection_rule, agg_method,
       CAST(reconstructed_value AS FLOAT64)  AS reconstructed_value,
       CAST(published_ons_value AS FLOAT64)  AS published_ons_value,
       n_observations, index_day_exact, index_day_offset_days,
       weights_are_placeholder
FROM `{index_table}`
WHERE is_current
ORDER BY index_month, haul_category, months_ahead
"""

COVERAGE = """
SELECT
  (SELECT COUNT(*) FROM `{view}`)                            AS panel_rows,
  (SELECT COUNT(DISTINCT scrape_date) FROM `{view}`)         AS panel_days,
  (SELECT MIN(scrape_date) FROM `{view}`)                    AS panel_first_day,
  (SELECT MAX(scrape_date) FROM `{view}`)                    AS panel_last_day,
  (SELECT COUNT(*) FROM `{published}` WHERE is_current)      AS published_values,
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


def build_export(reader, config: Config, *, generated: dt.datetime | None = None) -> dict[str, Any]:
    """Assemble the export. Never raises for a query failure."""
    generated = generated or dt.datetime.now(dt.timezone.utc)
    view = config.table_ref("current_scrapes")
    published = config.table_ref("ons_published_index")

    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "generated_ts": generated.isoformat(),
        "project": config.project,
        "dataset": config.dataset,
        "errors": {},
    }

    sections: dict[str, Callable[[], Any]] = {
        "coverage": lambda: _rows(reader, COVERAGE.format(view=view, published=published)),
        "published_series": lambda: _rows(reader, PUBLISHED_SERIES.format(published=published)),
        "daily_by_series": lambda: _rows(reader, DAILY_BY_SERIES.format(view=view)),
        "latest_routes": lambda: _rows(reader, LATEST_ROUTES.format(view=view)),
        "reconstructions": lambda: _rows(reader, RECONSTRUCTIONS.format(
            index_table=config.index_ref)),
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
            # Empty of the *same type* the success path would produce. The first
            # live export returned `[]` for coverage on failure and `{}` on
            # success, so a consumer doing `coverage["panel_rows"]` hit
            # TypeError instead of KeyError -- a failure mode that looks like a
            # code bug rather than missing data.
            out[name] = {} if name in SINGLE_ROW_SECTIONS else []
            continue
        # `coverage` is a single-row summary; unwrap it so consumers do not have
        # to index into a one-element list.
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
        print(f"::error::configuration error: {exc}", file=sys.stderr, flush=True)
        return 2

    try:
        path = run_export(config, out_path=args.out)
    except Exception as exc:  # noqa: BLE001
        print(f"::error::export failed: {exc}", file=sys.stderr, flush=True)
        log.exception("export failed")
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    if data["errors"]:
        for name, err in data["errors"].items():
            print(f"::warning::export section '{name}' failed: {err}", file=sys.stderr, flush=True)

    print(str(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
