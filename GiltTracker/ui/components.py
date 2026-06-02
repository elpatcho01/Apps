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

def remit_gauge(issued: float, remit: float, label: str, colour: str = "#3B82F6") -> go.Figure:
    """Horizontal bullet-style progress chart — cleaner than a semicircle gauge."""
    pct = min(issued / remit * 100, 110) if remit else 0
    bar_pct = min(pct, 100)

    fig = go.Figure()

    # Background track
    fig.add_shape(type="rect", x0=0, x1=100, y0=0.28, y1=0.72,
                  fillcolor="#1B2A3C", line_width=0, layer="below")

    # Pace zone (80–100% = on-track band)
    fig.add_shape(type="rect", x0=80, x1=100, y0=0.28, y1=0.72,
                  fillcolor="rgba(255,255,255,0.04)", line_width=0)

    # Progress fill
    if bar_pct > 0:
        fig.add_shape(type="rect", x0=0, x1=bar_pct, y0=0.32, y1=0.68,
                      fillcolor=colour, line_width=0)

    # Target line at 100%
    fig.add_shape(type="line", x0=100, x1=100, y0=0.18, y1=0.82,
                  line=dict(color="#C7A84A", width=2, dash="dot"))

    # Percentage label (large, centred)
    fig.add_annotation(
        x=50, y=1.35,
        text=f"<b>{pct:.1f}%</b>",
        showarrow=False,
        font=dict(size=22, color=colour if pct < 95 else "#C7A84A"),
        xanchor="center",
    )
    # Sub-label
    issued_str = f"£{issued:.1f}bn" if issued else "—"
    remit_str  = f"£{remit:.1f}bn" if remit else "—"
    fig.add_annotation(
        x=50, y=-0.55,
        text=f"{issued_str} of {remit_str}",
        showarrow=False,
        font=dict(size=10, color="#6B8CAE"),
        xanchor="center",
    )

    fig.update_layout(
        title=dict(text=f"<b>{label}</b>", font=dict(size=11, color="#8BA0BB"), x=0.5, xanchor="center"),
        xaxis=dict(range=[-2, 108], showgrid=False, showticklabels=False, zeroline=False),
        yaxis=dict(range=[-0.8, 1.8], showgrid=False, showticklabels=False, zeroline=False),
        height=155,
        margin=dict(l=10, r=10, t=38, b=18),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e8ecf0",
    )
    return fig


# ---------------------------------------------------------------------------
# Stacked bar – sector issuance history
# ---------------------------------------------------------------------------

_SECTOR_LABELS = {
    "short": "Short (<7yr)", "medium": "Medium (7–15yr)",
    "long": "Long (>15yr)", "linkers": "Index-Linked",
}

_LAYOUT_BASE = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#C8D4E3", size=12),
    template=PLOTLY_TEMPLATE,
    margin=dict(l=10, r=10, t=44, b=10),
    legend=dict(
        orientation="h", y=-0.18, x=0.5, xanchor="center",
        font=dict(size=11), bgcolor="rgba(0,0,0,0)",
    ),
)


def _layout(**kw) -> dict:
    d = _LAYOUT_BASE.copy()
    d.update(kw)
    return d


def sector_history_bar(df: pd.DataFrame, title: str = "Annual Gilt Issuance by Sector") -> go.Figure:
    fig = go.Figure()
    for sector, colour in SECTOR_COLOURS.items():
        if sector in df.columns:
            fig.add_trace(go.Bar(
                name=_SECTOR_LABELS.get(sector, sector.capitalize()),
                x=df["fy"],
                y=df[sector],
                marker_color=colour,
            ))
    fig.update_layout(
        **_layout(
            barmode="stack",
            title=dict(text=title, font=dict(size=13), pad=dict(b=8)),
            xaxis=dict(type="category", title="Fiscal Year", tickfont=dict(size=11)),
            yaxis=dict(title="£bn"),
            height=CHART_HEIGHT,
        )
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

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(
        name="Quarterly Issued",
        x=df["quarter"],
        y=df["total"],
        marker_color="#3B82F6",
        marker_line_width=0,
    ), secondary_y=False)
    fig.add_trace(go.Scatter(
        name="Cumulative",
        x=df["quarter"],
        y=df["cumulative"],
        mode="lines+markers",
        line=dict(color="#C7A84A", width=2),
        marker=dict(size=7),
    ), secondary_y=True)
    fig.add_hline(
        y=remit_total, line_dash="dash", line_color="#F59E0B",
        annotation_text=f"FY Remit {fmt_bn(remit_total)}",
        annotation_font_size=11,
        secondary_y=True,
    )
    fig.update_layout(
        **_layout(
            title=dict(text=f"{fy} Quarterly Progress", font=dict(size=13)),
            height=CHART_HEIGHT,
        )
    )
    fig.update_yaxes(title_text="£bn (quarterly)", secondary_y=False, gridcolor="rgba(255,255,255,0.05)")
    fig.update_yaxes(title_text="£bn (cumulative)", secondary_y=True)
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
        **_layout(
            title=dict(text=title, font=dict(size=13)),
            xaxis=dict(type="category", title="Fiscal Year", tickfont=dict(size=11)),
            yaxis=dict(title="£bn"),
            height=CHART_HEIGHT_TALL,
        )
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
                name=_SECTOR_LABELS.get(sector, sector.capitalize()),
                x=df["fy"],
                y=df[col],
                mode="lines+markers",
                line=dict(color=colour, width=2),
                stackgroup="one",
                fillcolor=colour,
            ))
    fig.update_layout(
        **_layout(
            title=dict(text="Sector Allocation (% of Total Remit)", font=dict(size=13)),
            xaxis=dict(type="category", title="Fiscal Year", tickfont=dict(size=11)),
            yaxis=dict(title="% of total", ticksuffix="%"),
            height=CHART_HEIGHT,
        )
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
        **_layout(
            title=dict(text="UK Public Sector Net Debt", font=dict(size=13)),
            height=CHART_HEIGHT,
            barmode="overlay",
        )
    )
    fig.update_xaxes(type="category", tickfont=dict(size=11))
    fig.update_yaxes(title_text="PSND £bn", secondary_y=False, gridcolor="rgba(255,255,255,0.05)")
    fig.update_yaxes(title_text="PSND % GDP", secondary_y=True, ticksuffix="%")
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
        **_layout(
            title=dict(text=title, font=dict(size=13)),
            xaxis=dict(title="Date"),
            yaxis=dict(title="Yield (%)", ticksuffix="%"),
            height=CHART_HEIGHT,
        )
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
        colour = SECTOR_COLOURS.get(
            "linkers" if bond_type.lower() == "linker" else bond_type.lower(), "#aaa"
        )
        fig.add_trace(go.Scatter(
            name=bond_type,
            x=sub["date"],
            y=sub["cover_ratio"],
            mode="lines+markers",
            line=dict(color=colour, width=1.5),
            marker=dict(size=5, color=colour),
        ))
    fig.add_hline(y=2.0, line_dash="dash", line_color="rgba(255,255,255,0.3)",
                  annotation_text="2.0× floor", annotation_font_size=10)
    fig.update_layout(
        **_layout(
            title=dict(text="Bid-to-Cover Ratios by Sector", font=dict(size=13)),
            xaxis=dict(title="Date"),
            yaxis=dict(title="Cover Ratio", gridcolor="rgba(255,255,255,0.05)"),
            height=CHART_HEIGHT,
        )
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
    if data_store.last_refresh:
        age = (datetime.now() - data_store.last_refresh).seconds // 60
        dot = "🟢" if age < 60 else "🟡" if age < 1440 else "🔴"
        st.sidebar.caption(f"{dot} Refreshed {age} min ago")
    else:
        st.sidebar.caption("⚪ Live data not yet loaded")
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
