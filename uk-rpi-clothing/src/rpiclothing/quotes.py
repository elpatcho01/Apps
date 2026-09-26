"""Load ONS local-collection price quotes (clothing & footwear only) across
every schema era, normalised to one layout, with item weights.

Observed schema eras (research run 3, 2026-09-26):

* **2026-** consumption segments: CS_ID/CS_DESC, VALIDITY True/False,
  PRICE_RELATIVE_RPI and _CPI, BASE_PRICE_RPI and _CPI, INDEX_ALGORITHM_RPI.
  Item indices replaced by ``consumptionsegments`` files carrying RPI_INDEX,
  CPI_INDEX, RPI_WEIGHT, CPI_WEIGHT, RPI_SECTION per segment.
* **2020-2025** item + segment: ITEM_ID and CS_ID, VALIDITY TRUE/FALSE,
  separate RPI/CPI relatives and bases, TEMPORAL, INDEX_ALGORITHM_RPI.
* **-2019** item only: ITEM_ID, VALIDITY codes (3, 4 valid; 1, 2 not), one
  PRICE_RELATIVE and BASE_PRICE for both indices, BASE_VALIDITY. Item
  indices carry CPI ITEM_WEIGHT/COICOP_WEIGHT and INDEX_ALGORITHM but no
  RPI item weight.

Clothing & footwear is item prefix 51: 5101 men's outerwear, 5102 women's,
5103 children's, 5104 other clothing, 5105 footwear. Those are exactly the RPI
sub-sections published as DOCK/DOCL/DOCM/DOCN/DOCO with weights
CZXY/CZXZ/CZYA/CZYB/CZYC, which gives a sub-section-level answer key.

Editions are discovered from the dataset landing page and fetched through the
cy.ons.gov.uk mirror. Earlier years ship as annual or quarterly zips holding
several monthly CSVs; the month of every row is read from QUOTE_DATE, never
from the file name. Parsed months are cached as gzipped CSV under ``cache/``
so a research run re-downloads only what it has not seen.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import gzip
import io
import json
import logging
import pathlib
import re
import zipfile
from collections import defaultdict
from typing import Iterable, Iterator

from . import onsfetch

log = logging.getLogger(__name__)

CLOTHING_PREFIX = "51"

#: RPI sub-sections by item-id prefix, with their published index and weight CDIDs.
SUBSECTIONS: dict[str, dict[str, str]] = {
    "5101": {"name": "mens_outerwear", "index": "DOCK", "weight": "CZXY"},
    "5102": {"name": "womens_outerwear", "index": "DOCL", "weight": "CZXZ"},
    "5103": {"name": "childrens_outerwear", "index": "DOCM", "weight": "CZYA"},
    "5104": {"name": "other_clothing", "index": "DOCN", "weight": "CZYB"},
    "5105": {"name": "footwear", "index": "DOCO", "weight": "CZYC"},
}

NORMALISED_FIELDS = (
    "month", "item_id", "desc", "valid", "price", "indicator", "rel_rpi", "rel_cpi",
    "base_rpi", "base_cpi", "stratum_weight", "stratum_type", "stratum_cell", "region",
    "shop_type", "shop_weight", "shop_code", "algo_rpi", "era",
)

_QUOTE_EDITION = re.compile(r"price?s?quotes?", re.I)
_ITEMIDX_EDITION = re.compile(r"itemindices|consumptionsegmentindices", re.I)


def subsection_of(item_id: str) -> str | None:
    return item_id[:4] if item_id[:4] in SUBSECTIONS else None


def _f(v: str | None) -> float | None:
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def normalise_row(r: dict[str, str]) -> dict[str, object] | None:
    """One raw quote row -> the normalised layout, or None if not clothing."""
    item = (r.get("ITEM_ID") or r.get("CS_ID") or "").strip()
    if not item.startswith(CLOTHING_PREFIX):
        return None
    month = (r.get("QUOTE_DATE") or "").strip()[:6]
    raw_valid = (r.get("VALIDITY") or "").strip().upper()
    if raw_valid in ("TRUE", "FALSE"):
        valid, era = raw_valid == "TRUE", ("2026" if "ITEM_ID" not in r else "2020")
    else:
        valid, era = raw_valid in ("3", "4"), "2010"
    single_rel = _f(r.get("PRICE_RELATIVE"))
    single_base = _f(r.get("BASE_PRICE"))
    return {
        "month": month,
        "item_id": item,
        "desc": (r.get("CS_DESC") or r.get("ITEM_DESC") or "").strip(),
        "valid": valid,
        "price": _f(r.get("PRICE")),
        "indicator": (r.get("INDICATOR_BOX") or "").strip(),
        "rel_rpi": _f(r.get("PRICE_RELATIVE_RPI")) if "PRICE_RELATIVE_RPI" in r else single_rel,
        "rel_cpi": _f(r.get("PRICE_RELATIVE_CPI")) if "PRICE_RELATIVE_CPI" in r else single_rel,
        "base_rpi": _f(r.get("BASE_PRICE_RPI")) if "BASE_PRICE_RPI" in r else single_base,
        "base_cpi": _f(r.get("BASE_PRICE_CPI")) if "BASE_PRICE_CPI" in r else single_base,
        "stratum_weight": _f(r.get("STRATUM_WEIGHT")),
        "stratum_type": (r.get("STRATUM_TYPE") or "").strip(),
        "stratum_cell": (r.get("STRATUM_CELL") or "").strip(),
        "region": (r.get("REGION") or "").strip(),
        "shop_type": (r.get("SHOP_TYPE") or "").strip(),
        "shop_weight": _f(r.get("SHOP_WEIGHT")) or 1.0,
        "shop_code": (r.get("SHOP_CODE") or "").strip(),
        "algo_rpi": (r.get("INDEX_ALGORITHM_RPI") or "").strip(),
        "era": era,
    }


def iter_csv_members(blob: bytes, name: str) -> Iterator[tuple[str, str]]:
    """(member name, text) for a CSV or every CSV inside a zip."""
    if name.lower().endswith(".zip") or blob[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for m in zf.namelist():
                if m.lower().endswith(".csv"):
                    yield m, zf.read(m).decode("utf-8-sig", errors="replace")
    else:
        yield name, blob.decode("utf-8-sig", errors="replace")


def parse_quotes_text(text: str) -> dict[str, list[dict[str, object]]]:
    """Clothing rows by month from one CSV's text."""
    out: dict[str, list[dict[str, object]]] = defaultdict(list)
    reader = csv.DictReader(io.StringIO(text))
    for r in reader:
        n = normalise_row(r)
        if n is not None and n["month"]:
            out[str(n["month"])].append(n)
    return dict(out)


def parse_item_weights_text(text: str) -> dict[str, dict[str, dict[str, float]]]:
    """{month: {item_id: {...}}} from an item-indices or segment-indices CSV.

    Keeps, per clothing item: rpi_weight (2026 segment files only), cpi_item
    weight (ITEM_WEIGHT or CPI_WEIGHT), rpi_index and cpi_index where present.
    """
    out: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for r in csv.DictReader(io.StringIO(text)):
        item = (r.get("ITEM_ID") or r.get("CS_ID") or "").strip()
        month = (r.get("INDEX_DATE") or "").strip()[:6]
        if not item.startswith(CLOTHING_PREFIX) or not month:
            continue
        rec = {}
        for key, cols in {
            "rpi_weight": ("RPI_WEIGHT",),
            "cpi_weight": ("CPI_WEIGHT", "ITEM_WEIGHT"),
            "rpi_index": ("RPI_INDEX",),
            "cpi_index": ("CPI_INDEX", "ITEM_INDEX"),
            "algo": ("INDEX_ALGORITHM",),
        }.items():
            for c in cols:
                v = _f(r.get(c))
                if v is not None:
                    rec[key] = v
                    break
        rec["rpi_section"] = (r.get("RPI_SECTION") or "").strip()
        out[month][item] = rec
    return dict(out)


# --- cache --------------------------------------------------------------------------


@dataclasses.dataclass
class QuoteCache:
    root: pathlib.Path

    def __post_init__(self) -> None:
        (self.root / "quotes").mkdir(parents=True, exist_ok=True)
        (self.root / "items").mkdir(parents=True, exist_ok=True)

    def quotes_path(self, month: str) -> pathlib.Path:
        return self.root / "quotes" / f"{month}.csv.gz"

    def items_path(self, month: str) -> pathlib.Path:
        return self.root / "items" / f"{month}.json"

    def months(self) -> list[str]:
        return sorted(p.name[:6] for p in (self.root / "quotes").glob("*.csv.gz"))

    def write_quotes(self, month: str, rows: list[dict[str, object]]) -> None:
        with gzip.open(self.quotes_path(month), "wt", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=NORMALISED_FIELDS)
            w.writeheader()
            w.writerows(rows)

    def read_quotes(self, month: str) -> list[dict[str, object]]:
        with gzip.open(self.quotes_path(month), "rt", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            r["valid"] = r["valid"] == "True"
            for k in ("price", "rel_rpi", "rel_cpi", "base_rpi", "base_cpi", "stratum_weight", "shop_weight"):
                r[k] = float(r[k]) if r[k] not in ("", "None") else None
        return rows

    def write_items(self, month: str, items: dict[str, dict[str, float]]) -> None:
        self.items_path(month).write_text(json.dumps(items, sort_keys=True))

    def read_items(self, month: str) -> dict[str, dict[str, float]] | None:
        p = self.items_path(month)
        return json.loads(p.read_text()) if p.exists() else None

    def item_months(self) -> list[str]:
        return sorted(p.stem for p in (self.root / "items").glob("*.json"))

    def seen_path(self) -> pathlib.Path:
        return self.root / "seen_files.json"

    def seen(self) -> set[str]:
        p = self.seen_path()
        return set(json.loads(p.read_text())) if p.exists() else set()

    def mark_seen(self, name: str) -> None:
        s = self.seen()
        s.add(name)
        self.seen_path().write_text(json.dumps(sorted(s)))


def sync(cache: QuoteCache, *, since_year: int = 2010, session=None, max_files: int | None = None) -> dict:
    """Download every quotes and item/segment-index file not yet in the cache.

    Returns a summary: files fetched, months now cached, failures. A failed file
    is reported, not fatal -- the reconstruction then reports the months it
    lacks, which is the visible version of the same fact.
    """
    import requests

    session = session or requests.Session()
    editions = onsfetch.dataset_editions(session=session)
    seen = cache.seen()
    fetched, failures, skipped = [], [], []
    for uri in editions:
        name = uri.rstrip("/").rsplit("/", 1)[-1]
        years = [int(y) for y in re.findall(r"(20\d\d)", name)]
        if years and max(years) < since_year:
            continue
        is_q = bool(_QUOTE_EDITION.search(name)) and "aggregated" not in name
        is_i = bool(_ITEMIDX_EDITION.search(name))
        if not (is_q or is_i):
            continue
        try:
            files = onsfetch.edition_downloads(uri, session=session)
        except Exception as exc:  # noqa: BLE001
            failures.append({"edition": name, "error": str(exc)})
            continue
        for f in files:
            fname = f["file"]
            low = fname.lower()
            if fname in seen or not (low.endswith(".csv") or low.endswith(".zip")):
                continue
            if max_files is not None and len(fetched) >= max_files:
                return {"fetched": fetched, "failures": failures, "truncated": True,
                        "skipped_duplicate_months": skipped, "months": cache.months()}
            try:
                blob = onsfetch.download(f["url"], session=session)
                for member, text in iter_csv_members(blob, fname):
                    header = text[:400].upper()
                    if "PRICE" in header and "QUOTE_DATE" in header:
                        for month, rows in parse_quotes_text(text).items():
                            # Editions are listed newest first, so the first
                            # file to supply a month is the latest issue of it.
                            # A month is never merged across files: two issues
                            # of the same month would double-count every quote.
                            if cache.quotes_path(month).exists():
                                skipped.append({"file": fname, "month": month})
                                continue
                            cache.write_quotes(month, rows)
                    elif "INDEX_DATE" in header:
                        for month, items in parse_item_weights_text(text).items():
                            cache.write_items(month, items)
                cache.mark_seen(fname)
                fetched.append(fname)
            except Exception as exc:  # noqa: BLE001
                failures.append({"file": fname, "error": f"{type(exc).__name__}: {exc}"})
    return {"fetched": fetched, "failures": failures, "skipped_duplicate_months": skipped,
            "months": cache.months(), "item_months": cache.item_months()}
