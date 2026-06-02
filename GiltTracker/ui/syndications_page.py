"""
Page 3a: Syndications — dedicated analysis of DMO gilt syndications.

Syndications sit alongside competitive auctions in the DMO's issuance toolkit.
They are used for:
  - New benchmark launches (inaugural lines)
  - Large single-tranche reopenings where auction could cause dislocation
  - Ultra-long conventional and index-linked gilts where depth is narrower

Key metrics: book size, cover ratio, new issue premium (NIP/concession),
lead-manager participation, sector/tenor concentration.
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

from data.store import GiltDataStore
from utils.formatters import fmt_bn, fmt_pct, fmt_yield
from config import SECTOR_COLOURS, PLOTLY_TEMPLATE, CHART_HEIGHT, CHART_HEIGHT_TALL

_METHOD_COLOUR = {"Auction": "#1f77b4", "Syndication": "#e377c2"}
_PURPOSE_COLOUR = {
    "New Benchmark": "#2ca02c",
    "Reopening": "#ff7f0e",
    "Tap": "#9467bd",
    "Indicative": "#555555",
}


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------

def _book_cover_chart(df: pd.DataFrame) -> go.Figure:
    df = df.sort_values("date")
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(
        name="Allocation (£bn)",
        x=df["date"],
        y=df["size_bn"],
        marker_color="#1f77b4",
        opacity=0.7,
    ), secondary_y=False)
    fig.add_trace(go.Bar(
        name="Book Size (£bn)",
        x=df["date"],
        y=df["book_size_bn"],
        marker_color="#aec7e8",
        opacity=0.5,
    ), secondary_y=False)
    fig.add_trace(go.Scatter(
        name="Book Cover (×)",
        x=df["date"],
        y=df["book_cover"],
        mode="lines+markers",
        line=dict(color="#ff7f0e", width=2),
        marker=dict(size=8),
    ), secondary_y=True)
    fig.update_layout(
        title="Syndication Book Size & Cover Ratio",
        barmode="overlay",
        height=CHART_HEIGHT,
        template=PLOTLY_TEMPLATE,
        legend=dict(orientation="h", y=-0.22),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_yaxes(title_text="£bn", secondary_y=False)
    fig.update_yaxes(title_text="Book Cover (×)", secondary_y=True)
    return fig


def _nip_chart(df: pd.DataFrame) -> go.Figure:
    df = df.dropna(subset=["nip_bps"]).sort_values("date")
    colours = [SECTOR_COLOURS.get(t.lower(), "#aaa") for t in df["type"]]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        name="NIP (bps)",
        x=df["date"],
        y=df["nip_bps"],
        mode="lines+markers",
        line=dict(color="#d62728", width=2),
        marker=dict(color=colours, size=10, line=dict(color="white", width=1)),
        text=df["gilt"].str[:30],
        hovertemplate=(
            "<b>%{text}</b><br>"
            "Date: %{x|%d %b %Y}<br>"
            "NIP: %{y:.1f} bps<br>"
        ),
    ))
    # Rolling 3-point average
    if len(df) >= 3:
        rolling = df["nip_bps"].rolling(3, min_periods=2).mean()
        fig.add_trace(go.Scatter(
            name="3-deal rolling avg",
            x=df["date"], y=rolling,
            mode="lines",
            line=dict(color="#aaaaaa", width=1, dash="dot"),
        ))
    fig.update_layout(
        title="New Issue Premium (NIP) — Concession Offered at Syndication",
        xaxis_title="Date",
        yaxis_title="NIP (basis points)",
        height=CHART_HEIGHT,
        template=PLOTLY_TEMPLATE,
        legend=dict(orientation="h", y=-0.2),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def _sector_pie(df: pd.DataFrame, by_col: str = "size_bn",
                title: str = "By Sector (£bn allocated)") -> go.Figure:
    agg = df.dropna(subset=[by_col]).groupby("type")[by_col].sum().reset_index()
    colours = [SECTOR_COLOURS.get(t.lower(), "#aaa") for t in agg["type"]]
    fig = go.Figure(go.Pie(
        labels=agg["type"],
        values=agg[by_col].round(1),
        marker_colors=colours,
        hole=0.45,
        textinfo="label+percent",
    ))
    fig.update_layout(
        title=title,
        height=320,
        template=PLOTLY_TEMPLATE,
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", y=-0.1),
    )
    return fig


def _league_table_chart(lg: pd.DataFrame) -> go.Figure:
    top = lg.head(10)
    fig = go.Figure(go.Bar(
        x=top["deals"],
        y=top["bank"],
        orientation="h",
        marker_color="#e377c2",
        text=top["deals"],
        textposition="outside",
        customdata=top["total_size_bn"].round(1),
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Deals: %{x}<br>"
            "Total size: £%{customdata}bn<br>"
        ),
    ))
    fig.update_layout(
        title="Lead Manager League Table",
        xaxis_title="Number of Mandates",
        height=max(300, len(top) * 35 + 80),
        template=PLOTLY_TEMPLATE,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(autorange="reversed"),
        margin=dict(l=130, r=40, t=50, b=30),
    )
    return fig


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(store: GiltDataStore, selected_fy: str) -> None:
    st.title("Gilt Syndications")
    st.caption(
        "Analysis of DMO syndicated gilt issuance — book demand, new issue premiums, "
        "lead-manager participation, and comparison with competitive auction issuance."
    )

    synd_all = store.get_syndications()
    synd_fy = store.get_syndications(fy=selected_fy)
    synd_completed = store.get_syndications(completed_only=True)
    method_split = store.get_method_split(selected_fy)

    # -----------------------------------------------------------------------
    # KPIs for selected FY
    # -----------------------------------------------------------------------
    c1, c2, c3, c4, c5 = st.columns(5)
    fy_done = synd_fy[synd_fy["status"] == "Completed"]

    c1.metric("Syndications (FY)", len(fy_done))
    c2.metric("Total Syndicated", fmt_bn(fy_done["size_bn"].sum()))
    c3.metric(
        "Avg Book Cover",
        f"{fy_done['book_cover'].mean():.2f}×" if not fy_done.empty else "—",
        delta="demand multiple",
        delta_color="off",
    )
    c4.metric(
        "Avg NIP",
        f"{fy_done['nip_bps'].mean():.1f} bps" if not fy_done.empty else "—",
        delta="new issue concession",
        delta_color="off",
    )
    c5.metric(
        "Synd. Share of Remit",
        fmt_pct(method_split["syndication_pct"]),
        delta=f"Auctions: {fmt_pct(method_split['auction_pct'])}",
        delta_color="off",
    )

    st.divider()

    # -----------------------------------------------------------------------
    # Issuance method split
    # -----------------------------------------------------------------------
    st.subheader(f"Issuance Method Split — FY{selected_fy}")
    col_split, col_hist = st.columns(2)

    with col_split:
        fig_split = go.Figure(go.Pie(
            labels=["Auction", "Syndication"],
            values=[method_split["auction_bn"], method_split["syndication_bn"]],
            marker_colors=["#1f77b4", "#e377c2"],
            hole=0.5,
            textinfo="label+percent+value",
            texttemplate="%{label}<br>%{percent}<br>£%{value:.1f}bn",
        ))
        fig_split.update_layout(
            height=300, template=PLOTLY_TEMPLATE,
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(t=20, b=10),
        )
        st.plotly_chart(fig_split, use_container_width=True)

    with col_hist:
        summary = store.get_syndication_summary()
        remit_df = store.get_all_remits()[["fy", "current_remit"]]
        summary_merged = summary.merge(remit_df, on="fy", how="left")
        summary_merged["synd_pct"] = (
            summary_merged["total_size_bn"] / summary_merged["current_remit"] * 100
        ).round(1)

        fig_hist = go.Figure()
        fig_hist.add_trace(go.Bar(
            name="Syndication £bn",
            x=summary_merged["fy"],
            y=summary_merged["total_size_bn"],
            marker_color="#e377c2",
        ))
        fig_hist.add_trace(go.Scatter(
            name="% of Remit",
            x=summary_merged["fy"],
            y=summary_merged["synd_pct"],
            mode="lines+markers",
            line=dict(color="#ffd700", width=2),
            yaxis="y2",
        ))
        fig_hist.update_layout(
            title="Annual Syndication Volume",
            yaxis=dict(title="£bn"),
            yaxis2=dict(title="% of Remit", overlaying="y", side="right",
                        ticksuffix="%"),
            height=300, template=PLOTLY_TEMPLATE,
            legend=dict(orientation="h", y=-0.25),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_hist, use_container_width=True)

    st.divider()

    # -----------------------------------------------------------------------
    # Syndication detail table for selected FY
    # -----------------------------------------------------------------------
    st.subheader(f"FY{selected_fy} Syndication Detail")
    if synd_fy.empty:
        st.info(f"No syndications recorded for FY{selected_fy}.")
    else:
        def _fmt_lm(s):
            parts = [b.strip() for b in str(s).split(",") if b.strip() and b.strip() != "TBD"]
            return " · ".join(parts)

        display = synd_fy.copy()
        display["date_fmt"] = display["date"].dt.strftime("%d %b %Y")
        display["book_cover_fmt"] = display["book_cover"].apply(
            lambda x: f"{x:.2f}×" if pd.notna(x) else "TBD"
        )
        display["nip_fmt"] = display["nip_bps"].apply(
            lambda x: f"{x:.1f} bps" if pd.notna(x) else "TBD"
        )
        display["book_fmt"] = display["book_size_bn"].apply(
            lambda x: fmt_bn(x) if pd.notna(x) else "TBD"
        )
        display["lead_short"] = display["lead_managers"].apply(_fmt_lm)

        table_cols = {
            "date_fmt": "Date",
            "gilt": "Gilt",
            "size_bn": "Alloc. (£bn)",
            "book_fmt": "Book Size",
            "book_cover_fmt": "Book Cover",
            "nip_fmt": "NIP",
            "yield_pct": "Yield (%)",
            "type": "Sector",
            "purpose": "Purpose",
            "lead_short": "Lead Managers",
            "status": "Status",
        }
        st.dataframe(
            display[list(table_cols.keys())].rename(columns=table_cols),
            use_container_width=True, hide_index=True,
            column_config={
                "Alloc. (£bn)": st.column_config.NumberColumn(format="£%.1f bn"),
                "Yield (%)": st.column_config.NumberColumn(format="%.3f%%"),
            }
        )

    st.divider()

    # -----------------------------------------------------------------------
    # Multi-chart analysis section
    # -----------------------------------------------------------------------
    tab_demand, tab_nip, tab_sector, tab_league, tab_vs_auction = st.tabs([
        "📊 Demand & Books",
        "📉 NIP Trend",
        "🏗️ Sector Mix",
        "🏆 League Table",
        "⚖️ Auctions vs Syndications",
    ])

    with tab_demand:
        st.subheader("Order Book Demand Over Time")
        scope = st.radio("Scope", ["Selected FY", "All History"], horizontal=True,
                         key="demand_scope")
        df_demand = (synd_fy if scope == "Selected FY" else synd_completed).dropna(subset=["book_cover"])
        if df_demand.empty:
            st.info("No completed syndication data for this scope.")
        else:
            st.plotly_chart(_book_cover_chart(df_demand), use_container_width=True)

            # Scatter: book cover vs NIP
            df_scatter = df_demand.dropna(subset=["nip_bps"])
            if len(df_scatter) >= 3:
                colours = [SECTOR_COLOURS.get(t.lower(), "#aaa") for t in df_scatter["type"]]
                fig_sc = go.Figure(go.Scatter(
                    x=df_scatter["nip_bps"],
                    y=df_scatter["book_cover"],
                    mode="markers",
                    marker=dict(color=colours, size=10,
                                line=dict(color="white", width=1)),
                    text=df_scatter["gilt"].str[:28],
                    hovertemplate="<b>%{text}</b><br>NIP: %{x:.1f} bps<br>Cover: %{y:.2f}×",
                ))
                m, b = np.polyfit(df_scatter["nip_bps"], df_scatter["book_cover"], 1)
                x_r = np.linspace(df_scatter["nip_bps"].min(), df_scatter["nip_bps"].max(), 50)
                fig_sc.add_trace(go.Scatter(
                    x=x_r, y=m * x_r + b, mode="lines",
                    line=dict(color="#aaa", dash="dash"), name="OLS fit",
                ))
                corr = np.corrcoef(df_scatter["nip_bps"], df_scatter["book_cover"])[0, 1]
                fig_sc.update_layout(
                    title=f"NIP vs Book Cover (r = {corr:.2f})",
                    xaxis_title="New Issue Premium (bps)",
                    yaxis_title="Book Cover (×)",
                    height=360,
                    template=PLOTLY_TEMPLATE,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_sc, use_container_width=True)
                st.caption(
                    "Higher NIP (bigger concession) tends to attract larger books. "
                    "A negative correlation here would indicate over-pricing stress."
                )

    with tab_nip:
        st.subheader("New Issue Premium — Trend & Volatility")
        st.caption(
            "The NIP is the concession (in basis points) offered to investors versus the "
            "prevailing secondary market yield. A rising NIP signals worsening market "
            "conditions or investor pushback on price."
        )
        scope_nip = st.radio("Scope", ["Selected FY", "All History"], horizontal=True,
                             key="nip_scope")
        df_nip = (synd_fy if scope_nip == "Selected FY" else synd_completed).dropna(subset=["nip_bps"])
        if df_nip.empty:
            st.info("No NIP data for this scope.")
        else:
            st.plotly_chart(_nip_chart(df_nip), use_container_width=True)

            # NIP by sector box
            fig_box = go.Figure()
            for sector, colour in SECTOR_COLOURS.items():
                sub = df_nip[df_nip["type"].str.lower() == sector]
                if not sub.empty:
                    fig_box.add_trace(go.Box(
                        name=sector.capitalize(),
                        y=sub["nip_bps"],
                        marker_color=colour,
                        boxmean=True,
                    ))
            fig_box.update_layout(
                title="NIP Distribution by Sector",
                yaxis_title="NIP (basis points)",
                height=320,
                template=PLOTLY_TEMPLATE,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_box, use_container_width=True)

            # Summary table by sector
            nip_sum = (
                df_nip.groupby("type")["nip_bps"]
                .agg(mean="mean", median="median", min="min", max="max", count="count")
                .round(1)
                .reset_index()
                .rename(columns={
                    "type": "Sector", "mean": "Mean (bps)",
                    "median": "Median (bps)", "min": "Min (bps)",
                    "max": "Max (bps)", "count": "Deals"
                })
            )
            st.dataframe(nip_sum, use_container_width=True, hide_index=True)

    with tab_sector:
        st.subheader("Sector Concentration")
        scope_sec = st.radio("Scope", ["Selected FY", "All History"], horizontal=True,
                             key="sec_scope")
        df_sec = (synd_fy if scope_sec == "Selected FY" else synd_completed)
        if df_sec.empty:
            st.info("No data.")
        else:
            col_p1, col_p2 = st.columns(2)
            with col_p1:
                st.plotly_chart(_sector_pie(df_sec, "size_bn",
                    "By Sector — Allocated £bn"), use_container_width=True)
            with col_p2:
                count_pie = df_sec.groupby("type")["size_bn"].count().reset_index()
                count_pie.columns = ["type", "count"]
                colours = [SECTOR_COLOURS.get(t.lower(), "#aaa") for t in count_pie["type"]]
                fig_c = go.Figure(go.Pie(
                    labels=count_pie["type"], values=count_pie["count"],
                    marker_colors=colours, hole=0.45,
                    textinfo="label+percent",
                ))
                fig_c.update_layout(
                    title="By Sector — Number of Deals",
                    height=320, template=PLOTLY_TEMPLATE,
                    paper_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_c, use_container_width=True)

            # New benchmark vs reopening breakdown
            purpose_agg = (
                df_sec.groupby("purpose")["size_bn"].agg(["sum", "count"])
                .reset_index()
                .rename(columns={"purpose": "Purpose", "sum": "Total £bn", "count": "Deals"})
                .round(1)
            )
            st.subheader("By Purpose")
            st.dataframe(purpose_agg, use_container_width=True, hide_index=True)

            # Stacked bar by FY and sector
            hist_stack = (
                synd_completed.groupby(["fy", "type"])["size_bn"]
                .sum().reset_index()
            )
            fig_stack = go.Figure()
            for sector, colour in SECTOR_COLOURS.items():
                sub = hist_stack[hist_stack["type"].str.lower() == sector]
                if not sub.empty:
                    fig_stack.add_trace(go.Bar(
                        name=sector.capitalize(),
                        x=sub["fy"], y=sub["size_bn"],
                        marker_color=colour,
                    ))
            fig_stack.update_layout(
                barmode="stack",
                title="Annual Syndication Volume by Sector",
                yaxis_title="£bn", xaxis_title="Fiscal Year",
                height=CHART_HEIGHT, template=PLOTLY_TEMPLATE,
                legend=dict(orientation="h", y=-0.2),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_stack, use_container_width=True)

    with tab_league:
        st.subheader("Lead Manager League Table")
        st.caption(
            "Banks appointed as bookrunners/lead managers on DMO syndications. "
            "The DMO selects from its primary dealer panel for each deal."
        )
        scope_lg = st.radio("Scope", ["Selected FY", "All History"], horizontal=True,
                            key="lg_scope")
        df_lg_source = synd_fy if scope_lg == "Selected FY" else synd_completed
        lg = store.get_lead_manager_league(fy=selected_fy if scope_lg == "Selected FY" else None)

        if lg.empty:
            st.info("No league table data available.")
        else:
            col_chart_lg, col_tbl_lg = st.columns([2, 1])
            with col_chart_lg:
                st.plotly_chart(_league_table_chart(lg), use_container_width=True)
            with col_tbl_lg:
                st.dataframe(
                    lg.rename(columns={
                        "bank": "Bank", "deals": "Mandates",
                        "total_size_bn": "Total Size (£bn)"
                    }).assign(**{"Total Size (£bn)": lg["total_size_bn"].round(1)}),
                    use_container_width=True, hide_index=True,
                )

            # Market share donut
            top_banks = lg.head(8)
            others = lg.iloc[8:]["total_size_bn"].sum()
            labels = list(top_banks["bank"]) + (["Others"] if others > 0 else [])
            values = list(top_banks["total_size_bn"]) + ([others] if others > 0 else [])
            fig_share = go.Figure(go.Pie(
                labels=labels, values=[round(v, 1) for v in values],
                hole=0.45, textinfo="label+percent",
            ))
            fig_share.update_layout(
                title="Market Share by Total Size",
                height=350, template=PLOTLY_TEMPLATE,
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_share, use_container_width=True)

    with tab_vs_auction:
        st.subheader("Syndications vs Auctions — Comparative Statistics")
        st.caption(
            "Cover ratios between the two methods are not directly comparable "
            "(auction cover = competitive bids / amount; syndication cover = order-book / allocation). "
            "However, both signal demand intensity."
        )

        auctions_df = store.get_auction_results(fy=selected_fy)
        synd_compare = synd_fy[synd_fy["status"] == "Completed"].copy()

        col_av, col_sv = st.columns(2)

        def _stat_box(df, cover_col, label):
            if df.empty:
                return pd.DataFrame()
            return pd.DataFrame({
                "Metric": [
                    "Number of operations",
                    "Total size (£bn)",
                    "Avg size per deal (£bn)",
                    "Avg cover ratio",
                    "Min cover ratio",
                    "Max cover ratio",
                ],
                label: [
                    len(df),
                    fmt_bn(df["size_bn"].sum()),
                    fmt_bn(df["size_bn"].mean(), dp=2),
                    f"{df[cover_col].mean():.2f}×" if pd.notna(df[cover_col].mean()) else "—",
                    f"{df[cover_col].min():.2f}×" if pd.notna(df[cover_col].min()) else "—",
                    f"{df[cover_col].max():.2f}×" if pd.notna(df[cover_col].max()) else "—",
                ],
            })

        a_stats = _stat_box(auctions_df, "cover_ratio", "Auctions")
        s_stats = _stat_box(synd_compare, "book_cover", "Syndications")

        with col_av:
            st.caption("**Auctions**")
            if not a_stats.empty:
                st.dataframe(a_stats, use_container_width=True, hide_index=True)
        with col_sv:
            st.caption("**Syndications**")
            if not s_stats.empty:
                st.dataframe(s_stats, use_container_width=True, hide_index=True)

        # Side-by-side cover ratio distribution
        if not auctions_df.empty and not synd_compare.empty:
            fig_cmp = go.Figure()
            fig_cmp.add_trace(go.Histogram(
                x=auctions_df["cover_ratio"].dropna(),
                name="Auction Cover",
                marker_color="#1f77b4",
                opacity=0.7,
                nbinsx=15,
            ))
            fig_cmp.add_trace(go.Histogram(
                x=synd_compare["book_cover"].dropna(),
                name="Syndication Book Cover",
                marker_color="#e377c2",
                opacity=0.7,
                nbinsx=10,
            ))
            fig_cmp.update_layout(
                barmode="overlay",
                title="Cover Ratio Distribution: Auctions vs Syndications",
                xaxis_title="Cover Ratio (×)",
                yaxis_title="Count",
                height=320,
                template=PLOTLY_TEMPLATE,
                legend=dict(orientation="h", y=-0.2),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_cmp, use_container_width=True)

        # Combined timeline: both methods on same chart, colour-coded
        st.subheader("Combined Issuance Timeline")
        a_timeline = auctions_df[["date", "size_bn", "yield_pct", "type"]].copy()
        a_timeline["method"] = "Auction"
        a_timeline["label"] = auctions_df["gilt"].str[:30]

        s_timeline = synd_compare[["date", "size_bn", "yield_pct", "type"]].copy()
        s_timeline["method"] = "Syndication"
        s_timeline["label"] = synd_compare["gilt"].str[:30]

        timeline = pd.concat([a_timeline, s_timeline]).sort_values("date")
        if not timeline.empty:
            fig_tl = go.Figure()
            for method, colour in _METHOD_COLOUR.items():
                sub = timeline[timeline["method"] == method]
                fig_tl.add_trace(go.Scatter(
                    name=method,
                    x=sub["date"], y=sub["size_bn"],
                    mode="markers",
                    marker=dict(
                        color=colour, size=sub["size_bn"] * 3.5,
                        opacity=0.8, line=dict(width=1, color="white"),
                    ),
                    text=sub["label"],
                    hovertemplate=(
                        "<b>%{text}</b><br>"
                        "Date: %{x|%d %b %Y}<br>"
                        "Size: £%{y:.1f}bn"
                    ),
                ))
            fig_tl.update_layout(
                title=f"FY{selected_fy} Auction & Syndication Operations",
                xaxis_title="Date", yaxis_title="Size (£bn)",
                height=CHART_HEIGHT, template=PLOTLY_TEMPLATE,
                legend=dict(orientation="h", y=-0.2),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_tl, use_container_width=True)
