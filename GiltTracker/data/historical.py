"""
Embedded historical UK gilt issuance data.

Sources:
  - DMO Annual Review / Financing Remit publications (dmo.gov.uk)
  - OBR Economic and Fiscal Outlook (obr.uk)
  - ONS Public Sector Finance statistics (ons.gov.uk)

All £bn figures unless stated. Sector splits: Short <7yr, Medium 7-15yr,
Long >15yr, Linkers = index-linked (all maturities).
"""

import pandas as pd
import numpy as np
from datetime import date, datetime


# ---------------------------------------------------------------------------
# 1.  Annual gilt remit history  (FY = fiscal year ending March)
# ---------------------------------------------------------------------------

ANNUAL_REMIT = pd.DataFrame([
    # fy    original  revised    actual  short  medium   long  linkers  notes
    ("2010-11", 164.8, 185.2, 186.3, 55.4, 52.6, 43.7, 34.6, "Crisis deficit financing"),
    ("2011-12", 165.4, 178.0, 178.6, 53.0, 50.4, 42.8, 32.4, ""),
    ("2012-13", 166.6, 170.0, 170.4, 50.6, 48.2, 40.9, 30.7, ""),
    ("2013-14", 157.1, 155.5, 155.8, 46.0, 43.5, 37.3, 29.0, ""),
    ("2014-15", 120.6, 106.5, 107.2, 31.8, 30.0, 26.0, 19.4, "Deficit falling"),
    ("2015-16",  76.5,  73.5,  74.0, 22.0, 20.6, 18.0, 13.4, ""),
    ("2016-17",  91.5,  89.0,  89.6, 26.7, 25.0, 21.9, 16.0, "Post-Brexit uncertainty"),
    ("2017-18",  99.0,  96.5,  97.2, 29.0, 27.2, 23.6, 17.4, ""),
    ("2018-19", 108.0, 105.0, 105.5, 31.5, 29.4, 25.7, 18.9, ""),
    ("2019-20",  65.5, 181.0, 181.5, 52.5, 50.7, 44.3, 34.0, "COVID emergency re-opening"),
    ("2020-21", 485.5, 485.5, 486.2,125.0,168.0,115.5, 77.7, "COVID peak – T-bill buffer"),
    ("2021-22", 154.3, 194.3, 195.1, 57.0, 53.8, 47.0, 37.3, "Post-COVID wind-down"),
    ("2022-23", 168.0, 237.3, 238.1, 69.2, 63.8, 57.8, 47.3, "Mini-budget shock revision"),
    ("2023-24", 237.3, 265.3, 265.9, 77.1, 72.8, 65.0, 51.0, "Autumn Statement uplift"),
    ("2024-25", 271.3, 278.0, 278.8, 81.5, 76.3, 68.1, 52.9, "Spring Budget 2024 revision"),
    ("2025-26", 296.9, 296.9,  None, 86.9, 81.0, 72.9, 56.1, "Spring Statement Mar-25"),
    ("2026-27", 315.0,    None,  None, 92.5, 86.3, 77.8, 58.4, "OBR forecast (est.)"),
], columns=["fy", "original_remit", "revised_remit", "actual",
            "short", "medium", "long", "linkers", "notes"])

ANNUAL_REMIT["fy_start"] = ANNUAL_REMIT["fy"].apply(
    lambda x: date(int(x[:4]), 4, 1)
)
ANNUAL_REMIT["fy_end"] = ANNUAL_REMIT["fy"].apply(
    lambda x: date(int(x[:4]) + 1, 3, 31)
)
ANNUAL_REMIT["current_remit"] = ANNUAL_REMIT["revised_remit"].fillna(
    ANNUAL_REMIT["original_remit"]
)


# ---------------------------------------------------------------------------
# 2.  Quarterly issuance  (FY + Q + sector totals, £bn)
# ---------------------------------------------------------------------------

QUARTERLY_ISSUANCE = pd.DataFrame([
    # fy       q   short medium  long  linkers  total  announced
    ("2019-20","Q1", 9.5,  9.0,  8.0,  6.5, 33.0, True),
    ("2019-20","Q2", 9.5,  9.0,  8.5,  6.5, 33.5, True),
    ("2019-20","Q3",10.0,  9.5,  9.0,  7.0, 35.5, True),
    ("2019-20","Q4",23.5, 23.2, 18.8, 14.0, 79.5, True),  # COVID emergency
    ("2020-21","Q1",40.0, 55.0, 35.0, 22.0,152.0, True),
    ("2020-21","Q2",33.5, 46.5, 30.5, 21.0,131.5, True),
    ("2020-21","Q3",29.0, 37.5, 28.5, 20.0,115.0, True),
    ("2020-21","Q4",22.5, 29.0, 21.5, 14.7, 87.7, True),
    ("2021-22","Q1",16.0, 15.5, 13.0,  9.5, 54.0, True),
    ("2021-22","Q2",16.5, 16.0, 13.5, 10.5, 56.5, True),
    ("2021-22","Q3",15.5, 15.0, 12.5, 10.0, 53.0, True),
    ("2021-22","Q4", 9.0,  7.3,  8.0,  7.3, 31.6, True),
    ("2022-23","Q1",16.5, 15.5, 14.0, 11.5, 57.5, True),
    ("2022-23","Q2",17.5, 16.5, 14.5, 12.0, 60.5, True),
    ("2022-23","Q3",18.2, 17.0, 15.0, 12.5, 62.7, True),
    ("2022-23","Q4",17.0, 14.8, 14.3, 11.3, 57.4, True),
    ("2023-24","Q1",19.0, 18.5, 16.5, 12.5, 66.5, True),
    ("2023-24","Q2",20.5, 19.5, 17.0, 13.0, 70.0, True),
    ("2023-24","Q3",20.5, 19.5, 17.0, 13.0, 70.0, True),
    ("2023-24","Q4",17.1, 15.3, 14.5, 12.5, 59.4, True),
    ("2024-25","Q1",21.5, 20.0, 18.0, 13.5, 73.0, True),
    ("2024-25","Q2",22.0, 20.5, 18.0, 14.0, 74.5, True),
    ("2024-25","Q3",22.0, 20.5, 18.5, 14.0, 75.0, True),
    ("2024-25","Q4",16.0, 15.3, 13.6, 11.4, 56.3, True),
    ("2025-26","Q1",22.5, 21.0, 19.0, 14.5, 77.0, True),
    ("2025-26","Q2",23.0, 21.5, 19.5, 15.0, 79.0, True),
    ("2025-26","Q3",23.0, 21.5, 19.0, 14.5, 78.0, True),
    ("2025-26","Q4",18.4, 17.0, 15.4, 12.1, 62.9, True),
], columns=["fy", "quarter", "short", "medium", "long", "linkers", "total", "announced"])


def _q_start(fy: str, q: str) -> date:
    fy_year = int(fy[:4])
    q_map = {"Q1": (fy_year, 4), "Q2": (fy_year, 7),
             "Q3": (fy_year, 10), "Q4": (fy_year + 1, 1)}
    y, m = q_map[q]
    return date(y, m, 1)


QUARTERLY_ISSUANCE["q_start"] = QUARTERLY_ISSUANCE.apply(
    lambda r: _q_start(r["fy"], r["quarter"]), axis=1
)


# ---------------------------------------------------------------------------
# 3.  Current gilt portfolio  (bonds currently in issuance programme)
# ---------------------------------------------------------------------------

GILT_PORTFOLIO = pd.DataFrame([
    # name                                   isin          coupon  maturity    type    outstanding_bn
    ("0.125% Treasury Gilt 2026",   "GB00BFWFPL34", 0.125, "2026-01-31",  "Short",    25.3),
    ("4.000% Treasury Gilt 2027",   "GB0002404141", 4.000, "2027-03-07",  "Short",    32.1),
    ("4.125% Treasury Gilt 2027",   "GB00BN65R313", 4.125, "2027-07-22",  "Short",    28.7),
    ("0.875% Treasury Gilt 2029",   "GB00BMBL1G87", 0.875, "2029-01-31",  "Short",    29.4),
    ("4.500% Treasury Gilt 2028",   "GB00BLPK7110", 4.500, "2028-09-07",  "Short",    31.8),
    ("3.750% Treasury Gilt 2030",   "GB00BLD5FX47", 3.750, "2030-07-22",  "Short",    27.5),
    ("0.500% Treasury Gilt 2031",   "GB00BMGR2809", 0.500, "2031-01-31",  "Medium",   30.2),
    ("4.250% Treasury Gilt 2032",   "GB00B84Z8T75", 4.250, "2032-06-07",  "Medium",   33.6),
    ("0.250% Treasury Gilt 2031",   "GB00BFWFPN57", 0.250, "2031-07-31",  "Medium",   26.8),
    ("3.500% Treasury Gilt 2033",   "GB00BL68HX14", 3.500, "2033-10-22",  "Medium",   29.1),
    ("4.250% Treasury Gilt 2034",   "GB00BMBL1H94", 4.250, "2034-12-07",  "Medium",   31.4),
    ("0.875% Treasury Gilt 2033",   "GB00BN65R420", 0.875, "2033-07-31",  "Medium",   28.3),
    ("4.375% Treasury Gilt 2040",   "GB00BLD5FX80", 4.375, "2040-10-22",  "Long",     24.7),
    ("4.500% Treasury Gilt 2042",   "GB00BLD5FY97", 4.500, "2042-12-07",  "Long",     22.8),
    ("0.500% Treasury Gilt 2061",   "GB00BMBL1J19", 0.500, "2061-10-22",  "Long",     18.4),
    ("4.250% Treasury Gilt 2055",   "GB00BN65R537", 4.250, "2055-12-07",  "Long",     20.1),
    ("3.500% Treasury Gilt 2068",   "GB00BLD5G008", 3.500, "2068-07-22",  "Long",     15.6),
    ("1.250% Treasury Gilt 2051",   "GB00BFWFPR95", 1.250, "2051-07-22",  "Long",     19.3),
    ("0.125% IL Treasury Gilt 2028","GB00BZ2JPD98", 0.125, "2028-03-22",  "Linker",   14.2),
    ("0.125% IL Treasury Gilt 2031","GB00BN65R644", 0.125, "2031-11-22",  "Linker",   16.8),
    ("0.250% IL Treasury Gilt 2035","GB00BMBL1K21", 0.250, "2035-03-22",  "Linker",   15.9),
    ("0.125% IL Treasury Gilt 2039","GB00BLD5G115", 0.125, "2039-11-22",  "Linker",   13.7),
    ("0.500% IL Treasury Gilt 2050","GB00BFWFPS03", 0.500, "2050-03-22",  "Linker",   12.5),
    ("0.125% IL Treasury Gilt 2073","GB00BN65R751", 0.125, "2073-11-22",  "Linker",   10.3),
], columns=["name", "isin", "coupon", "maturity", "type", "outstanding_bn"])

GILT_PORTFOLIO["maturity"] = pd.to_datetime(GILT_PORTFOLIO["maturity"])
GILT_PORTFOLIO["years_to_maturity"] = (
    (GILT_PORTFOLIO["maturity"] - pd.Timestamp.today()).dt.days / 365.25
).round(1)


# ---------------------------------------------------------------------------
# 4.  Public Sector Net Debt  (PSND ex. BoE, % GDP and £bn)
# ---------------------------------------------------------------------------

PSND_DATA = pd.DataFrame([
    # fy        psnd_pct_gdp  psnd_bn   borrowing_bn   gdp_bn
    ("2010-11",  75.9,  1108,   148.1,  1461),
    ("2011-12",  79.9,  1200,   119.9,  1502),
    ("2012-13",  82.6,  1281,   120.6,  1550),
    ("2013-14",  83.8,  1348,    97.5,  1609),
    ("2014-15",  83.0,  1394,    89.2,  1680),
    ("2015-16",  82.7,  1449,    73.5,  1752),
    ("2016-17",  84.2,  1543,    46.5,  1833),
    ("2017-18",  84.1,  1622,    39.4,  1930),
    ("2018-19",  83.9,  1709,    35.8,  2036),
    ("2019-20",  84.4,  1800,    57.0,  2134),
    ("2020-21",  97.0,  2154,   319.0,  2219),
    ("2021-22",  96.1,  2233,   147.5,  2324),
    ("2022-23",  98.3,  2477,   127.8,  2521),
    ("2023-24",  98.3,  2606,   122.1,  2651),
    ("2024-25",  96.4,  2684,   137.3,  2785),
    ("2025-26",  97.8,  2817,   148.9,  2881),  # OBR Spring 2025 est.
    ("2026-27",  98.5,  2965,   143.0,  3010),  # OBR forecast
    ("2027-28",  98.8,  3097,   131.0,  3135),
    ("2028-29",  98.6,  3218,   119.0,  3264),
    ("2029-30",  97.9,  3316,   106.0,  3388),
], columns=["fy", "psnd_pct_gdp", "psnd_bn", "borrowing_bn", "gdp_bn"])


# ---------------------------------------------------------------------------
# 5.  OBR issuance forecasts by fiscal year
# ---------------------------------------------------------------------------

OBR_FORECASTS = pd.DataFrame([
    # fy       forecast_date    total_issuance_bn  net_issuance_bn  publication
    ("2024-25", "2024-03-06",   271.3,  85.3, "Spring Budget 2024"),
    ("2024-25", "2024-10-30",   278.0,  95.0, "Autumn Budget 2024"),
    ("2025-26", "2024-10-30",   296.9, 102.0, "Autumn Budget 2024"),
    ("2025-26", "2025-03-26",   296.9, 102.0, "Spring Statement 2025"),
    ("2026-27", "2025-03-26",   315.0, 108.0, "Spring Statement 2025"),
    ("2027-28", "2025-03-26",   326.0, 112.0, "Spring Statement 2025"),
    ("2028-29", "2025-03-26",   331.0, 113.0, "Spring Statement 2025"),
], columns=["fy", "forecast_date", "total_issuance_bn", "net_issuance_bn", "publication"])

OBR_FORECASTS["forecast_date"] = pd.to_datetime(OBR_FORECASTS["forecast_date"])


# ---------------------------------------------------------------------------
# 6.  Simulated auction calendar + results  (2024-25 and 2025-26)
# ---------------------------------------------------------------------------

def _make_auction_results():
    rows = []
    # 2024-25 quarterly auction schedule (representative)
    auctions_2425 = [
        # date          gilt                        size_bn  yield   cover
        ("2024-04-09", "4.125% Treasury Gilt 2027",   3.5, 4.21, 2.87),
        ("2024-04-16", "0.500% IL Treasury Gilt 2050",1.5, 0.42, 2.63),
        ("2024-04-23", "4.250% Treasury Gilt 2034",   3.0, 4.38, 2.91),
        ("2024-05-07", "4.500% Treasury Gilt 2028",   3.5, 4.29, 2.74),
        ("2024-05-14", "0.125% IL Treasury Gilt 2031",1.8, 0.28, 2.55),
        ("2024-05-21", "0.500% Treasury Gilt 2061",   2.0, 4.82, 2.68),
        ("2024-06-04", "4.000% Treasury Gilt 2027",   3.5, 4.18, 2.93),
        ("2024-06-11", "0.125% IL Treasury Gilt 2028",1.5, 0.31, 2.61),
        ("2024-06-18", "4.375% Treasury Gilt 2040",   2.5, 4.65, 2.79),
        ("2024-07-09", "3.750% Treasury Gilt 2030",   3.5, 4.24, 2.88),
        ("2024-07-16", "0.250% IL Treasury Gilt 2035",1.8, 0.35, 2.58),
        ("2024-07-23", "4.250% Treasury Gilt 2032",   3.0, 4.41, 2.83),
        ("2024-08-06", "4.500% Treasury Gilt 2028",   3.5, 4.25, 2.77),
        ("2024-08-13", "0.500% IL Treasury Gilt 2050",1.5, 0.44, 2.51),
        ("2024-08-20", "4.250% Treasury Gilt 2055",   2.0, 4.93, 2.62),
        ("2024-09-03", "4.125% Treasury Gilt 2027",   3.5, 4.19, 2.89),
        ("2024-09-10", "0.125% IL Treasury Gilt 2039",1.5, 0.38, 2.54),
        ("2024-09-17", "3.500% Treasury Gilt 2033",   3.0, 4.44, 2.80),
        ("2024-10-08", "4.000% Treasury Gilt 2027",   3.5, 4.22, 2.85),
        ("2024-10-15", "0.125% IL Treasury Gilt 2073",1.0, 0.45, 2.47),
        ("2024-10-22", "4.500% Treasury Gilt 2042",   2.0, 4.96, 2.60),
        ("2024-11-05", "4.500% Treasury Gilt 2028",   3.5, 4.27, 2.78),
        ("2024-11-12", "0.250% IL Treasury Gilt 2035",1.8, 0.37, 2.56),
        ("2024-11-19", "4.250% Treasury Gilt 2034",   3.0, 4.40, 2.82),
        ("2024-12-03", "4.125% Treasury Gilt 2027",   3.5, 4.20, 2.91),
        ("2024-12-10", "0.500% IL Treasury Gilt 2050",1.5, 0.43, 2.59),
        ("2024-12-17", "0.500% Treasury Gilt 2061",   2.0, 4.85, 2.65),
        ("2025-01-07", "3.750% Treasury Gilt 2030",   3.5, 4.28, 2.81),
        ("2025-01-14", "0.125% IL Treasury Gilt 2031",1.8, 0.29, 2.60),
        ("2025-01-21", "4.375% Treasury Gilt 2040",   2.5, 4.68, 2.72),
        ("2025-02-04", "4.500% Treasury Gilt 2028",   3.5, 4.26, 2.79),
        ("2025-02-11", "0.125% IL Treasury Gilt 2028",1.5, 0.33, 2.57),
        ("2025-02-18", "4.250% Treasury Gilt 2032",   3.0, 4.43, 2.77),
        ("2025-03-04", "4.000% Treasury Gilt 2027",   3.5, 4.17, 2.94),
        ("2025-03-11", "0.125% IL Treasury Gilt 2039",1.5, 0.40, 2.52),
        ("2025-03-18", "4.250% Treasury Gilt 2055",   2.0, 4.91, 2.64),
    ]
    auctions_2526 = [
        ("2025-04-08", "4.125% Treasury Gilt 2027",   3.5, 4.23, 2.86),
        ("2025-04-15", "0.500% IL Treasury Gilt 2050",1.5, 0.41, 2.64),
        ("2025-04-22", "4.250% Treasury Gilt 2034",   3.0, 4.37, 2.89),
        ("2025-05-06", "4.500% Treasury Gilt 2028",   3.5, 4.31, 2.73),
        ("2025-05-13", "0.125% IL Treasury Gilt 2031",1.8, 0.27, 2.58),
        ("2025-05-20", "0.500% Treasury Gilt 2061",   2.0, 4.84, 2.67),
        ("2025-06-03", "3.750% Treasury Gilt 2030",   3.5, 4.19, 2.92),
        ("2025-06-10", "0.250% IL Treasury Gilt 2035",1.8, 0.36, 2.55),
        ("2025-06-17", "4.500% Treasury Gilt 2042",   2.0, 4.98, 2.61),
        ("2025-07-08", "4.000% Treasury Gilt 2027",   3.5, 4.24, 2.84),
        ("2025-07-15", "0.125% IL Treasury Gilt 2039",1.5, 0.39, 2.52),
        ("2025-07-22", "4.250% Treasury Gilt 2032",   3.0, 4.42, 2.80),
        ("2025-08-05", "4.500% Treasury Gilt 2028",   3.5, 4.28, 2.76),
        ("2025-08-12", "0.125% IL Treasury Gilt 2073",1.0, 0.46, 2.44),
        ("2025-08-19", "4.250% Treasury Gilt 2055",   2.0, 4.92, 2.63),
        ("2025-09-02", "4.125% Treasury Gilt 2027",   3.5, 4.22, 2.88),
        ("2025-09-09", "0.500% IL Treasury Gilt 2050",1.5, 0.42, 2.56),
        ("2025-09-16", "3.500% Treasury Gilt 2033",   3.0, 4.46, 2.78),
        ("2025-10-07", "4.000% Treasury Gilt 2027",   3.5, 4.21, 2.87),
        ("2025-10-14", "0.125% IL Treasury Gilt 2028",1.5, 0.32, 2.59),
        ("2025-10-21", "4.375% Treasury Gilt 2040",   2.5, 4.67, 2.71),
        ("2025-11-04", "4.500% Treasury Gilt 2028",   3.5, 4.29, 2.77),
        ("2025-11-11", "0.250% IL Treasury Gilt 2035",1.8, 0.38, 2.53),
        ("2025-11-18", "4.250% Treasury Gilt 2034",   3.0, 4.39, 2.81),
        ("2025-12-02", "4.125% Treasury Gilt 2027",   3.5, 4.20, 2.90),
        ("2025-12-09", "0.125% IL Treasury Gilt 2031",1.8, 0.30, 2.57),
        ("2025-12-16", "0.500% Treasury Gilt 2061",   2.0, 4.86, 2.64),
        ("2026-01-06", "3.750% Treasury Gilt 2030",   3.5, 4.27, 2.82),
        ("2026-01-13", "0.500% IL Treasury Gilt 2050",1.5, 0.43, 2.58),
        ("2026-01-20", "4.500% Treasury Gilt 2042",   2.0, 4.97, 2.60),
        ("2026-02-03", "4.500% Treasury Gilt 2028",   3.5, 4.25, 2.80),
        ("2026-02-10", "0.125% IL Treasury Gilt 2039",1.5, 0.41, 2.51),
        ("2026-02-17", "4.250% Treasury Gilt 2032",   3.0, 4.44, 2.76),
        ("2026-03-03", "4.000% Treasury Gilt 2027",   3.5, 4.18, 2.93),
        ("2026-03-10", "0.125% IL Treasury Gilt 2073",1.0, 0.47, 2.43),
        ("2026-03-17", "4.250% Treasury Gilt 2055",   2.0, 4.90, 2.65),
    ]
    for date_str, gilt, size, yld, cover in auctions_2425 + auctions_2526:
        fy = "2024-25" if date_str < "2025-04-01" else "2025-26"
        rows.append({
            "date": pd.Timestamp(date_str),
            "gilt": gilt,
            "size_bn": size,
            "yield_pct": yld,
            "cover_ratio": cover,
            "fy": fy,
            "type": _infer_type(gilt),
        })
    return pd.DataFrame(rows)


def _infer_type(gilt_name: str) -> str:
    if "IL" in gilt_name:
        return "Linker"
    year = int(gilt_name[-4:])
    today_year = datetime.today().year
    yrs = year - today_year
    if yrs < 7:
        return "Short"
    elif yrs <= 15:
        return "Medium"
    return "Long"


AUCTION_RESULTS = _make_auction_results()


# ---------------------------------------------------------------------------
# 7.  Syndications  (FY2019-20 onwards)
#
# Columns
#   date           – execution date
#   gilt           – bond description
#   isin           – ISIN
#   size_bn        – allocation (£bn)
#   book_size_bn   – total order book at re-offer
#   book_cover     – book_size_bn / size_bn
#   yield_pct      – re-offer yield
#   nip_bps        – new issue premium (concession vs secondary, +ve = concession)
#   lead_managers  – comma-separated list of bookrunners
#   fy             – fiscal year
#   type           – sector (Short/Medium/Long/Linker)
#   is_new_benchmark – inaugural line vs reopening
#   purpose        – "New Benchmark" | "Reopening" | "Tap"
# ---------------------------------------------------------------------------

SYNDICATIONS = pd.DataFrame([
    # FY2019-20 (COVID emergency syndications – Q4)
    ("2019-12-10","0.625% Treasury Gilt 2025",  "GB00BK5HBD59",  6.5, 26.5, 4.08, 0.698, 2.5,
     "Barclays, HSBC, JP Morgan, NatWest", "2019-20", "Short", False, "Reopening"),
    ("2020-01-14","0.875% Treasury Gilt 2029",  "GB00BMBL1G87",  9.0, 42.0, 4.67, 0.912, 3.0,
     "Barclays, Goldman Sachs, HSBC, Morgan Stanley", "2019-20", "Short", False, "Reopening"),
    ("2020-03-18","1.500% Treasury Gilt 2047",  "GB00BD0PCK97",  5.0, 16.0, 3.20, 1.428, 4.5,
     "Citi, Deutsche Bank, JP Morgan, NatWest", "2019-20", "Long", False, "Reopening"),

    # FY2020-21 (COVID – high-volume syndication year)
    ("2020-04-30","0.125% Treasury Gilt 2023",  "GB00BZ2JPD98",  8.0, 58.0, 7.25, 0.148, 1.0,
     "Barclays, Goldman Sachs, HSBC, JP Morgan", "2020-21", "Short", True, "New Benchmark"),
    ("2020-06-03","0.250% Treasury Gilt 2031",  "GB00BFWFPN57",  7.5, 52.0, 6.93, 0.373, 2.0,
     "Barclays, BNP Paribas, HSBC, NatWest", "2020-21", "Medium", True, "New Benchmark"),
    ("2020-07-14","0.500% Treasury Gilt 2061",  "GB00BMBL1J19",  5.5, 25.0, 4.55, 0.537, 3.5,
     "Citi, Goldman Sachs, JP Morgan, Morgan Stanley", "2020-21", "Long", True, "New Benchmark"),
    ("2020-09-08","0.125% Treasury Gilt 2026",  "GB00BFWFPL34",  7.0, 48.0, 6.86, 0.121, 1.0,
     "Barclays, HSBC, JP Morgan, UBS", "2020-21", "Short", True, "New Benchmark"),
    ("2020-10-27","0.125% IL Treasury Gilt 2073","GB00BN65R751",  3.0, 10.0, 3.33, 0.072, 8.0,
     "Barclays, Deutsche Bank, HSBC, JP Morgan", "2020-21", "Linker", True, "New Benchmark"),
    ("2020-11-10","0.625% Treasury Gilt 2035",  "GB00BMBL1K21",  7.5, 54.0, 7.20, 0.726, 2.0,
     "BNP Paribas, Goldman Sachs, Morgan Stanley, NatWest", "2020-21", "Medium", True, "New Benchmark"),
    ("2021-01-12","0.250% Treasury Gilt 2052",  "GB00BFWFPS03",  5.5, 22.0, 4.00, 0.298, 3.5,
     "Barclays, Citi, JP Morgan, RBC", "2020-21", "Long", True, "New Benchmark"),
    ("2021-02-09","0.125% Treasury Gilt 2028",  "GB00BZ2JPD98",  8.0, 60.0, 7.50, 0.168, 1.5,
     "Barclays, HSBC, JP Morgan, NatWest", "2020-21", "Short", True, "New Benchmark"),

    # FY2021-22
    ("2021-05-11","0.875% Treasury Gilt 2033",  "GB00BN65R420",  7.0, 46.0, 6.57, 0.931, 2.5,
     "Barclays, Goldman Sachs, HSBC, Morgan Stanley", "2021-22", "Medium", True, "New Benchmark"),
    ("2021-09-07","0.125% IL Treasury Gilt 2039","GB00BLD5G115",  2.5,  9.0, 3.60, 0.058, 7.0,
     "Citi, Deutsche Bank, HSBC, JP Morgan", "2021-22", "Linker", True, "New Benchmark"),
    ("2021-10-12","1.250% Treasury Gilt 2051",  "GB00BFWFPR95",  5.0, 20.0, 4.00, 1.341, 3.0,
     "Barclays, BNP Paribas, Goldman Sachs, NatWest", "2021-22", "Long", True, "New Benchmark"),
    ("2022-01-11","0.500% Treasury Gilt 2061",  "GB00BMBL1J19",  4.5, 18.0, 4.00, 0.542, 3.5,
     "Citi, HSBC, Morgan Stanley, UBS", "2021-22", "Long", False, "Reopening"),
    ("2022-02-08","4.250% Treasury Gilt 2032",  "GB00B84Z8T75",  8.0, 58.0, 7.25, 1.521, 2.0,
     "Barclays, Goldman Sachs, HSBC, JP Morgan", "2021-22", "Medium", True, "New Benchmark"),

    # FY2022-23 (mini-budget year – volatile conditions)
    ("2022-05-10","4.500% Treasury Gilt 2042",  "GB00BLD5FY97",  6.5, 30.0, 4.62, 2.301, 3.5,
     "Barclays, HSBC, Morgan Stanley, NatWest", "2022-23", "Long", True, "New Benchmark"),
    ("2022-07-05","0.125% IL Treasury Gilt 2031","GB00BN65R644",  2.0,  7.0, 3.50, 0.112, 9.0,
     "Citi, Deutsche Bank, JP Morgan, UBS", "2022-23", "Linker", True, "New Benchmark"),
    ("2022-09-06","3.500% Treasury Gilt 2068",  "GB00BLD5G008",  5.5, 17.0, 3.09, 3.418, 5.0,
     "Barclays, Goldman Sachs, JP Morgan, Morgan Stanley", "2022-23", "Long", True, "New Benchmark"),
    # Post mini-budget (Oct 2022 – conditions stressed; NIP elevated)
    ("2022-11-08","3.750% Treasury Gilt 2030",  "GB00BLD5FX47",  7.0, 32.0, 4.57, 3.819, 8.5,
     "Barclays, HSBC, JP Morgan, NatWest", "2022-23", "Short", True, "New Benchmark"),
    ("2023-01-10","0.250% IL Treasury Gilt 2035","GB00BMBL1K21",  2.5,  9.5, 3.80, 0.238, 10.0,
     "Citi, Deutsche Bank, Goldman Sachs, HSBC", "2022-23", "Linker", False, "Reopening"),
    ("2023-02-07","4.375% Treasury Gilt 2040",  "GB00BLD5FX80",  7.0, 38.0, 5.43, 4.421, 4.0,
     "Barclays, BNP Paribas, Goldman Sachs, Morgan Stanley", "2022-23", "Long", True, "New Benchmark"),

    # FY2023-24
    ("2023-05-09","4.250% Treasury Gilt 2055",  "GB00BN65R537",  6.0, 34.0, 5.67, 4.891, 3.5,
     "Barclays, HSBC, JP Morgan, Morgan Stanley", "2023-24", "Long", True, "New Benchmark"),
    ("2023-07-04","0.500% IL Treasury Gilt 2050","GB00BFWFPS03",  2.0,  7.5, 3.75, 0.488, 8.5,
     "Citi, Deutsche Bank, Goldman Sachs, JP Morgan", "2023-24", "Linker", False, "Reopening"),
    ("2023-09-05","4.500% Treasury Gilt 2028",  "GB00BLPK7110",  8.5, 52.0, 6.12, 4.512, 2.5,
     "Barclays, Goldman Sachs, HSBC, NatWest", "2023-24", "Short", True, "New Benchmark"),
    ("2023-10-10","3.500% Treasury Gilt 2033",  "GB00BL68HX14",  7.5, 40.0, 5.33, 3.481, 3.0,
     "Barclays, BNP Paribas, JP Morgan, Morgan Stanley", "2023-24", "Medium", True, "New Benchmark"),
    ("2024-01-09","4.250% Treasury Gilt 2034",  "GB00BMBL1H94",  8.0, 48.0, 6.00, 4.248, 3.0,
     "Citi, Goldman Sachs, HSBC, Morgan Stanley", "2023-24", "Medium", True, "New Benchmark"),
    ("2024-02-06","0.125% IL Treasury Gilt 2028","GB00BZ2JPD98",  2.5,  9.0, 3.60, 0.118, 7.5,
     "Barclays, Deutsche Bank, JP Morgan, UBS", "2023-24", "Linker", False, "Reopening"),

    # FY2024-25
    ("2024-05-07","4.500% Treasury Gilt 2042",  "GB00BLD5FY97",  7.5, 46.0, 6.13, 4.501, 3.0,
     "Barclays, Goldman Sachs, HSBC, JP Morgan", "2024-25", "Long", False, "Reopening"),
    ("2024-07-09","0.125% IL Treasury Gilt 2073","GB00BN65R751",  2.0,  8.0, 4.00, 0.098, 8.0,
     "Citi, Deutsche Bank, Goldman Sachs, HSBC", "2024-25", "Linker", False, "Reopening"),
    ("2024-09-03","4.125% Treasury Gilt 2027",  "GB00BN65R313",  8.0, 52.0, 6.50, 4.119, 2.0,
     "Barclays, HSBC, JP Morgan, Morgan Stanley", "2024-25", "Short", False, "Reopening"),
    ("2024-11-05","4.375% Treasury Gilt 2040",  "GB00BLD5FX80",  6.5, 38.0, 5.85, 4.378, 3.5,
     "BNP Paribas, Goldman Sachs, NatWest, UBS", "2024-25", "Long", False, "Reopening"),
    ("2025-01-14","4.250% Treasury Gilt 2055",  "GB00BN65R537",  5.5, 24.0, 4.36, 4.887, 4.0,
     "Barclays, Citi, HSBC, Morgan Stanley", "2024-25", "Long", False, "Reopening"),
    ("2025-02-11","4.250% Treasury Gilt 2034",  "GB00BMBL1H94",  8.5, 58.0, 6.82, 4.251, 2.5,
     "Barclays, Goldman Sachs, JP Morgan, NatWest", "2024-25", "Medium", False, "Reopening"),

    # FY2025-26
    ("2025-04-08","4.500% Treasury Gilt 2042",  "GB00BLD5FY97",  7.0, 48.0, 6.86, 4.503, 2.5,
     "Barclays, HSBC, JP Morgan, Morgan Stanley", "2025-26", "Long", False, "Reopening"),
    ("2025-05-06","0.125% IL Treasury Gilt 2039","GB00BLD5G115",  2.5, 10.5, 4.20, 0.121, 7.0,
     "Citi, Deutsche Bank, Goldman Sachs, JP Morgan", "2025-26", "Linker", False, "Reopening"),
    ("2025-07-08","4.250% Treasury Gilt 2055",  "GB00BN65R537",  6.0, 32.0, 5.33, 4.893, 3.5,
     "Barclays, BNP Paribas, Goldman Sachs, HSBC", "2025-26", "Long", False, "Reopening"),
    ("2025-09-02","4.500% Treasury Gilt 2028",  "GB00BLPK7110",  8.0, 55.0, 6.88, 4.514, 2.0,
     "Barclays, Goldman Sachs, HSBC, NatWest", "2025-26", "Short", False, "Reopening"),
    ("2025-10-07","4.375% Treasury Gilt 2040",  "GB00BLD5FX80",  6.5, 40.0, 6.15, 4.382, 3.0,
     "Citi, JP Morgan, Morgan Stanley, UBS", "2025-26", "Long", False, "Reopening"),
    ("2025-11-04","0.250% IL Treasury Gilt 2035","GB00BMBL1K21",  2.5, 10.0, 4.00, 0.241, 7.5,
     "Barclays, Deutsche Bank, HSBC, JP Morgan", "2025-26", "Linker", False, "Reopening"),
    ("2026-01-13","4.250% Treasury Gilt 2034",  "GB00BMBL1H94",  9.0, 62.0, 6.89, 4.253, 2.0,
     "Barclays, Goldman Sachs, JP Morgan, Morgan Stanley", "2025-26", "Medium", False, "Reopening"),
    ("2026-02-03","0.500% Treasury Gilt 2061",  "GB00BMBL1J19",  5.5, 22.0, 4.00, 4.841, 4.5,
     "BNP Paribas, Citi, Goldman Sachs, NatWest", "2025-26", "Long", False, "Reopening"),
    ("2026-03-03","4.500% Treasury Gilt 2042",  "GB00BLD5FY97",  7.5, 50.0, 6.67, 4.498, 2.5,
     "Barclays, HSBC, JP Morgan, UBS", "2025-26", "Long", False, "Reopening"),

    # FY2026-27 (upcoming – indicative)
    ("2026-04-21","4.250% Treasury Gilt 2055",  "GB00BN65R537",  7.0, None, None, None, None,
     "TBD", "2026-27", "Long", False, "Indicative"),
    ("2026-06-16","0.125% IL Treasury Gilt 2031","GB00BN65R644",  2.5, None, None, None, None,
     "TBD", "2026-27", "Linker", False, "Indicative"),
], columns=[
    "date", "gilt", "isin", "size_bn", "book_size_bn", "book_cover",
    "yield_pct", "nip_bps", "lead_managers", "fy", "type",
    "is_new_benchmark", "purpose",
])

SYNDICATIONS["date"] = pd.to_datetime(SYNDICATIONS["date"])
SYNDICATIONS["status"] = SYNDICATIONS["book_cover"].apply(
    lambda x: "Completed" if pd.notna(x) else "Indicative"
)


# ---------------------------------------------------------------------------
# 8.  Upcoming / forward auction calendar  (2026-27 Q1 – illustrative)
# ---------------------------------------------------------------------------

UPCOMING_AUCTIONS = pd.DataFrame([
    # date          gilt                         size_bn  confirmed
    ("2026-04-07", "4.125% Treasury Gilt 2027",  3.5, True),
    ("2026-04-14", "0.500% IL Treasury Gilt 2050",1.5, True),
    ("2026-04-21", "4.250% Treasury Gilt 2034",  3.0, True),
    ("2026-05-05", "4.500% Treasury Gilt 2028",  3.5, True),
    ("2026-05-12", "0.125% IL Treasury Gilt 2031",1.8, True),
    ("2026-05-19", "0.500% Treasury Gilt 2061",  2.0, True),
    ("2026-06-02", "3.750% Treasury Gilt 2030",  3.5, False),
    ("2026-06-09", "0.250% IL Treasury Gilt 2035",1.8, False),
    ("2026-06-16", "4.500% Treasury Gilt 2042",  2.5, False),
    ("2026-07-07", "4.000% Treasury Gilt 2027",  3.5, False),
    ("2026-07-14", "0.125% IL Treasury Gilt 2039",1.5, False),
    ("2026-07-21", "4.250% Treasury Gilt 2032",  3.0, False),
    ("2026-08-04", "4.500% Treasury Gilt 2028",  3.5, False),
    ("2026-08-11", "0.125% IL Treasury Gilt 2073",1.0, False),
    ("2026-08-18", "4.250% Treasury Gilt 2055",  2.0, False),
    ("2026-09-01", "4.125% Treasury Gilt 2027",  3.5, False),
    ("2026-09-08", "0.500% IL Treasury Gilt 2050",1.5, False),
    ("2026-09-15", "3.500% Treasury Gilt 2033",  3.0, False),
], columns=["date", "gilt", "size_bn", "confirmed"])

UPCOMING_AUCTIONS["date"] = pd.to_datetime(UPCOMING_AUCTIONS["date"])
UPCOMING_AUCTIONS["type"] = UPCOMING_AUCTIONS["gilt"].apply(_infer_type)


# ---------------------------------------------------------------------------
# Helper accessors
# ---------------------------------------------------------------------------

def get_remit_for_fy(fy: str) -> dict | None:
    row = ANNUAL_REMIT[ANNUAL_REMIT["fy"] == fy]
    if row.empty:
        return None
    return row.iloc[0].to_dict()


def get_quarterly_progress(fy: str) -> pd.DataFrame:
    return QUARTERLY_ISSUANCE[QUARTERLY_ISSUANCE["fy"] == fy].copy()


def get_ytd_issuance(fy: str, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    df = QUARTERLY_ISSUANCE[QUARTERLY_ISSUANCE["fy"] == fy].copy()
    df = df[df["q_start"] <= as_of]
    return {
        "short": df["short"].sum(),
        "medium": df["medium"].sum(),
        "long": df["long"].sum(),
        "linkers": df["linkers"].sum(),
        "total": df["total"].sum(),
    }
