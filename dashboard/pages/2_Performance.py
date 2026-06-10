"""
2_Performance.py — System performance analytics.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from database import get_connection, init_db
from components.metrics import (
    clv_by_group, win_rate_table, calibration_data, rec_volume_by_day,
)
from components.formatters import fmt_pct
from components.styles import (
    inject_custom_css, empty_state, section_head, plotly_dark,
    C_ACCENT, C_YELLOW, C_RED_MUTED,
)

st.set_page_config(page_title='Performance — MLB Betting', page_icon='📊', layout='wide',
                   initial_sidebar_state='expanded')
inject_custom_css()
init_db()

st.markdown('<div style="font-size:22px;font-weight:700;margin-bottom:20px;">System Performance</div>',
            unsafe_allow_html=True)

_NO_DATA_SUB = 'Populates once green and yellow plays are graded — typically meaningful after ~30 graded plays.'

with get_connection() as conn:
    clv_color  = clv_by_group(conn, 'confidence_color')
    clv_market = clv_by_group(conn, 'market')
    clv_books  = clv_by_group(conn, "CASE WHEN num_books_in_consensus <= 4 THEN '3-4 books' "
                                     "WHEN num_books_in_consensus <= 6 THEN '5-6 books' "
                                     "ELSE '7+ books' END")
    wrt_color  = win_rate_table(conn, 'confidence_color')
    wrt_market = win_rate_table(conn, 'market')
    calib      = calibration_data(conn)
    vol        = rec_volume_by_day(conn, 30)

# ── A: CLV Breakdowns ─────────────────────────────────────────────────────────
section_head('A — CLV BREAKDOWNS')
tab1, tab2, tab3 = st.tabs(['BY COLOR', 'BY MARKET', 'BY CONSENSUS DEPTH'])

def _clv_df(rows, col_name):
    if not rows:
        empty_state('NO GRADED DATA', _NO_DATA_SUB)
        return
    df = pd.DataFrame(rows)
    df.columns = [col_name, 'AVG CLV %', 'SAMPLE']
    df['AVG CLV %'] = df['AVG CLV %'].apply(lambda x: fmt_pct(x) if x else '—')
    st.dataframe(df, width='stretch', hide_index=True)

with tab1: _clv_df(clv_color,  'COLOR')
with tab2: _clv_df(clv_market, 'MARKET')
with tab3:
    _clv_df(clv_books, 'CONSENSUS DEPTH')
    if clv_books:
        st.markdown(
            '<span style="font-size:11px;font-family:\'JetBrains Mono\',monospace;color:#8B92A8;">'
            'Consensus depth = books contributing to the vig-free fair line</span>',
            unsafe_allow_html=True
        )

# ── B: Win Rate / ROI ─────────────────────────────────────────────────────────
section_head('B — WIN RATE & ROI')

def _wrt(rows, label_col):
    if not rows:
        empty_state('NO GRADED DATA', _NO_DATA_SUB)
        return
    df = pd.DataFrame(rows).rename(columns={
        'group_key': label_col, 'total': 'PLAYS',
        'wins': 'W', 'losses': 'L', 'pushes': 'P', 'voids': 'V',
    })
    df['WIN %'] = (df['W'] / (df['W'] + df['L']).replace(0, float('nan')) * 100).apply(
        lambda x: f'{x:.1f}%' if pd.notna(x) else '—')
    df['AVG EDGE'] = df['avg_edge'].apply(lambda x: fmt_pct(x) if x else '—')
    df['AVG CLV']  = df['avg_clv'].apply(lambda x: fmt_pct(x) if x else '—')
    df['ROI %']    = df['avg_roi'].apply(lambda x: f'{x*100:+.1f}%' if x is not None else '—')
    st.dataframe(
        df[[label_col,'PLAYS','W','L','P','V','WIN %','AVG EDGE','AVG CLV','ROI %']],
        width='stretch', hide_index=True
    )

tb1, tb2 = st.tabs(['BY COLOR', 'BY MARKET'])
with tb1: _wrt(wrt_color,  'COLOR')
with tb2: _wrt(wrt_market, 'MARKET')

# ── C: Calibration ────────────────────────────────────────────────────────────
section_head('C — CALIBRATION CHECK')
st.markdown(
    '<span style="font-size:11px;font-family:\'JetBrains Mono\',monospace;color:#8B92A8;">'
    'Implied win probability (fair price) vs actual win rate observed. '
    'Large gaps signal miscalibration — the most important diagnostic in this system.</span>',
    unsafe_allow_html=True
)
st.markdown('<br>', unsafe_allow_html=True)

if calib:
    rows = []
    for r in calib:
        implied = r['avg_implied_prob']
        actual  = r['actual_win_rate']
        diff    = (actual - implied) * 100 if implied and actual else None
        rows.append({
            'COLOR':          r['confidence_color'].upper(),
            'IMPLIED WIN %':  f'{implied*100:.1f}%' if implied else '—',
            'ACTUAL WIN %':   f'{actual*100:.1f}%'  if actual  else '—',
            'GAP':            fmt_pct(diff)          if diff is not None else '—',
            'SAMPLE':         r['n'],
        })
    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    st.markdown(
        '<span style="font-size:11px;font-family:\'JetBrains Mono\',monospace;color:#8B92A8;">'
        'GAP = actual − implied. Negative = system over-confident. Positive = system under-confident.</span>',
        unsafe_allow_html=True
    )
else:
    empty_state('NO GRADED DATA', _NO_DATA_SUB)

# ── D: Volume chart ───────────────────────────────────────────────────────────
section_head('D — RECOMMENDATION VOLUME · LAST 30 DAYS')
if vol:
    df_vol = pd.DataFrame(vol)
    fig = go.Figure(go.Bar(
        x=df_vol['date'], y=df_vol['n'],
        marker_color=C_ACCENT, marker_opacity=0.75,
    ))
    fig.update_layout(yaxis_title='GREEN + YELLOW PLAYS')
    plotly_dark(fig, height=220)
    st.plotly_chart(fig, width='stretch')
else:
    empty_state('NO RECOMMENDATION VOLUME', 'Chart will populate once green/yellow plays are generated.')
