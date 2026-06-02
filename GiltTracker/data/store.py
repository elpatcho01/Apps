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
    SYNDICATIONS,
    get_remit_for_fy,
    get_quarterly_progress,
    get_ytd_issuance,
)
from data.live_fetcher import refresh_all_live_data


CURRENT_FY = "2026-27"
NEXT_FY = "2027-28"


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
        self.syndications: pd.DataFrame = SYNDICATIONS.copy()
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
        as_of = as_of or date.today()
        base = get_ytd_issuance(fy, as_of)

        # If quarterly aggregates exist use them; otherwise build from raw results
        if base["total"] > 0:
            return base

        # Fallback: sum confirmed upcoming auctions + completed syndications
        # that fall within this FY and on/before as_of
        fy_year = int(fy[:4])
        fy_start = date(fy_year, 4, 1)
        fy_end = date(fy_year + 1, 3, 31)

        totals: dict = {k: 0.0 for k in ("short", "medium", "long", "linkers", "total")}

        # Confirmed upcoming auctions already executed
        upcoming = self.upcoming_auctions.copy()
        executed = upcoming[
            (upcoming["confirmed"] == True) &
            (upcoming["date"].dt.date >= fy_start) &
            (upcoming["date"].dt.date <= as_of)
        ]
        for _, row in executed.iterrows():
            size = float(row["size_bn"])
            sector = str(row["type"]).lower().replace("linker", "linkers")
            if sector in totals:
                totals[sector] += size
            totals["total"] += size

        # Past auction results that fall in this FY
        results = self.auction_results.copy()
        fy_results = results[
            (results["fy"] == fy) &
            (results["date"].dt.date <= as_of)
        ]
        for _, row in fy_results.iterrows():
            size = float(row["size_bn"])
            sector = str(row["type"]).lower().replace("linker", "linkers")
            if sector in totals:
                totals[sector] += size
            totals["total"] += size

        # Completed syndications in this FY
        synd = self.syndications[
            (self.syndications["fy"] == fy) &
            (self.syndications["status"] == "Completed") &
            (self.syndications["date"].dt.date <= as_of)
        ]
        for _, row in synd.iterrows():
            size = float(row["size_bn"])
            sector = str(row["type"]).lower().replace("linker", "linkers")
            if sector in totals:
                totals[sector] += size
            totals["total"] += size

        return totals

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
        sector_sum = df[["short", "medium", "long", "linkers"]].sum(axis=1)
        df["unallocated_pct"] = (df["current_remit"] - sector_sum).clip(lower=0) / df["current_remit"] * 100
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
        # Recompute years_to_maturity at call time so bonds auto-expire after redemption
        df["years_to_maturity"] = (
            (df["maturity"] - pd.Timestamp.today()).dt.days / 365.25
        ).round(1)
        # Remove bonds that have already matured
        df = df[df["maturity"] >= pd.Timestamp.today()]
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
    # Syndication accessors
    # ------------------------------------------------------------------

    def get_syndications(self, fy: Optional[str] = None,
                         bond_type: Optional[str] = None,
                         completed_only: bool = False) -> pd.DataFrame:
        df = self.syndications.copy()
        if fy:
            df = df[df["fy"] == fy]
        if bond_type:
            df = df[df["type"] == bond_type]
        if completed_only:
            df = df[df["status"] == "Completed"]
        return df.sort_values("date", ascending=False)

    def get_syndication_summary(self, fy: Optional[str] = None) -> pd.DataFrame:
        """Per-FY summary: count, total size, avg book cover, avg NIP."""
        df = self.syndications[self.syndications["status"] == "Completed"].copy()
        if fy:
            df = df[df["fy"] == fy]
        return (
            df.groupby("fy")
            .agg(
                count=("size_bn", "count"),
                total_size_bn=("size_bn", "sum"),
                avg_book_cover=("book_cover", "mean"),
                avg_nip_bps=("nip_bps", "mean"),
                avg_book_size_bn=("book_size_bn", "mean"),
            )
            .reset_index()
            .sort_values("fy")
        )

    def get_lead_manager_league(self, fy: Optional[str] = None) -> pd.DataFrame:
        """Rank banks by number of syndications led."""
        df = self.syndications[self.syndications["status"] == "Completed"].copy()
        if fy:
            df = df[df["fy"] == fy]
        rows = []
        for _, row in df.iterrows():
            for bank in [b.strip() for b in str(row["lead_managers"]).split(",")]:
                if bank and bank != "TBD":
                    rows.append({"bank": bank, "size_bn": row["size_bn"]})
        if not rows:
            return pd.DataFrame(columns=["bank", "deals", "total_size_bn"])
        lg = pd.DataFrame(rows)
        return (
            lg.groupby("bank")
            .agg(deals=("size_bn", "count"), total_size_bn=("size_bn", "sum"))
            .sort_values("deals", ascending=False)
            .reset_index()
        )

    def get_method_split(self, fy: str) -> dict:
        """Return auction vs syndication issuance split for a given FY.

        For completed FYs the quarterly aggregate is authoritative (matches
        the published DMO remit outturn), so auction = quarterly_total - synds.
        For the current / future FY we fall back to summing individual records.
        """
        synd = self.get_syndications(fy=fy, completed_only=True)
        synd_total = float(synd["size_bn"].sum())

        quarterly = self.quarterly_issuance[self.quarterly_issuance["fy"] == fy]
        if not quarterly.empty:
            grand = float(quarterly["total"].sum())
            auction_total = max(grand - synd_total, 0.0)
        else:
            # Current / future FY: sum completed auction results + confirmed
            # upcoming auctions whose date has already passed
            auctions = self.get_auction_results(fy=fy)
            auction_total = float(auctions["size_bn"].sum())
            fy_year = int(fy[:4])
            fy_start = pd.Timestamp(fy_year, 4, 1)
            executed_upcoming = self.upcoming_auctions[
                (self.upcoming_auctions["confirmed"] == True) &
                (self.upcoming_auctions["date"] >= fy_start) &
                (self.upcoming_auctions["date"] <= pd.Timestamp.today())
            ]
            auction_total += float(executed_upcoming["size_bn"].sum())
            grand = auction_total + synd_total

        return {
            "auction_bn": auction_total,
            "syndication_bn": synd_total,
            "total_bn": grand,
            "auction_pct": auction_total / grand * 100 if grand else 0,
            "syndication_pct": synd_total / grand * 100 if grand else 0,
        }

    # ------------------------------------------------------------------
    # Calendar helpers
    # ------------------------------------------------------------------

    def get_calendar_for_fy(self, fy: str) -> pd.DataFrame:
        fy_year = int(fy[:4])
        fy_start = pd.Timestamp(fy_year, 4, 1)
        fy_end = pd.Timestamp(fy_year + 1, 3, 31)

        # Auctions
        past = self.auction_results[self.auction_results["fy"] == fy].copy()
        past["status"] = "Completed"
        past["method"] = "Auction"

        # Only include upcoming auctions whose date falls within this FY
        future_a = self.upcoming_auctions.copy()
        future_a = future_a[
            (future_a["date"] >= fy_start) & (future_a["date"] <= fy_end)
        ]
        future_a["status"] = future_a["confirmed"].map(
            {True: "Confirmed", False: "Indicative"}
        )
        future_a["yield_pct"] = None
        future_a["cover_ratio"] = None
        future_a["fy"] = fy
        future_a["method"] = "Auction"

        # Syndications
        synd_all = self.syndications[self.syndications["fy"] == fy].copy()
        synd_all["cover_ratio"] = synd_all["book_cover"]
        synd_all["method"] = "Syndication"
        synd_all = synd_all.rename(columns={"nip_bps": "_nip"})

        cols = ["date", "gilt", "size_bn", "yield_pct", "cover_ratio",
                "type", "status", "method"]

        # Align columns for concat
        for df in [past, future_a]:
            if "method" not in df.columns:
                df["method"] = "Auction"
        for c in cols:
            if c not in synd_all.columns:
                synd_all[c] = None

        all_rows = pd.concat(
            [past[cols], future_a[cols], synd_all[cols]], ignore_index=True
        )
        return all_rows.sort_values("date")
