"""Number and date formatting helpers."""

from datetime import date, datetime
from typing import Optional
import pandas as pd


def fmt_bn(value: Optional[float], dp: int = 1, suffix: str = "bn") -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return f"£{value:,.{dp}f}{suffix}"


def fmt_pct(value: Optional[float], dp: int = 1) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return f"{value:.{dp}f}%"


def fmt_yield(value: Optional[float]) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return f"{value:.3f}%"


def fmt_cover(value: Optional[float]) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return f"{value:.2f}×"


def fmt_fy(fy: str) -> str:
    return f"FY{fy}"


def fy_to_year_range(fy: str) -> str:
    start = fy[:4]
    end = str(int(fy[:4]) + 1)
    return f"{start}/{end[2:]}"


def weeks_remaining_in_fy(fy: str, as_of: Optional[date] = None) -> int:
    as_of = as_of or date.today()
    fy_end = date(int(fy[:4]) + 1, 3, 31)
    if as_of >= fy_end:
        return 0
    return max(0, (fy_end - as_of).days // 7)


def progress_colour(pct: float) -> str:
    if pct >= 100:
        return "#2ca02c"
    if pct >= 85:
        return "#ff7f0e"
    return "#1f77b4"


def delta_arrow(current: float, previous: float) -> str:
    if current > previous:
        return "↑"
    if current < previous:
        return "↓"
    return "→"
