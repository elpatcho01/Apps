"""
Econometric forecasting models for UK gilt issuance.

Models implemented:
  1. ARIMA  – univariate total issuance time series
  2. OLS bridge  – PSND/borrowing → gross gilt issuance
  3. Seasonal decomposition  – quarterly pace patterns
  4. Sector allocator  – predict sector splits for a given total
  5. Monte Carlo  – uncertainty envelopes around point forecasts
"""

import warnings
import numpy as np
import pandas as pd
from typing import Optional
from datetime import date, datetime

# statsmodels
try:
    from statsmodels.tsa.arima.model import ARIMA
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    from statsmodels.tsa.seasonal import seasonal_decompose
    from statsmodels.regression.linear_model import OLS
    from statsmodels.tools import add_constant
    _STATSMODELS_OK = True
except ImportError:
    _STATSMODELS_OK = False

# sklearn
try:
    from sklearn.linear_model import LinearRegression, Ridge
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import cross_val_score
    _SKLEARN_OK = True
except ImportError:
    _SKLEARN_OK = False

warnings.filterwarnings("ignore")


# ---------------------------------------------------------------------------
# 1.  ARIMA on annual total issuance
# ---------------------------------------------------------------------------

def fit_arima_annual(annual_df: pd.DataFrame,
                     horizon: int = 5) -> pd.DataFrame:
    """
    Fits ARIMA(1,1,1) on annual total actual / revised remit issuance.
    Returns a DataFrame with fy, forecast, lower_95, upper_95.
    """
    series = (
        annual_df.dropna(subset=["actual"])
        .set_index("fy")["actual"]
    )
    if len(series) < 6 or not _STATSMODELS_OK:
        return _naive_trend_forecast(series, horizon)

    try:
        model = ARIMA(series.values, order=(1, 1, 1))
        fit = model.fit()
        fc = fit.get_forecast(steps=horizon)
        mean = fc.predicted_mean
        ci = fc.conf_int(alpha=0.05)

        last_fy_year = int(series.index[-1][:4])
        fys = [
            f"{last_fy_year + i}-{str(last_fy_year + i + 1)[2:]}"
            for i in range(1, horizon + 1)
        ]
        return pd.DataFrame({
            "fy": fys,
            "forecast": mean,
            "lower_95": ci[:, 0],
            "upper_95": ci[:, 1],
            "model": "ARIMA(1,1,1)",
        })
    except Exception:
        return _naive_trend_forecast(series, horizon)


def _naive_trend_forecast(series: pd.Series, horizon: int) -> pd.DataFrame:
    n = len(series)
    x = np.arange(n)
    y = series.values.astype(float)
    m, b = np.polyfit(x, y, 1)
    last_fy_year = int(series.index[-1][:4])
    residual_std = np.std(y - (m * x + b))

    rows = []
    for i in range(1, horizon + 1):
        forecast = m * (n + i - 1) + b
        rows.append({
            "fy": f"{last_fy_year + i}-{str(last_fy_year + i + 1)[2:]}",
            "forecast": max(forecast, 50),
            "lower_95": max(forecast - 1.96 * residual_std * i**0.5, 50),
            "upper_95": forecast + 1.96 * residual_std * i**0.5,
            "model": "Trend (OLS)",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 2.  OLS bridge: borrowing requirement → gross gilt issuance
# ---------------------------------------------------------------------------

def fit_psnd_bridge(psnd_df: pd.DataFrame,
                    remit_df: pd.DataFrame) -> dict:
    """
    Regresses gross annual gilt issuance on:
      - annual PSNB (public sector net borrowing)
      - PSND lagged 1 year (% GDP)
      - post-2020 dummy

    Returns a dict with model summary stats and predict() callable.
    """
    merged = psnd_df.merge(
        remit_df[["fy", "actual", "revised_remit"]].assign(
            issuance=lambda d: d["actual"].fillna(d["revised_remit"])
        ),
        on="fy"
    ).dropna(subset=["borrowing_bn", "psnd_pct_gdp", "issuance"])

    X = merged[["borrowing_bn", "psnd_pct_gdp"]].copy()
    X["covid_dummy"] = (merged["fy"] == "2020-21").astype(int)
    y = merged["issuance"]

    if len(X) < 5 or not _STATSMODELS_OK:
        return _simple_bridge(merged)

    try:
        X_const = add_constant(X)
        model = OLS(y, X_const).fit()
        return {
            "model": model,
            "r_squared": model.rsquared,
            "params": model.params.to_dict(),
            "predict": lambda borrow, psnd_pct: float(
                model.predict(
                    add_constant(
                        pd.DataFrame([{"borrowing_bn": borrow,
                                       "psnd_pct_gdp": psnd_pct,
                                       "covid_dummy": 0}]),
                        has_constant="add"
                    )
                )[0]
            ),
            "summary": str(model.summary()),
        }
    except Exception:
        return _simple_bridge(merged)


def _simple_bridge(merged: pd.DataFrame) -> dict:
    corr = np.corrcoef(merged["borrowing_bn"], merged["issuance"])[0, 1]
    ratio = merged["issuance"].mean() / merged["borrowing_bn"].mean()
    return {
        "model": None,
        "r_squared": corr**2,
        "params": {"ratio": ratio},
        "predict": lambda borrow, psnd_pct: borrow * ratio,
        "summary": f"Simple ratio model: issuance ≈ {ratio:.2f} × borrowing",
    }


# ---------------------------------------------------------------------------
# 3.  Seasonal quarterly pace decomposition
# ---------------------------------------------------------------------------

def quarterly_seasonal_factors(quarterly_df: pd.DataFrame) -> pd.DataFrame:
    """
    Estimates average seasonal pace for each quarter relative to
    the full-year total.

    Returns a DataFrame: quarter, mean_share, std_share, historical_quarters.
    """
    df = quarterly_df.copy()
    fy_totals = df.groupby("fy")["total"].sum().rename("fy_total")
    df = df.merge(fy_totals, on="fy")
    df["q_share"] = df["total"] / df["fy_total"]

    return (
        df.groupby("quarter")["q_share"]
        .agg(mean_share="mean", std_share="std",
             historical_quarters="count")
        .reset_index()
        .sort_values("quarter")
    )


def predict_unannounced_quarters(
    fy: str,
    remit_total: float,
    completed_quarters: pd.DataFrame,
    seasonal_factors: pd.DataFrame,
) -> pd.DataFrame:
    """
    For quarters where no issuance has been announced/completed, predict
    issuance using remaining remit and seasonal factors.

    completed_quarters: rows from QUARTERLY_ISSUANCE with announced=True.
    Returns a DataFrame with predicted quarter totals and sectors.
    """
    all_quarters = ["Q1", "Q2", "Q3", "Q4"]
    done = set(completed_quarters["quarter"].tolist())
    remaining_qs = [q for q in all_quarters if q not in done]

    if not remaining_qs:
        return pd.DataFrame()

    issued_total = completed_quarters["total"].sum()
    remaining_total = remit_total - issued_total

    sf = seasonal_factors.set_index("quarter")["mean_share"]
    remaining_sf_sum = sf.reindex(remaining_qs).sum()

    rows = []
    for q in remaining_qs:
        q_weight = sf.get(q, 0.25)
        q_total = remaining_total * (q_weight / remaining_sf_sum)
        rows.append({
            "fy": fy,
            "quarter": q,
            "total": round(q_total, 1),
            "source": "Predicted",
        })

    pred_df = pd.DataFrame(rows)
    # Add sector splits using historical average proportions
    if not completed_quarters.empty:
        for sector in ["short", "medium", "long", "linkers"]:
            sector_share = (
                completed_quarters[sector].sum()
                / completed_quarters["total"].sum()
            )
            pred_df[sector] = (pred_df["total"] * sector_share).round(1)
    return pred_df


# ---------------------------------------------------------------------------
# 4.  Sector allocator  – forecast sector splits given total
# ---------------------------------------------------------------------------

def forecast_sector_splits(
    total_issuance: float,
    historical_df: pd.DataFrame,
    n_recent_years: int = 5,
) -> dict:
    """
    Given a total annual issuance forecast, predict sector splits
    using weighted average of recent years' proportions.

    Returns dict: short, medium, long, linkers (all in £bn).
    """
    recent = historical_df.dropna(subset=["short"]).tail(n_recent_years)
    if recent.empty:
        base = {"short": 0.29, "medium": 0.27, "long": 0.24, "linkers": 0.20}
        return {k: round(total_issuance * v, 1) for k, v in base.items()}

    splits = {}
    for sector in ["short", "medium", "long", "linkers"]:
        share = (
            recent[sector] / (recent["short"] + recent["medium"]
                              + recent["long"] + recent["linkers"])
        ).mean()
        splits[sector] = round(total_issuance * share, 1)
    return splits


# ---------------------------------------------------------------------------
# 5.  Monte Carlo uncertainty envelopes
# ---------------------------------------------------------------------------

def monte_carlo_issuance(
    base_forecast: pd.Series,
    n_simulations: int = 10_000,
    shock_std_pct: float = 0.08,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Generates Monte Carlo fan-chart bands around a base forecast path.

    base_forecast: indexed by fy, values = £bn
    Returns percentile bands: p5, p25, p50 (median), p75, p95.
    """
    rng = np.random.default_rng(seed)
    n = len(base_forecast)
    shocks = rng.normal(0, shock_std_pct, size=(n_simulations, n))
    paths = base_forecast.values[None, :] * (1 + shocks)

    return pd.DataFrame({
        "fy": base_forecast.index,
        "p5":  np.percentile(paths, 5,  axis=0),
        "p25": np.percentile(paths, 25, axis=0),
        "p50": np.percentile(paths, 50, axis=0),
        "p75": np.percentile(paths, 75, axis=0),
        "p95": np.percentile(paths, 95, axis=0),
    })


# ---------------------------------------------------------------------------
# 6.  Full forward issuance forecast combining all models
# ---------------------------------------------------------------------------

def build_full_forecast(
    annual_df: pd.DataFrame,
    psnd_df: pd.DataFrame,
    quarterly_df: pd.DataFrame,
    horizon: int = 5,
) -> dict:
    """
    Runs all models and returns a unified forecast dict.
    """
    arima_fc = fit_arima_annual(annual_df, horizon)
    bridge = fit_psnd_bridge(psnd_df, annual_df)
    seasonal = quarterly_seasonal_factors(quarterly_df)

    # PSND-bridge point estimates using OBR PSND forecasts
    bridge_preds = []
    for _, row in psnd_df[psnd_df["fy"].isin(arima_fc["fy"])].iterrows():
        pred = bridge["predict"](row["borrowing_bn"], row["psnd_pct_gdp"])
        bridge_preds.append({"fy": row["fy"], "bridge_forecast": pred})
    bridge_df = pd.DataFrame(bridge_preds)

    combined = arima_fc.merge(bridge_df, on="fy", how="left")
    combined["consensus"] = combined.apply(
        lambda r: (
            r["forecast"] * 0.5 + r["bridge_forecast"] * 0.5
            if pd.notna(r.get("bridge_forecast"))
            else r["forecast"]
        ),
        axis=1,
    )

    mc = monte_carlo_issuance(
        pd.Series(combined["consensus"].values, index=combined["fy"])
    )

    return {
        "arima": arima_fc,
        "bridge": bridge,
        "seasonal": seasonal,
        "combined": combined,
        "monte_carlo": mc,
    }
