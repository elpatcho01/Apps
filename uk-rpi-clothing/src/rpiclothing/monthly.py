"""The monthly production run: compute, append to BigQuery, write the digest and
the analytics export.

    python -m rpiclothing.monthly --month 2026-08

Failure posture is the digest's, not the collector's (as uk-airfares): every
section is computed independently and a failure becomes a line in the report.
A section that could not be computed is listed under "Needs attention" --
an unanswered question is itself a concern, and the report never says
"nothing flagged" above a section that failed.

Outputs, and only these (no speculative views):

* WS1 exposure table: next 12 months of clothing's contribution to the RPI
  all-items MoM, in bp, exact chain-linked formula.
* Wedge: latest RPI-CPI clothing log wedge and its two-year decomposition.
* WS2a reconstruction: latest months' rebuilt CHBJ vs published, formula wedge.
* WS3 nowcast: only when a licensed panel is configured; otherwise the digest
  says there is none.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import math
import pathlib
import sys
import traceback
from typing import Any, Callable, Mapping

from . import PIPELINE_VERSION, fixing, nowcast, onsfetch
from .config import Config, ConfigError
from .series import add_months
from .ws1 import INCUMBENT

log = logging.getLogger(__name__)

REPORTS = pathlib.Path(__file__).resolve().parents[2] / "reports"
EXPORT_SCHEMA_VERSION = 1


@dataclasses.dataclass
class Section:
    name: str
    ok: bool
    data: Any = None
    error: str | None = None
    notes: list[str] = dataclasses.field(default_factory=list)


def _run(name: str, fn: Callable[[], Any]) -> Section:
    try:
        data, notes = fn()
        return Section(name, True, data, notes=notes)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a report line
        log.exception("section %s failed", name)
        return Section(name, False, error=f"{type(exc).__name__}: {exc}",
                       notes=[traceback.format_exc(limit=3)])


def compute_exposure(series: Mapping[str, Mapping], weights: Mapping[int, float], candidate: str):
    chbj, chaw = series["CHBJ"], series["CHAW"]
    origin = max(chbj)
    table = fixing.exposure_table(chbj, chaw, weights, origin, fixing.FanSpec(INCUMBENT, candidate))
    notes = []
    if any(r["weight_carried_forward"] for r in table):
        notes.append("Months after next January use the latest published CZHJ weight "
                     "(next year's weight is published in February).")
    return {"origin_month": origin.isoformat(), "rows": table, "candidate": candidate,
            "point_model": INCUMBENT.label()}, notes


def compute_wedge(series: Mapping[str, Mapping]):
    chbj, d7bw = series["CHBJ"], series["D7BW"]
    last = max(d for d in chbj if d in d7bw)
    dec = nowcast.wedge_decomposition(chbj, d7bw, last)
    hist = [{"month": d.isoformat(), "wedge_pp": round(w, 3)}
            for d in sorted(chbj)[-13:] if (w := nowcast.log_wedge(chbj, d7bw, d)) is not None]
    return {"month": last.isoformat(), **{k: round(v, 3) for k, v in (dec or {}).items()},
            "last_13": hist, "definition": "12m log change, CHBJ minus D7BW, pp"}, []


def compute_nowcast(config: Config):
    if not config.panel_vendor:
        return {"status": "no_vendor"}, [
            "No licensed panel is configured (PANEL_VENDOR is empty), so there is no "
            "index-day nowcast. The incumbent seasonal+drift h1 forecast stands."]
    raise NotImplementedError(f"panel vendor {config.panel_vendor!r} has no adapter yet")


def build_digest(month: str, sections: list[Section]) -> str:
    by = {s.name: s for s in sections}
    lines = [f"# RPI clothing & footwear digest — {month}", ""]
    concerns = [f"**{s.name}** could not be computed: `{s.error}`" for s in sections if not s.ok]
    ex = by.get("exposure")
    if ex and ex.ok:
        d = ex.data
        lines += [f"## Fixing exposure (from {d['origin_month'][:7]})", "",
                  f"Clothing's contribution to the RPI all-items monthly change, bp. "
                  f"Point model `{d['point_model']}`, fan `{d['candidate']}`. "
                  "Exact chain-linked contribution (validated against ONS CZFY).", "",
                  "| Month | Expected | p10 | p90 | Effective weight (ppt) |",
                  "|---|---|---|---|---|"]
        for r in d["rows"]:
            lines.append(f"| {r['target_month'][:7]} | {r['expected_bp']:+.1f} | {r['p10_bp']:+.1f} | "
                         f"{r['p90_bp']:+.1f} | {r['effective_weight_ppt']:.1f}"
                         f"{' *' if r['weight_carried_forward'] else ''} |")
        lines += [""] + [f"_{n}_" for n in ex.notes] + [""]
    we = by.get("wedge")
    if we and we.ok:
        d = we.data
        lines += [f"## RPI–CPI clothing wedge ({d['month'][:7]})", "",
                  f"- 12-month log wedge: **{d.get('yoy', math.nan):.2f}pp**",
                  f"- built this year (Jan→{d['month'][5:7]}): {d.get('this_year_part', math.nan):.2f}pp; "
                  f"carried in from last year: {d.get('carried_in', math.nan):.2f}pp", ""]
    rc = by.get("reconstruction")
    if rc and rc.ok and rc.data:
        d = rc.data
        lines += ["## Carli reconstruction", "",
                  f"Variant `{d.get('variant')}`. Latest month {d.get('month')}: "
                  f"reconstructed error vs published CHBJ {d.get('error_pct', math.nan):+.3f}%.", ""]
        if d.get("error_pct") is not None and abs(d["error_pct"]) > 0.2:
            concerns.append(f"Reconstruction error {d['error_pct']:+.2f}% exceeds 0.2% — "
                            "a quote-handling rule may have changed.")
    nc = by.get("nowcast")
    if nc and nc.ok:
        lines += ["## Index-day nowcast", ""] + [f"- {n}" for n in nc.notes] + [""]
    lines += ["## Needs attention", ""]
    lines += [f"- {c}" for c in concerns] if concerns else ["Nothing flagged."]
    lines += ["", f"_pipeline {PIPELINE_VERSION}; generated "
                  f"{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_", ""]
    return "\n".join(lines)


def build_export(sections: list[Section]) -> dict:
    """Aggregates only, sorted keys so an unchanged month is byte-identical.
    No generated timestamp inside: it would manufacture a diff every run."""
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "sections": {s.name: ({"ok": True, "data": s.data} if s.ok else {"ok": False, "error": s.error})
                     for s in sections},
    }


def _ts() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_rows(config: Config, sections: list[Section], series_objs: Mapping[str, onsfetch.Series]) -> None:
    from .bq import BigQueryWriter, DryRunWriter

    writer = DryRunWriter() if config.dry_run else BigQueryWriter(config.project)
    t = config.table if not config.dry_run else (lambda n: n)
    ts = _ts()
    ons_rows = []
    for s in series_objs.values():
        ons_rows += [{"cdid": s.cdid, "period": d.isoformat(), "frequency": "monthly", "value": v,
                      "title": s.title, "source_url": s.source_url, "fetched_ts": ts} for d, v in s.values]
        ons_rows += [{"cdid": s.cdid, "period": f"{y}-01-01", "frequency": "annual", "value": v,
                      "title": s.title, "source_url": s.source_url, "fetched_ts": ts} for y, v in s.annual]
    writer.append(t("ons_series"), ons_rows)
    ex = next((s for s in sections if s.name == "exposure" and s.ok), None)
    if ex:
        writer.append(t("exposure"), [
            {"origin_month": ex.data["origin_month"], **{k: r[k] for k in (
                "target_month", "horizon", "expected_bp", "sd_bp", "p10_bp", "p50_bp", "p90_bp",
                "effective_weight_ppt", "nominal_weight_ppt", "weight_carried_forward",
                "variance_candidate")}, "point_model": ex.data["point_model"],
             "pipeline_version": config.pipeline_version, "computed_ts": ts}
            for r in ex.data["rows"]])


SERIES = ("CHBJ", "D7BW", "CHAW", "CZFY", "CZHJ", "DOCK", "DOCL", "DOCM", "DOCN", "DOCO",
          "CZXY", "CZXZ", "CZYA", "CZYB", "CZYC")


def run(month: str, config: Config, *, fetch=onsfetch.fetch_mm23, write: bool = True) -> list[Section]:
    sections: list[Section] = []
    mm23 = None
    try:
        mm23 = fetch()
    except Exception as exc:  # noqa: BLE001
        sections.append(Section("ons_fetch", False, error=f"{type(exc).__name__}: {exc}"))
    if mm23 is not None:
        missing = [c for c in SERIES if c not in mm23]
        if missing:
            sections.append(Section("ons_fetch", False, error=f"series missing from MM23: {missing}"))
        series = {c: mm23[c].as_dict() for c in SERIES if c in mm23}
        weights = mm23["CZHJ"].annual_dict() if "CZHJ" in mm23 else {}
        sections.append(_run("exposure", lambda: compute_exposure(series, weights, config.fan_candidate)))
        sections.append(_run("wedge", lambda: compute_wedge(series)))
    sections.append(_run("nowcast", lambda: compute_nowcast(config)))
    if write and mm23 is not None:
        try:
            write_rows(config, sections, {c: mm23[c] for c in SERIES if c in mm23})
        except Exception as exc:  # noqa: BLE001
            sections.append(Section("bigquery_write", False, error=f"{type(exc).__name__}: {exc}"))
    return sections


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--month", required=True, help="YYYY-MM the digest is filed under")
    ap.add_argument("--reports", default=str(REPORTS))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    sections = run(args.month, config)
    reports = pathlib.Path(args.reports)
    (reports / "data").mkdir(parents=True, exist_ok=True)
    (reports / f"{args.month}.md").write_text(build_digest(args.month, sections), encoding="utf-8")
    (reports / "data" / "analytics.json").write_text(
        json.dumps(build_export(sections), indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    failed = [s.name for s in sections if not s.ok]
    if failed:
        print(f"::warning::sections failed: {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
