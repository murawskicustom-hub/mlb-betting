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
from components.formatters import fmt_game_time, market_label
from components.styles import (
    inject_custom_css, play_card, empty_state, section_head,
    _next_slot,
)

EASTERN = pytz.timezone('US/Eastern')

st.set_page_config(page_title='Today — MLB Betting', page_icon='📋', layout='wide')
inject_custom_css()
init_db()

# ── Date nav bar ──────────────────────────────────────────────────────────────
today = datetime.now(EASTERN).date()

col_prev, col_date, col_next, col_refresh = st.columns([1, 3, 1, 2])

if 'selected_date' not in st.session_state:
    st.session_state.selected_date = today

if col_prev.button('← PREV'):
    st.session_state.selected_date -= timedelta(days=1)
if col_next.button('NEXT →'):
    st.session_state.selected_date += timedelta(days=1)

selected_date = col_date.date_input(
    'Date', value=st.session_state.selected_date, label_visibility='collapsed'
)
st.session_state.selected_date = selected_date
date_str = selected_date.strftime('%Y-%m-%d')

if col_refresh.button('↻ RE-ANALYZE'):
    with st.spinner('Scanning odds for edges...'):
        with get_connection() as conn:
            from analyzer import generate_all_recommendations
            result = generate_all_recommendations(conn)
    n = result['total_written']
    st.success(f'{n} new recommendation{"s" if n != 1 else ""} written across {result["games_analyzed"]} games.')
    st.rerun()

# Page heading
st.markdown(
    f'<div style="font-size:22px;font-weight:700;letter-spacing:-0.01em;margin:8px 0 20px;">'
    f'{selected_date.strftime("%A, %B")} {selected_date.day}, {selected_date.year}</div>',
    unsafe_allow_html=True
)

with get_connection() as conn:
    all_recs  = recs_for_date(conn, date_str)
    all_games = games_for_date(conn, date_str)

real_recs    = [r for r in all_recs if r['is_shadow'] == 0]
shadow_recs  = [r for r in all_recs if r['is_shadow'] == 1]
games_w_recs = {r['game_pk'] for r in all_recs}

# ── Section: Live plays ───────────────────────────────────────────────────────
section_head(f'QUALIFYING EDGES — {len(real_recs)} PLAY{"S" if len(real_recs) != 1 else ""}')

if real_recs:
    for rec in real_recs:
        st.markdown(play_card(rec), unsafe_allow_html=True)
        # Log bet button sits just below each card
        col_log, col_spacer = st.columns([2, 8])
        with col_log:
            btn_label = f'LOG BET → {market_label(rec["market"], rec["side"], rec.get("line")).upper()}'
            if st.button(btn_label, key=f'log_{rec["id"]}'):
                st.session_state['prefill_bet'] = {
                    'game_pk': rec['game_pk'],
                    'market':  rec['market'],
                    'side':    rec['side'],
                    'line':    rec.get('line'),
                    'price':   rec['target_price_american'],
                    'rec_id':  rec['id'],
                }
                st.switch_page('pages/3_My_Bets.py')
else:
    n_games = len(all_games)
    next_slot = _next_slot()
    empty_state(
        f'NO QUALIFYING EDGES',
        f'{n_games} GAMES SCANNED — MARKET EFFICIENT · NEXT SCAN: {next_slot}'
    )

# ── Section: Shadow ledger ────────────────────────────────────────────────────
with st.expander(f'SHADOW LEDGER — {len(shadow_recs)} CONTROL GROUP PLAYS (NOT ADVISED)'):
    st.markdown('<div class="shadow-header">RED SIGNAL · TRACKED FOR CALIBRATION ONLY</div>',
                unsafe_allow_html=True)
    if shadow_recs:
        for rec in shadow_recs:
            st.markdown(play_card(rec), unsafe_allow_html=True)
    else:
        st.markdown(
            '<span style="font-size:12px;font-family:\'JetBrains Mono\',monospace;'
            'color:#8B92A8;">NO SHADOW PLAYS THIS DATE</span>',
            unsafe_allow_html=True
        )

# ── Section: No-recommendation games ─────────────────────────────────────────
no_rec = [g for g in all_games if g['game_pk'] not in games_w_recs]
with st.expander(f'FULL SLATE — {len(no_rec)} GAME{"S" if len(no_rec) != 1 else ""} WITHOUT RECOMMENDATIONS'):
    if no_rec:
        rows = []
        for g in no_rec:
            ap = g['away_pitcher'] or 'TBD'
            hp = g['home_pitcher'] or 'TBD'
            with get_connection() as conn:
                has_odds = conn.execute(
                    'SELECT 1 FROM odds_snapshots WHERE game_pk=? LIMIT 1', (g['game_pk'],)
                ).fetchone()
            rows.append({
                'GAME':        f"{g['away_team']} @ {g['home_team']}",
                'PITCHING':    f'{ap} vs {hp}',
                'FIRST PITCH': fmt_game_time(g['game_datetime_utc']),
                'STATUS':      'TIGHT MARKET' if has_odds else 'NOT YET ANALYZED',
            })
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.markdown(
            '<span style="font-size:12px;font-family:\'JetBrains Mono\',monospace;'
            'color:#8B92A8;">ALL GAMES HAVE RECOMMENDATIONS</span>',
            unsafe_allow_html=True
        )
