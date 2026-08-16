"""Scoring the reconstruction against ONS's published sub-indices.

Deliberately hard to get a favourable answer out of. The failure mode this
module exists to prevent is declaring victory on three months of data and a
methodology variant picked with hindsight.

Guards, in the order they bite:

1.  **Minimum overlap.** Fewer than one full quarter of months carrying a
    backfilled `published_ons_value` and the verdict is INSUFFICIENT_DATA. No
    MAE is reported as a headline, because with n=2 it means nothing.

2.  **Rolling origin.** Errors are reported as a rolling-origin sequence -- each
    month scored using only what was knowable before it -- rather than as a
    single in-sample average. Same discipline as the RPI Rent workbook. An
    in-sample fit across all months would flatter the pipeline by letting later
    months inform earlier ones.

3.  **Variant selection is reported, not hidden.** The pipeline computes several
    attribution/selection/aggregation combinations. Whichever scores best was
    chosen *after* seeing the answers, so its MAE is optimistically biased. That
    is stated explicitly, along with how many variants were in the running, and
    the honest out-of-sample number is the one where the variant is fixed on
    early data and applied blind to later months.

4.  **Provenance blockers.** Placeholder weights or a cache-backed fare source
    downgrade the verdict regardless of how good the numbers look.

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

log = logging.getLogger("ukairfares.validate")

#: One full quarter of overlap before any headline accuracy claim is allowed.
MIN_OVERLAP_MONTHS = 3

SCORE_QUERY = """
WITH latest AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY index_month, haul_category, attribution_rule, selection_rule, agg_method
    ORDER BY computed_ts DESC
  ) AS rn
  FROM `{table}`
  WHERE published_ons_value IS NOT NULL
)
SELECT
  index_month, haul_category, attribution_rule, selection_rule, agg_method,
  reconstructed_value, published_ons_value, n_observations,
  index_day_exact, index_day_offset_days,
  weights_are_placeholder, source_is_cached
FROM latest
WHERE rn = 1
ORDER BY index_month
"""


@dataclasses.dataclass(frozen=True, slots=True)
class VariantScore:
    haul_category: str
    attribution_rule: str
    selection_rule: str
    agg_method: str
    n_months: int
    mae: float
    bias: float
    mape: float
    rolling_mae: float | None
    months: tuple[dt.date, ...]

    @property
    def key(self) -> str:
        return f"{self.attribution_rule}/{self.selection_rule}/{self.agg_method}"


def _pct_change(series: Sequence[float]) -> list[float]:
    return [
        (b - a) / a * 100.0
        for a, b in zip(series, series[1:])
        if a not in (0, None)
    ]


def score_variant(rows: list[dict[str, Any]]) -> VariantScore | None:
    """MAE, bias and a rolling-origin MAE for one variant on one haul category.

    Levels are compared as month-on-month percentage changes, not raw levels.
    Our reconstruction is a mean fare in pounds; ONS publish an index on a
    Jan=100 basis. The levels are not comparable, but their *movements* are --
    and movement is what a nowcast is for.
    """
    rows = sorted(rows, key=lambda r: r["index_month"])
    if len(rows) < 2:
        return None

    recon = [float(r["reconstructed_value"]) for r in rows]
    published = [float(r["published_ons_value"]) for r in rows]

    recon_ch = _pct_change(recon)
    pub_ch = _pct_change(published)
    if not recon_ch or len(recon_ch) != len(pub_ch):
        return None

    errors = [r - p for r, p in zip(recon_ch, pub_ch)]
    abs_errors = [abs(e) for e in errors]
    mae = statistics.fmean(abs_errors)
    bias = statistics.fmean(errors)
    denom = [abs(p) for p in pub_ch]
    mape = (
        statistics.fmean([abs(e) / d * 100 for e, d in zip(errors, denom) if d > 0])
        if any(d > 0 for d in denom)
        else float("nan")
    )

    # Rolling origin: score month k using only months < k. With a mean-fare
    # reconstruction there is no fitted parameter to re-estimate, so this
    # reduces to the expanding-window average of out-of-sample absolute error --
    # which is still the honest number to quote, and differs from `mae` because
    # early months carry less information.
    rolling: list[float] = []
    for k in range(1, len(abs_errors)):
        rolling.append(statistics.fmean(abs_errors[:k]))
    rolling_mae = statistics.fmean(rolling) if rolling else None

    return VariantScore(
        haul_category=rows[0]["haul_category"],
        attribution_rule=rows[0]["attribution_rule"],
        selection_rule=rows[0]["selection_rule"],
        agg_method=rows[0]["agg_method"],
        n_months=len(rows),
        mae=mae,
        bias=bias,
        mape=mape,
        rolling_mae=rolling_mae,
        months=tuple(r["index_month"] for r in rows),
    )


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "verdict": "INSUFFICIENT_DATA",
            "reason": (
                "No reconstructed months carry a backfilled published_ons_value. "
                "Backfill from the ONS ad hoc release before scoring."
            ),
            "n_scored_months": 0,
        }

    groups: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            row["haul_category"],
            row["attribution_rule"],
            row["selection_rule"],
            row["agg_method"],
        )
        groups.setdefault(key, []).append(row)

    scores = [s for s in (score_variant(g) for g in groups.values()) if s is not None]
    overlap = len({r["index_month"] for r in rows})

    blockers: list[str] = []
    if any(r.get("weights_are_placeholder") for r in rows):
        blockers.append(
            "Some rows were aggregated with PLACEHOLDER weights, not real ONS weights."
        )
    if any(r.get("source_is_cached") for r in rows):
        blockers.append(
            "Underlying fares came from a cache-backed provider (Travelpayouts serves "
            "prices found by users in the preceding 48h). These are not index-day "
            "quotes, so any agreement with ONS is weaker evidence than it appears."
        )
    n_inexact = sum(1 for r in rows if r.get("index_day_exact") is False)
    if n_inexact:
        blockers.append(
            f"{n_inexact} row(s) used a substitute scrape date rather than the "
            "confirmed index day; fare drift is baked into their error."
        )

    if overlap < MIN_OVERLAP_MONTHS:
        verdict = "INSUFFICIENT_DATA"
        reason = (
            f"Only {overlap} month(s) of overlap; {MIN_OVERLAP_MONTHS} "
            "(one full quarter) required before any accuracy claim. "
            "Index-day-timing risk means early reconstructions may be "
            "systematically off by a week or more of fare drift."
        )
    elif blockers:
        verdict = "PROVISIONAL"
        reason = "Enough overlap to score, but provenance issues limit what it means."
    else:
        verdict = "SCORED"
        reason = f"{overlap} months of overlap with clean provenance."

    by_haul: dict[str, Any] = {}
    for haul in sorted({s.haul_category for s in scores}):
        haul_scores = sorted(
            (s for s in scores if s.haul_category == haul), key=lambda s: s.mae
        )
        best = haul_scores[0]
        by_haul[haul] = {
            "n_variants_compared": len(haul_scores),
            "best_variant": best.key,
            "best_variant_mae_pp": round(best.mae, 4),
            "best_variant_bias_pp": round(best.bias, 4),
            "best_variant_rolling_mae_pp": (
                round(best.rolling_mae, 4) if best.rolling_mae is not None else None
            ),
            "n_months": best.n_months,
            "selection_caveat": (
                f"This variant was chosen as best-of-{len(haul_scores)} AFTER seeing the "
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
                    "n_months": s.n_months,
                }
                for s in haul_scores
            ],
        }

    return {
        "verdict": verdict,
        "reason": reason,
        "n_scored_months": overlap,
        "min_required_months": MIN_OVERLAP_MONTHS,
        "blockers": blockers,
        "units": "percentage points of month-on-month change",
        "by_haul_category": by_haul,
    }


def run_validate(config: Config, *, reader=None) -> dict[str, Any]:
    reader = reader or bq.BigQueryWriter(config.project)
    rows = reader.query(SCORE_QUERY.format(table=config.index_ref))
    return build_report(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score reconstructed sub-indices against published ONS values."
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
        print(f"::error::configuration error: {exc}", flush=True)
        return 2

    try:
        report = run_validate(config)
    except Exception as exc:  # noqa: BLE001
        print(f"::error::validation failed: {exc}", flush=True)
        log.exception("validation failed")
        return 1

    print(json.dumps(report, indent=2, default=str))
    if report["verdict"] == "INSUFFICIENT_DATA":
        print(f"::notice::{report['reason']}", flush=True)
    for blocker in report.get("blockers", []):
        print(f"::warning::{blocker}", flush=True)
    # Always exit 0: "not enough data yet" is the expected state for months and
    # is not a pipeline failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())
