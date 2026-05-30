"""
app.py — Home page (operations summary).
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
    todays_rec_counts, avg_clv, personal_pl,
    last_update_times, clv_trend, rec_distribution, daily_activity,
)
from components.formatters import fmt_dollars, fmt_pct, utc_to_eastern, fmt_date_friendly, _no_pad

EASTERN = pytz.timezone('US/Eastern')

st.set_page_config(
    page_title='MLB Betting — Home',
    page_icon='⚾',
    layout='wide',
    initial_sidebar_state='expanded',
)

init_db()

st.title('⚾ MLB Betting Platform')
st.caption(f'Today: {fmt_date_friendly(datetime.now(EASTERN))}')

with get_connection() as conn:
    rec_counts = todays_rec_counts(conn)
    clv_7      = avg_clv(conn, 7)
    clv_30     = avg_clv(conn, 30)
    pl_7       = personal_pl(conn, 7)
    pl_all     = personal_pl(conn, None)
    upd        = last_update_times(conn)
    trend_data = clv_trend(conn, 30)
    dist_data  = rec_distribution(conn, 30)
    activity   = daily_activity(conn, 7)

# ── Section 1: Metric tiles ───────────────────────────────────────────────────
st.subheader('At a Glance')
c1, c2, c3, c4, c5 = st.columns(5)

total_real = rec_counts['green'] + rec_counts['yellow']
delta_str  = f"{rec_counts['green']} green / {rec_counts['yellow']} yellow" if total_real else '—'
c1.metric('Today\'s Plays', total_real, delta=delta_str if total_real else None)

clv_7_str  = fmt_pct(clv_7)  if clv_7  is not None else '—'
clv_30_str = fmt_pct(clv_30) if clv_30 is not None else '—'
c2.metric('System CLV (7d)',  clv_7_str,  help='Avg CLV% — green & yellow recs only')
c3.metric('System CLV (30d)', clv_30_str, help='Avg CLV% — green & yellow recs only')

pl7_color  = 'normal' if pl_7  >= 0 else 'inverse'
pla_color  = 'normal' if pl_all >= 0 else 'inverse'
c4.metric('Personal P/L (7d)',   fmt_dollars(pl_7,  always_sign=True),  delta_color=pl7_color)
c5.metric('Personal P/L (All)',  fmt_dollars(pl_all, always_sign=True),  delta_color=pla_color)

st.divider()

# ── Section 2: CLV trend chart ────────────────────────────────────────────────
st.subheader('System CLV Trend — Green & Yellow Recommendations')
if trend_data:
    df_trend = pd.DataFrame(trend_data)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df_trend['date'], y=df_trend['cumulative_avg'],
        mode='lines+markers', name='Cumulative Avg CLV %',
        line=dict(color='#2196F3', width=2),
    ))
    fig.add_hline(y=0, line_dash='dash', line_color='gray', opacity=0.5)
    fig.update_layout(
        yaxis_title='CLV %', xaxis_title='Date',
        height=280, margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
    )
    st.plotly_chart(fig, width='stretch')
else:
    st.info(
        '📊 CLV trend will populate as recommendations are graded. '
        'Typically meaningful after ~30–60 graded plays.'
    )

st.divider()

# ── Section 3: Rec distribution chart ────────────────────────────────────────
st.subheader('Recommendation Results (Last 30 Days)')
if dist_data:
    df_dist = pd.DataFrame(dist_data)
    colors  = {'green': '#4CAF50', 'yellow': '#FFC107', 'red': '#F44336'}
    fig2 = go.Figure()
    for color in ('green', 'yellow', 'red'):
        sub = df_dist[df_dist['confidence_color'] == color]
        wins   = sub[sub['result'] == 'win']['n'].sum()  if not sub.empty else 0
        losses = sub[sub['result'] == 'loss']['n'].sum() if not sub.empty else 0
        fig2.add_trace(go.Bar(name=f'{color.capitalize()} W',
                              x=[color], y=[wins],
                              marker_color=colors.get(color, 'gray')))
        fig2.add_trace(go.Bar(name=f'{color.capitalize()} L',
                              x=[color], y=[-losses],
                              marker_color='#9E9E9E'))
    fig2.update_layout(
        barmode='overlay', height=250,
        margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
        yaxis_title='Wins (up) / Losses (down)',
    )
    st.plotly_chart(fig2, width='stretch')

    # Shadow control group note
    red_rows = [d for d in dist_data if d['confidence_color'] == 'red']
    rw = sum(d['n'] for d in red_rows if d['result'] == 'win')
    rl = sum(d['n'] for d in red_rows if d['result'] == 'loss')
    rp = sum(d['n'] for d in red_rows if d['result'] in ('push', 'void'))
    st.caption(
        f'🔴 Shadow (red) control group — last 30 days: '
        f'{rw} W / {rl} L / {rp} push/void. '
        f'Expected if model is correctly calibrated: ~break-even minus vig (≈−4.5% ROI).'
    )
else:
    st.info('No graded recommendations yet. Results will appear here once plays are graded.')

st.divider()

# ── Section 4: Daily activity table ──────────────────────────────────────────
st.subheader('Daily Activity (Last 7 Days)')
df_act = pd.DataFrame(activity)
df_act['Recs (G/Y/R)'] = df_act.apply(
    lambda r: f"{r['green']}G / {r['yellow']}Y / {r['red']}R", axis=1)
df_act['P/L'] = df_act['pl'].apply(lambda x: fmt_dollars(x, always_sign=True))
st.dataframe(
    df_act[['date', 'games', 'Recs (G/Y/R)', 'bets', 'P/L']].rename(columns={
        'date': 'Date', 'games': 'Games', 'bets': 'Bets Placed'
    }),
    width='stretch', hide_index=True,
)

st.divider()

# ── Section 5: Footer ─────────────────────────────────────────────────────────
def _age_str(utc_str):
    dt = utc_to_eastern(utc_str)
    if dt is None:
        return 'never', False
    now = datetime.now(EASTERN)
    hours = (now - dt.replace(tzinfo=EASTERN)).total_seconds() / 3600
    label = f'{_no_pad(dt, "%I")}:{dt.strftime("%M %p")} ET ({hours:.1f}h ago)'
    return label, hours > 8

last_snap_str, snap_stale = _age_str(upd['last_snapshot'])
req_rem = upd['requests_remaining']
req_str = str(req_rem) if req_rem is not None else '—'

col_a, col_b, col_c = st.columns(3)
col_a.caption(f'🗄️ Games last updated: {_age_str(upd["games_last_updated"])[0]}')
if snap_stale:
    col_b.markdown(f'⚠️ **Last odds pull: {last_snap_str}** — stale!', unsafe_allow_html=False)
else:
    col_b.caption(f'📈 Last odds pull: {last_snap_str}')
col_c.caption(f'🔑 API requests remaining: {req_str}/month')
