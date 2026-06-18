"""
auth.py — password gate for the dashboard.

require_login() must be called at the very top of EVERY page (app.py and every
file in dashboard/pages/), after st.set_page_config + inject_custom_css and
BEFORE any DB connection, query, or content render. It st.stop()s the script
until the correct password is entered, so no page — including deep-linked ones —
renders anything before authentication.

The password is read ONLY from st.secrets["APP_PASSWORD"] (set in the Streamlit
Cloud secrets UI). There is no hardcoded or default password. Comparison is
constant-time. Only a boolean flag is stored in session_state — never the
password itself.
"""

import os
import hmac

import streamlit as st


def _bootstrap_db_config():
    """Make the same code run on Streamlit Cloud and locally.

    The dashboard always targets Postgres (SQLite is the frozen rollback copy).
    On Streamlit Cloud, DATABASE_URL comes from st.secrets; locally it comes
    from .env (loaded by database.py). We copy the secret into os.environ so the
    existing os.environ-based get_connection() works unchanged in both places.
    """
    os.environ.setdefault('DB_BACKEND', 'postgres')
    try:
        if 'DATABASE_URL' in st.secrets:
            os.environ['DATABASE_URL'] = st.secrets['DATABASE_URL']
    except Exception:
        # No secrets.toml locally — database.py falls back to .env / os.environ.
        pass


def _expected_password():
    try:
        pw = st.secrets['APP_PASSWORD']
    except Exception:
        return None
    return pw if pw else None


def _render_login():
    expected = _expected_password()
    if not expected:
        st.error(
            'APP_PASSWORD is not configured. Set it in Streamlit Cloud → '
            'Manage app → Settings → Secrets (or in .streamlit/secrets.toml '
            'locally), then reload.'
        )
        st.stop()

    left, mid, right = st.columns([1, 1.3, 1])
    with mid:
        st.markdown(
            "<div style='text-align:center;margin-top:14vh'>"
            "<div style='font-size:40px;font-weight:800;letter-spacing:-1px'>⚾ MLB Betting</div>"
            "<div style='color:#8b8b8b;margin:6px 0 18px;font-size:14px'>"
            "Enter password to continue</div></div>",
            unsafe_allow_html=True,
        )
        with st.form('login_form', clear_on_submit=False):
            pw = st.text_input('Password', type='password',
                               label_visibility='collapsed', placeholder='Password')
            submitted = st.form_submit_button('Sign in', use_container_width=True)

        if submitted:
            # Constant-time comparison to avoid timing leaks.
            if hmac.compare_digest(pw.encode('utf-8'), expected.encode('utf-8')):
                st.session_state['authenticated'] = True
                st.rerun()
            else:
                st.error('Incorrect password.')


def require_login():
    """Gate the page. Returns only when authenticated; otherwise st.stop()s."""
    _bootstrap_db_config()

    if st.session_state.get('authenticated') is True:
        return

    _render_login()
    # If we reach here the user is not authenticated (wrong password or first
    # load). Halt so nothing below this call renders.
    st.stop()
