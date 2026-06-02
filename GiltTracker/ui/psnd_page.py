"""
Page 5: PSND & Borrowing — public sector net debt tracking and OBR forecasts.
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

from data.store import GiltDataStore
from analysis.forecasting import fit_psnd_bridge
from ui.components import psnd_chart
from utils.formatters import fmt_bn, fmt_pct
from config import PLOTLY_TEMPLATE, CHART_HEIGHT, CHART_HEIGHT_TALL


def render(store: GiltDataStore, selected_fy: str) -> None:
    st.title("Public Sector Net Debt & Borrowing")
    st.caption(
        "PSND and PSNB time series (ONS actuals + OBR forecasts), and the "
        "structural relationship between borrowing and gilt issuance."
    )

    psnd_df = store.get_psnd()
    annual_df = store.get_all_remits()

    # -----------------------------------------------------------------------
    # Headline KPIs
    # -----------------------------------------------------------------------
    latest = psnd_df.iloc[-1]
    prev = psnd_df.iloc[-2]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        f"PSND ({latest['fy']})",
        fmt_bn(latest["psnd_bn"]),
        delta=fmt_bn(latest["psnd_bn"] - prev["psnd_bn"]) + " YoY",
    )
    c2.metric(
        "PSND % GDP",
        fmt_pct(latest["psnd_pct_gdp"]),
        delta=fmt_pct(latest["psnd_pct_gdp"] - prev["psnd_pct_gdp"]) + " YoY",
        delta_color="inverse",
    )
    c3.metric(
        f"PSNB ({latest['fy']})",
        fmt_bn(latest["borrowing_bn"]),
        delta=fmt_bn(latest["borrowing_bn"] - prev["borrowing_bn"]) + " YoY",
        delta_color="inverse",
    )
    obr = store.get_latest_obr_forecast()
    next_fy_obr = obr[obr["fy"] == "2026-27"]
    if not next_fy_obr.empty:
        c4.metric(
            "OBR FY26-27 Issuance Forecast",
            fmt_bn(next_fy_obr.iloc[0]["total_issuance_bn"]),
            delta=next_fy_obr.iloc[0]["publication"],
            delta_color="off",
        )

    st.divider()

    # -----------------------------------------------------------------------
    # PSND dual-axis chart
    # -----------------------------------------------------------------------
    st.subheader("PSND: Level and % of GDP")
    st.plotly_chart(psnd_chart(psnd_df), use_container_width=True)

    # -----------------------------------------------------------------------
    # PSNB vs Gilt Issuance scatter
    # -----------------------------------------------------------------------
    st.subheader("PSNB vs Gilt Issuance — Structural Relationship")
    col_scatter, col_time = st.columns(2)

    merged = psnd_df.merge(
        annual_df[["fy", "actual", "current_remit"]].assign(
            issuance=lambda d: d["actual"].fillna(d["current_remit"])
        ),
        on="fy"
    ).dropna(subset=["borrowing_bn", "issuance"])

    with col_scatter:
        is_covid = merged["fy"] == "2020-21"
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=merged[~is_covid]["borrowing_bn"],
            y=merged[~is_covid]["issuance"],
            mode="markers+text",
            text=merged[~is_covid]["fy"],
            textposition="top right",
            textfont=dict(size=9),
            marker=dict(color="#1f77b4", size=10),
            name="Normal years",
        ))
        fig.add_trace(go.Scatter(
            x=merged[is_covid]["borrowing_bn"],
            y=merged[is_covid]["issuance"],
            mode="markers+text",
            text=merged[is_covid]["fy"],
            textposition="top right",
            textfont=dict(size=9),
            marker=dict(color="#d62728", size=14, symbol="star"),
            name="COVID 2020-21",
        ))

        # Fit line (exclude COVID)
        normal = merged[~is_covid].dropna(subset=["borrowing_bn", "issuance"])
        if len(normal) >= 3:
            m, b = np.polyfit(normal["borrowing_bn"], normal["issuance"], 1)
            x_range = np.linspace(normal["borrowing_bn"].min(),
                                  normal["borrowing_bn"].max(), 50)
            fig.add_trace(go.Scatter(
                x=x_range, y=m * x_range + b,
                mode="lines",
                line=dict(color="#aaaaaa", dash="dash"),
                name=f"OLS fit (R²={np.corrcoef(normal['borrowing_bn'], normal['issuance'])[0,1]**2:.2f})",
            ))

        fig.update_layout(
            title="PSNB vs Gross Gilt Issuance",
            xaxis_title="Net Borrowing £bn",
            yaxis_title="Gross Gilt Issuance £bn",
            height=CHART_HEIGHT,
            template=PLOTLY_TEMPLATE,
            legend=dict(orientation="h", y=-0.25),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_time:
        fig2 = make_subplots(specs=[[{"secondary_y": True}]])
        fig2.add_trace(go.Bar(
            name="Borrowing (PSNB)",
            x=merged["fy"], y=merged["borrowing_bn"],
            marker_color="#1f77b4", opacity=0.8,
        ), secondary_y=False)
        fig2.add_trace(go.Scatter(
            name="Gross Issuance",
            x=merged["fy"], y=merged["issuance"],
            mode="lines+markers",
            line=dict(color="#ff7f0e", width=2),
        ), secondary_y=True)
        fig2.update_layout(
            title="Borrowing & Gross Issuance Over Time",
            height=CHART_HEIGHT,
            template=PLOTLY_TEMPLATE,
            legend=dict(orientation="h", y=-0.25),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        fig2.update_yaxes(title_text="PSNB £bn", secondary_y=False)
        fig2.update_yaxes(title_text="Gross Issuance £bn", secondary_y=True)
        st.plotly_chart(fig2, use_container_width=True)

    st.divider()

    # -----------------------------------------------------------------------
    # OBR forecast vintages
    # -----------------------------------------------------------------------
    st.subheader("OBR Forecast Vintages")
    obr_all = store.obr_forecasts.copy()
    if not obr_all.empty:
        obr_pivot = obr_all.pivot_table(
            values="total_issuance_bn",
            index="fy",
            columns="publication",
            aggfunc="first"
        )
        st.dataframe(
            obr_pivot.style.format("{:.1f}").highlight_max(axis=1, color="#1e3a1e")
                            .highlight_min(axis=1, color="#3a1e1e"),
            use_container_width=True,
        )
        st.caption("Gross gilt issuance (£bn) as forecast across successive OBR publications.")

    st.divider()

    # -----------------------------------------------------------------------
    # OLS bridge model – scenario analysis
    # -----------------------------------------------------------------------
    st.subheader("PSND-Bridge Model: Scenario Analysis")
    st.caption(
        "Applies the fitted OLS regression (gross issuance ~ PSNB + PSND/GDP) "
        "to OBR central, upside, and downside PSND scenarios."
    )

    bridge = fit_psnd_bridge(psnd_df, annual_df)
    r2 = bridge.get("r_squared", 0)
    st.info(f"Model R² = {r2:.3f}. {'High explanatory power.' if r2 > 0.8 else 'Moderate fit — use alongside ARIMA.'}")

    # Scenarios
    scenarios = [
        ("Central",  latest["psnd_pct_gdp"] + 2.0, latest["borrowing_bn"] * 0.95),
        ("Adverse",  latest["psnd_pct_gdp"] + 5.0, latest["borrowing_bn"] * 1.15),
        ("Benign",   latest["psnd_pct_gdp"] - 1.0, latest["borrowing_bn"] * 0.80),
    ]

    scenario_rows = []
    for name, psnd_pct, borrow in scenarios:
        pred = bridge["predict"](borrow, psnd_pct)
        scenario_rows.append({
            "Scenario": name,
            "PSND % GDP": fmt_pct(psnd_pct),
            "PSNB £bn": fmt_bn(borrow),
            "Implied Gross Issuance": fmt_bn(pred),
        })

    st.dataframe(pd.DataFrame(scenario_rows), use_container_width=True, hide_index=True)

    with st.expander("📋 OLS Model Summary"):
        if bridge.get("summary"):
            st.code(bridge["summary"], language="text")
        else:
            st.caption("Using simple ratio model as fallback.")
