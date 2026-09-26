"""Read ONS's published monthly series instead of quoting them.

WHAT THIS FIXES

Asked what air fares printed in August 2026, the honest answer from our own data
was "we do not have it". ONS had published it on 16 September. The gap was not
access -- it was that this project only ever loaded the ad hoc sub-index release,
which is annual, lagged to February 2026, and rebased to January = 100 every
year. That rebase severs year-on-year by construction, so those six series can
describe seasonality and nothing else. Every question about a published level or
annual rate got answered from bulletin prose read over someone's shoulder.

mm23 is the monthly dataset behind the bulletin. It carries the CPI air fares
index continuously, its monthly and annual rates, its basket weight, and the RPI
fares group. Loading it means a question about a published number is a query.

THE RPI SERIES, AND THE TRAP IN THEM

RPI publishes no standalone air fares series. Air sits inside "other travel
costs" (DOCY), inside "fares and other travel costs" (CHBR), with rail and bus.
That dilution is tolerable for one specific reason: rail and bus fares are
administered and barely move mid-year, so a September move in these series is
very largely air. It is also the only route to a long history -- RPI from January
1987 against CPI's 2001 -- and history is the binding constraint on every
interval this project quotes for a September step, having seen just 19 of them.

The trap: RPI is not a leading indicator and must not be modelled as one. Both
measures are compiled from the SAME price quotes and published in the SAME
release on the same day. There is no lead to exploit. What RPI buys is history,
and a measurement of the formula effect -- RPI aggregates arithmetically where
CPI uses a geometric mean, which given identical inputs is the whole difference.

ON NOT ASSUMING THE PAYLOAD SHAPE

The bank measurement searched `raw_response` for a key called `departure_time`,
found nothing in any live payload because the provider stores the departure at
`departure_airport.time`, and reported "no candidate departure times found". It
was wrong rather than empty, and it went green. So this module does not quietly
return zero rows when the JSON is not what it expected: an unreadable payload
raises, and the error names the keys that WERE present.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import requests

from . import bq
from .config import Config, ConfigError

log = logging.getLogger("ukairfares.mm23")

ONS_BASE = "https://www.ons.gov.uk"
DATASET = "mm23"
USER_AGENT = "uk-airfares-nowcasting/1.0 (research pipeline; contact via repository owner)"

TABLE = "ons_mm23_series"


class Mm23Error(RuntimeError):
    """The endpoint answered, but not with a series we can read."""


class Series:
    """One ONS series, and what its numbers mean.

    `kind` is stored on every row because these series are not
    interchangeable: D7EH is an index level, D7MB a percentage change, CJXW a
    weight. Averaging across them would produce a number with no meaning, and
    nothing in the values themselves would give that away.
    """

    __slots__ = ("cdid", "kind", "measure", "label", "basis", "why")

    def __init__(self, cdid, kind, measure, label, basis=None, why=""):
        self.cdid, self.kind, self.measure = cdid.upper(), kind, measure
        self.label, self.basis, self.why = label, basis, why

    @property
    def url(self) -> str:
        return (f"{ONS_BASE}/economy/inflationandpriceindices/timeseries/"
                f"{self.cdid.lower()}/{DATASET}/data")


#: What to fetch, and why each one is worth a request.
SERIES: tuple[Series, ...] = (
    Series("D7EH", "index", "cpi", "CPI INDEX 07.3.3: Passenger transport by air",
           "2015=100",
           "The headline air fares index, continuous and unrebased -- the thing "
           "our January-100 sub-indices cannot be compared across years."),
    Series("D7MB", "monthly_rate", "cpi", "CPI MONTHLY RATE 07.3.3: Passenger transport by air",
           why="The month-on-month print itself. Every figure quoted in this "
               "project's analysis so far came from bulletin prose; this is the series."),
    Series("D7IT", "annual_rate", "cpi", "CPI ANNUAL RATE 07.3.3: Passenger transport by air",
           why="The annual rate, which the annually-rebased sub-indices cannot yield."),
    Series("CJXW", "weight", "cpi", "CPI WEIGHTS 07.3.3: Passenger transport by air",
           why="How much air fares can move the headline. Without it a large "
               "monthly swing cannot be turned into a contribution."),
    Series("CHBR", "index", "rpi", "RPI: Fares and other travel costs",
           "Jan 1987=100",
           "Where RPI keeps air fares -- diluted with rail and bus, but running "
           "from 1987 against CPI's 2001."),
    Series("CZFN", "monthly_rate", "rpi", "RPI: Percentage change over 1 month, fares and other travel costs",
           why="The RPI monthly step, directly published rather than derived."),
    Series("CZED", "annual_rate", "rpi", "RPI: Percentage change over 12 months, fares and other travel costs",
           why="The RPI annual rate, for the formula-effect comparison against D7IT."),
    Series("DOCW", "index", "rpi", "RPI: fares and other travel costs, rail fares",
           "Jan 1987=100",
           "Administered and near-flat mid-year. Subtracting it is what makes the "
           "September move in CHBR readable as air."),
    Series("DOCX", "index", "rpi", "RPI: fares and other travel costs, bus and coach fares",
           "Jan 1987=100", "The other administered component, for the same reason."),
    Series("DOCY", "index", "rpi", "RPI: fares and other travel costs, other travel costs",
           "Jan 1987=100",
           "The subgroup air fares actually sits in -- one level closer than CHBR."),
    Series("CZHM", "weight", "rpi", "RPI: Weights (parts per 1000), fares and other travel costs",
           why="Needed before any claim about how much of CHBR is air."),
)

BY_CDID = {s.cdid: s for s in SERIES}

_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}


def _period(raw: Any) -> dt.date | None:
    """First of the month from ONS's date spellings.

    Seen in the wild as "2026 AUG"; also handles "2026 August", "AUG 2026" and
    an ISO date, because the spelling is not part of any contract.
    """
    text = str(raw or "").strip().replace(",", " ")
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10]).replace(day=1)
    except ValueError:
        pass
    parts = [p for p in text.split() if p]
    year = month = None
    for part in parts:
        token = part.upper()[:3]
        if token in _MONTHS:
            month = _MONTHS[token]
        elif part.isdigit() and len(part) == 4:
            year = int(part)
    return dt.date(year, month, 1) if year and month else None


def _value(raw: Any) -> Decimal | None:
    text = str(raw if raw is not None else "").strip().replace(",", "")
    if not text or text in {"..", "-", "N/A"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_series(payload: Any, series: Series) -> list[dict[str, Any]]:
    """Monthly observations from one mm23 response.

    Raises rather than returning an empty list when the payload has no monthly
    block at all -- the difference between "ONS published nothing" and "the shape
    changed under us" has to reach a human, and an empty list does not.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError) as exc:
            raise Mm23Error(f"{series.cdid}: response was not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise Mm23Error(f"{series.cdid}: expected an object, got {type(payload).__name__}")
    months = payload.get("months")
    if not isinstance(months, list):
        raise Mm23Error(
            f"{series.cdid}: no 'months' list in the response. Keys present: "
            f"{sorted(payload)[:15]}. The endpoint shape may have changed."
        )

    rows: list[dict[str, Any]] = []
    skipped = 0
    for item in months:
        if not isinstance(item, dict):
            skipped += 1
            continue
        period = _period(item.get("date") or f"{item.get('year','')} {item.get('month','')}")
        value = _value(item.get("value"))
        if period is None or value is None:
            skipped += 1
            continue
        rows.append({"period": period, "value": value})
    if not rows:
        raise Mm23Error(
            f"{series.cdid}: 'months' had {len(months)} entries and none were "
            f"readable. First entry: {months[0] if months else None!r}"
        )
    if skipped:
        log.warning("%s: %d of %d monthly entries unreadable", series.cdid, skipped, len(months))
    rows.sort(key=lambda r: r["period"])
    return rows


def fetch(series: Series, session: requests.Session | None = None) -> list[dict[str, Any]]:
    session = session or requests.Session()
    log.info("fetching %s (%s)", series.cdid, series.label)
    resp = session.get(series.url, timeout=60, headers={"User-Agent": USER_AGENT})
    if resp.status_code != 200:
        raise Mm23Error(f"{series.cdid}: HTTP {resp.status_code} from {series.url}")
    return parse_series(resp.text, series)


def build_rows(series: Series, observations: Iterable[dict[str, Any]], *,
               run_id: str, fetched_ts: dt.datetime) -> list[dict[str, Any]]:
    return [
        {
            "period": obs["period"],
            "cdid": series.cdid,
            "value": obs["value"],
            "kind": series.kind,
            "measure": series.measure,
            "series_label": series.label,
            "basis": series.basis,
            "source_url": series.url,
            "fetched_ts": fetched_ts,
            "is_current": True,
            "run_id": run_id,
        }
        for obs in observations
    ]


def render(fetched: dict[str, list[dict[str, Any]]], *, months: int = 14) -> str:
    """The recent months, for the run log.

    Printed because a fetch that lands only in BigQuery is invisible to anyone
    reading the run, and because the interesting check -- does the number match
    the bulletin -- is a glance, not a query.
    """
    lines: list[str] = []
    for cdid, rows in fetched.items():
        s = BY_CDID[cdid]
        unit = {"index": "", "monthly_rate": "%", "annual_rate": "%", "weight": ""}[s.kind]
        lines.append(f"{cdid}  {s.label}")
        lines.append(f"    {len(rows)} months, {rows[0]['period']} to {rows[-1]['period']}")
        tail = rows[-months:]
        lines.append("    " + "  ".join(
            f"{r['period'].strftime('%b%y')} {r['value']}{unit}" for r in tail))
        lines.append("")
    return "\n".join(lines)


def run_fetch(config: Config, *, writer=None, session=None,
              cdids: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    # build_writer, not BigQueryWriter directly: it honours DRY_RUN, which is how
    # this runs without credentials.
    writer = writer or bq.build_writer(config)
    # Fully qualified, because the client demands 'project.dataset.table' and
    # rejects a bare name. The first live run died here after fetching and
    # parsing eleven series correctly -- every test passed because DryRunWriter
    # ignores the table argument entirely, so nothing checked what was passed.
    table = TABLE if config.dry_run else config.table_ref(TABLE)
    session = session or requests.Session()
    run_id = str(uuid.uuid4())
    fetched_ts = dt.datetime.now(dt.timezone.utc)
    wanted = [BY_CDID[c.upper()] for c in cdids] if cdids else list(SERIES)

    out: dict[str, list[dict[str, Any]]] = {}
    failures: list[str] = []
    for series in wanted:
        try:
            observations = fetch(series, session)
        except (Mm23Error, requests.RequestException) as exc:
            # One dead series must not cost the other ten: the CPI air fares
            # index is the load-bearing one and an RPI subgroup going missing is
            # not a reason to lose it.
            log.error("%s failed: %s", series.cdid, exc)
            failures.append(f"{series.cdid}: {exc}")
            continue
        rows = build_rows(series, observations, run_id=run_id, fetched_ts=fetched_ts)
        writer.append(table, rows)
        out[series.cdid] = observations

    if failures:
        for message in failures:
            print(f"::warning::mm23 {message}", file=sys.stderr, flush=True)
    if not out:
        raise Mm23Error("every series failed; nothing was written")
    log.info("loaded %d of %d series", len(out), len(wanted))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch ONS mm23 series into BigQuery.")
    parser.add_argument("--cdid", action="append", default=None,
                        help="Fetch only this series (repeatable). Default: all.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and print, write nothing.")
    parser.add_argument("--list", action="store_true",
                        help="List the series and why each is fetched, then exit.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if args.list:
        for s in SERIES:
            print(f"{s.cdid}  {s.measure:3}  {s.kind:12}  {s.label}\n      {s.why}")
        return 0
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"::error::configuration error: {exc}", file=sys.stderr, flush=True)
        return 2
    writer = bq.DryRunWriter() if args.dry_run else None
    try:
        fetched = run_fetch(config, writer=writer, cdids=args.cdid)
    except Mm23Error as exc:
        print(f"::error::mm23 fetch failed: {exc}", file=sys.stderr, flush=True)
        return 1
    print(render(fetched))
    return 0


if __name__ == "__main__":
    sys.exit(main())
