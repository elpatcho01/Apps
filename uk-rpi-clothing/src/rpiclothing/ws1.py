"""Workstream 1 research run: backtest the variance candidates on real data and
produce the forward exposure table.

    python -m rpiclothing.ws1 --out ws1.json
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import math
import sys
from typing import Any, Mapping

from . import fixing, onsfetch
from .series import SeasonalDriftSpec, add_months, mom

log = logging.getLogger(__name__)

#: The incumbent point model. Chosen by Task 0's grid search as the
#: parameterisation closest to the brief's MAE table on the 121 origins
#: (10 trailing years of seasonal factors, 12-month trailing-mean drift):
#: h1 0.87 / h3 1.31 / h6 1.80 / h12 2.97 against the brief's
#: 0.90 / 1.38 / 1.72 / 2.74 (and 2.98 for the leg).
INCUMBENT = SeasonalDriftSpec(seasonal_years=10, drift_months=12)

HORIZONS = (1, 2, 3, 6, 12)


def _score_dict(sc: fixing.FanScore) -> dict[str, Any]:
    return {
        "n": sc.n,
        "crps": None if math.isnan(sc.crps) else round(sc.crps, 4),
        "coverage": {str(k): None if math.isnan(v) else round(v, 3) for k, v in sc.coverage.items()},
        "coverage_error": None if math.isnan(sc.crps) else round(sc.coverage_error, 4),
        "mean_sd": None if math.isnan(sc.mean_sd) else round(sc.mean_sd, 3),
    }


def run(
    chbj: Mapping[dt.date, float],
    chaw: Mapping[dt.date, float],
    weights: Mapping[int, float],
    *,
    origins: list[dt.date] | None = None,
) -> dict[str, Any]:
    origins = origins or fixing.default_origins()
    scores = fixing.backtest_fans(chbj, origins, HORIZONS, INCUMBENT, by_month=True)
    out: dict[str, Any] = {"incumbent_point": INCUMBENT.label(), "n_origins": len(origins)}
    out["scores"] = {
        c: {str(h): _score_dict(scores[c][h]) for h in HORIZONS} for c in fixing.ALL_CANDIDATES
    }
    out["h1_by_month"] = {
        c: {str(m): _score_dict(scores[c]["by_month"][1][m]) for m in range(1, 13)}
        for c in fixing.ALL_CANDIDATES
    }
    selection = {}
    for h in HORIZONS:
        choice, why = fixing.select_fan_candidate(scores, h)
        selection[str(h)] = why
    out["selection"] = selection
    # Alternative rule, reported alongside and NOT used unless chosen by the
    # user: calibration first. Only fans whose mean coverage error at h is
    # within 0.03 are eligible; then the 5%/simplest rule on CRPS.
    alt = {}
    for h in HORIZONS:
        try:
            _, why = fixing.select_simplest(
                {c: scores[c][h].crps for c in fixing.ALL_CANDIDATES},
                fixing.ALL_CANDIDATES,
                eligible=lambda c, h=h: scores[c][h].coverage_error <= 0.03,
            )
        except ValueError:
            why = {"chosen": None, "note": "no candidate within 0.03 coverage error"}
        alt[str(h)] = why
    out["selection_calibration_first"] = alt
    chosen = selection["1"]["chosen"]
    last = max(chbj)
    spec = fixing.FanSpec(INCUMBENT, chosen)
    pts = fixing.fan(chbj, last, 12, spec)
    out["fan_from_latest"] = [
        {"target": p.target.isoformat(), "h": p.horizon, "mean": round(p.mean_level, 2),
         "sd_pct": round(p.sd_pct, 3), "share_drift": round(p.share_drift, 3),
         "month_sd": round(math.sqrt(p.month_var), 3), "drift_sd": round(math.sqrt(p.drift_var), 4)}
        for p in pts
    ]
    out["exposure_table"] = fixing.exposure_table(chbj, chaw, weights, last, spec)
    # The same table under every candidate, so the monthly sd differences are visible.
    out["month_sd_by_candidate_latest"] = {
        c: {str(p.target.month): round(math.sqrt(p.month_var), 3)
            for p in fixing.fan(chbj, last, 12, fixing.FanSpec(INCUMBENT, c))}
        for c in fixing.ALL_CANDIDATES
    }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="ws1.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    mm23 = onsfetch.fetch_mm23()
    res = run(mm23["CHBJ"].as_dict(), mm23["CHAW"].as_dict(), mm23["CZHJ"].annual_dict())
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    for k, v in res.items():
        print(f"WS1::{k}::" + json.dumps(v, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
