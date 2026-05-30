"""
1_Today.py — Today's recommendations and game slate.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import pytz

from database import get_connection, init_db
from components.metrics import recs_for_date, games_for_date
from components.formatters import (
    fmt_american, fmt_pct, fmt_dollars, fmt_game_time,
    color_tag, market_label, utc_to_eastern,
    fmt_date_friendly, fmt_date_short,
)

EASTERN = pytz.timezone('US/Eastern')

st.set_page_config(page_title='Today — MLB Betting', page_icon='📋', layout='wide')
init_db()

# ── Date selector ─────────────────────────────────────────────────────────────
today = datetime.now(EASTERN).date()

col_prev, col_date, col_next, col_refresh = st.columns([1, 3, 1, 2])

if 'selected_date' not in st.session_state:
    st.session_state.selected_date = today

if col_prev.button('← Prev'):
    st.session_state.selected_date -= timedelta(days=1)
if col_next.button('Next →'):
    st.session_state.selected_date += timedelta(days=1)

selected_date = col_date.date_input(
    'Date', value=st.session_state.selected_date, label_visibility='collapsed'
)
st.session_state.selected_date = selected_date
date_str = selected_date.strftime('%Y-%m-%d')

# Refresh button — re-runs analyzer against existing snapshots
if col_refresh.button('🔄 Re-analyze'):
    with st.spinner('Running analyzer...'):
        with get_connection() as conn:
            from analyzer import generate_all_recommendations
            result = generate_all_recommendations(conn)
    st.success(
        f"Analyzer done: {result['total_written']} new recommendations written "
        f"across {result['games_analyzed']} games."
    )
    st.rerun()

from datetime import date as _date
_dt_selected = datetime.combine(selected_date, datetime.min.time())
st.subheader(f'📋 {selected_date.strftime("%A, %B")} {selected_date.day}, {selected_date.year}')

with get_connection() as conn:
    all_recs  = recs_for_date(conn, date_str)
    all_games = games_for_date(conn, date_str)

real_recs   = [r for r in all_recs if r['is_shadow'] == 0]
shadow_recs = [r for r in all_recs if r['is_shadow'] == 1]

# Which games have recs?
games_with_recs = {r['game_pk'] for r in all_recs}

# ── Helper: build display dataframe ──────────────────────────────────────────
def build_rec_df(recs):
    rows = []
    for r in recs:
        away_p = r['away_pitcher'] or 'TBD'
        home_p = r['home_pitcher'] or 'TBD'
        rows.append({
            'Game':            f"{r['away_team']} @ {r['home_team']}  {fmt_game_time(r['game_datetime_utc'])}",
            'Pitching':        f'{away_p} vs {home_p}',
            'Market':          market_label(r['market'], r['side'], r['line']),
            'Target':          fmt_american(r['target_price_american']),
            'Fair':            fmt_american(r['fair_price_american']),
            'Edge':            fmt_pct(r['edge_percent']),
            'Color':           color_tag(r['confidence_color']) + ' ' + r['confidence_color'].capitalize(),
            'Stake $':         fmt_dollars(r['recommended_stake_dollars_at_2500']),
            'Books':           r['num_books_in_consensus'],
            '_color':          r['confidence_color'],
            '_id':             r['id'],
            '_game_pk':        r['game_pk'],
        })
    return pd.DataFrame(rows)


def style_rec_df(df):
    """Color rows by confidence_color."""
    def row_style(row):
        c = row['_color']
        bg = {'green': '#E8F5E9', 'yellow': '#FFFDE7', 'red': '#FFEBEE'}.get(c, '')
        return [f'background-color: {bg}'] * len(row)
    return df.style.apply(row_style, axis=1)


# ── Section: Real recommendations ────────────────────────────────────────────
st.subheader('Today\'s Recommendations')

if real_recs:
    df_real = build_rec_df(real_recs)
    display_cols = ['Game', 'Pitching', 'Market', 'Target', 'Fair', 'Edge', 'Color', 'Stake $', 'Books']
    st.dataframe(
        style_rec_df(df_real[display_cols + ['_color']]).hide(axis='index'),
        width='stretch',
        hide_index=True,
    )

    # "+ Log this bet" buttons
    st.caption('Log a bet from a recommendation:')
    btn_cols = st.columns(min(len(real_recs), 4))
    for i, rec in enumerate(real_recs):
        with btn_cols[i % 4]:
            label = (f"{rec['away_team']} @ {rec['home_team']} — "
                     f"{market_label(rec['market'], rec['side'], rec['line'])}")
            if st.button(f'+ Log: {label[:40]}', key=f'log_{rec["id"]}'):
                st.session_state['prefill_bet'] = {
                    'game_pk':    rec['game_pk'],
                    'market':     rec['market'],
                    'side':       rec['side'],
                    'line':       rec['line'],
                    'price':      rec['target_price_american'],
                    'rec_id':     rec['id'],
                }
                st.switch_page('pages/3_My_Bets.py')
else:
    st.info(
        f'No green or yellow recommendations for {selected_date.strftime("%B")} {selected_date.day}. '
        'Either the analyzer found no edges today or it hasn\'t run yet — '
        'click 🔄 Re-analyze to check against current odds.'
    )

# ── Section: Shadow plays ────────────────────────────────────────────────────
with st.expander(f'🔴 Shadow plays (red) — {len(shadow_recs)} tracked, not advised as real bets'):
    if shadow_recs:
        df_shadow = build_rec_df(shadow_recs)
        display_cols = ['Game', 'Market', 'Target', 'Fair', 'Edge', 'Books']
        st.dataframe(df_shadow[display_cols], width='stretch', hide_index=True)
    else:
        st.write('No shadow plays for this date.')

# ── Section: Games with no recommendations ────────────────────────────────────
no_rec_games = [g for g in all_games if g['game_pk'] not in games_with_recs]
with st.expander(f'⚪ Games with no recommendations — {len(no_rec_games)} game(s)'):
    if no_rec_games:
        rows = []
        for g in no_rec_games:
            away_p = g['away_pitcher'] or 'TBD'
            home_p = g['home_pitcher'] or 'TBD'
            # Determine reason
            with get_connection() as conn:
                has_odds = conn.execute(
                    "SELECT 1 FROM odds_snapshots WHERE game_pk=? LIMIT 1", (g['game_pk'],)
                ).fetchone()
            if not has_odds:
                reason = 'Not yet analyzed'
            else:
                reason = 'Tight market'
            rows.append({
                'Game':     f"{g['away_team']} @ {g['home_team']}",
                'Pitching': f'{away_p} vs {home_p}',
                'First Pitch': fmt_game_time(g['game_datetime_utc']),
                'Reason':   reason,
            })
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.write('All games for this date have recommendations or no games scheduled.')
