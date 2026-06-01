"""
Reusable Plotly + Streamlit UI components.
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
from typing import Optional, Dict, List

from config import SECTOR_COLOURS, CHART_HEIGHT, CHART_HEIGHT_TALL, PLOTLY_TEMPLATE
from utils.formatters import fmt_bn, fmt_pct, fmt_yield, fmt_cover


# ---------------------------------------------------------------------------
# Remit progress gauge
# ---------------------------------------------------------------------------

def remit_gauge(issued: float, remit: float, label: str, colour: str = "#1f77b4") -> go.Figure:
    pct = min(issued / remit * 100, 110) if remit else 0
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=pct,
        delta={"reference": 100, "valueformat": ".1f", "suffix": "%"},
        number={"suffix": "%", "valueformat": ".1f"},
        title={"text": label, "font": {"size": 13}},
        gauge={
            "axis": {"range": [0, 110], "tickwidth": 1, "tickcolor": "white"},
            "bar": {"color": colour, "thickness": 0.25},
            "steps": [
                {"range": [0, 80],   "color": "#1c2333"},
                {"range": [80, 95],  "color": "#2a3555"},
                {"range": [95, 110], "color": "#1e3a1e"},
            ],
            "threshold": {
                "line": {"color": "#ffd700", "width": 3},
                "thickness": 0.75,
                "value": 100,
            },
        },
    ))
    fig.update_layout(
        height=200,
        margin=dict(l=20, r=20, t=40, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#e8ecf0",
        template=PLOTLY_TEMPLATE,
    )
    return fig


# ---------------------------------------------------------------------------
# Stacked bar – sector issuance history
# ---------------------------------------------------------------------------

def sector_history_bar(df: pd.DataFrame, title: str = "Annual Gilt Issuance by Sector") -> go.Figure:
    fig = go.Figure()
    for sector, colour in SECTOR_COLOURS.items():
        if sector in df.columns:
            fig.add_trace(go.Bar(
                name=sector.capitalize(),
                x=df["fy"],
                y=df[sector],
                marker_color=colour,
            ))
    fig.update_layout(
        barmode="stack",
        title=title,
        xaxis_title="Fiscal Year",
        yaxis_title="£bn",
        height=CHART_HEIGHT,
        template=PLOTLY_TEMPLATE,
        legend=dict(orientation="h", y=-0.2),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


# ---------------------------------------------------------------------------
# Progress bar – quarterly vs remit
# ---------------------------------------------------------------------------

def quarterly_vs_remit_chart(
    quarterly_df: pd.DataFrame,
    remit_total: float,
    fy: str,
) -> go.Figure:
    df = quarterly_df.copy()
    df["cumulative"] = df["total"].cumsum()
    target_per_q = remit_total / 4

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(
        name="Quarterly Issued",
        x=df["quarter"],
        y=df["total"],
        marker_color="#1f77b4",
    ), secondary_y=False)
    fig.add_trace(go.Scatter(
        name="Cumulative",
        x=df["quarter"],
        y=df["cumulative"],
        mode="lines+markers",
        line=dict(color="#ffd700", width=2),
        marker=dict(size=8),
    ), secondary_y=True)
    fig.add_hline(
        y=remit_total, line_dash="dash", line_color="#ff7f0e",
        annotation_text=f"Full Year Remit {fmt_bn(remit_total)}",
        secondary_y=True
    )
    fig.update_layout(
        title=f"{fy} Issuance Progress",
        height=CHART_HEIGHT,
        template=PLOTLY_TEMPLATE,
        legend=dict(orientation="h", y=-0.2),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_yaxes(title_text="Quarterly £bn", secondary_y=False)
    fig.update_yaxes(title_text="Cumulative £bn", secondary_y=True)
    return fig


# ---------------------------------------------------------------------------
# Forecast fan chart
# ---------------------------------------------------------------------------

def fan_chart(
    historical: pd.DataFrame,
    forecast: pd.DataFrame,
    title: str = "Gilt Issuance Forecast",
) -> go.Figure:
    fig = go.Figure()

    # Historical bars
    hist = historical.dropna(subset=["actual"])
    fig.add_trace(go.Bar(
        name="Historical Actual",
        x=hist["fy"],
        y=hist["actual"],
        marker_color="#1f77b4",
        opacity=0.8,
    ))

    # Remit-only years (no actual yet)
    remit_only = historical[historical["actual"].isna() & historical["current_remit"].notna()]
    fig.add_trace(go.Bar(
        name="Remit (announced)",
        x=remit_only["fy"],
        y=remit_only["current_remit"],
        marker_color="#ff7f0e",
        opacity=0.8,
    ))

    # 95% CI band
    if "p95" in forecast.columns and "p5" in forecast.columns:
        fig.add_trace(go.Scatter(
            name="95% CI",
            x=list(forecast["fy"]) + list(forecast["fy"])[::-1],
            y=list(forecast["p95"]) + list(forecast["p5"])[::-1],
            fill="toself",
            fillcolor="rgba(255,127,14,0.15)",
            line=dict(color="rgba(255,127,14,0)"),
            showlegend=True,
        ))

    # 68% CI band
    if "p75" in forecast.columns and "p25" in forecast.columns:
        fig.add_trace(go.Scatter(
            name="68% CI",
            x=list(forecast["fy"]) + list(forecast["fy"])[::-1],
            y=list(forecast["p75"]) + list(forecast["p25"])[::-1],
            fill="toself",
            fillcolor="rgba(255,127,14,0.30)",
            line=dict(color="rgba(255,127,14,0)"),
            showlegend=True,
        ))

    # Point forecast line
    fc_col = "consensus" if "consensus" in forecast.columns else (
        "forecast" if "forecast" in forecast.columns else "p50"
    )
    if fc_col in forecast.columns:
        fig.add_trace(go.Scatter(
            name="Point Forecast",
            x=forecast["fy"],
            y=forecast[fc_col],
            mode="lines+markers",
            line=dict(color="#ff7f0e", width=2, dash="dash"),
            marker=dict(size=7),
        ))

    fig.update_layout(
        title=title,
        xaxis_title="Fiscal Year",
        yaxis_title="£bn",
        height=CHART_HEIGHT_TALL,
        template=PLOTLY_TEMPLATE,
        legend=dict(orientation="h", y=-0.18),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


# ---------------------------------------------------------------------------
# Sector proportion time series
# ---------------------------------------------------------------------------

def sector_proportion_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for sector, colour in SECTOR_COLOURS.items():
        col = f"{sector}_pct"
        if col in df.columns:
            fig.add_trace(go.Scatter(
                name=sector.capitalize(),
                x=df["fy"],
                y=df[col],
                mode="lines+markers",
                line=dict(color=colour, width=2),
                stackgroup="one",
                fillcolor=colour,
            ))
    fig.update_layout(
        title="Sector Allocation (% of Total Remit)",
        xaxis_title="Fiscal Year",
        yaxis_title="% of total",
        height=CHART_HEIGHT,
        template=PLOTLY_TEMPLATE,
        legend=dict(orientation="h", y=-0.2),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(ticksuffix="%"),
    )
    return fig


# ---------------------------------------------------------------------------
# PSND dual-axis chart
# ---------------------------------------------------------------------------

def psnd_chart(df: pd.DataFrame) -> go.Figure:
    actual = df[df["fy"] <= "2024-25"]
    forecast = df[df["fy"] >= "2024-25"]

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Bar(
        name="PSND £bn (actual)",
        x=actual["fy"], y=actual["psnd_bn"],
        marker_color="#1f77b4",
    ), secondary_y=False)
    fig.add_trace(go.Bar(
        name="PSND £bn (OBR forecast)",
        x=forecast["fy"], y=forecast["psnd_bn"],
        marker_color="#1f77b4",
        marker_pattern_shape="/",
        opacity=0.7,
    ), secondary_y=False)
    fig.add_trace(go.Scatter(
        name="PSND % GDP (actual)",
        x=actual["fy"], y=actual["psnd_pct_gdp"],
        mode="lines+markers",
        line=dict(color="#ff7f0e", width=2),
    ), secondary_y=True)
    fig.add_trace(go.Scatter(
        name="PSND % GDP (forecast)",
        x=forecast["fy"], y=forecast["psnd_pct_gdp"],
        mode="lines+markers",
        line=dict(color="#ff7f0e", width=2, dash="dash"),
    ), secondary_y=True)

    fig.update_layout(
        title="UK Public Sector Net Debt",
        height=CHART_HEIGHT,
        template=PLOTLY_TEMPLATE,
        barmode="overlay",
        legend=dict(orientation="h", y=-0.22),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_yaxes(title_text="PSND £bn", secondary_y=False)
    fig.update_yaxes(title_text="PSND % GDP", secondary_y=True,
                     ticksuffix="%")
    return fig


# ---------------------------------------------------------------------------
# Auction results scatter
# ---------------------------------------------------------------------------

def auction_scatter(df: pd.DataFrame, x_col: str = "date",
                    title: str = "Auction Yields Over Time") -> go.Figure:
    fig = go.Figure()
    for bond_type in df["type"].unique():
        sub = df[df["type"] == bond_type]
        colour = SECTOR_COLOURS.get(bond_type.lower(), "#aaaaaa")
        fig.add_trace(go.Scatter(
            name=bond_type,
            x=sub[x_col],
            y=sub["yield_pct"],
            mode="markers",
            marker=dict(
                color=colour,
                size=sub["size_bn"] * 3,
                opacity=0.75,
                line=dict(width=1, color="white"),
            ),
            hovertemplate=(
                "<b>%{text}</b><br>"
                "Date: %{x|%d %b %Y}<br>"
                "Yield: %{y:.3f}%<br>"
                "Size: £%{customdata:.1f}bn<br>"
                "Cover: %{marker.color}"
            ),
            text=sub["gilt"],
            customdata=sub["size_bn"],
        ))
    fig.update_layout(
        title=title,
        xaxis_title="Date",
        yaxis_title="Yield (%)",
        height=CHART_HEIGHT,
        template=PLOTLY_TEMPLATE,
        legend=dict(orientation="h", y=-0.2),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(ticksuffix="%"),
    )
    return fig


# ---------------------------------------------------------------------------
# Bid-to-cover history
# ---------------------------------------------------------------------------

def cover_ratio_chart(df: pd.DataFrame) -> go.Figure:
    df_sorted = df.sort_values("date")
    fig = go.Figure()
    for bond_type in df_sorted["type"].unique():
        sub = df_sorted[df_sorted["type"] == bond_type]
        fig.add_trace(go.Scatter(
            name=bond_type,
            x=sub["date"],
            y=sub["cover_ratio"],
            mode="lines+markers",
            marker=dict(size=5),
        ))
    fig.add_hline(y=2.0, line_dash="dash", line_color="#aaaaaa",
                  annotation_text="2.0× threshold")
    fig.update_layout(
        title="Bid-to-Cover Ratios by Sector",
        xaxis_title="Date",
        yaxis_title="Cover Ratio",
        height=CHART_HEIGHT,
        template=PLOTLY_TEMPLATE,
        legend=dict(orientation="h", y=-0.2),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


# ---------------------------------------------------------------------------
# Monte Carlo distribution
# ---------------------------------------------------------------------------

def mc_distribution_chart(mc_df: pd.DataFrame, fy: str) -> go.Figure:
    row = mc_df[mc_df["fy"] == fy].iloc[0] if not mc_df[mc_df["fy"] == fy].empty else None
    if row is None:
        return go.Figure()

    p5, p95, p50 = row["p5"], row["p95"], row["p50"]
    x = np.linspace(p5 * 0.95, p95 * 1.05, 400)
    std = (p95 - p5) / (2 * 1.96)
    y = (1 / (std * np.sqrt(2 * np.pi))) * np.exp(-0.5 * ((x - p50) / std) ** 2)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=y, fill="tozeroy",
        fillcolor="rgba(31,119,180,0.3)",
        line=dict(color="#1f77b4"),
        name="Forecast distribution",
    ))
    for p_label, p_val, colour in [
        ("P5",  p5,        "#d62728"),
        ("P25", row["p25"], "#ff7f0e"),
        ("P50", p50,       "#2ca02c"),
        ("P75", row["p75"], "#ff7f0e"),
        ("P95", p95,       "#d62728"),
    ]:
        fig.add_vline(x=p_val, line_dash="dot", line_color=colour,
                      annotation_text=f"{p_label}: {fmt_bn(p_val)}")

    fig.update_layout(
        title=f"Monte Carlo Issuance Distribution – {fy}",
        xaxis_title="Total Issuance £bn",
        yaxis_title="Probability density",
        height=CHART_HEIGHT,
        template=PLOTLY_TEMPLATE,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


# ---------------------------------------------------------------------------
# Sidebar refresh widget
# ---------------------------------------------------------------------------

def render_data_status(data_store) -> None:
    from datetime import datetime
    st.sidebar.divider()
    st.sidebar.caption("**Data Sources**")
    if data_store.last_refresh:
        age = (datetime.now() - data_store.last_refresh).seconds // 60
        colour = "🟢" if age < 60 else "🟡" if age < 1440 else "🔴"
        st.sidebar.caption(f"{colour} Last refresh: {age} min ago")
    else:
        st.sidebar.caption("🔴 No live refresh yet")
    if data_store.live_status.get("errors"):
        with st.sidebar.expander("⚠️ Fetch warnings"):
            for e in data_store.live_status["errors"]:
                st.caption(e)


# ---------------------------------------------------------------------------
# KPI metric row
# ---------------------------------------------------------------------------

def kpi_row(metrics: List[Dict]) -> None:
    cols = st.columns(len(metrics))
    for col, m in zip(cols, metrics):
        with col:
            st.metric(
                label=m["label"],
                value=m["value"],
                delta=m.get("delta"),
                delta_color=m.get("delta_colour", "normal"),
                help=m.get("help"),
            )
