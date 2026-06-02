"""
Page 2: Sector Breakdown — deep-dive into Short / Medium / Long / Index-Linked.
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import numpy as np

from data.store import GiltDataStore
from ui.components import sector_proportion_chart, cover_ratio_chart
from utils.formatters import fmt_bn, fmt_pct
from config import SECTOR_COLOURS, CHART_HEIGHT, PLOTLY_TEMPLATE


_SECTOR_META = {
    "short": {
        "label": "Short Gilts (<7yr)",
        "bond_type": "Short",
        "colour": SECTOR_COLOURS["short"],
        "description": (
            "Conventional gilts with residual maturity below 7 years at issuance. "
            "Typically 3- and 5-year benchmark tenors. "
            "Highest liquidity; preferred by money-market and bank liquidity portfolios."
        ),
    },
    "medium": {
        "label": "Medium Gilts (7–15yr)",
        "bond_type": "Medium",
        "colour": SECTOR_COLOURS["medium"],
        "description": (
            "Conventional gilts with residual maturity between 7 and 15 years. "
            "10-year benchmarks are the global pricing anchor. "
            "Core holding for insurance, pension, and macro funds."
        ),
    },
    "long": {
        "label": "Long Gilts (>15yr)",
        "bond_type": "Long",
        "colour": SECTOR_COLOURS["long"],
        "description": (
            "Conventional gilts with residual maturity above 15 years. "
            "30-year and 50-year benchmarks. "
            "Dominated by LDI (liability-driven investment) demand from DB pension schemes."
        ),
    },
    "linkers": {
        "label": "Index-Linked Gilts",
        "bond_type": "Linker",
        "colour": SECTOR_COLOURS["linkers"],
        "description": (
            "Gilts where principal and coupons are indexed to RPI (pre-2012 issuance) "
            "or CPIH (post-2012). Demand driven by inflation-sensitive pension liabilities "
            "and real-money funds seeking inflation protection."
        ),
    },
}


def _sector_tab(store: GiltDataStore, sector: str, selected_fy: str) -> None:
    meta = _SECTOR_META[sector]
    st.caption(meta["description"])

    # --- Annual history bar
    hist = store.get_sector_history().dropna(subset=[sector])
    fig = go.Figure(go.Bar(
        x=hist["fy"],
        y=hist[sector],
        marker_color=meta["colour"],
        name=meta["label"],
    ))
    fig.update_layout(
        title=f"{meta['label']} Annual Issuance",
        xaxis_title="Fiscal Year", yaxis_title="£bn",
        xaxis=dict(type="category"),
        height=CHART_HEIGHT, template=PLOTLY_TEMPLATE,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)

    # --- Proportion line
    prop = store.get_sector_proportions()
    prop = prop.dropna(subset=[f"{sector}_pct"])
    fig2 = go.Figure(go.Scatter(
        x=prop["fy"],
        y=prop[f"{sector}_pct"],
        mode="lines+markers",
        line=dict(color=meta["colour"], width=2),
        marker=dict(size=7),
        fill="tozeroy",
        fillcolor=meta["colour"] + "33",
    ))
    fig2.update_layout(
        title=f"{meta['label']} Share of Total Remit (%)",
        xaxis_title="Fiscal Year",
        yaxis_title="% of total", yaxis=dict(ticksuffix="%"),
        xaxis=dict(type="category"),
        height=300, template=PLOTLY_TEMPLATE,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig2, use_container_width=True)

    # --- Current FY sector detail
    st.subheader(f"FY{selected_fy} Sector Detail")
    remit = store.get_remit(selected_fy)
    ytd = store.get_ytd(selected_fy)
    if remit:
        sec_remit = remit.get(sector, 0) or 0
        sec_issued = ytd.get(sector, 0)
        pct = sec_issued / sec_remit * 100 if sec_remit else 0
        c1, c2, c3 = st.columns(3)
        c1.metric(f"{meta['label']} Remit", fmt_bn(sec_remit))
        c2.metric("YTD Issued", fmt_bn(sec_issued))
        c3.metric("% Complete", fmt_pct(pct))

        progress_bar_val = min(pct / 100, 1.0)
        st.progress(progress_bar_val, text=f"{fmt_pct(pct)} of {fmt_bn(sec_remit)} remit")

    # --- Auction results for this sector
    st.subheader("Recent Auctions")
    auctions = store.get_auction_results(bond_type=meta["bond_type"])

    if not auctions.empty:
        recent = auctions.head(15)[["date", "gilt", "size_bn", "yield_pct", "cover_ratio"]].copy()
        recent["date"] = recent["date"].dt.strftime("%d %b %Y")
        recent.columns = ["Date", "Gilt", "Size (£bn)", "Yield (%)", "Cover"]
        st.dataframe(
            recent,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Size (£bn)": st.column_config.NumberColumn(format="£%.1f bn"),
                "Yield (%)": st.column_config.NumberColumn(format="%.3f%%"),
                "Cover": st.column_config.NumberColumn(format="%.2f×"),
            }
        )


def render(store: GiltDataStore, selected_fy: str) -> None:
    st.title("📊 Sector Breakdown")
    st.caption(
        "Detailed analysis of gilt issuance split across Short (<7yr), "
        "Medium (7–15yr), Long (>15yr), and Index-Linked sectors."
    )

    # Cross-sector overview
    with st.expander("📈 Cross-Sector Overview", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            prop_df = store.get_sector_proportions()
            st.plotly_chart(sector_proportion_chart(prop_df), use_container_width=True)

        with col2:
            # Heatmap of sector allocations
            hist_df = store.get_sector_history().dropna(subset=["short"])
            pivot = hist_df.set_index("fy")[["short", "medium", "long", "linkers"]].tail(10)
            fig = go.Figure(go.Heatmap(
                z=pivot.values.T,
                x=pivot.index.tolist(),
                y=["Short", "Medium", "Long", "Linkers"],
                colorscale="Blues",
                text=[[fmt_bn(v) for v in row] for row in pivot.values.T],
                texttemplate="%{text}",
                showscale=True,
                colorbar=dict(title="£bn"),
            ))
            fig.update_layout(
                title="Sector Issuance Heatmap (£bn)",
                height=CHART_HEIGHT,
                template=PLOTLY_TEMPLATE,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)

    # Sector correlation matrix
    with st.expander("🔗 Sector Correlation Analysis"):
        hist_df2 = store.get_sector_history().dropna(subset=["short"]).tail(12)
        corr = hist_df2[["short", "medium", "long", "linkers"]].corr()
        fig = px.imshow(
            corr,
            text_auto=".2f",
            color_continuous_scale="RdBu",
            zmin=-1, zmax=1,
            title="Sector Allocation Correlation Matrix",
            template=PLOTLY_TEMPLATE,
        )
        fig.update_layout(height=350, paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Positive correlation indicates sectors that tend to rise/fall together "
            "in the annual remit allocation. Negative correlation suggests substitution "
            "between sectors during DMO remit setting."
        )

    st.divider()

    # Per-sector tabs
    tab_labels = [
        "Short (<7yr)", "Medium (7–15yr)", "Long (>15yr)", "Index-Linked"
    ]
    tabs = st.tabs(tab_labels)
    for tab, sector in zip(tabs, ["short", "medium", "long", "linkers"]):
        with tab:
            _sector_tab(store, sector, selected_fy)
