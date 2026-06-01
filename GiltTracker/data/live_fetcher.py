"""
Attempts to pull live data from DMO, ONS and OBR public endpoints.
Falls back to embedded historical data on any error.

DMO base: https://www.dmo.gov.uk
ONS API: https://api.ons.gov.uk/v1/datasets
"""

import requests
import pandas as pd
import numpy as np
import json
from datetime import datetime, date
from typing import Optional
import io

_TIMEOUT = 10  # seconds per HTTP request
_HEADERS = {"User-Agent": "UKGiltTracker/1.0 (+research)"}


def _safe_get(url: str, **kw) -> Optional[requests.Response]:
    try:
        r = requests.get(url, timeout=_TIMEOUT, headers=_HEADERS, **kw)
        r.raise_for_status()
        return r
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ONS – Public Sector Finance (PSND, borrowing)
# Dataset: public-sector-finances  series: BKFW (PSND ex. BoE £bn)
# ---------------------------------------------------------------------------

ONS_PSND_URL = (
    "https://api.ons.gov.uk/v1/datasets/public-sector-finances/timeseries/"
    "BKFW/data"
)
ONS_BORROW_URL = (
    "https://api.ons.gov.uk/v1/datasets/public-sector-finances/timeseries/"
    "J5II/data"  # PSNB ex. BoE annual
)


def fetch_ons_psnd() -> Optional[pd.DataFrame]:
    r = _safe_get(ONS_PSND_URL)
    if r is None:
        return None
    try:
        payload = r.json()
        annual = payload.get("years", [])
        rows = [
            {"year": int(e["year"]), "psnd_bn": float(e["value"])}
            for e in annual if e.get("value") not in ("", None)
        ]
        if not rows:
            return None
        return pd.DataFrame(rows).set_index("year")
    except Exception:
        return None


def fetch_ons_borrowing() -> Optional[pd.DataFrame]:
    r = _safe_get(ONS_BORROW_URL)
    if r is None:
        return None
    try:
        payload = r.json()
        annual = payload.get("years", [])
        rows = [
            {"year": int(e["year"]), "borrowing_bn": float(e["value"])}
            for e in annual if e.get("value") not in ("", None)
        ]
        if not rows:
            return None
        return pd.DataFrame(rows).set_index("year")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DMO – Gilt remit summary (HTML parsing fallback)
# ---------------------------------------------------------------------------

DMO_REMIT_URL = "https://www.dmo.gov.uk/responsibilities/gilt-market/gilt-issuance/financing-remit/"


def fetch_dmo_remit_summary() -> Optional[dict]:
    """
    Returns a dict with keys total_bn, short_bn, medium_bn, long_bn,
    linkers_bn, fy pulled from DMO's latest remit page.
    Returns None if the page cannot be parsed.
    """
    r = _safe_get(DMO_REMIT_URL)
    if r is None:
        return None
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "lxml")
        tables = soup.find_all("table")
        if not tables:
            return None
        # First table typically has the remit breakdown
        df = pd.read_html(io.StringIO(str(tables[0])))[0]
        # Normalise column names
        df.columns = [str(c).lower().strip() for c in df.columns]
        return {"raw_table": df}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DMO – Auction results CSV export
# ---------------------------------------------------------------------------

DMO_RESULTS_CSV = (
    "https://www.dmo.gov.uk/data/pdfdatareport?reportCode=D1A"
)


def fetch_dmo_auction_results() -> Optional[pd.DataFrame]:
    r = _safe_get(DMO_RESULTS_CSV)
    if r is None:
        return None
    try:
        df = pd.read_csv(io.StringIO(r.text), parse_dates=["Date"])
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
        return df
    except Exception:
        return None


# ---------------------------------------------------------------------------
# OBR – EFO forecast tables (latest published)
# ---------------------------------------------------------------------------

OBR_EFO_URL = "https://obr.uk/efo/economic-and-fiscal-outlook-march-2025/"


def fetch_obr_forecasts() -> Optional[dict]:
    """Light scrape of OBR page to locate forecast table links."""
    r = _safe_get(OBR_EFO_URL)
    if r is None:
        return None
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "lxml")
        xls_links = [
            a["href"] for a in soup.find_all("a", href=True)
            if a["href"].endswith((".xlsx", ".xls"))
        ]
        return {"table_links": xls_links[:5]}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------

def refresh_all_live_data() -> dict:
    """
    Attempt to refresh all live data sources.
    Returns a dict with status flags and any successfully fetched DataFrames.
    """
    results = {
        "timestamp": datetime.now().isoformat(),
        "ons_psnd": None,
        "ons_borrowing": None,
        "dmo_remit": None,
        "dmo_auctions": None,
        "obr_forecasts": None,
        "errors": [],
    }

    for key, fn in [
        ("ons_psnd", fetch_ons_psnd),
        ("ons_borrowing", fetch_ons_borrowing),
        ("dmo_remit", fetch_dmo_remit_summary),
        ("dmo_auctions", fetch_dmo_auction_results),
        ("obr_forecasts", fetch_obr_forecasts),
    ]:
        try:
            results[key] = fn()
        except Exception as e:
            results["errors"].append(f"{key}: {e}")

    return results
