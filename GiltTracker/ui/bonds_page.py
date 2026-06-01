"""
Page 4: Bond Tracker — individual gilt tracking by ISIN.
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from data.store import GiltDataStore
from utils.formatters import fmt_bn, fmt_yield, fmt_cover
from config import SECTOR_COLOURS, PLOTLY_TEMPLATE, CHART_HEIGHT


def render(store: GiltDataStore, selected_fy: str) -> None:
    st.title("🔍 Bond-Level Gilt Tracker")
    st.caption(
        "Search the gilt portfolio, view outstanding amounts, and inspect the "
        "full auction history for any individual gilt."
    )

    portfolio = store.get_portfolio()

    # -----------------------------------------------------------------------
    # Filters sidebar panel
    # -----------------------------------------------------------------------
    with st.sidebar:
        st.subheader("Bond Filters")
        sector_filter = st.multiselect(
            "Sector",
            options=["Short", "Medium", "Long", "Linker"],
            default=["Short", "Medium", "Long", "Linker"],
        )
        maturity_range = st.slider(
            "Maturity Year",
            min_value=2025,
            max_value=2075,
            value=(2025, 2075),
        )
        search_term = st.text_input("Search name / ISIN", "")

    # Apply filters
    filtered = portfolio[portfolio["type"].isin(sector_filter)].copy()
    filtered = filtered[
        (filtered["maturity"].dt.year >= maturity_range[0]) &
        (filtered["maturity"].dt.year <= maturity_range[1])
    ]
    if search_term:
        mask = (
            filtered["name"].str.contains(search_term, case=False, na=False) |
            filtered["isin"].str.contains(search_term, case=False, na=False)
        )
        filtered = filtered[mask]

    # -----------------------------------------------------------------------
    # Portfolio summary metrics
    # -----------------------------------------------------------------------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Gilts", len(filtered))
    c2.metric("Total Outstanding", fmt_bn(filtered["outstanding_bn"].sum()))
    c3.metric("Avg Coupon", f"{filtered['coupon'].mean():.2f}%")
    c4.metric("Avg Years to Maturity",
              f"{filtered['years_to_maturity'].mean():.1f}yr")

    st.divider()

    # -----------------------------------------------------------------------
    # Outstanding by sector bar
    # -----------------------------------------------------------------------
    col_chart, col_table = st.columns([1, 2])

    with col_chart:
        sector_outstanding = (
            filtered.groupby("type")["outstanding_bn"].sum().reset_index()
        )
        colours = [SECTOR_COLOURS.get(s.lower(), "#aaa")
                   for s in sector_outstanding["type"]]
        fig = go.Figure(go.Bar(
            x=sector_outstanding["type"],
            y=sector_outstanding["outstanding_bn"],
            marker_color=colours,
        ))
        fig.update_layout(
            title="Outstanding by Sector",
            yaxis_title="£bn",
            height=280,
            template=PLOTLY_TEMPLATE,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_table:
        # Maturity profile — group by maturity decade
        profile = filtered.copy()
        profile["maturity_decade"] = (profile["maturity"].dt.year // 10 * 10).astype(str) + "s"
        decade_summary = (
            profile.groupby("maturity_decade")["outstanding_bn"]
            .sum()
            .reset_index()
            .sort_values("maturity_decade")
        )
        fig2 = go.Figure(go.Bar(
            x=decade_summary["maturity_decade"],
            y=decade_summary["outstanding_bn"],
            marker_color="#1f77b4",
        ))
        fig2.update_layout(
            title="Outstanding by Maturity Decade",
            yaxis_title="£bn",
            height=280,
            template=PLOTLY_TEMPLATE,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig2, use_container_width=True)

    st.divider()

    # -----------------------------------------------------------------------
    # Main bond table
    # -----------------------------------------------------------------------
    st.subheader(f"Gilt Portfolio ({len(filtered)} bonds)")

    display = filtered[[
        "name", "isin", "coupon", "maturity",
        "type", "outstanding_bn", "years_to_maturity"
    ]].copy()
    display["maturity"] = display["maturity"].dt.strftime("%d %b %Y")
    display.columns = [
        "Name", "ISIN", "Coupon (%)", "Maturity",
        "Sector", "Outstanding (£bn)", "Years to Maturity"
    ]

    selected_rows = st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Coupon (%)": st.column_config.NumberColumn(format="%.3f%%"),
            "Outstanding (£bn)": st.column_config.NumberColumn(format="£%.1f bn"),
            "Years to Maturity": st.column_config.NumberColumn(format="%.1f yr"),
        }
    )

    # -----------------------------------------------------------------------
    # Bond detail panel
    # -----------------------------------------------------------------------
    selected_indices = (
        selected_rows.selection.rows
        if hasattr(selected_rows, "selection") else []
    )
    if selected_indices:
        sel_row = filtered.iloc[selected_indices[0]]
        st.divider()
        st.subheader(f"🔎 Detail: {sel_row['name']}")

        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric("ISIN", sel_row["isin"])
        d2.metric("Coupon", f"{sel_row['coupon']:.3f}%")
        d3.metric("Maturity", sel_row["maturity"].strftime("%d %b %Y"))
        d4.metric("Sector", sel_row["type"])
        d5.metric("Outstanding", fmt_bn(sel_row["outstanding_bn"]))

        # Auction history for this gilt
        all_auctions = store.get_auction_results()
        gilt_auctions = all_auctions[
            all_auctions["gilt"].str.contains(
                sel_row["name"].split("Gilt")[0].strip() if "Gilt" in sel_row["name"]
                else sel_row["name"][:20],
                case=False, na=False
            )
        ].copy()

        if gilt_auctions.empty:
            st.info("No auction records found for this gilt in the current dataset.")
        else:
            gilt_auctions_sorted = gilt_auctions.sort_values("date")

            col_yield_chart, col_cover_chart = st.columns(2)

            with col_yield_chart:
                fig3 = go.Figure(go.Scatter(
                    x=gilt_auctions_sorted["date"],
                    y=gilt_auctions_sorted["yield_pct"],
                    mode="lines+markers",
                    marker=dict(size=8, color="#1f77b4"),
                    line=dict(color="#1f77b4", width=2),
                    hovertemplate="Date: %{x|%d %b %Y}<br>Yield: %{y:.3f}%",
                ))
                fig3.update_layout(
                    title="Auction Yield History",
                    yaxis_title="Yield (%)",
                    height=280,
                    template=PLOTLY_TEMPLATE,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(t=40, b=20),
                )
                st.plotly_chart(fig3, use_container_width=True)

            with col_cover_chart:
                fig4 = go.Figure(go.Bar(
                    x=gilt_auctions_sorted["date"],
                    y=gilt_auctions_sorted["cover_ratio"],
                    marker_color="#9467bd",
                ))
                fig4.add_hline(y=2.0, line_dash="dash", line_color="#aaaaaa",
                               annotation_text="2.0× threshold")
                fig4.update_layout(
                    title="Bid-to-Cover Ratio",
                    yaxis_title="Cover",
                    height=280,
                    template=PLOTLY_TEMPLATE,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(t=40, b=20),
                )
                st.plotly_chart(fig4, use_container_width=True)

            # Cumulative issuance chart
            gilt_auctions_sorted["cumulative_bn"] = gilt_auctions_sorted["size_bn"].cumsum()
            fig5 = go.Figure(go.Scatter(
                x=gilt_auctions_sorted["date"],
                y=gilt_auctions_sorted["cumulative_bn"],
                mode="lines+markers",
                fill="tozeroy",
                fillcolor="rgba(44,160,44,0.2)",
                line=dict(color="#2ca02c", width=2),
            ))
            fig5.update_layout(
                title="Cumulative Issuance",
                yaxis_title="£bn",
                height=250,
                template=PLOTLY_TEMPLATE,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig5, use_container_width=True)

            # Auction history table
            st.dataframe(
                gilt_auctions_sorted[["date", "size_bn", "yield_pct", "cover_ratio", "fy"]]
                .rename(columns={
                    "date": "Date", "size_bn": "Size (£bn)",
                    "yield_pct": "Yield (%)", "cover_ratio": "Cover",
                    "fy": "FY"
                })
                .assign(Date=lambda d: d["Date"].dt.strftime("%d %b %Y"))
                .sort_values("Date", ascending=False),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Size (£bn)": st.column_config.NumberColumn(format="£%.2f bn"),
                    "Yield (%)": st.column_config.NumberColumn(format="%.3f%%"),
                    "Cover": st.column_config.NumberColumn(format="%.2f×"),
                }
            )
