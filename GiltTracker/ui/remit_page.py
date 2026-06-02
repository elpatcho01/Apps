"""
Page 1: Remit Overview — headline dashboard for the current fiscal year.
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from datetime import date

from data.store import GiltDataStore
from ui.components import (
    remit_gauge, sector_history_bar, quarterly_vs_remit_chart,
    fan_chart, kpi_row,
)
from utils.formatters import fmt_bn, fmt_pct, weeks_remaining_in_fy
from config import SECTOR_COLOURS


def render(store: GiltDataStore, selected_fy: str) -> None:
    st.title("UK Gilt Remit Dashboard")
    st.caption(f"Annual issuance remit, sector progress, and pace analysis for FY{selected_fy}.")

    remit = store.get_remit(selected_fy)
    if remit is None:
        st.error(f"No remit data found for {selected_fy}.")
        return

    ytd = store.get_ytd(selected_fy)
    total_remit = remit["current_remit"]
    total_issued = ytd["total"]
    pct_complete = total_issued / total_remit * 100 if total_remit else 0
    weeks_left = weeks_remaining_in_fy(selected_fy)

    st.divider()

    # -----------------------------------------------------------------------
    # KPI row
    # -----------------------------------------------------------------------
    prev_remit_row = store.get_all_remits()
    prev_fy_idx = prev_remit_row[prev_remit_row["fy"] == selected_fy].index
    if len(prev_fy_idx) > 0 and prev_fy_idx[0] > 0:
        prev_fy_row = prev_remit_row.loc[prev_fy_idx[0] - 1]
        prev_remit = prev_fy_row["current_remit"]
        remit_delta = f"{fmt_bn(total_remit - prev_remit)} vs prior year"
    else:
        remit_delta = None

    kpi_row([
        {
            "label": f"FY{selected_fy} Remit",
            "value": fmt_bn(total_remit),
            "delta": remit_delta,
            "help": f"Latest published remit. Original: {fmt_bn(remit['original_remit'])}",
        },
        {
            "label": "YTD Issued",
            "value": fmt_bn(total_issued),
            "delta": fmt_pct(pct_complete) + " of remit",
            "delta_colour": "off",
            "help": "Cumulative issuance in completed quarters this fiscal year.",
        },
        {
            "label": "Remaining",
            "value": fmt_bn(total_remit - total_issued),
            "delta": f"{weeks_left} weeks left" if weeks_left else "FY complete",
            "delta_colour": "off",
        },
        {
            "label": "Implied Weekly Pace",
            "value": fmt_bn((total_remit - total_issued) / weeks_left, dp=2)
            if weeks_left else "—",
            "delta": "to hit remit",
            "delta_colour": "off",
        },
    ])

    st.divider()

    # -----------------------------------------------------------------------
    # Sector gauges
    # -----------------------------------------------------------------------
    st.subheader("Progress by Sector")
    g_cols = st.columns(4)
    sectors = [("short", "Short  (<7yr)"), ("medium", "Medium  (7–15yr)"),
               ("long", "Long  (>15yr)"), ("linkers", "Index-Linked")]
    for col, (sector, label) in zip(g_cols, sectors):
        with col:
            issued = ytd.get(sector, 0)
            r = remit.get(sector, 0) or 0
            st.plotly_chart(
                remit_gauge(issued, r, label, SECTOR_COLOURS[sector]),
                use_container_width=True,
            )

    st.divider()

    # -----------------------------------------------------------------------
    # Quarterly progress vs remit
    # -----------------------------------------------------------------------
    col_left, col_right = st.columns(2)

    with col_left:
        q_df = store.get_quarterly_progress(selected_fy)
        if not q_df.empty:
            st.plotly_chart(
                quarterly_vs_remit_chart(q_df, total_remit, selected_fy),
                use_container_width=True,
            )
        else:
            st.caption("Quarterly outturn data not yet published for this fiscal year.")

    with col_right:
        st.subheader("Quarterly Sector Breakdown")
        if not q_df.empty:
            _LABELS = {"short": "Short (<7yr)", "medium": "Medium (7–15yr)",
                       "long": "Long (>15yr)", "linkers": "Index-Linked"}
            melt = q_df.melt(
                id_vars=["quarter"],
                value_vars=["short", "medium", "long", "linkers"],
                var_name="sector",
                value_name="issued_bn",
            )
            fig = go.Figure()
            for sector, colour in SECTOR_COLOURS.items():
                sub = melt[melt["sector"] == sector]
                fig.add_trace(go.Bar(
                    name=_LABELS.get(sector, sector.capitalize()),
                    x=sub["quarter"],
                    y=sub["issued_bn"],
                    marker_color=colour,
                    marker_line_width=0,
                ))
            fig.update_layout(
                barmode="stack",
                template="plotly_dark",
                height=400,
                xaxis_title="Quarter",
                yaxis_title="£bn",
                legend=dict(orientation="h", y=-0.2, bgcolor="rgba(0,0,0,0)"),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10, r=10, t=10, b=10),
                font=dict(color="#C8D4E3", size=12),
            )
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # -----------------------------------------------------------------------
    # Issuance method split — auction vs syndication
    # -----------------------------------------------------------------------
    st.subheader("Issuance Method: Auction vs Syndication")
    method_split = store.get_method_split(selected_fy)
    synd_fy_df = store.get_syndications(fy=selected_fy, completed_only=True)

    ms_cols = st.columns([1, 1, 2])
    with ms_cols[0]:
        st.metric("Via Auction",
                  fmt_bn(method_split["auction_bn"]),
                  delta=fmt_pct(method_split["auction_pct"]) + " of total",
                  delta_color="off")
    with ms_cols[1]:
        st.metric("Via Syndication",
                  fmt_bn(method_split["syndication_bn"]),
                  delta=fmt_pct(method_split["syndication_pct"]) + " of total",
                  delta_color="off")
    with ms_cols[2]:
        if not synd_fy_df.empty:
            # Compact syndication table inline
            compact = synd_fy_df[["date", "gilt", "size_bn", "book_cover", "nip_bps"]].copy()
            compact["date"] = compact["date"].dt.strftime("%d %b %Y")
            compact["book_cover"] = compact["book_cover"].apply(
                lambda x: f"{x:.2f}×" if pd.notna(x) else "—")
            compact["nip_bps"] = compact["nip_bps"].apply(
                lambda x: f"{x:.1f}" if pd.notna(x) else "—")
            compact.columns = ["Date", "Gilt", "Size (£bn)", "Cover", "NIP (bps)"]
            st.dataframe(compact, use_container_width=True, hide_index=True,
                         column_config={"Size (£bn)": st.column_config.NumberColumn(format="£%.1f bn")})
        else:
            st.caption("No syndications recorded for this FY.")

    st.divider()

    # -----------------------------------------------------------------------
    # Historical remit trend
    # -----------------------------------------------------------------------
    st.subheader("Historical Gilt Remit — 10-Year View")
    hist_df = store.get_sector_history()
    # Show last 10 years + 1 forecast
    hist_view = hist_df.tail(12).copy()
    st.plotly_chart(
        sector_history_bar(hist_view, "Annual Gilt Issuance by Sector (£bn)"),
        use_container_width=True,
    )

    st.divider()

    # -----------------------------------------------------------------------
    # Issuance pace table
    # -----------------------------------------------------------------------
    st.subheader("Pace Analysis")
    q_df2 = store.get_quarterly_progress(selected_fy)
    if not q_df2.empty:
        completed = q_df2[q_df2["total"] > 0]
        n_q = len(completed)
        if n_q > 0:
            pace_per_q = completed["total"].mean()
            remaining_total = total_remit - total_issued
            qs_needed = remaining_total / pace_per_q if pace_per_q else 0

            pace_data = {
                "Metric": [
                    "Avg quarterly pace so far",
                    "Required pace to hit remit",
                    "Quarters of issuance remaining",
                    "Full-year run-rate (×4 quarters)",
                ],
                "Value": [
                    fmt_bn(pace_per_q),
                    fmt_bn(remaining_total / max(4 - n_q, 1)),
                    f"{4 - n_q} quarter(s)",
                    fmt_bn(pace_per_q * 4),
                ],
            }
            st.dataframe(pd.DataFrame(pace_data), use_container_width=True,
                         hide_index=True)

    # -----------------------------------------------------------------------
    # Remit notes
    # -----------------------------------------------------------------------
    notes = remit.get("notes", "")
    if notes:
        st.info(f"ℹ️  **Remit note:** {notes}")
