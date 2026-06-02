"""
Page 6: Econometric Forecasting — ARIMA, OLS, seasonal decomposition, Monte Carlo.
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")

from data.store import GiltDataStore
from analysis.forecasting import (
    fit_arima_annual,
    quarterly_seasonal_factors,
    monte_carlo_issuance,
    build_full_forecast,
    forecast_sector_splits,
)
from ui.components import fan_chart, mc_distribution_chart
from utils.formatters import fmt_bn, fmt_pct
from config import PLOTLY_TEMPLATE, CHART_HEIGHT, CHART_HEIGHT_TALL, NEXT_FY


def render(store: GiltDataStore, selected_fy: str) -> None:
    st.title("🔬 Econometric Forecasting")
    st.caption(
        "ARIMA time-series models, OLS regression (borrowing → issuance), "
        "seasonal decomposition, and Monte Carlo uncertainty quantification."
    )

    annual_df = store.get_all_remits()
    psnd_df = store.get_psnd()
    quarterly_df = store.quarterly_issuance

    # -----------------------------------------------------------------------
    # Build full forecast (cached inside the page session)
    # -----------------------------------------------------------------------
    @st.cache_resource
    def _run_forecast(_annual, _psnd, _quarterly):
        return build_full_forecast(_annual, _psnd, _quarterly, horizon=5)

    with st.spinner("Running forecasting models…"):
        fc = _run_forecast(annual_df, psnd_df, quarterly_df)

    tabs = st.tabs([
        "📈 ARIMA", "📊 OLS Bridge",
        "🌊 Seasonal Decomp", "🎲 Monte Carlo",
        "🎯 Combined Forecast"
    ])

    # -----------------------------------------------------------------------
    # Tab 1: ARIMA
    # -----------------------------------------------------------------------
    with tabs[0]:
        st.subheader("ARIMA Time-Series Forecast")
        st.caption(
            "Fitted on annual actual/revised gilt issuance data since FY2010-11. "
            "Order selected as ARIMA(1,1,1); d=1 reflects non-stationarity of the series."
        )

        arima_fc = fc["arima"]
        model_name = arima_fc["model"].iloc[0] if not arima_fc.empty else "ARIMA"

        # Actual series
        actual_series = annual_df.dropna(subset=["actual"])

        col_left, col_right = st.columns([3, 1])
        with col_left:
            st.plotly_chart(
                fan_chart(
                    annual_df,
                    arima_fc.rename(columns={"forecast": "consensus"}),
                    title=f"Annual Gilt Issuance — {model_name} Forecast",
                ),
                use_container_width=True,
            )
        with col_right:
            st.dataframe(
                arima_fc[["fy", "forecast", "lower_95", "upper_95"]].rename(
                    columns={"fy": "FY", "forecast": "Forecast £bn",
                             "lower_95": "Lower 95%", "upper_95": "Upper 95%"}
                ).round(1),
                use_container_width=True,
                hide_index=True,
            )

        # Residual diagnostics
        if len(actual_series) > 3:
            with st.expander("📋 Residual Diagnostics"):
                y = actual_series["actual"].values
                trend_fit = np.polyfit(np.arange(len(y)), y, 1)
                residuals = y - np.polyval(trend_fit, np.arange(len(y)))

                fig_res = make_subplots(rows=1, cols=2, subplot_titles=[
                    "Residuals Over Time", "Residual Distribution"
                ])
                fig_res.add_trace(go.Scatter(
                    x=actual_series["fy"].tolist(),
                    y=residuals,
                    mode="lines+markers",
                    marker=dict(color="#1f77b4"),
                ), row=1, col=1)
                fig_res.add_hline(y=0, line_dash="dash", line_color="#aaa", row=1, col=1)
                fig_res.add_trace(go.Histogram(
                    x=residuals,
                    nbinsx=10,
                    marker_color="#ff7f0e",
                    opacity=0.7,
                ), row=1, col=2)
                fig_res.update_layout(
                    height=280, template=PLOTLY_TEMPLATE,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                fig_res.update_xaxes(type="category", col=1)
                st.plotly_chart(fig_res, use_container_width=True)

                rmse = np.sqrt(np.mean(residuals**2))
                st.caption(f"RMSE: {fmt_bn(rmse, dp=1)} | Skewness: {pd.Series(residuals).skew():.2f}")

    # -----------------------------------------------------------------------
    # Tab 2: OLS Bridge
    # -----------------------------------------------------------------------
    with tabs[1]:
        st.subheader("OLS Regression: PSND & Borrowing → Issuance")
        bridge = fc["bridge"]

        colA, colB = st.columns([1, 2])
        with colA:
            params = bridge.get("params", {})
            r2 = bridge.get("r_squared", 0)
            st.metric("R²", f"{r2:.3f}")
            st.metric("Model", "OLS (statsmodels)" if bridge.get("model") else "Ratio fallback")
            if params:
                st.caption("**Coefficients:**")
                for k, v in params.items():
                    if k != "ratio":
                        st.caption(f"  `{k}`: {v:.3f}")

        with colB:
            # Actual vs fitted
            merged = psnd_df.merge(
                annual_df[["fy", "actual", "current_remit"]].assign(
                    issuance=lambda d: d["actual"].fillna(d["current_remit"])
                ), on="fy"
            ).dropna(subset=["borrowing_bn", "issuance"])

            fitted = [
                bridge["predict"](row["borrowing_bn"], row["psnd_pct_gdp"])
                for _, row in merged.iterrows()
            ]
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=merged["fy"], y=merged["issuance"],
                mode="lines+markers", name="Actual",
                line=dict(color="#1f77b4", width=2),
            ))
            fig.add_trace(go.Scatter(
                x=merged["fy"], y=fitted,
                mode="lines+markers", name="Fitted",
                line=dict(color="#ff7f0e", width=2, dash="dash"),
            ))
            fig.update_layout(
                title="Actual vs OLS-Fitted Issuance",
                yaxis_title="£bn",
                xaxis=dict(type="category"),
                height=CHART_HEIGHT,
                template=PLOTLY_TEMPLATE,
                legend=dict(orientation="h", y=-0.2),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)

        if bridge.get("summary"):
            with st.expander("📋 Full OLS Summary Table"):
                st.code(bridge["summary"], language="text")

    # -----------------------------------------------------------------------
    # Tab 3: Seasonal Decomposition
    # -----------------------------------------------------------------------
    with tabs[2]:
        st.subheader("Seasonal Decomposition of Quarterly Issuance")
        st.caption(
            "STL-style decomposition separating trend, seasonal patterns (Q1–Q4 "
            "fiscal quarters), and irregular residuals."
        )

        seasonal_df = fc["seasonal"]
        if not seasonal_df.empty:
            # Bar chart of seasonal factors
            fig = go.Figure(go.Bar(
                x=seasonal_df["quarter"],
                y=(seasonal_df["mean_share"] * 100).round(1),
                error_y=dict(
                    type="data",
                    array=(seasonal_df["std_share"] * 100).tolist(),
                    visible=True,
                ),
                marker_color=["#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd"],
                text=(seasonal_df["mean_share"] * 100).round(1).astype(str) + "%",
                textposition="outside",
            ))
            fig.update_layout(
                title="Seasonal Factors: Average Quarterly Share of Annual Issuance",
                yaxis_title="% of full-year total",
                yaxis=dict(ticksuffix="%"),
                height=320,
                template=PLOTLY_TEMPLATE,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)

            # Quarterly time series with trend
            q_series = quarterly_df.sort_values("q_start")
            if not q_series.empty:
                fig2 = go.Figure()
                fig2.add_trace(go.Bar(
                    x=q_series["q_start"],
                    y=q_series["total"],
                    name="Quarterly Issuance",
                    marker_color="#1f77b4",
                    opacity=0.7,
                ))
                # Rolling 4Q average as trend
                q_series_sorted = q_series.set_index("q_start")["total"].sort_index()
                trend = q_series_sorted.rolling(4, min_periods=2).mean()
                fig2.add_trace(go.Scatter(
                    x=trend.index, y=trend.values,
                    name="4Q Rolling Average (Trend)",
                    mode="lines",
                    line=dict(color="#ff7f0e", width=2),
                ))
                fig2.update_layout(
                    title="Quarterly Issuance with Trend",
                    yaxis_title="£bn",
                    height=CHART_HEIGHT,
                    template=PLOTLY_TEMPLATE,
                    legend=dict(orientation="h", y=-0.2),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig2, use_container_width=True)

            st.dataframe(
                seasonal_df.assign(
                    **{"Mean Share (%)": (seasonal_df["mean_share"] * 100).round(2),
                       "Std Dev (%)": (seasonal_df["std_share"] * 100).round(2)}
                )[["quarter", "Mean Share (%)", "Std Dev (%)", "historical_quarters"]].rename(
                    columns={"quarter": "Quarter", "historical_quarters": "Obs."}
                ),
                use_container_width=True, hide_index=True
            )

    # -----------------------------------------------------------------------
    # Tab 4: Monte Carlo
    # -----------------------------------------------------------------------
    with tabs[3]:
        st.subheader("Monte Carlo Uncertainty Analysis")
        st.caption(
            "10,000-path simulation drawing from the residual distribution of the "
            "combined ARIMA+OLS forecast. Produces probability-weighted issuance ranges."
        )

        combined_fc = fc.get("combined", pd.DataFrame())
        mc_df = fc.get("monte_carlo", pd.DataFrame())

        if not mc_df.empty:
            # Fiscal year selector for distribution chart
            fc_fys = mc_df["fy"].tolist()
            selected_mc_fy = st.selectbox("Select Forecast Year", fc_fys, index=0)

            col_dist, col_table = st.columns([2, 1])
            with col_dist:
                st.plotly_chart(
                    mc_distribution_chart(mc_df, selected_mc_fy),
                    use_container_width=True,
                )
            with col_table:
                row = mc_df[mc_df["fy"] == selected_mc_fy].iloc[0]
                pct_table = pd.DataFrame({
                    "Percentile": ["5th", "25th", "50th (Median)", "75th", "95th"],
                    "Issuance (£bn)": [
                        fmt_bn(row["p5"]),  fmt_bn(row["p25"]),
                        fmt_bn(row["p50"]), fmt_bn(row["p75"]), fmt_bn(row["p95"]),
                    ]
                })
                st.dataframe(pct_table, use_container_width=True, hide_index=True)

                p_central = row["p50"]
                range_90 = row["p95"] - row["p5"]
                st.metric("90% Range", fmt_bn(range_90))
                st.metric("Central Estimate", fmt_bn(p_central))

            # Fan chart for all forecast years
            if not combined_fc.empty:
                st.plotly_chart(
                    fan_chart(annual_df, mc_df.rename(columns={"p50": "consensus"}),
                              title="Monte Carlo Fan Chart — All Forecast Years"),
                    use_container_width=True,
                )

            # Full percentile table
            st.dataframe(
                mc_df.round(1).rename(columns={
                    "fy": "FY", "p5": "P5", "p25": "P25",
                    "p50": "Median", "p75": "P75", "p95": "P95"
                }),
                use_container_width=True, hide_index=True,
                column_config={c: st.column_config.NumberColumn(format="£%.1f bn")
                               for c in ["P5", "P25", "Median", "P75", "P95"]},
            )

    # -----------------------------------------------------------------------
    # Tab 5: Combined Forecast
    # -----------------------------------------------------------------------
    with tabs[4]:
        st.subheader("Combined Forecast: ARIMA + OLS Bridge")
        st.caption(
            "Consensus forecast blending ARIMA time-series (50% weight) and "
            "OLS-bridge model (50% weight). Sector splits derived from recent allocation history."
        )

        combined = fc.get("combined", pd.DataFrame())
        if combined.empty:
            st.warning("Insufficient data to generate combined forecast.")
            return

        # Headline table
        st.subheader(f"Point Forecast — Next {len(combined)} Years")

        display_rows = []
        for _, row in combined.iterrows():
            splits = forecast_sector_splits(row["consensus"], annual_df)
            display_rows.append({
                "FY": row["fy"],
                "Consensus £bn": round(row["consensus"], 1),
                "ARIMA £bn": round(row["forecast"], 1),
                "OLS Bridge £bn": round(row.get("bridge_forecast", row["forecast"]), 1),
                "Short": round(splits["short"], 1),
                "Medium": round(splits["medium"], 1),
                "Long": round(splits["long"], 1),
                "Linkers": round(splits["linkers"], 1),
            })

        display_df = pd.DataFrame(display_rows)
        st.dataframe(
            display_df,
            use_container_width=True, hide_index=True,
            column_config={
                c: st.column_config.NumberColumn(format="£%.1f bn")
                for c in ["Consensus £bn", "ARIMA £bn", "OLS Bridge £bn",
                          "Short", "Medium", "Long", "Linkers"]
            }
        )

        # Fan chart with MC bands
        mc_data = fc.get("monte_carlo", pd.DataFrame())
        st.plotly_chart(
            fan_chart(annual_df, mc_data.rename(columns={"p50": "consensus"}),
                      title="Combined Forecast with 68% / 95% Confidence Intervals"),
            use_container_width=True,
        )

        # Sector split forecast bar chart
        sector_cols = ["Short", "Medium", "Long", "Linkers"]
        colour_list = ["#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd"]
        fig = go.Figure()
        for col, colour in zip(sector_cols, colour_list):
            fig.add_trace(go.Bar(
                name=col,
                x=display_df["FY"],
                y=display_df[col],
                marker_color=colour,
            ))
        fig.update_layout(
            barmode="stack",
            title="Forecast Sector Split (Combined Model)",
            yaxis_title="£bn",
            xaxis=dict(type="category"),
            height=CHART_HEIGHT,
            template=PLOTLY_TEMPLATE,
            legend=dict(orientation="h", y=-0.2),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)

        # Model weight explanation
        with st.expander("ℹ️ Methodology Notes"):
            st.markdown("""
**Model combination**: Equal-weighted average of ARIMA(1,1,1) and OLS bridge.
In production, weights should be updated using expanding-window out-of-sample RMSE.

**ARIMA**: Fitted on FY2010–11 through FY2025–26 actual/revised issuance.
First-differencing (d=1) addresses the non-stationary trend in nominal issuance.

**OLS Bridge**: Regresses gross issuance on annual PSNB, PSND/GDP ratio, and a
post-COVID structural dummy. Uses OBR central-scenario PSND projections for
forward inputs.

**Sector splits**: Weighted average of last 5 years' sector allocation proportions.
In practice, the DMO communicates sector preferences in the annual remit and quarterly
operations notices, which should override model-implied splits.

**Monte Carlo**: Normal shocks calibrated to 8% standard deviation (annualised)
applied multiplicatively across simulation paths. 10,000 iterations.
            """)
