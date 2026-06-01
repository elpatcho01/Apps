"""Global configuration constants for the Gilt Tracker application."""

from datetime import date

# ---------------------------------------------------------------------------
# Fiscal year
# ---------------------------------------------------------------------------

CURRENT_FY = "2026-27"
NEXT_FY = "2027-28"
ALL_FYS = [
    "2010-11", "2011-12", "2012-13", "2013-14", "2014-15",
    "2015-16", "2016-17", "2017-18", "2018-19", "2019-20",
    "2020-21", "2021-22", "2022-23", "2023-24", "2024-25",
    "2025-26", "2026-27",
]

# ---------------------------------------------------------------------------
# Sector definitions (years to maturity AT ISSUANCE)
# ---------------------------------------------------------------------------

SECTORS = {
    "short":   {"label": "Short (<7yr)",    "colour": "#1f77b4", "max_years": 7},
    "medium":  {"label": "Medium (7–15yr)", "colour": "#ff7f0e", "min_years": 7,  "max_years": 15},
    "long":    {"label": "Long (>15yr)",    "colour": "#2ca02c", "min_years": 15},
    "linkers": {"label": "Index-Linked",    "colour": "#9467bd"},
}

SECTOR_COLOURS = {k: v["colour"] for k, v in SECTORS.items()}

# ---------------------------------------------------------------------------
# DMO public data endpoints
# ---------------------------------------------------------------------------

DMO_BASE = "https://www.dmo.gov.uk"
DMO_REMIT_PAGE = f"{DMO_BASE}/responsibilities/gilt-market/annual-remit/"
DMO_OPERATIONS_PAGE = f"{DMO_BASE}/responsibilities/gilt-market/gilt-operations/"
DMO_RESULTS_XLSX = f"{DMO_BASE}/data/pdfdatareport?reportCode=D4B"
DMO_REGISTER_XML = f"{DMO_BASE}/data/XmlDataReport?reportCode=D1A"

# ---------------------------------------------------------------------------
# ONS API (beta) – Public Sector Finance time series
# ---------------------------------------------------------------------------

ONS_API_BASE = "https://api.beta.ons.gov.uk/v1"
ONS_SERIES = {
    "psnd_bn":      "HF6X",   # PSND excl. BoE £bn
    "psnd_pct_gdp": "BKQK",   # PSND as % GDP
    "psnb_bn":      "NMFJ",   # Public sector net borrowing
}

# ---------------------------------------------------------------------------
# OBR
# ---------------------------------------------------------------------------

OBR_EFO_BASE = "https://obr.uk/efo/"

# ---------------------------------------------------------------------------
# Cache TTLs (seconds)
# ---------------------------------------------------------------------------

TTL = {
    "dmo_results":  3_600,    # 1 hour
    "dmo_calendar": 1_800,    # 30 min
    "dmo_register": 86_400,   # 24 hours
    "ons_psnd":     86_400,   # 24 hours
    "obr_forecast": 86_400,   # 24 hours
}

# ---------------------------------------------------------------------------
# Chart / display settings
# ---------------------------------------------------------------------------

CHART_HEIGHT = 400
CHART_HEIGHT_TALL = 550
PLOTLY_TEMPLATE = "plotly_dark"
NAVY = "#003082"
LIGHT_GREY = "#8c9ab0"
