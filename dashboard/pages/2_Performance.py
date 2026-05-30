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

st.set_page_config(page_title='Performance — MLB Betting', page_icon='📊', layout='wide')
init_db()
st.title('📊 System Performance')

_NO_DATA = '📭 No graded data yet — this section will populate as recommendations are graded over the coming weeks.'


def _placeholder(msg=_NO_DATA):
    st.info(msg)


with get_connection() as conn:
    clv_color   = clv_by_group(conn, 'confidence_color')
    clv_market  = clv_by_group(conn, 'market')
    clv_books   = clv_by_group(conn, "CASE WHEN num_books_in_consensus <= 4 THEN '3-4 books' "
                                      "WHEN num_books_in_consensus <= 6 THEN '5-6 books' "
                                      "ELSE '7+ books' END")
    wrt_color   = win_rate_table(conn, 'confidence_color')
    wrt_market  = win_rate_table(conn, 'market')
    calib       = calibration_data(conn)
    vol         = rec_volume_by_day(conn, 30)

# ── Section A: CLV breakdowns ─────────────────────────────────────────────────
st.subheader('A — CLV Breakdowns')
tab1, tab2, tab3 = st.tabs(['By Color', 'By Market', 'By Consensus Depth'])

with tab1:
    if clv_color:
        df = pd.DataFrame(clv_color)
        df.columns = ['Color', 'Avg CLV %', 'Sample Size']
        df['Avg CLV %'] = df['Avg CLV %'].apply(lambda x: fmt_pct(x) if x else '—')
        st.dataframe(df, width='stretch', hide_index=True)
    else:
        _placeholder()

with tab2:
    if clv_market:
        df = pd.DataFrame(clv_market)
        df.columns = ['Market', 'Avg CLV %', 'Sample Size']
        df['Avg CLV %'] = df['Avg CLV %'].apply(lambda x: fmt_pct(x) if x else '—')
        st.dataframe(df, width='stretch', hide_index=True)
    else:
        _placeholder()

with tab3:
    if clv_books:
        df = pd.DataFrame(clv_books)
        df.columns = ['Consensus Depth', 'Avg CLV %', 'Sample Size']
        df['Avg CLV %'] = df['Avg CLV %'].apply(lambda x: fmt_pct(x) if x else '—')
        st.dataframe(df, width='stretch', hide_index=True)
        st.caption('Consensus depth = number of books that contributed to the vig-free fair line.')
    else:
        _placeholder()

st.divider()

# ── Section B: Win rate / ROI tables ─────────────────────────────────────────
st.subheader('B — Win Rate & ROI')

def _wrt_display(rows, label_col):
    if not rows:
        _placeholder()
        return
    df = pd.DataFrame(rows)
    df = df.rename(columns={
        'group_key': label_col, 'total': 'Plays',
        'wins': 'W', 'losses': 'L', 'pushes': 'P', 'voids': 'V',
    })
    df['Win %'] = (df['W'] / (df['W'] + df['L']).replace(0, float('nan')) * 100).apply(
        lambda x: f'{x:.1f}%' if pd.notna(x) else '—')
    df['Avg Edge'] = df['avg_edge'].apply(lambda x: fmt_pct(x) if x else '—')
    df['Avg CLV']  = df['avg_clv'].apply(lambda x: fmt_pct(x)  if x else '—')
    df['ROI %']    = df['avg_roi'].apply(
        lambda x: f'{x*100:+.1f}%' if x is not None else '—')
    cols = [label_col, 'Plays', 'W', 'L', 'P', 'V', 'Win %', 'Avg Edge', 'Avg CLV', 'ROI %']
    st.dataframe(df[cols], width='stretch', hide_index=True)

tab_b1, tab_b2 = st.tabs(['By Color', 'By Market'])
with tab_b1:
    _wrt_display(wrt_color, 'Color')
with tab_b2:
    _wrt_display(wrt_market, 'Market')

st.divider()

# ── Section C: Calibration ────────────────────────────────────────────────────
st.subheader('C — Calibration Check')
st.caption('Compares the system\'s implied win probability (from the fair price) against the actual observed win rate.')

if calib:
    rows = []
    for r in calib:
        implied = r['avg_implied_prob']
        actual  = r['actual_win_rate']
        diff    = (actual - implied) * 100 if implied and actual else None
        rows.append({
            'Color':        r['confidence_color'].capitalize(),
            'Avg Implied %': f'{implied*100:.1f}%' if implied else '—',
            'Actual Win %':  f'{actual*100:.1f}%'  if actual  else '—',
            'Gap':           fmt_pct(diff)          if diff is not None else '—',
            'Sample':        r['n'],
        })
    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    st.caption('Gap = Actual − Implied. Negative gap means the system is over-confident; positive means under-confident.')
else:
    _placeholder()

st.divider()

# ── Section D: Daily volume chart ─────────────────────────────────────────────
st.subheader('D — Recommendation Volume (Last 30 Days)')
if vol:
    df_vol = pd.DataFrame(vol)
    fig = go.Figure(go.Bar(x=df_vol['date'], y=df_vol['n'],
                           marker_color='#2196F3'))
    fig.update_layout(
        yaxis_title='Green + Yellow Recs',
        height=250, margin=dict(l=0, r=0, t=10, b=0),
    )
    st.plotly_chart(fig, width='stretch')
else:
    _placeholder('No recommendation volume data yet for the last 30 days.')
