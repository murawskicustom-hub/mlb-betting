"""
4_Settings.py — Bankroll settings and data health status.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from database import get_connection, init_db
from settings import get_bankroll, set_bankroll, get_setting
from components.metrics import data_status
from components.formatters import utc_to_eastern, fmt_datetime_et

st.set_page_config(page_title='Settings — MLB Betting', page_icon='⚙️', layout='wide')
init_db()
st.title('⚙️ Settings')

# ── Bankroll ──────────────────────────────────────────────────────────────────
st.subheader('Bankroll')

with get_connection() as conn:
    current_bankroll = get_bankroll(conn)
    bankroll_updated = get_setting(conn, 'bankroll_dollars')  # get raw to check timestamp
    updated_row = conn.execute(
        "SELECT updated_at_utc FROM settings WHERE key='bankroll_dollars'"
    ).fetchone()
    updated_at = updated_row[0] if updated_row else None

col_val, col_form = st.columns([1, 2])
col_val.metric('Current Bankroll', f'${current_bankroll:,.2f}')
if updated_at:
    dt = utc_to_eastern(updated_at)
    col_val.caption(f'Last updated: {fmt_datetime_et(updated_at)}')

with col_form:
    with st.form('bankroll_form'):
        new_bankroll = st.number_input(
            'New bankroll ($)',
            min_value=100.0,
            max_value=1_000_000.0,
            value=float(current_bankroll),
            step=100.0,
            format='%.2f',
        )
        if st.form_submit_button('💾 Save Bankroll'):
            with get_connection() as conn:
                set_bankroll(conn, new_bankroll)
            st.success(f'Bankroll updated to ${new_bankroll:,.2f}')
            st.rerun()

st.divider()

# ── Data status ───────────────────────────────────────────────────────────────
st.subheader('Data Status')

with get_connection() as conn:
    status = data_status(conn)

col_a, col_b, col_c = st.columns(3)
col_a.metric('Total Games',        status['total_games'])
col_a.metric('Total Odds Snapshots', f"{status['total_snapshots']:,}")
col_b.metric('Recommendations',    status['total_recs'],
             delta=f"{status['graded_recs']} graded / {status['pending_recs']} pending")
col_c.metric('Personal Bets',      status['total_bets'],
             delta=f"{status['graded_bets']} graded / {status['pending_bets']} pending")

st.subheader('Last Scheduled Run Per Slot')
slot_data = []
for slot, last_date in status['last_slot_runs'].items():
    slot_data.append({'Slot': slot.capitalize(), 'Last Run Date': last_date or '— never —'})

import pandas as pd
st.dataframe(pd.DataFrame(slot_data), width='stretch', hide_index=True)
st.caption('Based on log files in logs/scheduled/. A missing date means that slot has never run successfully.')
