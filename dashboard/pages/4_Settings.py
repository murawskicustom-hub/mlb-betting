"""
4_Settings.py — Bankroll settings and data health status.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st
import pandas as pd

from database import get_connection, init_db
from settings import get_bankroll, set_bankroll, get_setting
from components.metrics import data_status
from components.formatters import fmt_datetime_et
from components.styles import inject_custom_css, section_head, page_header

st.set_page_config(page_title='Settings — MLB Betting', page_icon='⚙️', layout='wide',
                   initial_sidebar_state='expanded')
inject_custom_css()
init_db()

page_header('SETTINGS', 'Bankroll & data health')

# ── Bankroll ──────────────────────────────────────────────────────────────────
section_head('BANKROLL')

with get_connection() as conn:
    current_bankroll = get_bankroll(conn)
    updated_row = conn.execute(
        "SELECT updated_at_utc FROM settings WHERE key='bankroll_dollars'"
    ).fetchone()
    updated_at = updated_row[0] if updated_row else None

col_val, col_form = st.columns([1, 2])
col_val.markdown(
    f'<div class="m-tile positive" style="display:inline-block;min-width:160px;">'
    f'<div class="m-label">CURRENT BANKROLL</div>'
    f'<div class="m-value accent">${current_bankroll:,.2f}</div>'
    f'</div>',
    unsafe_allow_html=True
)
if updated_at:
    col_val.markdown(
        f'<span style="font-size:11px;font-family:\'JetBrains Mono\',monospace;color:#8B92A8;">'
        f'UPDATED: {fmt_datetime_et(updated_at)}</span>',
        unsafe_allow_html=True
    )

with col_form:
    with st.form('bankroll_form'):
        new_bankroll = st.number_input(
            'NEW BANKROLL ($)', min_value=100.0, max_value=1_000_000.0,
            value=float(current_bankroll), step=100.0, format='%.2f'
        )
        if st.form_submit_button('SAVE'):
            with get_connection() as conn:
                set_bankroll(conn, new_bankroll)
            st.success(f'BANKROLL UPDATED → ${new_bankroll:,.2f}')
            st.rerun()

# ── Data status ───────────────────────────────────────────────────────────────
section_head('DATA STATUS')

with get_connection() as conn:
    status = data_status(conn)

col_a, col_b, col_c = st.columns(3)
col_a.metric('TOTAL GAMES',     status['total_games'])
col_a.metric('ODDS SNAPSHOTS',  f"{status['total_snapshots']:,}")
col_b.metric('RECOMMENDATIONS', status['total_recs'],
             delta=f"{status['graded_recs']} graded / {status['pending_recs']} pending")
col_c.metric('PERSONAL BETS',   status['total_bets'],
             delta=f"{status['graded_bets']} graded / {status['pending_bets']} pending")

section_head('LAST SCHEDULED RUN PER SLOT')
slot_data = [
    {'SLOT': s.upper(), 'LAST RUN DATE': d or '— NEVER —'}
    for s, d in status['last_slot_runs'].items()
]
st.dataframe(pd.DataFrame(slot_data), width='stretch', hide_index=True)
st.markdown(
    '<span style="font-size:11px;font-family:\'JetBrains Mono\',monospace;color:#8B92A8;">'
    'Based on log files in logs/scheduled/</span>',
    unsafe_allow_html=True
)
