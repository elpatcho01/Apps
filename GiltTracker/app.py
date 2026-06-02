"""
UK Gilt Issuance Tracker & Forecasting Platform

Entry point for Streamlit. Initialises the data store, renders the
sidebar navigation, and delegates to the appropriate page module.

Run:  streamlit run app.py
"""

import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))


def _current_fy() -> str:
    today = date.today()
    start_year = today.year if today.month >= 4 else today.year - 1
    return f"{start_year}-{str(start_year + 1)[2:]}"


import streamlit as st
from datetime import datetime

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

# ---------------------------------------------------------------------------
# Global CSS — injected once, applies to all pages
# ---------------------------------------------------------------------------

CUSTOM_CSS = """
<style>
/* ── Sidebar ─────────────────────────────────────────────────────────── */
[data-testid="stSidebar"] > div:first-child {
    background-color: #0D1B2A !important;
    border-right: 1px solid #1B2D45 !important;
}
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] .stCaption p {
    color: #6B8CAE !important;
    font-size: 0.75rem !important;
}

/* ── Navigation radio items ──────────────────────────────────────────── */
[data-testid="stRadio"] > div { gap: 2px !important; }
[data-testid="stRadio"] label {
    padding: 6px 10px !important;
    border-radius: 7px !important;
    transition: background 0.12s ease;
    cursor: pointer;
    display: flex !important;
    align-items: center !important;
}
[data-testid="stRadio"] label:hover { background: rgba(255,255,255,0.07) !important; }
[data-testid="stRadio"] label p { font-size: 0.875rem !important; }

/* ── Metric cards ────────────────────────────────────────────────────── */
[data-testid="metric-container"] {
    background: rgba(255,255,255,0.03) !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
    border-radius: 12px !important;
    padding: 1.1rem 1.25rem !important;
}
[data-testid="stMetricValue"] {
    font-size: 2.1rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.03em !important;
    line-height: 1.15 !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.7rem !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.08em !important;
    opacity: 0.55 !important;
    margin-bottom: 0.2rem !important;
}
[data-testid="stMetricDelta"] {
    font-size: 0.8rem !important;
    font-weight: 500 !important;
}

/* ── Page titles ─────────────────────────────────────────────────────── */
h1 {
    font-weight: 700 !important;
    letter-spacing: -0.025em !important;
    line-height: 1.2 !important;
    margin-bottom: 0.25rem !important;
}

/* ── Section headers (subheader = h2 in Streamlit) ───────────────────── */
h2 {
    font-size: 0.78rem !important;
    font-weight: 700 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.1em !important;
    color: rgba(232,236,240,0.45) !important;
    margin-top: 1.8rem !important;
    margin-bottom: 0.6rem !important;
    padding-bottom: 0.4rem !important;
    border-bottom: 1px solid rgba(255,255,255,0.06) !important;
}

/* ── Dividers ────────────────────────────────────────────────────────── */
hr { border-top: 1px solid rgba(255,255,255,0.07) !important; margin: 1.25rem 0 !important; }

/* ── Tabs ────────────────────────────────────────────────────────────── */
[data-testid="stTabs"] button[data-baseweb="tab"] {
    font-size: 0.85rem !important;
    font-weight: 500 !important;
    padding: 0.5rem 1.1rem !important;
}

/* ── Expanders ───────────────────────────────────────────────────────── */
[data-testid="stExpander"] {
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-radius: 10px !important;
}
[data-testid="stExpander"] summary p { font-weight: 600 !important; font-size: 0.9rem !important; }

/* ── Dataframes ──────────────────────────────────────────────────────── */
[data-testid="stDataFrame"] {
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-radius: 8px !important;
    overflow: hidden !important;
}

/* ── Alert / info boxes ──────────────────────────────────────────────── */
[data-testid="stAlertContainer"] {
    border-radius: 8px !important;
    border-left-width: 3px !important;
}

/* ── Buttons ─────────────────────────────────────────────────────────── */
[data-testid="stButton"] button {
    border-radius: 8px !important;
    font-weight: 500 !important;
    font-size: 0.875rem !important;
}

/* ── Selectbox ───────────────────────────────────────────────────────── */
[data-testid="stSelectbox"] label { font-size: 0.75rem !important; text-transform: uppercase; letter-spacing: 0.06em; opacity: 0.6; }

/* ── Caption text ────────────────────────────────────────────────────── */
[data-testid="stCaptionContainer"] p { font-size: 0.78rem !important; line-height: 1.5 !important; }
</style>
"""

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

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
    # Text logo — reliable across all environments (no external image dependency)
    st.markdown(
        """
        <div style="padding:0.6rem 0 0.8rem 0;">
            <div style="font-size:1.15rem;font-weight:700;color:#E8ECF0;letter-spacing:-0.01em;line-height:1.2;">
                UK Gilt Tracker
            </div>
            <div style="font-size:0.68rem;color:#4A6580;text-transform:uppercase;
                        letter-spacing:0.1em;margin-top:3px;">
                Issuance &middot; Forecasting &middot; Analytics
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
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

    if st.button("Refresh Live Data", use_container_width=True):
        with st.spinner("Fetching from DMO, ONS, OBR…"):
            result = store.refresh()
        errors = result.get("errors", [])
        if errors:
            st.warning(f"Partial refresh: {len(errors)} source(s) unavailable.")
        else:
            st.success("Data refreshed.")
        st.rerun()

    render_data_status(store)

    st.divider()
    st.caption(
        f"DMO · ONS · OBR  \n"
        f"As of {datetime.now().strftime('%d %b %Y')}"
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
