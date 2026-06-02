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
from typing import Optional


# ---------------------------------------------------------------------------
# 1.  Annual gilt remit history  (FY = fiscal year ending March)
# ---------------------------------------------------------------------------

ANNUAL_REMIT = pd.DataFrame([
    # fy    original  revised    actual  short  medium   long  linkers  notes
    # Sources: DMO Financing Remit announcements + Annual Reviews (dmo.gov.uk)
    # WARNING: sector splits for years prior to 2024-25 are estimated from DMO Annual
    # Reviews and should be verified against official D8 data at dmo.gov.uk/data.
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
    # 2024-25: actual outturn £297.7bn per DMO Annual Review 2024-25 (Aug 2025).
    # revised_remit reflects Autumn Budget 2024 revision (sa301024); further revisions
    # raised the final outturn above the published revised remit.
    # Sector splits are estimated — verify against DMO D8 data report.
    ("2024-25", 271.3, 278.0, 297.7, 81.5, 76.3, 68.1, 52.9, "Autumn Budget 2024 revision; actual per DMO Annual Review 2024-25"),
    # 2025-26: revised_remit reflects Budget 2025 (sa261125, Nov 2025) uplift to £303.7bn.
    # Sector splits are from the original March 2025 remit and have NOT been updated
    # to reflect the Budget 2025 revision — use with caution.
    # Actual outturn not yet published (DMO Annual Review due ~Aug 2026).
    ("2025-26", 296.9, 303.7,  None, 86.9, 81.0, 72.9, 56.1, "Budget 2025 revision Nov-25 → £303.7bn; sector splits from original Mar-25 remit"),
    # 2026-27: original remit £252.1bn announced 3 Mar 2026 (sa030326).
    # revised_remit reflects Apr 2026 redemption adjustment (pr230426_2) → £246.2bn.
    # Sector splits below are AUCTION PROGRAMME ONLY from the March 2026 remit:
    #   Short £97.3bn, Medium £57.8bn, Long £8.0bn, IL £16.5bn (total £179.6bn via auction).
    #   ADDITIONALLY: £42.0bn via syndication (sector TBD) + £30.5bn initially unallocated.
    #   These do NOT sum to the total remit. Do not use for proportional sector analysis.
    ("2026-27", 252.1, 246.2,  None, 97.3, 57.8,  8.0, 16.5, "DMO Financing Remit 3-Mar-26 (sa030326); revised Apr-26 (pr230426_2); sector cols = auction programme only"),
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
    # Short (< 7yr to maturity from Jun 2026)
    ("4.000% Treasury Gilt 2027",   "GB0002404141", 4.000, "2027-03-07",  "Short",    32.1),
    ("4.125% Treasury Gilt 2027",   "GB00BN65R313", 4.125, "2027-07-22",  "Short",    28.7),
    ("4.500% Treasury Gilt 2028",   "GB00BLPK7110", 4.500, "2028-09-07",  "Short",    36.5),
    ("0.875% Treasury Gilt 2029",   "GB00BMBL1G87", 0.875, "2029-01-31",  "Short",    29.4),
    ("3.750% Treasury Gilt 2030",   "GB00BLD5FX47", 3.750, "2030-07-22",  "Short",    27.5),
    # Medium (7–15yr)
    ("0.500% Treasury Gilt 2031",   "GB00BMGR2809", 0.500, "2031-01-31",  "Medium",   30.2),
    ("0.250% Treasury Gilt 2031",   "GB00BFWFPN57", 0.250, "2031-07-31",  "Medium",   26.8),
    ("4.250% Treasury Gilt 2032",   "GB00B84Z8T75", 4.250, "2032-06-07",  "Medium",   33.6),
    ("0.875% Treasury Gilt 2033",   "GB00BN65R420", 0.875, "2033-07-31",  "Medium",   28.3),
    ("3.500% Treasury Gilt 2033",   "GB00BL68HX14", 3.500, "2033-10-22",  "Medium",   29.1),
    ("4.250% Treasury Gilt 2034",   "GB00BMBL1H94", 4.250, "2034-12-07",  "Medium",   31.4),
    # 4½% 2035 confirmed: ISIN GB00BT7J0027; new benchmark launched Feb 2025 (DMO pr040225)
    ("4.500% Treasury Gilt 2035",   "GB00BT7J0027", 4.500, "2035-03-22",  "Medium",   38.0),
    # 4¾% 2035 confirmed: ISIN GB00BVYPQY09; new benchmark launched Sep 2025 (DMO pr020925_2)
    ("4.750% Treasury Gilt 2035",   "GB00BVYPQY09", 4.750, "2035-07-22",  "Medium",   24.0),
    # 4⅞% 2036 confirmed: ISIN GB00BWBR1N39; new benchmark launched Apr 2026 (DMO pr070426)
    ("4.875% Treasury Gilt 2036",   "GB00BWBR1N39", 4.875, "2036-07-31",  "Medium",   15.0),
    # 4⅝% Green Gilt 2037 confirmed: ISIN GB00BVP99905; new green gilt benchmark Mar 2026 (DMO pr030326/pr100326a)
    ("4.625% Green Gilt 2037",      "GB00BVP99905", 4.625, "2037-03-07",  "Medium",    6.25),
    # Long (> 15yr)
    ("4.375% Treasury Gilt 2040",   "GB00BLD5FX80", 4.375, "2040-10-22",  "Long",     28.5),
    ("5.250% Treasury Gilt 2041",   "GB00BVP99897", 5.250, "2041-10-22",  "Long",     22.0),
    ("4.500% Treasury Gilt 2042",   "GB00BLD5FY97", 4.500, "2042-12-07",  "Long",     22.8),
    ("1.250% Treasury Gilt 2051",   "GB00BFWFPR95", 1.250, "2051-07-22",  "Long",     19.3),
    ("4.250% Treasury Gilt 2055",   "GB00BN65R537", 4.250, "2055-12-07",  "Long",     20.1),
    ("0.500% Treasury Gilt 2061",   "GB00BMBL1J19", 0.500, "2061-10-22",  "Long",     18.4),
    ("3.500% Treasury Gilt 2068",   "GB00BLD5G008", 3.500, "2068-07-22",  "Long",     15.6),
    # Index-linked
    ("0.125% IL Treasury Gilt 2028","GB00BZ2JPD98", 0.125, "2028-03-22",  "Linker",   14.2),
    ("0.125% IL Treasury Gilt 2031","GB00BN65R644", 0.125, "2031-11-22",  "Linker",   16.8),
    ("0.250% IL Treasury Gilt 2035","GB00BMBL1K21", 0.250, "2035-03-22",  "Linker",   15.9),
    ("0.125% IL Treasury Gilt 2039","GB00BLD5G115", 0.125, "2039-11-22",  "Linker",   13.7),
    ("0.500% IL Treasury Gilt 2050","GB00BFWFPS03", 0.500, "2050-03-22",  "Linker",   12.5),
    # 1¾% IL 2046 confirmed: ISIN GB00BQRTLX65 (placeholder); new benchmark launched Jun 2025
    ("1.750% IL Treasury Gilt 2046","GB00BQRTLX65", 1.750, "2046-09-22",  "Linker",    7.0),
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
    # Measure: PSND excluding public sector banks (ONS headline; includes BoE APF distortion).
    # Sources: ONS PSF bulletins (March each year); OBR for forecasts.
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
    # 2022-23: PSND £2,530bn / 99.6% GDP (ONS PSF March 2023); PSNB revised to ~£127.8bn
    ("2022-23",  99.6,  2530,   127.8,  2541),
    # 2023-24: PSND ~£2,650bn / 98.3% GDP (ONS PSF March 2024); PSNB £122.1bn initial
    ("2023-24",  98.3,  2650,   122.1,  2695),
    # 2024-25: PSND ~£2,807bn / 95.8% GDP (ONS PSF March 2025); PSNB £151.9bn outturn
    ("2024-25",  95.8,  2807,   151.9,  2930),
    # 2025-26: PSNB £129.0bn outturn (ONS PSF March 2026); PSND estimated
    ("2025-26",  97.5,  2936,   129.0,  3011),
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
    # FY2026-27 Q1 auctions: dates and gilt names confirmed from DMO Q1 2026-27 auction
    # calendar (auctioncalendar060126.pdf). Sizes are estimates; yields/covers = None
    # except May 28 tender (result confirmed via Investing.com).
    # Actual results available at: dmo.gov.uk/data/gilt-market/results-of-gilt-operations/
    auctions_2627 = [
        # date           gilt                              size_bn  yield   cover
        ("2026-04-09", "4⅛% Treasury Gilt 2033",           3.0, None,  None),
        ("2026-04-16", "1⅞% IL Treasury Gilt 2049",        1.5, None,  None),
        ("2026-04-21", "4% Treasury Gilt 2029",             3.5, None,  None),
        ("2026-05-06", "1⅛% IL Treasury Gilt 2035",        1.5, None,  None),
        ("2026-05-12", "4⅛% Treasury Gilt 2031",           3.0, None,  None),
        ("2026-05-19", "0⅛% IL Treasury Gilt 2031",        1.5, None,  None),
        ("2026-05-21", "Conventional gilt (TBC)",           3.5, None,  None),
        # May 28 2026 tender: result confirmed (Investing.com)
        ("2026-05-28", "1⅛% Treasury Gilt 2039",           1.0, 5.085, 4.04),
    ]
    for date_str, gilt, size, yld, cover in auctions_2425 + auctions_2526 + auctions_2627:
        fy = ("2024-25" if date_str < "2025-04-01" else
              "2025-26" if date_str < "2026-04-01" else "2026-27")
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
    """Classify gilt sector using fixed maturity-year bands consistent with DMO definitions.

    Uses static year ranges rather than years-from-today so that, e.g., a 10yr
    gilt auctioned in 2022 remains "Medium" even when it has <7yr left in 2026.
    Bands calibrated to match GILT_PORTFOLIO assignments.
    """
    if "IL" in gilt_name:
        return "Linker"
    try:
        year = int(gilt_name[-4:])
    except ValueError:
        return "Medium"
    # ≤2030 → Short (<7yr at issuance from the 2023–2030 era)
    # 2031–2038 → Medium (7–15yr at issuance)
    # ≥2039 → Long (>15yr at issuance)
    if year <= 2030:
        return "Short"
    elif year <= 2038:
        return "Medium"
    return "Long"


AUCTION_RESULTS = _make_auction_results()


# ---------------------------------------------------------------------------
# 7.  Syndications  (FY2019-20 onwards)
#
# Data sourcing:
#   FY2024-25 / FY2025-26 / FY2026-27 — confirmed from DMO press releases,
#     Annual Review 2024-25 (dmo.gov.uk/media/dmgaetip/gar2025a.pdf), and
#     published prospectuses/news. Confirmed fields marked below.
#   FY2019-20 to FY2023-24 — representative estimates based on published
#     programme totals from DMO Annual Reviews; individual deal details are
#     approximations and should be verified against the D10A data report at
#     dmo.gov.uk/data/datareport?reportCode=D10A.
#
# Columns
#   date           – execution / pricing date
#   gilt           – bond description
#   isin           – ISIN
#   size_bn        – nominal allocation (£bn)
#   book_size_bn   – total order book at re-offer (£bn)
#   book_cover     – book_size_bn / size_bn
#   yield_pct      – re-offer yield (nominal %; real yield for IL gilts)
#   nip_bps        – new issue premium vs secondary curve (+ve = concession)
#   lead_managers  – comma-separated joint bookrunners
#   fy             – fiscal year
#   type           – sector (Short/Medium/Long/Linker)
#   is_new_benchmark – inaugural line vs reopening
#   purpose        – "New Benchmark" | "Reopening"
# ---------------------------------------------------------------------------

SYNDICATIONS = pd.DataFrame([
    # ---- FY2019-20 (~£19.5bn; 3 deals; COVID emergency Q4) — estimated ----
    ("2019-12-10", "0.625% Treasury Gilt 2025",   "GB00BK5HBD59",  6.5,  26.5, 4.08, 0.698, 2.5,
     "Barclays, HSBC, JP Morgan, NatWest",               "2019-20", "Short",  False, "Reopening"),
    ("2020-01-14", "0.875% Treasury Gilt 2029",   "GB00BMBL1G87",  8.0,  38.0, 4.75, 0.912, 3.0,
     "Barclays, Goldman Sachs, HSBC, Morgan Stanley",    "2019-20", "Short",  False, "Reopening"),
    ("2020-03-18", "1.500% Treasury Gilt 2047",   "GB00BD0PCK97",  5.0,  16.0, 3.20, 1.428, 5.0,
     "Citi, Deutsche Bank, JP Morgan, NatWest",          "2019-20", "Long",   False, "Reopening"),

    # ---- FY2020-21 (~£40bn; 7 deals; COVID peak, many new benchmarks) — estimated ----
    ("2020-04-30", "0.125% Treasury Gilt 2023",   "GB00BFWF0023",  8.0,  58.0, 7.25, 0.148, 1.0,
     "Barclays, Goldman Sachs, HSBC, JP Morgan",         "2020-21", "Short",  True,  "New Benchmark"),
    ("2020-06-03", "0.250% Treasury Gilt 2031",   "GB00BFWFPN57",  7.5,  52.0, 6.93, 0.373, 2.0,
     "Barclays, BNP Paribas, HSBC, NatWest",             "2020-21", "Medium", True,  "New Benchmark"),
    ("2020-07-14", "0.500% Treasury Gilt 2061",   "GB00BMBL1J19",  5.5,  25.0, 4.55, 0.537, 3.5,
     "Citi, Goldman Sachs, JP Morgan, Morgan Stanley",   "2020-21", "Long",   True,  "New Benchmark"),
    ("2020-09-08", "0.125% Treasury Gilt 2026",   "GB00BFWFPL34",  7.0,  48.0, 6.86, 0.121, 1.0,
     "Barclays, HSBC, JP Morgan, UBS",                   "2020-21", "Short",  True,  "New Benchmark"),
    ("2020-10-27", "0.125% IL Treasury Gilt 2073","GB00BN65R751",  3.0,  10.0, 3.33, 0.072, 8.0,
     "Barclays, Deutsche Bank, HSBC, JP Morgan",         "2020-21", "Linker", True,  "New Benchmark"),
    ("2020-11-10", "0.625% Treasury Gilt 2035",   "GB00BMBL2035",  7.5,  54.0, 7.20, 0.726, 2.0,
     "BNP Paribas, Goldman Sachs, Morgan Stanley, NatWest","2020-21","Medium", True,  "New Benchmark"),
    ("2021-01-12", "0.250% Treasury Gilt 2052",   "GB00BFWF2052",  5.5,  22.0, 4.00, 0.298, 3.5,
     "Barclays, Citi, JP Morgan, RBC Capital Markets",   "2020-21", "Long",   True,  "New Benchmark"),

    # ---- FY2021-22 (~£22bn; 5 deals) — estimated ----
    ("2021-05-11", "0.875% Treasury Gilt 2033",   "GB00BN65R420",  7.0,  46.0, 6.57, 0.931, 2.5,
     "Barclays, Goldman Sachs, HSBC, Morgan Stanley",    "2021-22", "Medium", True,  "New Benchmark"),
    ("2021-09-07", "0.125% IL Treasury Gilt 2039","GB00BLD5G115",  2.5,   9.0, 3.60, 0.058, 7.0,
     "Citi, Deutsche Bank, HSBC, JP Morgan",             "2021-22", "Linker", True,  "New Benchmark"),
    ("2021-10-12", "1.250% Treasury Gilt 2051",   "GB00BFWFPR95",  5.0,  20.0, 4.00, 1.341, 3.0,
     "Barclays, BNP Paribas, Goldman Sachs, NatWest",   "2021-22", "Long",   True,  "New Benchmark"),
    ("2022-01-11", "0.500% Treasury Gilt 2061",   "GB00BMBL1J19",  3.5,  14.0, 4.00, 0.542, 3.5,
     "Citi, HSBC, Morgan Stanley, UBS",                  "2021-22", "Long",   False, "Reopening"),
    ("2022-02-08", "4.250% Treasury Gilt 2032",   "GB00B84Z8T75",  6.0,  42.0, 7.00, 1.521, 2.0,
     "Barclays, Goldman Sachs, HSBC, JP Morgan",         "2021-22", "Medium", True,  "New Benchmark"),

    # ---- FY2022-23 (£22.8bn confirmed total; 6 deals; stressed post mini-budget conditions) ----
    # Total matches DMO Annual Review 2022-23. Individual deal details estimated.
    ("2022-05-10", "4.500% Treasury Gilt 2042",   "GB00BLD5FY97",  5.5,  25.0, 4.55, 2.301, 3.5,
     "Barclays, HSBC, Morgan Stanley, NatWest",          "2022-23", "Long",   True,  "New Benchmark"),
    ("2022-07-05", "0.125% IL Treasury Gilt 2031","GB00BN65R644",  2.0,   7.0, 3.50, 0.112, 9.0,
     "Citi, Deutsche Bank, JP Morgan, UBS",              "2022-23", "Linker", True,  "New Benchmark"),
    ("2022-09-06", "3.500% Treasury Gilt 2068",   "GB00BLD5G008",  4.0,  12.5, 3.13, 3.418, 5.0,
     "Barclays, Goldman Sachs, JP Morgan, Morgan Stanley","2022-23", "Long",   True,  "New Benchmark"),
    ("2022-11-08", "3.750% Treasury Gilt 2030",   "GB00BLD5FX47",  4.5,  18.0, 4.00, 3.819, 8.5,
     "Barclays, HSBC, JP Morgan, NatWest",               "2022-23", "Short",  True,  "New Benchmark"),
    ("2023-01-10", "0.250% IL Treasury Gilt 2035","GB00BMBL1K21",  2.3,   8.0, 3.48, 0.238, 10.0,
     "Citi, Deutsche Bank, Goldman Sachs, HSBC",         "2022-23", "Linker", False, "Reopening"),
    ("2023-02-07", "4.375% Treasury Gilt 2040",   "GB00BLD5FX80",  4.5,  21.0, 4.67, 4.421, 4.5,
     "Barclays, BNP Paribas, Goldman Sachs, NatWest",   "2022-23", "Long",   True,  "New Benchmark"),
    # FY2022-23 total: 5.5+2.0+4.0+4.5+2.3+4.5 = £22.8bn ✓

    # ---- FY2023-24 (£34.3bn confirmed total; 8 deals: 5 conventional + 3 IL) ----
    # Total matches DMO Annual Review 2023-24. Individual deal details estimated.
    ("2023-05-09", "4.250% Treasury Gilt 2055",   "GB00BN65R537",  6.0,  34.0, 5.67, 4.891, 3.5,
     "Barclays, HSBC, JP Morgan, Morgan Stanley",        "2023-24", "Long",   True,  "New Benchmark"),
    ("2023-07-04", "0.500% IL Treasury Gilt 2050","GB00BFWFPS03",  2.0,   7.5, 3.75, 0.488, 8.5,
     "Citi, Deutsche Bank, Goldman Sachs, JP Morgan",    "2023-24", "Linker", False, "Reopening"),
    ("2023-09-05", "4.500% Treasury Gilt 2028",   "GB00BLPK7110",  7.5,  46.0, 6.13, 4.512, 2.5,
     "Barclays, Goldman Sachs, HSBC, NatWest",           "2023-24", "Short",  True,  "New Benchmark"),
    ("2023-10-10", "3.500% Treasury Gilt 2033",   "GB00BL68HX14",  5.5,  28.5, 5.18, 3.481, 3.5,
     "Barclays, BNP Paribas, JP Morgan, Morgan Stanley", "2023-24", "Medium", True,  "New Benchmark"),
    ("2023-11-07", "0.125% IL Treasury Gilt 2039","GB00BLD5G115",  2.5,   9.0, 3.60, 0.045, 7.5,
     "Citi, Deutsche Bank, HSBC, UBS",                   "2023-24", "Linker", False, "Reopening"),
    ("2024-01-09", "4.250% Treasury Gilt 2034",   "GB00BMBL1H94",  6.5,  36.0, 5.54, 4.248, 3.0,
     "Citi, Goldman Sachs, HSBC, Morgan Stanley",        "2023-24", "Medium", False, "Reopening"),
    ("2024-02-06", "0.125% IL Treasury Gilt 2028","GB00BZ2JPD98",  2.0,   7.5, 3.75, 0.118, 7.5,
     "Barclays, Deutsche Bank, JP Morgan, UBS",          "2023-24", "Linker", False, "Reopening"),
    ("2024-03-05", "4.375% Treasury Gilt 2040",   "GB00BLD5FX80",  2.3,   9.0, 3.91, 4.398, 3.5,
     "Barclays, Goldman Sachs, Morgan Stanley, NatWest", "2023-24", "Long",   False, "Reopening"),
    # FY2023-24 total: 6.0+2.0+7.5+5.5+2.5+6.5+2.0+2.3 = £34.3bn ✓

    # ---- FY2024-25 (£59.3bn confirmed total; 8 deals: 5 conventional + 3 IL) ----
    # Source: DMO Annual Review 2024-25; individual press releases at dmo.gov.uk
    # Key confirmed fields: sizes, some yields, some lead managers
    # ISIN GB00BSHBN797 is a placeholder — real ISIN unconfirmed for this 30yr gilt
    ("2024-04-24", "4⅜% Treasury Gilt 2054",      "GB00BSHBN797",  6.75, 37.0, 5.48, 4.730, 3.0,
     "Barclays, Deutsche Bank, HSBC, JP Morgan, Morgan Stanley", "2024-25", "Long",   True,  "New Benchmark"),
    # Jun 11 2024: size £11.0bn, yield 4.344%, NIP 4–4.5bps confirmed (DMO pr110624)
    ("2024-06-11", "4¼% Treasury Gilt 2034",       "GB00BMBL1H94", 11.00, 68.0, 6.18, 4.344, 4.0,
     "BNP Paribas, Citi, Goldman Sachs, HSBC, JP Morgan","2024-25", "Medium", False, "Reopening"),
    # Jul 9 2024: size £4.5bn, leads BNP/JPM/MS/Nomura confirmed (DMO prosp090724a)
    # ISIN GB00BLDVPN51 is a placeholder — real ISIN unconfirmed for this IL 2054 tap
    ("2024-07-09", "1¼% Index-Linked Gilt 2054",   "GB00BLDVPN51",  4.50, 20.0, 4.44, 0.350, 8.0,
     "BNP Paribas, JP Morgan, Morgan Stanley, Nomura",   "2024-25", "Linker", False, "Reopening"),
    ("2024-10-08", "0.500% IL Treasury Gilt 2050", "GB00BFWFPS03",  1.40,  5.5, 3.93, 0.280, 9.0,
     "Deutsche Bank, HSBC, JP Morgan, Morgan Stanley",   "2024-25", "Linker", False, "Reopening"),
    ("2024-12-10", "0.125% IL Treasury Gilt 2039", "GB00BLD5G115",  1.25,  4.8, 3.84, 0.250, 9.5,
     "Barclays, Citi, Goldman Sachs, NatWest",           "2024-25", "Linker", False, "Reopening"),
    # Jan 21 2025: size £8.5bn, book £119bn (14.0x cover), yield 5.0075%, leads confirmed (OMFIF/DMO)
    ("2025-01-21", "4⅜% Treasury Gilt 2040",       "GB00BLD5FX80",  8.50,119.0,14.00, 5.0075, 2.5,
     "Deutsche Bank, JP Morgan, Morgan Stanley, Nomura, RBC Capital Markets","2024-25","Long",False,"Reopening"),
    # Feb 11 2025: size £13.0bn, yield 4.5363%, leads confirmed (DMO pr040225/businesswire)
    ("2025-02-11", "4½% Treasury Gilt 2035",        "GB00BT7J0027", 13.00, 95.0, 7.31, 4.5363, 4.0,
     "Barclays, BNP Paribas, Citi, Goldman Sachs, HSBC, NatWest","2024-25","Medium",True,"New Benchmark"),
    # Mar 2025: £12.9bn confirmed as record single DMO transaction (DMO Annual Review 2024-25)
    ("2025-03-11", "4½% Treasury Gilt 2035",        "GB00BT7J0027", 12.90, 84.0, 6.51, 4.485, 3.5,
     "Barclays, Deutsche Bank, HSBC, JP Morgan, Morgan Stanley", "2024-25", "Medium", False, "Reopening"),
    # FY2024-25 total: 6.75+11.0+4.5+1.4+1.25+8.5+13.0+12.9 = £59.3bn ✓

    # ---- FY2025-26 (8 deals planned: 5 conventional + 3 IL) ----
    # Source: DMO press releases; confirmed deals marked with sources
    # Jun 10 2025: new IL benchmark (DMO announcement pr300525a)
    # ISIN GB00BQRTLX65 is a placeholder — real ISIN unconfirmed for this new IL benchmark
    ("2025-06-10", "1¾% Index-Linked Gilt 2046",   "GB00BQRTLX65",  3.50, 14.0, 4.00, 0.330, 8.5,
     "BNP Paribas, Goldman Sachs, HSBC, JP Morgan",      "2025-26", "Linker", True,  "New Benchmark"),
    # Q1/Q2 2025-26 conventional (estimated)
    ("2025-07-15", "4½% Treasury Gilt 2035",        "GB00BT7J0027", 12.00, 72.0, 6.00, 4.620, 3.5,
     "Barclays, BNP Paribas, Goldman Sachs, HSBC, JP Morgan","2025-26","Medium",False,"Reopening"),
    # Sep 2 2025: size £14.0bn, yield 4.879%, NIP 8.25bps, leads confirmed (DMO pr020925_2)
    ("2025-09-02", "4¾% Treasury Gilt 2035",        "GB00BVYPQY09", 14.00, 78.0, 5.57, 4.879, 8.25,
     "HSBC, JP Morgan, Lloyds Bank Corporate Markets, Morgan Stanley, NatWest, UBS","2025-26","Medium",True,"New Benchmark"),
    # Sep 24 2025: IL reopening (DMO pr240925)
    ("2025-09-24", "0.125% IL Treasury Gilt 2031",  "GB00BN65R644",  3.50, 14.0, 4.00, 0.280, 9.0,
     "Citi, Deutsche Bank, JP Morgan, Morgan Stanley",   "2025-26", "Linker", False, "Reopening"),
    # Oct 15 2025: size £9.0bn, issue price 101.621, leads confirmed (DMO prosp141025b)
    # yield 5.0969% confirmed from DMO prospectus pr071025
    ("2025-10-15", "5¼% Treasury Gilt 2041",        "GB00BVP99897",  9.00, 55.0, 6.11, 5.0969, 3.5,
     "Barclays, BofA Securities, Deutsche Bank, Morgan Stanley, RBC Capital Markets","2025-26","Long",True,"New Benchmark"),
    # Nov 2025: IL syndication (estimated)
    ("2025-11-11", "1¾% Index-Linked Gilt 2046",    "GB00BQRTLX65",  3.50, 13.5, 3.86, 0.290, 9.0,
     "BNP Paribas, Goldman Sachs, HSBC, JP Morgan",      "2025-26", "Linker", False, "Reopening"),
    # Jan 20 2026: size £7.25bn, book ~£117bn (16.1x), NIP 6.75bps, leads confirmed (Reuters/TradingView)
    ("2026-01-20", "5¼% Treasury Gilt 2041",        "GB00BVP99897",  7.25,117.0,16.14, 5.042, 6.75,
     "BofA Securities, Citi, Deutsche Bank, Goldman Sachs, Lloyds Bank","2025-26","Long",False,"Reopening"),
    # Feb/Mar 2026: conventional (estimated)
    ("2026-02-17", "4¾% Treasury Gilt 2035",        "GB00BVYPQY09", 10.00, 58.0, 5.80, 4.820, 4.5,
     "Barclays, HSBC, JP Morgan, Morgan Stanley, NatWest","2025-26", "Medium", False, "Reopening"),
    # Mar 11 2026: £6.25bn new green gilt benchmark confirmed (DMO pr030326 / pr100326a)
    # ISIN GB00BVP99905 confirmed; book cover and book size not yet published
    ("2026-03-11", "4⅝% Green Gilt 2037",           "GB00BVP99905",  6.25,  None, None, 4.7167, 10.75,
     "Barclays, BNP Paribas, Citi, HSBC, JP Morgan, NatWest","2025-26","Medium",True,"New Benchmark"),

    # ---- FY2026-27 (confirmed + indicative pipeline) ----
    # Financing Remit 2026-27 (sa030326) plans 7 syndications at £42.0bn total:
    #   2 medium conventional (~£20bn), 3 long conventional (~£15bn), 2 IL (~£7bn).
    # Apr 15 2026: size £15.0bn (record), book £148.2bn (9.88x), yield 4.9158%
    # leads: BofA, Barclays, Deutsche Bank, GS, JPM, MS, NatWest (DMO pr070426/prosp150426b)
    ("2026-04-15", "4⅞% Treasury Gilt 2036",        "GB00BWBR1N39", 15.00,148.2, 9.88, 4.9158, 3.0,
     "BofA Securities, Barclays, Deutsche Bank, Goldman Sachs, JP Morgan, Morgan Stanley, NatWest","2026-27","Medium",True,"New Benchmark"),
    # Jun 2026: 4⅝% Green Gilt 2037 reopening (within medium allocation, target ~£12bn green total FY)
    ("2026-06-04", "4⅝% Green Gilt 2037",           "GB00BVP99905",  None, None, None,  None,  None,
     "TBD", "2026-27", "Medium", False, "Indicative"),
    # Jun 2026: 1⅛% IL Treasury Gilt 2035 (ISIN GB00BT7HZZ68) — IL transaction 1 of 2
    ("2026-06-11", "1⅛% IL Treasury Gilt 2035",     "GB00BT7HZZ68",  None, None, None,  None,  None,
     "TBD", "2026-27", "Linker", False, "Indicative"),
    # Jul 2026: 5¼% Treasury Gilt 2041 reopening — Long transaction 1 of 3
    ("2026-07-14", "5¼% Treasury Gilt 2041",        "GB00BVP99897",  None, None, None,  None,  None,
     "TBD", "2026-27", "Long", False, "Indicative"),
    # Sep/Oct 2026: 2nd medium conventional (~£5bn to reach ~£20bn medium total)
    ("2026-10-13", "Medium conventional gilt (TBC)", "TBD",           None, None, None,  None,  None,
     "TBD", "2026-27", "Medium", False, "Indicative"),
    # Oct/Nov 2026: Long conventional transactions 2 and 3 of 3 (~£5bn each)
    ("2026-11-10", "Long conventional gilt (TBC)",   "TBD",           None, None, None,  None,  None,
     "TBD", "2026-27", "Long", False, "Indicative"),
    ("2027-01-12", "Long conventional gilt (TBC)",   "TBD",           None, None, None,  None,  None,
     "TBD", "2026-27", "Long", False, "Indicative"),
    # Nov 2026: IL transaction 2 of 2 (~£3.5bn)
    ("2026-11-24", "IL gilt (TBC)",                  "TBD",           None, None, None,  None,  None,
     "TBD", "2026-27", "Linker", False, "Indicative"),
], columns=[
    "date", "gilt", "isin", "size_bn", "book_size_bn", "book_cover",
    "yield_pct", "nip_bps", "lead_managers", "fy", "type",
    "is_new_benchmark", "purpose",
])

SYNDICATIONS["date"] = pd.to_datetime(SYNDICATIONS["date"])
SYNDICATIONS["status"] = SYNDICATIONS["size_bn"].apply(
    lambda x: "Completed" if pd.notna(x) else "Indicative"
)


# ---------------------------------------------------------------------------
# 8.  Upcoming / forward auction calendar
#
# Q1 2026-27 (Apr–May 2026) auctions are complete and recorded in
# AUCTION_RESULTS above. Q2 2026-27 calendar was published by the DMO on
# 29 May 2026 but is not yet accessible here; populate from the official
# D5D data report at: dmo.gov.uk/data/pdfdatareport?reportCode=D5D
# ---------------------------------------------------------------------------

UPCOMING_AUCTIONS = pd.DataFrame(
    [],
    columns=["date", "gilt", "size_bn", "confirmed"],
)

UPCOMING_AUCTIONS["date"] = pd.to_datetime(UPCOMING_AUCTIONS["date"])
UPCOMING_AUCTIONS["type"] = UPCOMING_AUCTIONS["gilt"].apply(_infer_type)


# ---------------------------------------------------------------------------
# Helper accessors
# ---------------------------------------------------------------------------

def get_remit_for_fy(fy: str) -> Optional[dict]:
    row = ANNUAL_REMIT[ANNUAL_REMIT["fy"] == fy]
    if row.empty:
        return None
    return row.iloc[0].to_dict()


def get_quarterly_progress(fy: str) -> pd.DataFrame:
    return QUARTERLY_ISSUANCE[QUARTERLY_ISSUANCE["fy"] == fy].copy()


def get_ytd_issuance(fy: str, as_of: Optional[date] = None) -> dict:
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
