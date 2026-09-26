"""Fetch ONS series and files from the cy.ons.gov.uk mirror.

Why the mirror: www.ons.gov.uk sits behind a cache that has served stale or
truncated tables for the time-series and dataset endpoints this project reads.
The Welsh-language mirror serves the same content from the same backend and
has been reliable for current full tables. It is the single configured base.

There is deliberately **no silent fallback to www**. A fallback would make the
source of any given vintage depend on which host happened to answer, and a
stale table from the cache would then look exactly like a fresh one. If the
mirror fails, the run fails, and every stored row records the URL it came from
so a mixed history is visible.

ons.gov.uk is not reachable from the development sandbox (egress policy), so
everything here is exercised against committed fixtures in tests and runs for
real only inside GitHub Actions.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import io
import logging
import os
import re
import time
from typing import Any, Iterable

import requests

log = logging.getLogger(__name__)

DEFAULT_BASE = "https://cy.ons.gov.uk"

USER_AGENT = "uk-rpi-clothing-nowcast/1.0 (research pipeline; contact via repository owner)"

TIMESERIES_PATH = "/economy/inflationandpriceindices/timeseries/{cdid}/{dataset}/data"
MM23_CSV_PATH = (
    "/file?uri=/economy/inflationandpriceindices/datasets/consumerpriceindices/current/mm23.csv"
)
PRICE_QUOTES_DATASET_PATH = (
    "/economy/inflationandpriceindices/datasets/"
    "consumerpriceindicescpiandretailpricesindexrpiitemindicesandpricequotes"
)

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"],
        start=1,
    )
}
_MONTH_LABEL = re.compile(r"^\s*(\d{4})\s+([A-Z]{3})\s*$")
_YEAR_LABEL = re.compile(r"^\s*(\d{4})\s*$")

#: Minimum spacing between requests, and the retry schedule for 429/5xx. The
#: mirror rate-limits a crawl of dataset editions (observed: HTTP 429 after a
#: few dozen rapid requests), so every request is paced and a 429 honours
#: Retry-After before giving up.
MIN_INTERVAL_S = float(os.environ.get("ONS_MIN_INTERVAL_S", "0.6"))
RETRY_DELAYS_S = (2, 5, 15, 30, 60)
_last_request = [0.0]


class OnsFetchError(RuntimeError):
    """The mirror answered with something we cannot use. Always fatal."""


def base_url() -> str:
    return os.environ.get("ONS_BASE_URL", DEFAULT_BASE).rstrip("/")


def parse_month_label(label: str) -> dt.date | None:
    """'2026 JUN' -> date(2026, 6, 1). Anything else (annual, quarterly) -> None."""
    m = _MONTH_LABEL.match(label.upper())
    if not m or m.group(2) not in _MONTHS:
        return None
    return dt.date(int(m.group(1)), _MONTHS[m.group(2)], 1)


@dataclasses.dataclass(frozen=True)
class Series:
    cdid: str
    title: str
    source_url: str
    #: Monthly observations, sorted by date. May be empty for an annual-only
    #: series (the RPI weights are published once a year).
    values: tuple[tuple[dt.date, float], ...]
    release_date: str | None = None
    #: Annual observations, {year: value}.
    annual: tuple[tuple[int, float], ...] = ()

    def as_dict(self) -> dict[dt.date, float]:
        return dict(self.values)

    def annual_dict(self) -> dict[int, float]:
        return dict(self.annual)

    @property
    def first(self) -> tuple[dt.date, float] | None:
        return self.values[0] if self.values else None

    @property
    def last(self) -> tuple[dt.date, float] | None:
        return self.values[-1] if self.values else None


def parse_timeseries_json(payload: dict[str, Any], source_url: str) -> Series:
    """Parse the ONS time-series `/data` JSON.

    Only the `months` block is used. Blank values (a series that starts later
    than its dataset) are skipped rather than read as zero.
    """
    desc = payload.get("description") or {}
    cdid = str(desc.get("cdid") or "").upper()
    months = payload.get("months")
    if not cdid or not isinstance(months, list):
        raise OnsFetchError(f"{source_url}: not a time-series payload (keys={sorted(payload)})")
    annual: list[tuple[int, float]] = []
    for row in payload.get("years") or []:
        y = _YEAR_LABEL.match(str(row.get("date", "")))
        raw = str(row.get("value", "")).strip()
        if y and raw:
            try:
                annual.append((int(y.group(1)), float(raw)))
            except ValueError:
                continue
    out: list[tuple[dt.date, float]] = []
    for row in months:
        d = parse_month_label(str(row.get("date", "")))
        raw = str(row.get("value", "")).strip()
        if d is None or not raw:
            continue
        try:
            out.append((d, float(raw)))
        except ValueError as exc:
            raise OnsFetchError(f"{source_url}: unparseable value {raw!r} at {d}") from exc
    if not out and not annual:
        raise OnsFetchError(f"{source_url}: {cdid} has no observations")
    out.sort()
    return Series(
        cdid=cdid,
        title=str(desc.get("title") or ""),
        source_url=source_url,
        values=tuple(out),
        release_date=desc.get("releaseDate"),
        annual=tuple(sorted(annual)),
    )


def _get(
    session: requests.Session, url: str, *, timeout: int = 60, sleep=time.sleep
) -> requests.Response:
    for attempt in range(len(RETRY_DELAYS_S) + 1):
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_request[0])
        if wait > 0:
            sleep(wait)
        _last_request[0] = time.monotonic()
        resp = session.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        if resp.status_code == 200:
            return resp
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < len(RETRY_DELAYS_S):
            delay = float(RETRY_DELAYS_S[attempt])
            ra = resp.headers.get("Retry-After", "")
            if ra.strip().isdigit():
                delay = max(delay, float(ra))
            log.warning("%s: HTTP %s, retrying in %.0fs", url, resp.status_code, delay)
            sleep(delay)
            continue
        raise OnsFetchError(f"{url}: HTTP {resp.status_code}")
    raise OnsFetchError(f"{url}: retries exhausted")  # pragma: no cover


def fetch_timeseries(
    cdid: str, dataset: str = "mm23", *, session: requests.Session | None = None
) -> Series:
    session = session or requests.Session()
    url = base_url() + TIMESERIES_PATH.format(cdid=cdid.lower(), dataset=dataset.lower())
    resp = _get(session, url)
    try:
        payload = resp.json()
    except ValueError as exc:
        raise OnsFetchError(f"{url}: response is not JSON") from exc
    return parse_timeseries_json(payload, url)


def parse_mm23_csv(text: str, source_url: str = "mm23.csv") -> dict[str, Series]:
    """Parse the whole MM23 dataset CSV into one Series per CDID.

    Layout (observed): row 0 is `Title,...`, row 1 is `CDID,...`, then a few
    metadata rows (PreUnit, Unit, Release Date, Next release, Important Notes),
    then data rows labelled '1987', '1987 Q1' or '1987 JAN'. Only monthly rows
    are kept. Rows are located by their first-column label, never by offset.
    """
    rows = list(csv.reader(io.StringIO(text)))
    titles = next((r for r in rows if r and r[0].strip().lower() == "title"), None)
    cdids = next((r for r in rows if r and r[0].strip().upper() == "CDID"), None)
    if titles is None or cdids is None:
        raise OnsFetchError(f"{source_url}: no Title/CDID header rows")
    release = next(
        (r[1] for r in rows if r and r[0].strip().lower() == "release date" and len(r) > 1), None
    )
    cols: dict[int, list[tuple[dt.date, float]]] = {i: [] for i in range(1, len(cdids))}
    ann: dict[int, list[tuple[int, float]]] = {i: [] for i in range(1, len(cdids))}
    for r in rows:
        if not r:
            continue
        y = _YEAR_LABEL.match(r[0])
        if y:
            for i in range(1, min(len(r), len(cdids))):
                v = r[i].strip()
                if v:
                    try:
                        ann[i].append((int(y.group(1)), float(v)))
                    except ValueError:
                        continue
            continue
        d = parse_month_label(r[0])
        if d is None:
            continue
        for i in range(1, min(len(r), len(cdids))):
            v = r[i].strip()
            if v:
                try:
                    cols[i].append((d, float(v)))
                except ValueError:
                    continue
    out: dict[str, Series] = {}
    for i, vals in cols.items():
        cdid = cdids[i].strip().upper()
        if not cdid or not (vals or ann[i]):
            continue
        vals.sort()
        out[cdid] = Series(
            cdid=cdid,
            title=titles[i].strip() if i < len(titles) else "",
            source_url=source_url,
            values=tuple(vals),
            release_date=release,
            annual=tuple(sorted(ann[i])),
        )
    return out


def fetch_mm23(*, session: requests.Session | None = None) -> dict[str, Series]:
    session = session or requests.Session()
    url = base_url() + MM23_CSV_PATH
    resp = _get(session, url, timeout=180)
    return parse_mm23_csv(resp.content.decode("utf-8-sig", errors="replace"), url)


def search_titles(series: dict[str, Series], *needles: str) -> list[Series]:
    """Series whose title contains every needle (case-insensitive)."""
    lowered = [n.lower() for n in needles]
    return sorted(
        (s for s in series.values() if all(n in s.title.lower() for n in lowered)),
        key=lambda s: s.cdid,
    )


# --- dataset pages ----------------------------------------------------------


def fetch_json(path_or_url: str, *, session: requests.Session | None = None) -> dict[str, Any]:
    session = session or requests.Session()
    url = path_or_url if path_or_url.startswith("http") else base_url() + path_or_url
    resp = _get(session, url)
    try:
        return resp.json()
    except ValueError as exc:
        raise OnsFetchError(f"{url}: response is not JSON") from exc


def dataset_editions(
    landing_path: str = PRICE_QUOTES_DATASET_PATH, *, session: requests.Session | None = None
) -> list[str]:
    """Edition URIs listed on a dataset landing page, in the page's order."""
    landing = fetch_json(landing_path.rstrip("/") + "/data", session=session)
    return [ed["uri"] for ed in landing.get("datasets") or [] if isinstance(ed, dict) and ed.get("uri")]


def edition_downloads(uri: str, *, session: requests.Session | None = None) -> list[dict[str, str]]:
    """Files attached to one dataset edition."""
    page = fetch_json(uri.rstrip("/") + "/data", session=session)
    desc = page.get("description") or {}
    out = []
    for kind in ("downloads", "supplementaryFiles"):
        for f in page.get(kind) or []:
            name = f.get("file") if isinstance(f, dict) else None
            if name:
                out.append(
                    {
                        "edition": str(desc.get("edition") or ""),
                        "release_date": str(desc.get("releaseDate") or ""),
                        "title": str(f.get("title") or ""),
                        "file": name,
                        "uri": uri,
                        "url": f"{base_url()}/file?uri={uri.rstrip('/')}/{name}",
                    }
                )
    return out


def dataset_download_links(
    landing_path: str = PRICE_QUOTES_DATASET_PATH, *, session: requests.Session | None = None
) -> list[dict[str, str]]:
    """Every downloadable file under a dataset landing page, all editions.

    The Zebedee `/data` JSON for a dataset landing page lists its editions
    under `datasets`; each edition's `/data` lists `downloads` (file names)
    plus `supplementaryFiles`. Returned newest-first by edition release date,
    as far as the payload says.
    """
    session = session or requests.Session()
    landing = fetch_json(landing_path.rstrip("/") + "/data", session=session)
    editions = landing.get("datasets") or []
    links: list[dict[str, str]] = []
    for ed in editions:
        uri = ed.get("uri") if isinstance(ed, dict) else None
        if not uri:
            continue
        page = fetch_json(uri.rstrip("/") + "/data", session=session)
        desc = page.get("description") or {}
        for kind in ("downloads", "supplementaryFiles"):
            for f in page.get(kind) or []:
                name = f.get("file") if isinstance(f, dict) else None
                if not name:
                    continue
                links.append(
                    {
                        "edition": str(desc.get("edition") or ""),
                        "release_date": str(desc.get("releaseDate") or ""),
                        "title": str(f.get("title") or ""),
                        "file": name,
                        "url": f"{base_url()}/file?uri={uri.rstrip('/')}/{name}",
                    }
                )
    return links


def download(url: str, *, session: requests.Session | None = None, timeout: int = 300) -> bytes:
    session = session or requests.Session()
    return _get(session, url, timeout=timeout).content


def iter_rows(rows: Iterable[dict[str, str]], **equals: str) -> Iterable[dict[str, str]]:
    for r in rows:
        if all(r.get(k) == v for k, v in equals.items()):
            yield r
