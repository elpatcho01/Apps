"""Wedge research run: score wedge forecasters on the 121 origins, and state
every accuracy number in bp of headline RPI using the *actual* weights.

    python -m rpiclothing.wedge --out wedge.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import statistics
import sys
from typing import Any, Mapping

from . import fixing, nowcast, onsfetch
from .series import add_months, effective_weight_ppt

HORIZONS = (1, 3, 6, 12)

#: Pre-registered order, simplest first.
WEDGE_CANDIDATES = {
    "random_walk": nowcast.random_walk_wedge,
    "seasonal_rw": nowcast.seasonal_wedge,
}


def bp_per_pp(chbj, chaw, weights, months) -> dict[str, float]:
    """bp of headline RPI per 1pp error on the clothing leg, averaged over
    ``months`` using the exact effective weight."""
    ews = [effective_weight_ppt(chbj, chaw, weights, d) for d in months]
    ews = [e for e in ews if e is not None]
    return {"mean_effective_weight_ppt": statistics.fmean(ews), "bp_per_pp": statistics.fmean(ews) / 10}


def run(chbj: Mapping, d7bw: Mapping, chaw: Mapping, weights: Mapping[int, float]) -> dict[str, Any]:
    origins = fixing.default_origins()
    out: dict[str, Any] = {"scores": {}}
    for name, f in WEDGE_CANDIDATES.items():
        out["scores"][name] = {str(h): v for h, v in nowcast.score_wedge(chbj, d7bw, origins, HORIZONS, f).items()}
    maes = {n: out["scores"][n]["12"]["mae_pp"] for n in WEDGE_CANDIDATES}
    out["selection_h12"] = fixing.select_simplest(maes, tuple(WEDGE_CANDIDATES))[1]
    out["selection_h1"] = fixing.select_simplest(
        {n: out["scores"][n]["1"]["mae_pp"] for n in WEDGE_CANDIDATES}, tuple(WEDGE_CANDIDATES))[1]
    last = max(d for d in chbj if d in d7bw)
    out["latest"] = {"month": last.isoformat(), **(nowcast.wedge_decomposition(chbj, d7bw, last) or {})}
    out["jun2026"] = nowcast.wedge_decomposition(chbj, d7bw, dt.date(2026, 6, 1))
    # Conversion to headline bp: the brief used 4.2bp/pp (42ppt). Actual:
    yr = lambda y: [dt.date(y, m, 1) for m in range(1, 13) if dt.date(y, m, 1) <= last]
    out["bp_per_pp_2026"] = bp_per_pp(chbj, chaw, weights, yr(2026))
    out["bp_per_pp_backtest_window"] = bp_per_pp(
        chbj, chaw, weights, [add_months(o, 12) for o in origins])
    out["weights_ppt"] = {str(y): w for y, w in sorted(weights.items()) if y >= 2010}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="wedge.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    mm23 = onsfetch.fetch_mm23()
    res = run(mm23["CHBJ"].as_dict(), mm23["D7BW"].as_dict(), mm23["CHAW"].as_dict(),
              mm23["CZHJ"].annual_dict())
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, sort_keys=True, default=str)
    for k, v in res.items():
        print(f"WEDGE::{k}::" + json.dumps(v, sort_keys=True, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
