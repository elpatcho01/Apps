"""Monthly digest: the thing you read when you come back.

Two jobs, and the second is not incidental.

**Something to read.** Once a month, summarise what was collected, how healthy
it looked, what was reconstructed, and whether anything needs attention. Written
as Markdown so it renders in the repo without tooling.

**Keeping the schedules alive.** GitHub disables scheduled workflows after 60
days of repository inactivity, and *workflow runs do not count as activity* --
only commits do. So the failure mode is specifically the success case: a
pipeline that collects perfectly for two months, needs no attention and
therefore receives no commits, gets switched off around day 60 with one email
that is easy to miss. The gap is unrecoverable, because a rate quoted six weeks
before a night that has passed cannot be re-collected. Committing this report is
a real commit on a monthly cadence, so the counter never gets close.

EVERY SECTION DEGRADES INDEPENDENTLY
------------------------------------
A query that fails or returns nothing produces a note saying so rather than
aborting the report. A digest that says "no reconstructions yet" is useful; a
digest that failed to generate is not.

And a failed query is itself recorded as a concern. That is not a nicety: the
first real digest on the sibling project printed "Nothing flagged. Collection
healthy." underneath two sections reading "unavailable: NotFound". The health
checks sat inside the success branch, so a failed query meant nothing was ever
checked. A report that cannot see the data must never conclude the data is fine
-- an unanswered question is itself a concern.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import pathlib
import sys
from typing import Any, Callable

from . import bq
from .config import Config, ConfigError, PIPELINE_VERSION
from .onscal import add_months, collection_days_for_index_month

log = logging.getLogger("ukhotels.digest")

REPORTS_DIR = pathlib.Path("reports")

COLLECTION_HEALTH = """
SELECT
  COUNT(DISTINCT scrape_date) AS days_collected,
  COUNT(DISTINCT run_id)      AS runs,
  COUNT(DISTINCT check_in)    AS stay_nights,
  COUNTIF(status = 'ok')      AS ok,
  COUNTIF(status = 'no_data') AS no_data,
  COUNTIF(status = 'error')   AS errors,
  MIN(scrape_date)            AS first_day,
  MAX(scrape_date)            AS last_day
FROM `{view}`
WHERE scrape_date BETWEEN @start AND @end
"""

BY_REGION = """
SELECT
  location, property_tier,
  COUNTIF(status = 'ok')                   AS ok,
  COUNT(DISTINCT property_token)           AS properties,
  ROUND(AVG(price_gbp))                    AS avg_rate_gbp,
  ROUND(AVG(price_cheapest_gbp))           AS avg_cheapest_gbp,
  ROUND(AVG(SAFE_DIVIDE(price_gbp - price_cheapest_gbp, price_cheapest_gbp)) * 100, 1)
                                           AS pct_above_cheapest,
  ROUND(AVG(cell_price_spread_ratio), 2)   AS spread_ratio,
  ROUND(AVG(n_quotes), 1)                  AS returned,
  ROUND(AVG(n_considered), 1)              AS considered,
  ROUND(AVG(n_dropped_rate_basis), 1)      AS dropped_rate_basis
FROM `{view}`
WHERE scrape_date BETWEEN @start AND @end AND status = 'ok'
GROUP BY 1, 2
ORDER BY 1, 2
"""

PROBLEM_CELLS = """
SELECT location, check_in, stay_night_kind, status, ANY_VALUE(error_message) AS message
FROM `{view}`
WHERE scrape_date BETWEEN @start AND @end AND status != 'ok'
GROUP BY 1, 2, 3, 4
ORDER BY location, check_in
LIMIT 15
"""

CHURN = """
SELECT churn_status, COUNT(*) AS properties,
       ROUND(AVG(presence_rate), 2) AS avg_presence_rate
FROM `{churn_view}`
GROUP BY 1
ORDER BY 1
"""

CHURNED_PANEL = """
SELECT location, property_tier, property_name, last_month, months_absent
FROM `{churn_view}`
WHERE churn_status = 'left' AND is_panel_property
ORDER BY months_absent DESC, location
LIMIT 10
"""

RECONSTRUCTIONS = """
SELECT index_month, location, property_tier, stay_night_kind,
       reconstructed_value, published_ons_value,
       n_observations, n_properties, index_day_exact, index_day_offset_days
FROM `{index_table}`
WHERE is_current
  AND attribution_rule     = 'stay_month'
  AND collection_alignment = 'per_night'
  AND sample_rule          = 'matched_census'
  AND agg_method           = 'geometric_mean'
  AND property_tier        = 'all'
  AND stay_night_kind      = 'both'
ORDER BY index_month DESC, location
LIMIT 30
"""

PUBLISHED_COVERAGE = """
SELECT series_source,
       COUNT(*) AS values_,
       COUNT(DISTINCT index_month) AS months,
       MIN(index_month) AS first_month,
       MAX(index_month) AS last_month,
       COUNT(DISTINCT location) AS locations,
       STRING_AGG(DISTINCT basis) AS bases
FROM `{published_table}`
WHERE is_current
GROUP BY 1
ORDER BY 1
"""


def _safe(
    fn: Callable[[], Any], label: str, concerns: list[str] | None = None
) -> tuple[Any, str | None]:
    """Run a query, converting failure into a note rather than an abort.

    The failure is also recorded as a concern -- see the module docstring for
    why that matters more than it looks like it should.
    """
    try:
        return fn(), None
    except Exception as exc:  # noqa: BLE001 - a digest must always be produced
        log.warning("%s failed: %s", label, exc)
        if concerns is not None:
            hint = ""
            if type(exc).__name__ == "NotFound":
                hint = (
                    " The table or view is missing — `ensure_tables` runs at the start "
                    "of every collection run, so this clears itself on the next one."
                )
            concerns.append(f"Could not read {label} ({type(exc).__name__}).{hint}")
        return None, f"_{label} unavailable: {type(exc).__name__}_"


def _table(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    if not rows:
        return ["_No rows._"]
    out = ["| " + " | ".join(columns) + " |",
           "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        out.append("| " + " | ".join(
            "—" if row.get(c) is None else str(row.get(c)) for c in columns
        ) + " |")
    return out


def build_digest(
    reader,
    config: Config,
    *,
    period_start: dt.date,
    period_end: dt.date,
    generated: dt.datetime | None = None,
) -> str:
    """Render the monthly digest as Markdown."""
    generated = generated or dt.datetime.now(dt.timezone.utc)
    view = config.table_ref("current_scrapes")
    churn_view = config.table_ref("property_churn")
    params = {"start": period_start, "end": period_end}
    lines: list[str] = []
    concerns: list[str] = []

    # How many collection days SHOULD have run in this period. Because
    # collection happens six weeks ahead, a calendar month contains collection
    # days for parts of two different CPI months, so this is computed rather
    # than assumed to be a fixed number.
    expected_days = sorted(
        d
        for month_offset in (1, 2, 3)
        for d in collection_days_for_index_month(
            add_months(period_start, month_offset), advance_days=config.advance_days
        )
        if period_start <= d <= period_end
    )

    lines += [
        f"# Accommodation digest — {period_start:%B %Y}",
        "",
        f"Collection window `{period_start}` to `{period_end}`. "
        f"Generated {generated:%Y-%m-%d %H:%M} UTC by pipeline {PIPELINE_VERSION}.",
        "",
        "> Collection dates in this window price stay nights roughly six weeks "
        "later, so this report covers the *collection* month, not the CPI month "
        "those rates will be attributed to.",
        "",
    ]

    # --- Collection health -------------------------------------------------
    health, err = _safe(
        lambda: reader.query(COLLECTION_HEALTH.format(view=view), params),
        "collection health", concerns,
    )
    lines += ["## Collection", ""]
    if err:
        lines += [err, ""]
    elif health and health[0].get("days_collected"):
        h = health[0]
        total = (h["ok"] or 0) + (h["no_data"] or 0) + (h["errors"] or 0)
        lines += [
            f"- **{h['days_collected']} of {len(expected_days)} expected collection days** "
            f"({h['first_day']} to {h['last_day']}), {h['runs']} runs",
            f"- **{h['stay_nights']} distinct stay nights** priced",
            f"- **{h['ok']} ok**, {h['no_data']} no-data, {h['errors']} errors "
            f"of {total} observations",
            "",
        ]
        if total and (h["errors"] or 0) / total > 0.05:
            concerns.append(
                f"Error rate {(h['errors'] / total):.0%} — check the Actions log."
            )
        if expected_days and (h["days_collected"] or 0) < len(expected_days):
            missing = sorted(set(expected_days))
            concerns.append(
                f"Only {h['days_collected']} of {len(expected_days)} expected collection "
                f"days ran ({', '.join(d.isoformat() for d in missing)}). A missed "
                "collection day is a stay night that can never be priced — the rate is "
                "gone, not merely late."
            )
    else:
        lines += ["_No observations in this period._", ""]
        concerns.append(
            "**No data collected this period.** Check that scheduled workflows are "
            "still enabled — GitHub disables them after 60 days without commits."
        )

    # --- Per-region detail -------------------------------------------------
    regions, err = _safe(
        lambda: reader.query(BY_REGION.format(view=view), params),
        "per-region breakdown", concerns,
    )
    lines += ["## By region and tier", ""]
    if err:
        lines += [err, ""]
    else:
        cols = ["location", "property_tier", "ok", "properties", "avg_rate_gbp",
                "avg_cheapest_gbp", "pct_above_cheapest", "spread_ratio",
                "returned", "considered", "dropped_rate_basis"]
        lines += _table(regions or [], cols) + [""]
        for row in regions or []:
            label = f"{row['location']}/{row['property_tier']}"
            # The diagnostic that caught the equivalent bug on the air-fares
            # panel. A comparable set should not span a 5x range after
            # filtering; if it does, the filter has stopped working.
            if (row.get("spread_ratio") or 0) > 4:
                concerns.append(
                    f"{label}: comparable set spans {row['spread_ratio']}x from cheapest "
                    "to dearest. The comparability filter may be letting a "
                    "non-comparable product through — check `comparability_basis` "
                    "on the raw rows."
                )
            if (row.get("properties") or 0) < config.min_properties_per_cell:
                concerns.append(
                    f"{label}: only {row['properties']} comparable properties. Below "
                    f"{config.min_properties_per_cell} a matched-sample relative is "
                    "noise, not a measurement."
                )
            if (row.get("dropped_rate_basis") or 0) > (row.get("considered") or 0):
                concerns.append(
                    f"{label}: more properties dropped for cancellation basis "
                    f"({row['dropped_rate_basis']}) than kept ({row['considered']}). "
                    f"RATE_BASIS={config.rate_basis} is thinning this cell severely."
                )

    # --- Problem cells -----------------------------------------------------
    problems, err = _safe(
        lambda: reader.query(PROBLEM_CELLS.format(view=view), params),
        "failed cells", concerns,
    )
    if problems:
        lines += ["### Cells that failed or returned nothing", "",
                  *_table(problems, ["location", "check_in", "stay_night_kind",
                                     "status", "message"]), ""]

    # --- Property churn ----------------------------------------------------
    lines += ["## Property churn", "",
              "_Properties leaving the sample is the normal monthly condition here, "
              "not an incident. Matched-sample relatives absorb it; a steadily "
              "thinning panel is what to watch for._", ""]
    churn, err = _safe(
        lambda: reader.query(CHURN.format(churn_view=churn_view)),
        "property churn", concerns,
    )
    if err:
        lines += [err, ""]
    else:
        lines += _table(churn or [], ["churn_status", "properties", "avg_presence_rate"]) + [""]

    gone, err = _safe(
        lambda: reader.query(CHURNED_PANEL.format(churn_view=churn_view)),
        "churned panel properties", concerns,
    )
    if gone:
        lines += ["### Pinned properties no longer appearing", "",
                  *_table(gone, ["location", "property_tier", "property_name",
                                 "last_month", "months_absent"]), ""]
        concerns.append(
            f"{len(gone)} pinned panel propert(ies) have stopped appearing. Run "
            "`python -m ukhotels.discover --substitute` to draw replacements; "
            "substitutions are recorded in property_panel.csv rather than made silently."
        )

    # --- Reconstructions ---------------------------------------------------
    recon, err = _safe(
        lambda: reader.query(RECONSTRUCTIONS.format(index_table=config.index_ref)),
        "reconstructions", concerns,
    )
    lines += ["## Reconstructions", "",
              "_Stay-month / per-night alignment / matched census / Jevons variant, "
              "tiers and nights pooled. Every other variant is in the table._", ""]
    if err:
        lines += [err, ""]
    else:
        lines += _table(recon or [], [
            "index_month", "location", "reconstructed_value", "published_ons_value",
            "n_observations", "n_properties", "index_day_offset_days",
        ]) + [""]
        if not recon:
            lines += ["_None yet — reconciliation runs once ONS confirm the index day._", ""]

    # --- Validation --------------------------------------------------------
    lines += ["## Validation", ""]
    try:
        from .validate import run_validate

        report = run_validate(config, reader=reader)
        lines += [
            f"**Verdict: {report['verdict']}** — {report['reason']}",
            "",
            f"Longest consecutive published run: {report['longest_consecutive_run']} "
            f"month(s); {report['min_required_months']} required.",
            "",
        ]
        for blocker in report.get("blockers", []):
            lines.append(f"- {blocker}")
        if report.get("blockers"):
            lines.append("")
    except Exception as exc:  # noqa: BLE001
        lines += [f"_Validation unavailable: {type(exc).__name__}_", ""]
        concerns.append(f"Validation could not run ({type(exc).__name__}).")

    # --- Published target --------------------------------------------------
    pub, err = _safe(
        lambda: reader.query(
            PUBLISHED_COVERAGE.format(
                published_table=config.table_ref("ons_published_index")
            )
        ),
        "published series", concerns,
    )
    lines += ["## ONS published values (the target)", ""]
    if err:
        lines += [err, ""]
    elif pub:
        lines += _table(pub, ["series_source", "values_", "months", "first_month",
                              "last_month", "locations", "bases"]) + [""]
        by_source = {r["series_source"]: r for r in pub}
        # The regional ad hoc release is the only source that matches what we
        # reconstruct. Its absence is worth stating plainly every month, because
        # it is the single fact gating every accuracy claim the project can
        # make, and it does not resolve through anything happening in this repo.
        if "adhoc_regional" not in by_source:
            concerns.append(
                "The regional ad hoc release is not loaded, so validation can only "
                "score against the national time series — which covers all of "
                "11.2.0.1 including items we do not replicate. Run the backfill "
                "workflow with --discover."
            )
        else:
            last = by_source["adhoc_regional"]["last_month"]
            if last and period_start > last:
                gap = (period_start.year - last.year) * 12 + (period_start.month - last.month)
                concerns.append(
                    f"Regional ad hoc release ends {last:%Y-%m}, {gap} month(s) before "
                    "this period, so there is no overlap to validate against yet. This "
                    "closes only when ONS publish a newer vintage; the monthly backfill "
                    "re-discovers releases automatically, so no action is needed here."
                )
    else:
        lines += ["_Not loaded — run the ONS backfill workflow._", ""]
        concerns.append("Published ONS values not loaded; validation cannot run.")

    # --- Standing limitations ----------------------------------------------
    # Deliberately restated every month rather than left to the README. They do
    # not resolve on their own, and a reader coming back after eight weeks needs
    # them next to the numbers, not one click away.
    lines += [
        "## Standing limitations",
        "",
        "- **Cancellation policy is uncontrolled.** `rate_basis=any` mixes "
        "refundable and non-refundable rates, which differ by 30-40% on an identical "
        "room. This is the largest single contamination in the series. The source "
        "does not expose the field (a census of 214 live properties found it on "
        "none), so it does not clear by changing anything here.",
        "- **Board basis and room type are unknown on every row.** No implemented "
        "provider reports them, so room-only and breakfast-inclusive rates are mixed "
        "together. A second bias of unknown sign.",
        f"- **Regional weights are placeholders** (population-proportional, not "
        f"expenditure). Every weighted aggregate carries `weights_are_placeholder`.",
        "- **The published series has two methodology breaks** (2025 and February "
        "2026). Comparisons never span them.",
        "",
    ]

    # --- Concerns, last so they are the thing you leave with ---------------
    lines += ["## Needs attention", ""]
    lines += ([f"- {c}" for c in concerns] if concerns
              else ["Nothing flagged. Collection healthy."])
    lines += [
        "",
        "---",
        "",
        "_Generated by `ukhotels.digest`. Committing this report is also what keeps "
        "the scheduled workflows alive: GitHub disables them after 60 days without "
        "repository commits, and workflow runs do not count._",
        "",
    ]
    return "\n".join(lines)


def run_digest(
    config: Config,
    *,
    month: dt.date | None = None,
    reader=None,
    out_dir: pathlib.Path | None = None,
) -> pathlib.Path:
    """Generate the digest for `month` and write it to reports/YYYY-MM.md."""
    month = (month or add_months(dt.date.today().replace(day=1), -1)).replace(day=1)
    period_end = add_months(month, 1) - dt.timedelta(days=1)
    reader = reader or bq.BigQueryWriter(config.project)

    text = build_digest(reader, config, period_start=month, period_end=period_end)

    out_dir = out_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{month:%Y-%m}.md"
    path.write_text(text, encoding="utf-8")
    log.info("wrote %s (%d bytes)", path, len(text))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the monthly accommodation digest."
    )
    parser.add_argument(
        "--month",
        type=lambda s: dt.datetime.strptime(s, "%Y-%m").date().replace(day=1),
        default=None,
        help="Month to report on, YYYY-MM. Defaults to last month.",
    )
    parser.add_argument("--out-dir", type=pathlib.Path, default=None)
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
        path = run_digest(config, month=args.month, out_dir=args.out_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"::error::digest failed: {exc}", file=sys.stderr, flush=True)
        log.exception("digest failed")
        return 1

    print(str(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
