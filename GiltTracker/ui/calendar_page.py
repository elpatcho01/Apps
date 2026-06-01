"""
Page 3: Issuance Calendar — announced auctions + AI-predicted unannounced quarters.
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from datetime import date, timedelta

from data.store import GiltDataStore
from analysis.forecasting import (
    quarterly_seasonal_factors,
    predict_unannounced_quarters,
)
from utils.formatters import fmt_bn, fmt_pct, fmt_yield, fmt_cover
from config import SECTOR_COLOURS, PLOTLY_TEMPLATE, CHART_HEIGHT


def _week_heatmap(cal_df: pd.DataFrame, start: date, end: date) -> go.Figure:
    """Build a week-by-week calendar heatmap for the given date range."""
    # Build a grid of weekdays
    current = start
    cells = []
    while current <= end:
        if current.weekday() < 5:  # Mon–Fri
            cells.append(current)
        current += timedelta(days=1)

    cell_dates = pd.DataFrame({"date": cells})
    cell_dates["week"] = cell_dates["date"].apply(
        lambda d: (d - start).days // 7
    )
    cell_dates["dow"] = cell_dates["date"].dt.weekday  # 0=Mon … 4=Fri
    dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri"]

    # Match auction + syndication dates
    cal_df = cal_df.copy()
    cal_df["date_only"] = pd.to_datetime(cal_df["date"]).dt.normalize()
    merged = cell_dates.merge(
        cal_df[["date_only", "gilt", "size_bn", "status", "type",
                "method"] if "method" in cal_df.columns else
               ["date_only", "gilt", "size_bn", "status", "type"]],
        left_on="date", right_on="date_only", how="left"
    )

    # 0=none, 1=indicative, 2=confirmed, 3=completed auction, 4=syndication
    def _val(row):
        if pd.isna(row.get("status")):
            return 0
        if row.get("method") == "Syndication":
            return 4
        return {"Completed": 3, "Confirmed": 2, "Indicative": 1}.get(row["status"], 0)

    merged["val"] = merged.apply(_val, axis=1)

    n_weeks = merged["week"].max() + 1 if not merged.empty else 0
    z = np.zeros((5, n_weeks))
    hover = [["" for _ in range(n_weeks)] for _ in range(5)]

    for _, row in merged.iterrows():
        w, d = int(row["week"]), int(row["dow"])
        z[d, w] = row["val"]
        if pd.notna(row.get("gilt")):
            hover[d][w] = (
                f"{row['gilt']}<br>"
                f"Size: {fmt_bn(row['size_bn'])}<br>"
                f"Status: {row['status']}<br>"
                f"Sector: {row.get('type', '')}"
            )
        else:
            hover[d][w] = row["date"].strftime("%d %b %Y") if hasattr(row["date"], "strftime") else ""

    # Week labels
    week_starts = [
        (start + timedelta(weeks=w)).strftime("%d %b")
        for w in range(n_weeks)
    ]

    colorscale = [
        [0.00, "#1c2333"],   # none
        [0.25, "#9467bd"],   # indicative auction
        [0.50, "#1f77b4"],   # confirmed auction
        [0.75, "#2ca02c"],   # completed auction
        [1.00, "#e377c2"],   # syndication
    ]

    fig = go.Figure(go.Heatmap(
        z=z,
        x=week_starts,
        y=dow_labels,
        text=hover,
        hovertemplate="%{text}<extra></extra>",
        colorscale=colorscale,
        zmin=0, zmax=4,
        showscale=True,
        colorbar=dict(
            title="Type",
            tickvals=[0, 1, 2, 3, 4],
            ticktext=["None", "Indicative", "Confirmed", "Completed", "Syndication"],
        ),
    ))
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        height=220,
        margin=dict(l=50, r=20, t=30, b=20),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def _quarter_dates(fy: str, q: str):
    fy_year = int(fy[:4])
    q_map = {
        "Q1": (date(fy_year, 4, 1),     date(fy_year, 6, 30)),
        "Q2": (date(fy_year, 7, 1),     date(fy_year, 9, 30)),
        "Q3": (date(fy_year, 10, 1),    date(fy_year, 12, 31)),
        "Q4": (date(fy_year + 1, 1, 1), date(fy_year + 1, 3, 31)),
    }
    return q_map[q]


def render(store: GiltDataStore, selected_fy: str) -> None:
    st.title("📅 Gilt Issuance Calendar")
    st.caption(
        "Announced auctions and syndications from the DMO, overlaid with model-predicted "
        "auctions for unannounced periods. "
        "🟢 Completed auction · 🔵 Confirmed · 🟣 Indicative · 🌸 Syndication"
    )

    # -----------------------------------------------------------------------
    # Seasonal factors and unannounced quarter predictions
    # -----------------------------------------------------------------------
    all_quarterly = store.quarterly_issuance
    seasonal_factors = quarterly_seasonal_factors(all_quarterly)

    remit = store.get_remit(selected_fy)
    remit_total = remit["current_remit"] if remit else 300.0

    completed_qs = store.get_quarterly_progress(selected_fy)
    predicted_qs = predict_unannounced_quarters(
        selected_fy, remit_total, completed_qs, seasonal_factors
    )

    # -----------------------------------------------------------------------
    # Quarter tabs
    # -----------------------------------------------------------------------
    quarters = ["Q1 (Apr–Jun)", "Q2 (Jul–Sep)", "Q3 (Oct–Dec)", "Q4 (Jan–Mar)"]
    q_codes = ["Q1", "Q2", "Q3", "Q4"]
    tabs = st.tabs(quarters)

    for tab, q_label, q_code in zip(tabs, quarters, q_codes):
        with tab:
            q_start, q_end = _quarter_dates(selected_fy, q_code)
            completed_q = completed_qs[completed_qs["quarter"] == q_code]
            is_complete = not completed_q.empty and completed_q.iloc[0].get("announced", False)

            # Header metrics
            pred_row = predicted_qs[predicted_qs["quarter"] == q_code] if not predicted_qs.empty else pd.DataFrame()
            actual_row = completed_q

            if not actual_row.empty:
                q_total = actual_row.iloc[0]["total"]
                q_label_txt = "Actual"
            elif not pred_row.empty:
                q_total = pred_row.iloc[0]["total"]
                q_label_txt = "Predicted"
            else:
                q_total = remit_total / 4
                q_label_txt = "Estimate"

            c1, c2, c3, c4 = st.columns(4)
            c1.metric(f"Q Issuance ({q_label_txt})", fmt_bn(q_total))
            c2.metric("Q Target (implied)", fmt_bn(remit_total / 4))

            if not pred_row.empty:
                c3.metric("Short", fmt_bn(pred_row.iloc[0].get("short")))
                c4.metric("Linkers", fmt_bn(pred_row.iloc[0].get("linkers")))

            # Calendar heatmap
            st.subheader("Weekly Auction Calendar")
            cal_df = store.get_calendar_for_fy(selected_fy)
            st.plotly_chart(
                _week_heatmap(
                    cal_df[
                        (pd.to_datetime(cal_df["date"]).dt.date >= q_start) &
                        (pd.to_datetime(cal_df["date"]).dt.date <= q_end)
                    ],
                    q_start, q_end
                ),
                use_container_width=True,
            )

            # Auction list
            q_auctions = cal_df[
                (pd.to_datetime(cal_df["date"]).dt.date >= q_start) &
                (pd.to_datetime(cal_df["date"]).dt.date <= q_end)
            ].copy()

            if q_auctions.empty:
                st.info("No auction data for this quarter.")
            else:
                q_auctions["date_fmt"] = pd.to_datetime(q_auctions["date"]).dt.strftime("%d %b %Y")
                q_auctions["yield_fmt"] = q_auctions["yield_pct"].apply(
                    lambda x: f"{x:.3f}%" if pd.notna(x) else "TBD"
                )
                q_auctions["cover_fmt"] = q_auctions["cover_ratio"].apply(
                    lambda x: f"{x:.2f}×" if pd.notna(x) else "TBD"
                )
                display = q_auctions[[
                    "date_fmt", "gilt", "size_bn", "type", "yield_fmt", "cover_fmt", "status"
                ]].rename(columns={
                    "date_fmt": "Date",
                    "gilt": "Gilt",
                    "size_bn": "Size (£bn)",
                    "type": "Sector",
                    "yield_fmt": "Yield",
                    "cover_fmt": "Cover",
                    "status": "Status",
                })

                def _colour_status(val):
                    colours = {
                        "Completed": "background-color: #1e3a1e",
                        "Confirmed": "background-color: #1c3055",
                        "Indicative": "background-color: #2d1e3a",
                    }
                    return colours.get(val, "")

                st.dataframe(
                    display.style.map(_colour_status, subset=["Status"]),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Size (£bn)": st.column_config.NumberColumn(format="£%.1f bn"),
                    }
                )

                total_confirmed = q_auctions[
                    q_auctions["status"].isin(["Completed", "Confirmed"])
                ]["size_bn"].sum()
                total_indicative = q_auctions[
                    q_auctions["status"] == "Indicative"
                ]["size_bn"].sum()

                st.caption(
                    f"**Confirmed:** {fmt_bn(total_confirmed)}  "
                    f"| **Indicative (model):** {fmt_bn(total_indicative)}  "
                    f"| **Quarter target:** {fmt_bn(remit_total / 4)}"
                )

    st.divider()

    # -----------------------------------------------------------------------
    # Seasonal factors table
    # -----------------------------------------------------------------------
    with st.expander("📊 Historical Seasonal Factors"):
        sf_display = seasonal_factors.copy()
        sf_display["mean_share_pct"] = (sf_display["mean_share"] * 100).round(1)
        sf_display["std_share_pct"] = (sf_display["std_share"] * 100).round(2)
        sf_display = sf_display.rename(columns={
            "quarter": "Quarter",
            "mean_share_pct": "Avg Share of FY (%)",
            "std_share_pct": "Std Dev (%)",
            "historical_quarters": "Historical Obs.",
        })
        st.dataframe(sf_display[["Quarter", "Avg Share of FY (%)", "Std Dev (%)", "Historical Obs."]],
                     use_container_width=True, hide_index=True)
        st.caption(
            "Average quarterly share of full-year issuance based on historical data. "
            "Q4 is typically lighter due to pre-Budget concentration of issuance in Q1–Q3."
        )
