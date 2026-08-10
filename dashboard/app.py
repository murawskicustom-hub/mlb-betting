"""
app.py — Home page: season standings preview + this-week operations summary.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st
import pandas as pd

from database import get_connection, init_db
from components.metrics import (
    season_standings, current_week_pick_counts, current_season_week, last_update_times,
)
from components.formatters import fmt_pct
from components.styles import (
    inject_custom_css, status_bar, metric_tile, metrics_row,
    empty_state, section_head, page_header, BOT_COLORS,
)
from components.nav_check import render_nav_canary
from components.auth import require_login

SPORT = 'nfl'

st.set_page_config(
    page_title='3 Bettors — Home',
    page_icon='🏈',
    layout='wide',
    initial_sidebar_state='expanded',
)

inject_custom_css()
require_login()   # password gate — nothing below renders until authenticated
render_nav_canary()
init_db()

# ── Load data ─────────────────────────────────────────────────────────────────
with get_connection() as conn:
    season, week = current_season_week(conn, SPORT)
    standings    = season_standings(conn, SPORT)
    week_counts  = current_week_pick_counts(conn, SPORT, season, week)
    upd          = last_update_times(conn, SPORT)

status_bar(upd['last_snapshot'], None, upd['games_last_updated'])

page_header('DASHBOARD', f'NFL {season} — Week {week}')

# ── Section 1: Season standings ───────────────────────────────────────────────
section_head('SEASON STANDINGS — MOST UNITS WON')

tiles = []
for s in standings:
    u = s['total_units']
    accent = 'positive' if u > 0 else ('negative' if u < 0 else 'neutral')
    sign = '+' if u > 0 else ''
    delta = f"{s['wins']}W {s['losses']}L {s['pushes']}P" if s['graded'] else 'NO GRADED PICKS YET'
    tiles.append(metric_tile(s['display_name'].upper(), f'{sign}{u:.1f}u', delta, accent))
metrics_row(tiles)

st.markdown('<br>', unsafe_allow_html=True)

# ── Section 2: This week's activity ───────────────────────────────────────────
section_head(f"THIS WEEK'S ACTIVITY — SEASON {season}, WEEK {week}")

if any(c['picks'] or c['fades'] for c in week_counts):
    rows = []
    for c in week_counts:
        rows.append({
            'BOT':   c['display_name'],
            'PICKS': c['picks'],
            'FADES': c['fades'],
        })
    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
else:
    empty_state(
        'No activity yet this week',
        'Populates after the first lock slot (thursday_lock/sunday_lock/monday_lock) runs.',
        f'Current week: season {season}, week {week} — set via nfl_current_week in Settings.'
    )

st.markdown('<br>', unsafe_allow_html=True)
st.markdown(
    f'<span style="font-size:11px;font-family:\'JetBrains Mono\',monospace;color:#8B92A8;">'
    f'See This Week for the full pick/fade board, or Performance for per-bot breakdowns.</span>',
    unsafe_allow_html=True,
)
