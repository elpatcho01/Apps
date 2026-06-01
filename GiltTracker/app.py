"""
UK Gilt Issuance Tracker & Forecasting Platform

Entry point for Streamlit. Initialises the data store, renders the
sidebar navigation, and delegates to the appropriate page module.

Run:  streamlit run app.py
"""

import sys
import os
from datetime import date

# Ensure local packages are importable regardless of working directory
sys.path.insert(0, os.path.dirname(__file__))


def _current_fy() -> str:
    """Return the UK fiscal year string for today's date (e.g. '2026-27')."""
    today = date.today()
    start_year = today.year if today.month >= 4 else today.year - 1
    return f"{start_year}-{str(start_year + 1)[2:]}"

import streamlit as st
from datetime import datetime

# Must be first Streamlit call
st.set_page_config(
    page_title="UK Gilt Tracker | Issuance & Forecasting",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help": "https://www.dmo.gov.uk",
        "About": (
            "UK Gilt Issuance Tracker — built for DMO remit analysis, "
            "sector breakdown, and econometric forecasting. "
            "Data: DMO, ONS, OBR."
        ),
    },
)

from data.store import GiltDataStore
from ui.components import render_data_status
from config import ALL_FYS

# ---------------------------------------------------------------------------
# Initialise data store (singleton across the session)
# ---------------------------------------------------------------------------

@st.cache_resource
def get_data_store() -> GiltDataStore:
    return GiltDataStore()


store = get_data_store()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.image(
        "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ae/"
        "UK_Government_Web_Archive_logo.svg/320px-UK_Government_Web_Archive_logo.svg.png",
        width=60,
    )
    st.title("UK Gilt Tracker")
    st.caption("Issuance · Forecasting · Analytics")
    st.divider()

    page = st.radio(
        "Navigation",
        options=[
            "📊 Remit Overview",
            "🏗️ Sector Breakdown",
            "📅 Issuance Calendar",
            "🤝 Syndications",
            "🔍 Bond Tracker",
            "📉 PSND & Borrowing",
            "🔬 Econometric Forecasting",
        ],
        label_visibility="collapsed",
    )

    st.divider()

    fy_options = list(reversed(ALL_FYS))
    current_fy = _current_fy()
    default_idx = fy_options.index(current_fy) if current_fy in fy_options else 0
    selected_fy = st.selectbox(
        "Fiscal Year",
        options=fy_options,
        index=default_idx,
        format_func=lambda x: f"FY {x}",
    )

    st.divider()

    refresh_col, _ = st.columns([3, 1])
    with refresh_col:
        if st.button("🔄 Refresh Live Data", use_container_width=True):
            with st.spinner("Fetching from DMO, ONS, OBR…"):
                result = store.refresh()
            errors = result.get("errors", [])
            if errors:
                st.warning(f"Partial refresh: {len(errors)} source(s) unavailable. Using cached data.")
            else:
                st.success("Data refreshed.")
            st.rerun()

    render_data_status(store)

    st.divider()
    st.caption(
        f"**Data sources:** DMO · ONS · OBR  \n"
        f"**As of:** {datetime.now().strftime('%d %b %Y')}"
    )

# ---------------------------------------------------------------------------
# Page routing
# ---------------------------------------------------------------------------

if page == "📊 Remit Overview":
    from ui.remit_page import render
    render(store, selected_fy)

elif page == "🏗️ Sector Breakdown":
    from ui.sector_page import render
    render(store, selected_fy)

elif page == "📅 Issuance Calendar":
    from ui.calendar_page import render
    render(store, selected_fy)

elif page == "🤝 Syndications":
    from ui.syndications_page import render
    render(store, selected_fy)

elif page == "🔍 Bond Tracker":
    from ui.bonds_page import render
    render(store, selected_fy)

elif page == "📉 PSND & Borrowing":
    from ui.psnd_page import render
    render(store, selected_fy)

elif page == "🔬 Econometric Forecasting":
    from ui.forecasting_page import render
    render(store, selected_fy)
