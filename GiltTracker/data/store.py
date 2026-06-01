"""
Central data store. Merges embedded historical data with any live
data successfully fetched, caching everything in Streamlit session state.
"""

import pandas as pd
import numpy as np
from datetime import date, datetime
from typing import Optional

from data.historical import (
    ANNUAL_REMIT,
    QUARTERLY_ISSUANCE,
    GILT_PORTFOLIO,
    PSND_DATA,
    OBR_FORECASTS,
    AUCTION_RESULTS,
    UPCOMING_AUCTIONS,
    get_remit_for_fy,
    get_quarterly_progress,
    get_ytd_issuance,
)
from data.live_fetcher import refresh_all_live_data


CURRENT_FY = "2025-26"
NEXT_FY = "2026-27"


class GiltDataStore:
    """Single source of truth for the application."""

    def __init__(self):
        self.annual_remit: pd.DataFrame = ANNUAL_REMIT.copy()
        self.quarterly_issuance: pd.DataFrame = QUARTERLY_ISSUANCE.copy()
        self.gilt_portfolio: pd.DataFrame = GILT_PORTFOLIO.copy()
        self.psnd: pd.DataFrame = PSND_DATA.copy()
        self.obr_forecasts: pd.DataFrame = OBR_FORECASTS.copy()
        self.auction_results: pd.DataFrame = AUCTION_RESULTS.copy()
        self.upcoming_auctions: pd.DataFrame = UPCOMING_AUCTIONS.copy()
        self.last_refresh: Optional[datetime] = None
        self.live_status: dict = {}

    # ------------------------------------------------------------------
    # Live refresh
    # ------------------------------------------------------------------

    def refresh(self) -> dict:
        live = refresh_all_live_data()
        self.last_refresh = datetime.now()
        self.live_status = live
        # Future: merge live data into DataFrames when parsing is confirmed
        return live

    # ------------------------------------------------------------------
    # Remit accessors
    # ------------------------------------------------------------------

    def get_remit(self, fy: str) -> Optional[dict]:
        return get_remit_for_fy(fy)

    def get_current_remit(self) -> Optional[dict]:
        return get_remit_for_fy(CURRENT_FY)

    def get_all_remits(self) -> pd.DataFrame:
        return self.annual_remit.copy()

    # ------------------------------------------------------------------
    # Progress tracking
    # ------------------------------------------------------------------

    def get_ytd(self, fy: str, as_of: Optional[date] = None) -> dict:
        return get_ytd_issuance(fy, as_of)

    def get_quarterly_progress(self, fy: str) -> pd.DataFrame:
        return get_quarterly_progress(fy)

    def get_progress_pct(self, fy: str, sector: str = "total") -> float:
        remit = self.get_remit(fy)
        if remit is None:
            return 0.0
        ytd = self.get_ytd(fy)
        remit_val = remit.get("current_remit" if sector == "total" else sector, 0)
        if remit_val == 0:
            return 0.0
        return min(ytd[sector] / remit_val * 100, 100.0)

    # ------------------------------------------------------------------
    # Sector-level data
    # ------------------------------------------------------------------

    def get_sector_history(self) -> pd.DataFrame:
        """Long-form annual sector breakdown for charting."""
        df = self.annual_remit[
            ["fy", "fy_start", "short", "medium", "long", "linkers",
             "current_remit"]
        ].copy()
        df = df.dropna(subset=["short"])
        return df

    def get_sector_proportions(self) -> pd.DataFrame:
        df = self.get_sector_history()
        for col in ["short", "medium", "long", "linkers"]:
            df[f"{col}_pct"] = df[col] / df["current_remit"] * 100
        return df

    # ------------------------------------------------------------------
    # Auction data
    # ------------------------------------------------------------------

    def get_auction_results(self, fy: Optional[str] = None,
                            bond_type: Optional[str] = None) -> pd.DataFrame:
        df = self.auction_results.copy()
        if fy:
            df = df[df["fy"] == fy]
        if bond_type:
            df = df[df["type"] == bond_type]
        return df.sort_values("date", ascending=False)

    def get_upcoming_auctions(self, weeks_ahead: int = 12) -> pd.DataFrame:
        cutoff = pd.Timestamp.today() + pd.Timedelta(weeks=weeks_ahead)
        df = self.upcoming_auctions.copy()
        return df[df["date"] <= cutoff].sort_values("date")

    # ------------------------------------------------------------------
    # Bond portfolio
    # ------------------------------------------------------------------

    def get_portfolio(self, bond_type: Optional[str] = None) -> pd.DataFrame:
        df = self.gilt_portfolio.copy()
        if bond_type:
            df = df[df["type"] == bond_type]
        return df.sort_values(["type", "maturity"])

    # ------------------------------------------------------------------
    # PSND data
    # ------------------------------------------------------------------

    def get_psnd(self, include_forecasts: bool = True) -> pd.DataFrame:
        df = self.psnd.copy()
        if not include_forecasts:
            current_yr = datetime.today().year
            current_fy = f"{current_yr - 1}-{str(current_yr)[2:]}"
            df = df[df["fy"] <= current_fy]
        return df

    # ------------------------------------------------------------------
    # OBR forecasts
    # ------------------------------------------------------------------

    def get_latest_obr_forecast(self) -> pd.DataFrame:
        df = self.obr_forecasts.copy()
        idx = df.groupby("fy")["forecast_date"].idxmax()
        return df.loc[idx].sort_values("fy")

    # ------------------------------------------------------------------
    # Calendar helpers
    # ------------------------------------------------------------------

    def get_calendar_for_fy(self, fy: str) -> pd.DataFrame:
        past = self.auction_results[self.auction_results["fy"] == fy].copy()
        past["status"] = "Completed"
        future = self.upcoming_auctions.copy()
        future["status"] = future["confirmed"].map(
            {True: "Confirmed", False: "Indicative"}
        )
        future["yield_pct"] = None
        future["cover_ratio"] = None
        future["fy"] = fy
        cols = ["date", "gilt", "size_bn", "yield_pct", "cover_ratio",
                "type", "status"]
        all_rows = pd.concat(
            [past[cols], future[cols]], ignore_index=True
        )
        return all_rows.sort_values("date")
