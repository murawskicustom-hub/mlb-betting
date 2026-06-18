"""
app.py — Home page: operations summary.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime
import pytz

from database import get_connection, init_db
from components.metrics import (
    todays_rec_counts, todays_model_count, avg_clv, avg_clv_by_algo, personal_pl,
    last_update_times, clv_trend, rec_distribution, daily_activity,
)
from components.formatters import fmt_dollars, fmt_pct, fmt_date_friendly
from components.styles import (
    inject_custom_css, status_bar, metric_tile, metrics_row,
    empty_state, section_head, plotly_dark, page_header,
    C_ACCENT, C_YELLOW, C_MUTED, C_GRID, C_BORDER, C_RED_MUTED, C_MODEL,
)
from components.nav_check import render_nav_canary
from components.auth import require_login

EASTERN = pytz.timezone('US/Eastern')

st.set_page_config(
    page_title='MLB Betting — Home',
    page_icon='⚾',
    layout='wide',
    initial_sidebar_state='expanded',
)

inject_custom_css()
require_login()   # password gate — nothing below renders until authenticated
render_nav_canary()
init_db()

# ── Load data ─────────────────────────────────────────────────────────────────
with get_connection() as conn:
    rec_counts   = todays_rec_counts(conn)    # devig, is_shadow=0 only
    model_count  = todays_model_count(conn)   # algo 2 observation picks
    clv_7        = avg_clv(conn, 7)
    clv_30      = avg_clv(conn, 30)
    a1_clv_7    = avg_clv_by_algo(conn, 7,  'devig')
    a1_clv_30   = avg_clv_by_algo(conn, 30, 'devig')
    a2_clv_7    = avg_clv_by_algo(conn, 7,  'model_v1')
    a2_clv_30   = avg_clv_by_algo(conn, 30, 'model_v1')
    pl_7        = personal_pl(conn, 7)
    pl_all      = personal_pl(conn, None)
    upd         = last_update_times(conn)
    trend_data  = clv_trend(conn, 30)
    dist_data   = rec_distribution(conn, 30)
    activity    = daily_activity(conn, 7)

# ── Status bar ────────────────────────────────────────────────────────────────
status_bar(
    upd['last_snapshot'],
    upd['requests_remaining'],
    upd['games_last_updated'],
)

# Page heading — dynamic headline leads with the fact that matters most
total_real = rec_counts['green'] + rec_counts['yellow']
if total_real:
    _headline = f'{total_real} qualifying edge{"s" if total_real != 1 else ""} today'
else:
    _headline = 'No qualifying edges today'

page_header('DASHBOARD', _headline)

# ── Section 1: Metric tiles ───────────────────────────────────────────────────
section_head('PERFORMANCE SNAPSHOT')

plays_val  = str(total_real) if total_real else '—'
plays_delta = f"{rec_counts['green']}G · {rec_counts['yellow']}Y" if total_real else 'NO QUALIFYING EDGES'

def _clv_tile(label, val):
    if val is None:
        return metric_tile(label, '—', 'NO DATA', 'neutral')
    accent = 'positive' if val >= 0 else 'negative'
    return metric_tile(label, fmt_pct(val), '', accent)

def _pl_tile(label, val):
    if val == 0:
        return metric_tile(label, '$0.00', '', 'neutral')
    accent = 'positive' if val > 0 else 'negative'
    return metric_tile(label, fmt_dollars(val, always_sign=True), '', accent)

tiles = [
    metric_tile("TODAY'S PLAYS", plays_val, plays_delta, 'positive' if total_real else 'neutral'),
    _clv_tile('SYSTEM CLV · 7D', clv_7),
    _clv_tile('SYSTEM CLV · 30D', clv_30),
    _pl_tile('PERSONAL P/L · 7D', pl_7),
    _pl_tile('PERSONAL P/L · ALL', pl_all),
]
metrics_row(tiles)

# Algo 2 subordinate activity line — never uses bettable color language
if model_count:
    _a2_label = f'ALGO 2 &middot; {model_count} pick{"s" if model_count != 1 else ""} (paper, units tracked)'
else:
    _a2_label = 'ALGO 2 &middot; no picks today'
st.markdown(
    f'<div style="font-size:11px;font-family:\'JetBrains Mono\',monospace;'
    f'color:{C_MODEL};letter-spacing:0.08em;margin:-4px 0 10px 2px;">'
    f'{_a2_label}</div>',
    unsafe_allow_html=True,
)

# Algo comparison row
def _algo_clv_tile(label, val, color_override=None):
    if val is None:
        return metric_tile(label, '—', 'CALIBRATING', 'neutral')
    accent = 'positive' if val >= 0 else 'negative'
    return metric_tile(label, fmt_pct(val), '', accent)

algo_tiles = [
    _algo_clv_tile('ALGO 1 CLV · 7D',  a1_clv_7),
    _algo_clv_tile('ALGO 1 CLV · 30D', a1_clv_30),
    _algo_clv_tile('ALGO 2 CLV · 7D',  a2_clv_7),
    _algo_clv_tile('ALGO 2 CLV · 30D', a2_clv_30),
    metric_tile('ALGO 2 STATUS', 'SHADOW', '$0 STAKES', 'neutral'),
]
metrics_row(algo_tiles)

st.markdown('<br>', unsafe_allow_html=True)

# ── Section 2: CLV trend ──────────────────────────────────────────────────────
section_head('CLV TREND — GREEN & YELLOW · LAST 30 DAYS')

if trend_data:
    df_trend = pd.DataFrame(trend_data)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df_trend['date'], y=df_trend['cumulative_avg'],
        mode='lines+markers', name='Cumulative CLV',
        line=dict(color=C_ACCENT, width=2),
        marker=dict(size=4, color=C_ACCENT),
        fill='tozeroy',
        fillcolor='rgba(0,212,170,0.06)',
    ))
    fig.add_hline(y=0, line_dash='dot', line_color=C_MUTED, line_width=1, opacity=0.4)
    plotly_dark(fig, height=260)
    fig.update_layout(yaxis_title='CLV %', xaxis_title=None)
    st.plotly_chart(fig, width='stretch')
else:
    empty_state(
        'Awaiting graded plays',
        'CLV trend activates after first plays are graded.',
        'CLV TREND · Cumulative closing-line value line chart, updated nightly.'
    )

# ── Section 3: Result distribution ───────────────────────────────────────────
section_head('RESULT DISTRIBUTION · LAST 30 DAYS')

if dist_data:
    df_dist = pd.DataFrame(dist_data)
    COLORS = {'green': C_ACCENT, 'yellow': C_YELLOW, 'red': C_RED_MUTED}
    fig2 = go.Figure()
    for color in ('green', 'yellow', 'red'):
        sub = df_dist[df_dist['confidence_color'] == color]
        wins   = int(sub[sub['result'] == 'win']['n'].sum())   if not sub.empty else 0
        losses = int(sub[sub['result'] == 'loss']['n'].sum())  if not sub.empty else 0
        c = COLORS[color]
        fig2.add_trace(go.Bar(name=f'{color} W', x=[color], y=[wins],
                              marker_color=c, marker_opacity=0.85))
        fig2.add_trace(go.Bar(name=f'{color} L', x=[color], y=[-losses],
                              marker_color=c, marker_opacity=0.25))
    fig2.update_layout(barmode='overlay', yaxis_title='W (↑) / L (↓)')
    plotly_dark(fig2, height=220)
    st.plotly_chart(fig2, width='stretch')

    red_rows = [d for d in dist_data if d['confidence_color'] == 'red']
    rw = sum(d['n'] for d in red_rows if d['result'] == 'win')
    rl = sum(d['n'] for d in red_rows if d['result'] == 'loss')
    rp = sum(d['n'] for d in red_rows if d['result'] in ('push', 'void'))
    st.markdown(
        f'<span style="font-size:11px;font-family:\'JetBrains Mono\',monospace;color:#8B92A8;">'
        f'SHADOW LEDGER — {rw}W / {rl}L / {rp} PUSH/VOID · '
        f'calibrated target: ~−4.5% ROI (break-even minus vig)</span>',
        unsafe_allow_html=True
    )
else:
    empty_state(
        'No graded recommendations yet',
        'Distribution populates once green and yellow plays are graded.',
        'RESULT DISTRIBUTION · Win/loss bars by signal color, last 30 days.'
    )

# ── Section 4: Daily activity ─────────────────────────────────────────────────
section_head('DAILY ACTIVITY · LAST 7 DAYS')

df_act = pd.DataFrame(activity)
df_act['RECS'] = df_act.apply(
    lambda r: f"{r['green']}G / {r['yellow']}Y / {r['red']}R", axis=1)
df_act['P/L'] = df_act['pl'].apply(lambda x: fmt_dollars(x, always_sign=True))
st.dataframe(
    df_act[['date','games','RECS','bets','P/L']].rename(columns={
        'date':'DATE','games':'GAMES','bets':'BETS'
    }),
    width='stretch', hide_index=True,
)
