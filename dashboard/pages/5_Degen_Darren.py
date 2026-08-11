"""
5_Degen_Darren.py — bio + full betting history for Degen Darren.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from database import get_connection, init_db
from components.auth import require_login
from components.bios import BOT_BIOS
from components.bot_profile import render_bio, render_profile
from components.styles import inject_custom_css, page_header, BOT_COLORS

SPORT = 'nfl'
BOT_KEY = 'degen_darren'
DISPLAY_NAME = 'Degen Darren'
ACCENT = BOT_COLORS[BOT_KEY]

st.set_page_config(page_title=f'{DISPLAY_NAME} — 3 Bettors', page_icon='🎲', layout='wide',
                   initial_sidebar_state='expanded')
inject_custom_css()
require_login()   # password gate — nothing below renders until authenticated
init_db()

page_header(DISPLAY_NAME.upper(), 'Bio, strategy & full betting history')

render_bio(BOT_BIOS[BOT_KEY], DISPLAY_NAME, ACCENT)
st.markdown('<br>', unsafe_allow_html=True)

with get_connection() as conn:
    render_profile(conn, SPORT, BOT_KEY, DISPLAY_NAME, ACCENT, key_prefix='bio_darren')
