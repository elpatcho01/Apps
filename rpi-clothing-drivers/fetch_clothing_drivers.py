#!/usr/bin/env python3
"""Fetch monthly trend-driver series for an RPI clothing & footwear (CHBJ) backtest.

Downloads five free public sources, keeps the raw files untouched, and writes a
tidy merged monthly CSV plus a provenance log:

    clothing_drivers/
      raw/                  untouched downloads, filename carries the download date
      drivers_monthly.csv   one row per month, one column per series
      fetch_log.md          source URL, download time, first/last date, row count

Re-runnable: ``python fetch_clothing_drivers.py``.

Design notes
------------
* Every series is fetched independently. A series that cannot be found or parsed
  is reported loudly and left blank in the CSV -- a different series is never
  substituted for it.
* Missing months are left blank. Nothing is forward-filled.
* Parsing happens entirely in code; the files under raw/ are never rewritten.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "clothing_drivers"
RAW_DIR = OUT_DIR / "raw"

START = pd.Timestamp("2000-01-01")

# The Bank of England rejects requests without a browser User-Agent. Sending one
# everywhere is harmless and keeps the World Bank / NY Fed CDNs happy too.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 120

BOE_DB_URL = (
    "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
    "?csv.x=yes&Datefrom=01/Jan/2000&Dateto=now"
    "&SeriesCodes=XUMAUSS,XUMABK67&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N"
)
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
FRED_TXT_URL = "https://fred.stlouisfed.org/data/{series_id}.txt"
FRED_CHINA_ID = "CHNTOT"
GSCPI_PAGE = "https://www.newyorkfed.org/research/policy/gscpi"
GSCPI_FALLBACK = (
    "https://www.newyorkfed.org/medialibrary/research/interactives/gscpi/downloads/gscpi_data.xlsx"
)
PINKSHEET_PAGE = "https://www.worldbank.org/en/research/commodity-markets"
DMP_PAGE = "https://www.bankofengland.co.uk/decision-maker-panel"

# Column order of the merged CSV.
COLUMNS = [
    "gbpusd",
    "gbp_eri",
    "china_import_px",
    "gscpi",
    "cotton_a",
    "dmp_price_exp",
]

# Sanity ranges for the most recent observation. china_import_px is an index
# with no stable expected level, so it is range-checked only for being finite.
EXPECTED_RANGES = {
    "gbpusd": (1.1, 1.5),
    "gbp_eri": (70.0, 90.0),
    "gscpi": (-2.0, 5.0),
    "cotton_a": (1.0, 4.0),
    "dmp_price_exp": (0.0, 10.0),
}

MAX_STALENESS_MONTHS = 3


# --------------------------------------------------------------------------- #
# Result plumbing
# --------------------------------------------------------------------------- #


@dataclass
class SeriesResult:
    """Outcome of fetching one driver series."""

    column: str
    label: str
    source_url: str | None = None
    page_url: str | None = None
    raw_path: Path | None = None
    downloaded_at: str | None = None
    data: pd.Series | None = None
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.data is not None and not self.data.empty

    def note(self, msg: str) -> None:
        self.notes.append(msg)


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-GB,en;q=0.9",
        }
    )
    return s


def download(session: requests.Session, url: str, filename: str) -> Path:
    """Download *url* into raw/ under *filename*. Returns the saved path."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    if not resp.content:
        raise RuntimeError(f"empty response body from {url}")
    path = RAW_DIR / filename
    path.write_bytes(resp.content)
    return path


def scrape_links(session: requests.Session, page_url: str, pattern: str) -> list[str]:
    """Return absolute hrefs on *page_url* whose URL matches *pattern* (regex, i-flag).

    Results keep page order, de-duplicated, so the caller can prefer the first hit.
    """
    resp = session.get(page_url, timeout=TIMEOUT)
    resp.raise_for_status()
    html = resp.text
    hrefs = re.findall(r"""href\s*=\s*["']([^"']+)["']""", html, flags=re.I)
    rx = re.compile(pattern, re.I)
    out: list[str] = []
    for href in hrefs:
        if not rx.search(href):
            continue
        abs_url = absolutize(href, page_url)
        if abs_url not in out:
            out.append(abs_url)
    return out


def absolutize(href: str, page_url: str) -> str:
    from urllib.parse import urljoin

    return urljoin(page_url, href.strip().replace("&amp;", "&"))


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #


def to_month_start(values: pd.Series) -> pd.Series:
    """Normalise any within-month date convention to the first of that month."""
    return values.dt.to_period("M").dt.to_timestamp()


def parse_dates(values: pd.Series) -> pd.Series:
    """Parse a column of dates, tolerating mixed formats and day-first ordering."""
    try:
        return pd.to_datetime(values, format="mixed", dayfirst=True, errors="coerce")
    except (TypeError, ValueError):
        return pd.to_datetime(values, dayfirst=True, errors="coerce")


def parse_pinksheet_period(values: pd.Series) -> pd.Series:
    """Parse World Bank Pink Sheet period labels such as ``2026M08``."""
    text = values.astype("string").str.strip().str.upper()
    extracted = text.str.extract(r"^(\d{4})M(\d{1,2})$")
    ok = extracted[0].notna()
    out = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    if ok.any():
        iso = extracted.loc[ok, 0] + "-" + extracted.loc[ok, 1].str.zfill(2) + "-01"
        out.loc[ok] = pd.to_datetime(iso, errors="coerce")
    return out


def to_numeric(values: pd.Series) -> pd.Series:
    """Coerce to float, treating FRED's ``.``, blanks and stray text as missing."""
    text = values.astype("string").str.strip()
    text = text.str.replace(",", "", regex=False)
    text = text.mask(text.isin({".", "", "..", "n/a", "N/A", "-", "NA"}))
    return pd.to_numeric(text, errors="coerce")


def cell_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def read_sheet_raw(path: Path, sheet: str | int) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=sheet, header=None, engine="openpyxl")


def find_header_cell(
    raw: pd.DataFrame, predicate: Callable[[str], bool], max_rows: int = 40
) -> tuple[int, int] | None:
    """Find the first (row, col) in the top *max_rows* whose text satisfies *predicate*."""
    limit = min(max_rows, len(raw))
    for r in range(limit):
        for c in range(raw.shape[1]):
            if predicate(cell_text(raw.iat[r, c])):
                return r, c
    return None


def pick_date_column(
    raw: pd.DataFrame, first_data_row: int, parser: Callable[[pd.Series], pd.Series]
) -> int | None:
    """Return the column index that parses as dates for most rows below the header."""
    body = raw.iloc[first_data_row:]
    if body.empty:
        return None
    best: tuple[float, int] | None = None
    for c in range(raw.shape[1]):
        col = body.iloc[:, c]
        if pd.api.types.is_datetime64_any_dtype(col):
            share = float(col.notna().mean())
        elif pd.api.types.is_numeric_dtype(col):
            # Bare numbers are values, not dates. Excel hands real dates over as
            # datetimes or strings, so skipping numerics stops a value column
            # from being mistaken for the date column.
            continue
        else:
            share = float(parser(col).notna().mean())
        if share >= 0.8 and (best is None or share > best[0]):
            best = (share, c)
    return None if best is None else best[1]


def build_series(dates: pd.Series, values: pd.Series, column: str) -> pd.Series:
    """Assemble a clean month-indexed series: valid rows only, deduplicated, sorted."""
    frame = pd.DataFrame({"date": dates, "value": values}).dropna(subset=["date"])
    frame = frame[frame["value"].notna()]
    if frame.empty:
        raise RuntimeError(f"no usable observations parsed for {column}")
    frame["date"] = to_month_start(frame["date"])
    # Keep the last observation if a month somehow appears twice in the source.
    frame = frame.drop_duplicates(subset="date", keep="last").sort_values("date")
    series = frame.set_index("date")["value"].astype(float)
    series.name = column
    return series[series.index >= START]


# --------------------------------------------------------------------------- #
# Series fetchers
# --------------------------------------------------------------------------- #


def fetch_boe_fx(session: requests.Session, stamp: str) -> list[SeriesResult]:
    """XUMAUSS (US$ into sterling) and XUMABK67 (sterling ERI), monthly averages."""
    gbpusd = SeriesResult("gbpusd", "GBP/USD monthly average (BoE XUMAUSS)")
    eri = SeriesResult("gbp_eri", "Sterling effective exchange rate index (BoE XUMABK67)")
    results = [gbpusd, eri]
    for r in results:
        r.source_url = BOE_DB_URL
        r.page_url = "https://www.bankofengland.co.uk/boeapps/database/"

    try:
        downloaded_at = now_iso()
        path = download(session, BOE_DB_URL, f"boe_iadb_xumauss_xumabk67_{stamp}.csv")
        raw = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001 - reported per series
        for r in results:
            r.error = short_error(exc)
        return results

    for r in results:
        r.raw_path = path
        r.downloaded_at = downloaded_at

    if raw.shape[1] < 2:
        for r in results:
            r.error = f"BoE CSV had {raw.shape[1]} column(s); expected a date column plus series"
        return results

    date_col = raw.columns[0]
    dates = parse_dates(raw[date_col])

    for result, code in ((gbpusd, "XUMAUSS"), (eri, "XUMABK67")):
        try:
            col = match_column(raw.columns, code)
            if col is None:
                result.error = (
                    f"series code {code} not present in BoE CSV "
                    f"(columns: {list(raw.columns)})"
                )
                continue
            result.data = build_series(dates, to_numeric(raw[col]), result.column)
            result.note(f"BoE IADB column `{col}` (code {code}), monthly average.")
        except Exception as exc:  # noqa: BLE001
            result.error = short_error(exc)
    return results


def match_column(columns, needle: str) -> str | None:
    needle_l = needle.lower()
    for col in columns:
        if needle_l in str(col).strip().lower():
            return col
    return None


def fetch_fred_china(session: requests.Session, stamp: str) -> SeriesResult:
    """FRED CHNTOT -- US import price index by origin, China."""
    result = SeriesResult("china_import_px", "US import price index, origin China (FRED CHNTOT)")
    url = FRED_CSV_URL.format(series_id=FRED_CHINA_ID)
    result.source_url = url
    result.page_url = f"https://fred.stlouisfed.org/series/{FRED_CHINA_ID}"

    try:
        result.downloaded_at = now_iso()
        path = download(session, url, f"fred_{FRED_CHINA_ID.lower()}_{stamp}.csv")
        result.raw_path = path
        raw = pd.read_csv(path)
        if raw.shape[1] < 2:
            raise RuntimeError(f"FRED CSV had {raw.shape[1]} column(s); expected 2")
        dates = parse_dates(raw[raw.columns[0]])
        values = to_numeric(raw[raw.columns[1]])
        result.data = build_series(dates, values, result.column)
        result.note(f"FRED series ID `{FRED_CHINA_ID}`, monthly index.")
    except Exception as exc:  # noqa: BLE001
        result.error = short_error(exc)
        return result

    # Confirm the ID still points at the series the brief asked for.
    try:
        meta = session.get(FRED_TXT_URL.format(series_id=FRED_CHINA_ID), timeout=TIMEOUT)
        meta.raise_for_status()
        title = meta.text.strip().splitlines()[0].strip() if meta.text.strip() else ""
        result.note(f"FRED title reported as: {title!r}")
        low = title.lower()
        if "import price index" in low and "china" in low:
            result.note("Title check passed (Import Price Index by Origin / China).")
        else:
            result.note(
                "TITLE CHECK FAILED: title does not read as 'Import Price Index by "
                "Origin ... China'. Verify the correct FRED ID before using this column."
            )
    except Exception as exc:  # noqa: BLE001
        result.note(f"Title check unavailable ({short_error(exc)}); data itself downloaded fine.")
    return result


def fetch_gscpi(session: requests.Session, stamp: str) -> SeriesResult:
    """NY Fed Global Supply Chain Pressure Index, monthly."""
    result = SeriesResult("gscpi", "Global Supply Chain Pressure Index (NY Fed)")
    result.page_url = GSCPI_PAGE

    try:
        url = None
        try:
            links = scrape_links(session, GSCPI_PAGE, r"gscpi[^\"']*\.xlsx?$")
            if not links:
                links = scrape_links(session, GSCPI_PAGE, r"\.xlsx?$")
            if links:
                url = links[0]
                result.note(f"Data file link scraped from {GSCPI_PAGE}.")
        except Exception as exc:  # noqa: BLE001
            result.note(f"Could not reach {GSCPI_PAGE} ({short_error(exc)}); trying the fallback URL.")
        if url is None:
            url = GSCPI_FALLBACK
            result.note(
                "No link found on the GSCPI page; used the documented fallback URL. "
                "Verify this is still the current file."
            )
        result.source_url = url
        result.downloaded_at = now_iso()
        suffix = ".xlsx" if url.lower().endswith("x") else ".xls"
        path = download(session, url, f"nyfed_gscpi_{stamp}{suffix}")
        result.raw_path = path
        require_xlsx(path)

        sheets = pd.ExcelFile(path, engine="openpyxl").sheet_names
        sheet = next(
            (s for s in sheets if "month" in s.lower() and "gscpi" in s.lower()),
            next((s for s in sheets if "gscpi" in s.lower()), sheets[0]),
        )
        raw = read_sheet_raw(path, sheet)

        hit = find_header_cell(raw, lambda t: t.lower().replace(" ", "") == "gscpi")
        if hit is None:
            hit = find_header_cell(raw, lambda t: "gscpi" in t.lower())
        if hit is None:
            raise RuntimeError(
                f"no 'GSCPI' column header found on sheet {sheet!r} "
                f"(sheets available: {sheets})"
            )
        header_row, value_col = hit
        first_data_row = header_row + 1
        date_col = pick_date_column(raw, first_data_row, parse_dates)
        if date_col is None or date_col == value_col:
            raise RuntimeError(f"no date column identified on sheet {sheet!r}")

        body = raw.iloc[first_data_row:]
        result.data = build_series(
            parse_dates(body.iloc[:, date_col]),
            to_numeric(body.iloc[:, value_col]),
            result.column,
        )
        result.note(f"Sheet {sheet!r}, GSCPI column index {value_col}, monthly index.")
    except Exception as exc:  # noqa: BLE001
        result.error = short_error(exc)
    return result


def fetch_cotton(session: requests.Session, stamp: str) -> SeriesResult:
    """World Bank Pink Sheet monthly prices, Cotton A Index (US$/kg)."""
    result = SeriesResult("cotton_a", "Cotton A Index, US$/kg (World Bank Pink Sheet)")
    result.page_url = PINKSHEET_PAGE

    try:
        links: list[str] = []
        try:
            links = scrape_links(
                session, PINKSHEET_PAGE, r"(historical[^\"']*monthly|monthly[^\"']*historical)[^\"']*\.xlsx?$"
            )
            if not links:
                links = scrape_links(session, PINKSHEET_PAGE, r"CMO[^\"']*\.xlsx?$")
            if not links:
                links = scrape_links(session, PINKSHEET_PAGE, r"\.xlsx?$")
        except Exception as exc:  # noqa: BLE001 - distinguish "page unreachable" from "link absent"
            raise RuntimeError(
                f"could not reach {PINKSHEET_PAGE} to locate the monthly-prices "
                f"workbook ({short_error(exc)})"
            ) from exc
        if not links:
            raise RuntimeError(
                "could not find the 'Monthly prices' historical xlsx link on "
                f"{PINKSHEET_PAGE} -- the release URL changes, so re-check the page"
            )
        url = links[0]
        result.source_url = url
        result.note(f"Monthly-prices workbook link scraped from {PINKSHEET_PAGE}.")
        result.downloaded_at = now_iso()
        suffix = ".xlsx" if url.lower().endswith("x") else ".xls"
        path = download(session, url, f"worldbank_pinksheet_monthly_{stamp}{suffix}")
        result.raw_path = path
        require_xlsx(path)

        sheets = pd.ExcelFile(path, engine="openpyxl").sheet_names
        sheet = next(
            (s for s in sheets if "month" in s.lower() and "price" in s.lower()),
            next((s for s in sheets if "month" in s.lower()), sheets[0]),
        )
        raw = read_sheet_raw(path, sheet)

        hit = find_header_cell(raw, lambda t: "cotton" in t.lower())
        if hit is None:
            raise RuntimeError(f"no 'Cotton' column header found on sheet {sheet!r}")
        header_row, value_col = hit
        header_label = cell_text(raw.iat[header_row, value_col])

        # Data starts at the first row whose period cell looks like 1960M01.
        period_rows = parse_pinksheet_period(raw.iloc[:, 0])
        data_rows = period_rows[period_rows.notna()]
        if data_rows.empty:
            raise RuntimeError(
                f"no YYYYMmm period labels found in the first column of sheet {sheet!r}"
            )
        first_data_row = int(data_rows.index[0])

        body = raw.iloc[first_data_row:]
        unit = cell_text(raw.iat[header_row + 1, value_col]) if header_row + 1 < len(raw) else ""
        result.data = build_series(
            parse_pinksheet_period(body.iloc[:, 0]),
            to_numeric(body.iloc[:, value_col]),
            result.column,
        )
        result.note(f"Sheet {sheet!r}, column {header_label!r} (unit cell: {unit or 'n/a'}).")
        if unit and "kg" not in unit.lower():
            result.note(
                f"UNIT WARNING: expected US$/kg but the unit cell reads {unit!r}. Check before use."
            )
    except Exception as exc:  # noqa: BLE001
        result.error = short_error(exc)
    return result


# Header patterns for the DMP headline expected own-price growth series.
DMP_REQUIRED = ("price",)
DMP_EXPECT = ("expect",)
DMP_HORIZON = ("year ahead", "year-ahead", "next 12", "12 month", "12-month", "yr ahead")
DMP_THREE_MONTH = ("three-month", "three month", "3-month", "3 month", "3m")


def dmp_header_score(text: str) -> int:
    """Score a header cell as a candidate for expected year-ahead own-price growth.

    Returns 0 for a non-candidate. Higher is better; three-month averages score
    above single-month ones because the DMP headline is the 3-month series.
    """
    low = text.lower()
    if not low or len(low) > 200:
        return 0
    if not all(tok in low for tok in DMP_REQUIRED):
        return 0
    if not any(tok in low for tok in DMP_EXPECT):
        return 0
    if not any(tok in low for tok in DMP_HORIZON):
        return 0
    score = 1
    if "own" in low:
        score += 2
    if any(tok in low for tok in DMP_THREE_MONTH):
        score += 1
    return score


def fetch_dmp(session: requests.Session, stamp: str) -> SeriesResult:
    """BoE Decision Maker Panel, expected year-ahead own-price growth (%)."""
    result = SeriesResult("dmp_price_exp", "DMP expected year-ahead own-price growth, %")
    result.page_url = DMP_PAGE

    try:
        try:
            links = scrape_links(session, DMP_PAGE, r"\.xlsx$")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"could not scrape {DMP_PAGE} ({short_error(exc)})") from exc
        if not links:
            raise RuntimeError(
                f"no .xlsx data link found on {DMP_PAGE} -- locate the latest DMP data file manually"
            )
        url = links[0]
        result.source_url = url
        result.note(f"Data workbook link scraped from {DMP_PAGE}.")
        result.downloaded_at = now_iso()
        path = download(session, url, f"boe_dmp_{stamp}.xlsx")
        result.raw_path = path
        require_xlsx(path)

        sheets = pd.ExcelFile(path, engine="openpyxl").sheet_names
        best: tuple[int, str, int, int] | None = None  # score, sheet, row, col
        seen_price_headers: list[str] = []
        for sheet in sheets:
            raw = read_sheet_raw(path, sheet)
            limit = min(40, len(raw))
            for r in range(limit):
                for c in range(raw.shape[1]):
                    text = cell_text(raw.iat[r, c])
                    if "price" in text.lower() and 0 < len(text) <= 200:
                        seen_price_headers.append(f"{sheet}!r{r}c{c}: {text}")
                    score = dmp_header_score(text)
                    if score and (best is None or score > best[0]):
                        best = (score, sheet, r, c)
        if best is None:
            preview = "; ".join(dict.fromkeys(seen_price_headers))[:800] or "none"
            raise RuntimeError(
                "no 'expected year-ahead own-price growth' column found in the DMP "
                f"workbook (sheets: {sheets}). Price-related headers seen: {preview}"
            )

        _, sheet, header_row, value_col = best
        raw = read_sheet_raw(path, sheet)
        header_label = cell_text(raw.iat[header_row, value_col])
        first_data_row = header_row + 1
        date_col = pick_date_column(raw, first_data_row, parse_dates)
        if date_col is None or date_col == value_col:
            raise RuntimeError(f"no date column identified on DMP sheet {sheet!r}")

        body = raw.iloc[first_data_row:]
        result.data = build_series(
            parse_dates(body.iloc[:, date_col]),
            to_numeric(body.iloc[:, value_col]),
            result.column,
        )
        basis = (
            "three-month moving average"
            if any(tok in header_label.lower() for tok in DMP_THREE_MONTH)
            else "single-month"
        )
        result.note(f"Sheet {sheet!r}, column {header_label!r}.")
        result.note(f"Series basis taken: {basis}.")
    except Exception as exc:  # noqa: BLE001
        result.error = short_error(exc)
    return result


def require_xlsx(path: Path) -> None:
    """Fail clearly on a legacy .xls or an HTML error page saved as a workbook."""
    head = path.read_bytes()[:8]
    if head[:2] == b"PK":
        return
    if head[:4] == b"\xd0\xcf\x11\xe0":
        raise RuntimeError(
            f"{path.name} is a legacy .xls workbook; openpyxl cannot read it "
            "(the source changed format -- update the parser)"
        )
    raise RuntimeError(
        f"{path.name} is not an xlsx file (first bytes: {head!r}) -- "
        "the download was probably an error or consent page"
    )


def short_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}".strip()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Merge, validate, report
# --------------------------------------------------------------------------- #


def merge_series(results: list[SeriesResult]) -> pd.DataFrame:
    """Outer-join every successful series onto a continuous monthly index."""
    good = {r.column: r.data for r in results if r.ok}
    if not good:
        return pd.DataFrame(columns=["date", *COLUMNS])

    last = max(s.index.max() for s in good.values())
    index = pd.date_range(START, last, freq="MS")
    frame = pd.DataFrame(index=index)
    for column in COLUMNS:
        series = good.get(column)
        frame[column] = series.reindex(index) if series is not None else pd.NA
    frame.index.name = "date"
    out = frame.reset_index()
    out["date"] = out["date"].dt.strftime("%Y-%m-01")
    return out


@dataclass
class Check:
    column: str
    status: str  # OK | WARN | FAIL
    message: str


def validate(results: list[SeriesResult], today: dt.date) -> list[Check]:
    checks: list[Check] = []
    cutoff = pd.Timestamp(today).to_period("M").to_timestamp() - pd.DateOffset(
        months=MAX_STALENESS_MONTHS
    )

    for result in results:
        col = result.column
        if not result.ok:
            checks.append(
                Check(col, "FAIL", result.error or "series unavailable -- left blank, not substituted")
            )
            continue

        series = result.data
        assert series is not None

        if series.index.has_duplicates:
            dupes = series.index[series.index.duplicated()].strftime("%Y-%m").tolist()
            checks.append(Check(col, "FAIL", f"duplicate dates: {dupes[:5]}"))
        else:
            checks.append(Check(col, "OK", "no duplicate dates"))

        last_date = series.index.max()
        if last_date < cutoff:
            checks.append(
                Check(
                    col,
                    "WARN",
                    f"last observation {last_date:%Y-%m} is more than "
                    f"{MAX_STALENESS_MONTHS} months old (today {today:%Y-%m})",
                )
            )
        else:
            checks.append(Check(col, "OK", f"up to date (last {last_date:%Y-%m})"))

        rng = EXPECTED_RANGES.get(col)
        if rng is not None:
            latest = float(series.iloc[-1])
            low, high = rng
            if low <= latest <= high:
                checks.append(
                    Check(col, "OK", f"latest value {latest:.4g} within expected {low}-{high}")
                )
            else:
                checks.append(
                    Check(
                        col,
                        "WARN",
                        f"latest value {latest:.4g} outside expected {low}-{high} "
                        "-- check the source column",
                    )
                )
    return checks


def write_log(results: list[SeriesResult], checks: list[Check], today: dt.date) -> str:
    lines: list[str] = []
    lines.append("# Clothing driver fetch log")
    lines.append("")
    lines.append(f"Run date: {today:%Y-%m-%d} (UTC {now_iso()})")
    lines.append(f"History starts: {START:%Y-%m-01} where available. No forward-filling.")
    lines.append("")
    lines.append("## Series")
    lines.append("")
    lines.append("| Column | Rows | First | Last | Downloaded (UTC) | Raw file | Status |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in results:
        if r.ok:
            s = r.data
            assert s is not None
            rows, first, last = len(s), f"{s.index.min():%Y-%m}", f"{s.index.max():%Y-%m}"
            status = "ok"
        else:
            rows, first, last, status = 0, "-", "-", "**NOT FETCHED**"
        raw_name = r.raw_path.name if r.raw_path else "-"
        dl = r.downloaded_at or "-"
        lines.append(f"| `{r.column}` | {rows} | {first} | {last} | {dl} | `{raw_name}` | {status} |")
    lines.append("")

    lines.append("## Sources and notes")
    lines.append("")
    for r in results:
        lines.append(f"### `{r.column}` -- {r.label}")
        lines.append("")
        if r.page_url:
            lines.append(f"- Landing page: {r.page_url}")
        lines.append(f"- Source URL: {r.source_url or 'not resolved'}")
        lines.append(f"- Downloaded: {r.downloaded_at or 'not downloaded'}")
        lines.append(f"- Raw file: `{r.raw_path.name if r.raw_path else 'none'}`")
        if r.ok:
            s = r.data
            assert s is not None
            lines.append(f"- Coverage: {s.index.min():%Y-%m} to {s.index.max():%Y-%m}, {len(s)} rows")
        for note in r.notes:
            lines.append(f"- {note}")
        if r.error:
            lines.append(f"- **SOURCE NOT FOUND / PARSE FAILED:** {r.error}")
            lines.append("- No substitute series was used; the column is left blank.")
        lines.append("")

    lines.append("## Validation")
    lines.append("")
    for c in checks:
        lines.append(f"- [{c.status}] `{c.column}`: {c.message}")
    lines.append("")
    return "\n".join(lines)


def print_summary(results: list[SeriesResult], checks: list[Check], frame: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    for r in results:
        if r.ok:
            s = r.data
            assert s is not None
            print(f"  {r.column:<16} {len(s):>5} rows  {s.index.min():%Y-%m} -> {s.index.max():%Y-%m}")
        else:
            print(f"  {r.column:<16} {'--':>5}        NOT FETCHED: {r.error}")

    print("\nChecks:")
    for c in checks:
        print(f"  [{c.status:<4}] {c.column:<16} {c.message}")

    missing = [r.column for r in results if not r.ok]
    if missing:
        print("\n  !! Source link/parse failed for: " + ", ".join(missing))
        print("     These columns are blank. No substitute series was used.")

    print(f"\nMerged frame: {len(frame)} rows x {len(frame.columns)} columns")
    print(f"Wrote {OUT_DIR / 'drivers_monthly.csv'}")
    print(f"Wrote {OUT_DIR / 'fetch_log.md'}")

    failures = sum(1 for c in checks if c.status == "FAIL")
    warns = sum(1 for c in checks if c.status == "WARN")
    print(f"\n{failures} failure(s), {warns} warning(s).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        help="comma-separated subset of columns to fetch (default: all)",
    )
    args = parser.parse_args(argv)

    today = dt.date.today()
    stamp = today.isoformat()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()

    wanted = {c.strip() for c in args.only.split(",")} if args.only else set(COLUMNS)

    results: list[SeriesResult] = []
    print("Fetching driver series...")

    if {"gbpusd", "gbp_eri"} & wanted:
        print("  - Bank of England XUMAUSS / XUMABK67 ...")
        results.extend(fetch_boe_fx(session, stamp))
    if "china_import_px" in wanted:
        print("  - FRED CHNTOT ...")
        results.append(fetch_fred_china(session, stamp))
    if "gscpi" in wanted:
        print("  - NY Fed GSCPI ...")
        results.append(fetch_gscpi(session, stamp))
    if "cotton_a" in wanted:
        print("  - World Bank Pink Sheet cotton ...")
        results.append(fetch_cotton(session, stamp))
    if "dmp_price_exp" in wanted:
        print("  - BoE Decision Maker Panel ...")
        results.append(fetch_dmp(session, stamp))

    results.sort(key=lambda r: COLUMNS.index(r.column))

    frame = merge_series(results)
    frame.to_csv(OUT_DIR / "drivers_monthly.csv", index=False, na_rep="")

    checks = validate(results, today)
    (OUT_DIR / "fetch_log.md").write_text(write_log(results, checks, today), encoding="utf-8")

    print_summary(results, checks, frame)
    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
