"""Scoring the reconstruction against ONS's published values.

Deliberately hard to get a favourable answer out of. The failure this module
exists to prevent is declaring victory on three months of data and a methodology
variant picked with hindsight.

GUARDS, IN THE ORDER THEY BITE
------------------------------
1.  **Minimum overlap, counted in published months that are actually
    consecutive.** Fewer than one full quarter and the verdict is
    INSUFFICIENT_DATA with no headline MAE. "A full quarter" means three
    consecutive *published* months, not three consecutive calendar months --
    published series have holes, and three months either side of a gap tell you
    nothing about a month-on-month relative.

2.  **Methodology breaks are not crossed.** This item was rebuilt twice in
    nineteen months: priced one day ahead before 2025, six weeks ahead from
    2025, and across two nights from February 2026. A relative spanning a break
    is a change of measurement, not a price movement. Overlap is therefore
    counted within a single era, and an overlap that only exists by straddling a
    break does not count at all.

3.  **Rolling origin.** Errors are reported as a rolling-origin sequence, each
    month scored on what was knowable before it. An in-sample average across all
    months would flatter the pipeline by letting later months inform earlier
    ones.

4.  **Variant selection is reported, not hidden.** Dozens of
    attribution/alignment/sample/aggregation combinations are computed.
    Whichever scores best was chosen *after* seeing the answers, so its MAE is
    optimistically biased. That is stated explicitly along with how many
    variants were in the running.

5.  **Provenance blockers.** Placeholder weights, a cache-backed source, a
    substituted collection date, or -- always, today -- an unknown board basis
    each downgrade the verdict regardless of how good the numbers look.

The output is a report, not a pass/fail gate. It is meant to be read.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import statistics
import sys
from typing import Any, Sequence

from . import bq
from .config import Config, ConfigError
from .index import methodology_era

log = logging.getLogger("ukhotels.validate")

#: One full quarter of *consecutive published* overlap before any headline
#: accuracy claim is allowed.
MIN_OVERLAP_MONTHS = 3

#: Our reconstructions joined to ONS's published series.
#:
#: The join is on (index_month, location) and is restricted to a single
#: `series_source`, because the three sources are on different bases and
#: different geographies. Joining across them would silently compare a regional
#: January-2025=100 sub-index with a national 2015=100 item index and produce a
#: number that looks fine and means nothing.
SCORE_QUERY = """
WITH latest_recon AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY index_month, location, property_tier, stay_night_kind,
                 attribution_rule, collection_alignment, sample_rule, agg_method
    ORDER BY computed_ts DESC
  ) AS rn
  FROM `{table}`
  WHERE reconstructed_value IS NOT NULL
),
latest_published AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY index_month, location, series_source, coicop_class
    ORDER BY fetched_ts DESC
  ) AS rn
  FROM `{published_table}`
  WHERE index_value IS NOT NULL
)
SELECT
  r.index_month, r.location, r.property_tier, r.stay_night_kind,
  r.attribution_rule, r.collection_alignment, r.sample_rule, r.agg_method,
  r.reconstructed_value,
  p.index_value AS published_ons_value,
  p.basis AS published_basis,
  p.series_source, p.coicop_class, p.methodology_era,
  r.n_observations, r.n_properties, r.n_properties_churned,
  r.index_day_exact, r.index_day_offset_days,
  r.weights_are_placeholder, r.source_is_cached, r.board_basis_known
FROM latest_recon r
JOIN latest_published p
  ON r.index_month = p.index_month
 AND r.location = p.location
WHERE r.rn = 1 AND p.rn = 1
  AND p.series_source = @series_source
ORDER BY r.index_month
"""


@dataclasses.dataclass(frozen=True, slots=True)
class VariantScore:
    location: str
    property_tier: str
    stay_night_kind: str
    attribution_rule: str
    collection_alignment: str
    sample_rule: str
    agg_method: str
    n_months: int
    n_pairs: int
    mae: float
    bias: float
    rolling_mae: float | None
    months: tuple[dt.date, ...]
    #: Mean absolute error of the spliced nowcast, in ONS index points -- the
    #: interpretable headline, because it is on the scale of the series being
    #: anticipated rather than on ours.
    splice_mae_index_points: float | None = None

    @property
    def key(self) -> str:
        return (
            f"{self.attribution_rule}/{self.collection_alignment}/"
            f"{self.sample_rule}/{self.agg_method}"
        )


def usable_pairs(months: Sequence[dt.date]) -> list[tuple[dt.date, dt.date]]:
    """Adjacent month pairs safe to compute a change across.

    Excludes non-consecutive months and pairs spanning a methodology break. Both
    exclusions are about the same thing: a "month-on-month change" that spans
    either is not a month-on-month change.
    """
    ordered = sorted(set(months))
    out: list[tuple[dt.date, dt.date]] = []
    for prev, month in zip(ordered, ordered[1:]):
        apart = (month.year - prev.year) * 12 + (month.month - prev.month)
        if apart != 1:
            continue
        if methodology_era(prev) != methodology_era(month):
            continue
        out.append((prev, month))
    return out


def longest_consecutive_run(months: Sequence[dt.date]) -> int:
    """Longest run of consecutive published months inside one methodology era.

    This, not the raw month count, is what gates an accuracy claim. Three months
    scattered across a gap and a methodology break support exactly zero usable
    month-on-month comparisons.
    """
    ordered = sorted(set(months))
    if not ordered:
        return 0
    best = run = 1
    for prev, month in zip(ordered, ordered[1:]):
        apart = (month.year - prev.year) * 12 + (month.month - prev.month)
        if apart == 1 and methodology_era(prev) == methodology_era(month):
            run += 1
            best = max(best, run)
        else:
            run = 1
    return best


def score_variant(rows: list[dict[str, Any]]) -> VariantScore | None:
    """MAE, bias and a rolling-origin MAE for one variant on one cell.

    Levels are compared as month-on-month percentage changes, not raw levels.
    Our reconstruction is a mean nightly rate in pounds; ONS publish an index.
    The levels are not comparable, but their *movements* are -- and movement is
    what a nowcast is for.
    """
    by_month = {r["index_month"]: r for r in rows}
    pairs = usable_pairs(list(by_month))
    if not pairs:
        return None

    errors: list[float] = []
    splice_errors: list[float] = []
    for prev, month in pairs:
        a, b = by_month[prev], by_month[month]
        prev_recon = float(a["reconstructed_value"])
        recon = float(b["reconstructed_value"])
        prev_pub = float(a["published_ons_value"])
        pub = float(b["published_ons_value"])
        if prev_recon <= 0 or prev_pub <= 0:
            continue
        errors.append((recon - prev_recon) / prev_recon * 100.0
                      - (pub - prev_pub) / prev_pub * 100.0)
        splice_errors.append(abs(prev_pub * (recon / prev_recon) - pub))

    if not errors:
        return None

    abs_errors = [abs(e) for e in errors]
    # Rolling origin: score pair k using only pairs before it. With a mean-rate
    # reconstruction there is no fitted parameter to re-estimate, so this
    # reduces to the expanding-window average of out-of-sample absolute error --
    # still the honest number to quote, and it differs from `mae` because early
    # months carry less information.
    rolling = [statistics.fmean(abs_errors[:k]) for k in range(1, len(abs_errors))]

    first = rows[0]
    return VariantScore(
        location=first["location"],
        property_tier=first.get("property_tier") or "all",
        stay_night_kind=first.get("stay_night_kind") or "both",
        attribution_rule=first["attribution_rule"],
        collection_alignment=first["collection_alignment"],
        sample_rule=first["sample_rule"],
        agg_method=first["agg_method"],
        n_months=len(by_month),
        n_pairs=len(errors),
        mae=statistics.fmean(abs_errors),
        bias=statistics.fmean(errors),
        rolling_mae=statistics.fmean(rolling) if rolling else None,
        months=tuple(sorted(by_month)),
        splice_mae_index_points=(
            statistics.fmean(splice_errors) if splice_errors else None
        ),
    )


def build_report(rows: list[dict[str, Any]], *, series_source: str) -> dict[str, Any]:
    if not rows:
        return {
            "verdict": "INSUFFICIENT_DATA",
            "reason": (
                f"No reconstructed months join to a published value from "
                f"'{series_source}'. Run the backfill, and check that the "
                "reconstruction's `location` values match the published ones -- "
                "the time series is national ('uk') and only the ad hoc release "
                "is regional."
            ),
            "series_source": series_source,
            "n_scored_months": 0,
            "longest_consecutive_run": 0,
            "min_required_months": MIN_OVERLAP_MONTHS,
            "blockers": [],
            "by_cell": {},
        }

    groups: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            row["location"],
            row.get("property_tier"),
            row.get("stay_night_kind"),
            row["attribution_rule"],
            row["collection_alignment"],
            row["sample_rule"],
            row["agg_method"],
        )
        groups.setdefault(key, []).append(row)

    scores = [s for s in (score_variant(g) for g in groups.values()) if s is not None]
    months = [r["index_month"] for r in rows]
    overlap = len(set(months))
    consecutive = longest_consecutive_run(months)

    blockers: list[str] = []
    if any(r.get("weights_are_placeholder") for r in rows):
        blockers.append(
            "Some rows were aggregated with PLACEHOLDER regional weights, not real ONS "
            "weights. Regional expenditure weights for this item are not published at "
            "the granularity needed, so this blocker may be permanent."
        )
    if any(r.get("source_is_cached") for r in rows):
        blockers.append(
            "Underlying rates came from a cache-backed provider. These are not "
            "collection-day quotes, so any agreement with ONS is weaker evidence "
            "than it appears."
        )
    n_inexact = sum(1 for r in rows if r.get("index_day_exact") is False)
    if n_inexact:
        blockers.append(
            f"{n_inexact} row(s) used a substitute collection date rather than the one "
            "the confirmed index day implies; rate drift is baked into their error."
        )
    if any(r.get("board_basis_known") is False for r in rows):
        blockers.append(
            "Board basis is UNKNOWN on every contributing row -- no implemented "
            "provider reports it. Room-only and breakfast-inclusive rates are mixed "
            "together in this series, and that is a real bias of unknown sign, not a "
            "presentational caveat."
        )
    eras = {r.get("methodology_era") for r in rows if r.get("methodology_era")}
    if len(eras) > 1:
        blockers.append(
            f"Published values span {len(eras)} methodology eras ({', '.join(sorted(eras))}). "
            "Cross-era comparisons are excluded from scoring, which reduces the usable "
            "overlap below the raw month count."
        )
    churned = sum(r.get("n_properties_churned") or 0 for r in rows)
    if churned:
        blockers.append(
            f"{churned} pinned property-month(s) went unpriced -- properties closed, "
            "rebranded, left the aggregator, or were fully booked. Matched-sample "
            "relatives absorb this, but a thinning sample is worth watching."
        )

    if consecutive < MIN_OVERLAP_MONTHS:
        verdict = "INSUFFICIENT_DATA"
        reason = (
            f"Longest run of consecutive published months within one methodology era "
            f"is {consecutive}; {MIN_OVERLAP_MONTHS} (one full quarter) required "
            f"before any accuracy claim. {overlap} month(s) overlap in total, but "
            "months either side of a gap or a methodology break support no "
            "month-on-month comparison."
        )
    elif blockers:
        verdict = "PROVISIONAL"
        reason = "Enough overlap to score, but provenance issues limit what it means."
    else:
        verdict = "SCORED"
        reason = f"{consecutive} consecutive published months with clean provenance."

    by_cell: dict[str, Any] = {}
    cells = {(s.location, s.property_tier, s.stay_night_kind) for s in scores}
    for location, tier, night in sorted(cells):
        cell_scores = sorted(
            (
                s for s in scores
                if (s.location, s.property_tier, s.stay_night_kind) == (location, tier, night)
            ),
            key=lambda s: s.mae,
        )
        best = cell_scores[0]
        by_cell[f"{location}/{tier}/{night}"] = {
            "n_variants_compared": len(cell_scores),
            "best_variant": best.key,
            "best_variant_mae_pp": round(best.mae, 4),
            "best_variant_bias_pp": round(best.bias, 4),
            "best_variant_rolling_mae_pp": (
                round(best.rolling_mae, 4) if best.rolling_mae is not None else None
            ),
            "best_variant_splice_mae_index_points": (
                round(best.splice_mae_index_points, 4)
                if best.splice_mae_index_points is not None
                else None
            ),
            "n_months": best.n_months,
            "n_pairs_scored": best.n_pairs,
            "selection_caveat": (
                f"This variant was chosen as best-of-{len(cell_scores)} AFTER seeing the "
                "published values, so its MAE is optimistically biased. Fix the variant "
                "on early months and re-score blind for an honest out-of-sample figure."
            ),
            "all_variants": [
                {
                    "variant": s.key,
                    "mae_pp": round(s.mae, 4),
                    "bias_pp": round(s.bias, 4),
                    "rolling_mae_pp": (
                        round(s.rolling_mae, 4) if s.rolling_mae is not None else None
                    ),
                    "n_pairs": s.n_pairs,
                }
                for s in cell_scores
            ],
        }

    return {
        "verdict": verdict,
        "reason": reason,
        "series_source": series_source,
        "n_scored_months": overlap,
        "longest_consecutive_run": consecutive,
        "min_required_months": MIN_OVERLAP_MONTHS,
        "blockers": blockers,
        "units": "percentage points of month-on-month change",
        "by_cell": by_cell,
    }


def run_validate(
    config: Config, *, reader=None, series_source: str = "adhoc_regional"
) -> dict[str, Any]:
    reader = reader or bq.BigQueryWriter(config.project)
    rows = reader.query(
        SCORE_QUERY.format(
            table=config.index_ref,
            published_table=config.table_ref("ons_published_index"),
        ),
        {"series_source": series_source},
    )
    return build_report(rows, series_source=series_source)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score reconstructed sub-indices against published ONS values."
    )
    parser.add_argument(
        "--series-source",
        default="adhoc_regional",
        choices=("adhoc_regional", "timeseries", "price_quotes"),
        help=(
            "Which published source to score against. The regional ad hoc release "
            "is the closest match to what we reconstruct; the national time series "
            "covers all of 11.2.0.1 including items we do not replicate, so "
            "agreement with it is weaker evidence."
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
        print(f"::error::configuration error: {exc}", file=sys.stderr, flush=True)
        return 2

    try:
        report = run_validate(config, series_source=args.series_source)
    except Exception as exc:  # noqa: BLE001
        print(f"::error::validation failed: {exc}", file=sys.stderr, flush=True)
        log.exception("validation failed")
        return 1

    print(json.dumps(report, indent=2, default=str))
    if report["verdict"] == "INSUFFICIENT_DATA":
        print(f"::notice::{report['reason']}", file=sys.stderr, flush=True)
    for blocker in report.get("blockers", []):
        print(f"::warning::{blocker}", file=sys.stderr, flush=True)
    # Always exit 0: "not enough data yet" is the expected state for months and
    # is not a pipeline failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())
