"""
4_Settings.py — current season/week override and data health status.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st
import pandas as pd

from database import get_connection, init_db
from components.auth import require_login, require_admin
from settings import get_setting, set_setting
from components.metrics import data_status, BOT_DISPLAY_NAMES
from components.styles import inject_custom_css, section_head, page_header

SPORT = 'nfl'

st.set_page_config(page_title='Settings — 3 Bettors', page_icon='⚙️', layout='wide',
                   initial_sidebar_state='expanded')
inject_custom_css()
require_login()   # password gate — nothing below renders until authenticated
require_admin()   # admin-only: season/week control + data health are admin-level actions
init_db()

page_header('SETTINGS', 'Season, bots & data health')

# ── Current season/week ───────────────────────────────────────────────────────
section_head('CURRENT SEASON / WEEK')
st.markdown(
    '<span style="font-size:11px;font-family:\'JetBrains Mono\',monospace;color:#8B92A8;">'
    'Drives which week run_slot.py operates on. tuesday_grade auto-advances the week; '
    'override here only to correct a mistake or manually skip ahead.</span>',
    unsafe_allow_html=True,
)

with get_connection() as conn:
    current_season = int(get_setting(conn, f'{SPORT}_current_season', '2026'))
    current_week = int(get_setting(conn, f'{SPORT}_current_week', '1'))

with st.form('season_week_form'):
    col1, col2 = st.columns(2)
    with col1:
        new_season = st.number_input('SEASON', value=current_season, step=1, format='%d')
    with col2:
        new_week = st.number_input('WEEK', value=current_week, min_value=1, max_value=22, step=1, format='%d')
    if st.form_submit_button('SAVE'):
        with get_connection() as conn:
            set_setting(conn, f'{SPORT}_current_season', str(int(new_season)))
            set_setting(conn, f'{SPORT}_current_week', str(int(new_week)))
        st.success(f'Set to season {int(new_season)}, week {int(new_week)}')
        st.rerun()

# ── Registered bots ────────────────────────────────────────────────────────────
section_head('REGISTERED BOTS')
st.dataframe(
    pd.DataFrame([{'BOT KEY': k, 'DISPLAY NAME': v} for k, v in BOT_DISPLAY_NAMES.items()]),
    width='stretch', hide_index=True,
)

# ── Data status ───────────────────────────────────────────────────────────────
section_head('DATA STATUS')

with get_connection() as conn:
    status = data_status(conn, SPORT)

col_a, col_b, col_c = st.columns(3)
col_a.metric('TOTAL GAMES',     status['total_games'])
col_a.metric('ODDS SNAPSHOTS',  f"{status['total_snapshots']:,}")
col_b.metric('RECOMMENDATIONS', status['total_recs'],
             delta=f"{status['graded_recs']} graded / {status['pending_recs']} pending")
col_c.metric('BETS',            status['total_bets'],
             delta=f"{status['graded_bets']} graded / {status['pending_bets']} pending")
st.markdown(
    f'<span style="font-size:11px;font-family:\'JetBrains Mono\',monospace;color:#8B92A8;">'
    f'{status["total_fades"]} fade rows tracked (excluded from grading/units)</span>',
    unsafe_allow_html=True,
)

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
