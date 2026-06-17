"""
nav_check.py — Sidebar liveness canary.

Call render_nav_canary() once per page load (in dev). A visible
"NAV OK" caption in the sidebar confirms the nav panel is alive.
If you can't see the sidebar at all, navigation is broken and
the sidebar CSS defensive rules need investigation.
"""

import streamlit as st


def render_nav_canary() -> None:
    """Render a tiny sidebar caption — visible proof the sidebar is alive."""
    st.sidebar.caption("NAV OK")
