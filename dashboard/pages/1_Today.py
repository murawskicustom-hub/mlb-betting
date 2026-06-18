"""
1_Today.py -- Today's recommendations and game slate.
Two sections: Algo 1 (devig/market consensus) and Algo 2 (predictive model, shadow only).
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
from components.auth import require_login
from components.metrics import recs_for_date, games_for_date
from components.formatters import fmt_game_time, market_label
from components.styles import (
    inject_custom_css, play_card, model_play_card, empty_state, section_head,
    page_header, _next_slot,
)

EASTERN = pytz.timezone('US/Eastern')

st.set_page_config(page_title='Today -- MLB Betting', page_icon='=', layout='wide',
                   initial_sidebar_state='expanded')
inject_custom_css()
require_login()   # password gate — nothing below renders until authenticated
init_db()

# -- Date nav bar ---------------------------------------------------------------
today = datetime.now(EASTERN).date()

col_prev, col_date, col_next, col_refresh = st.columns([1, 3, 1, 2])

if 'selected_date' not in st.session_state:
    st.session_state.selected_date = today

if col_prev.button('< PREV'):
    st.session_state.selected_date -= timedelta(days=1)
if col_next.button('NEXT >'):
    st.session_state.selected_date += timedelta(days=1)

selected_date = col_date.date_input(
    'Date', value=st.session_state.selected_date, label_visibility='collapsed'
)
st.session_state.selected_date = selected_date
date_str = selected_date.strftime('%Y-%m-%d')

if col_refresh.button('RE-ANALYZE'):
    with st.spinner('Scanning odds for edges...'):
        with get_connection() as conn:
            from analyzer import generate_all_recommendations, generate_model_recommendations
            result       = generate_all_recommendations(conn)
            model_result = generate_model_recommendations(conn)
    n1 = result['total_written']
    n2 = model_result['total_written']
    st.success(
        f'Algo 1: {n1} rec{"s" if n1 != 1 else ""} written. '
        f'Algo 2: {n2} model rec{"s" if n2 != 1 else ""} written '
        f'({model_result["abstained"]} abstained).'
    )
    st.rerun()

# Page heading
_date_str = f'{selected_date.strftime("%A, %B")} {selected_date.day}, {selected_date.year}'
page_header("TODAY'S BOARD", _date_str)

with get_connection() as conn:
    all_recs  = recs_for_date(conn, date_str)
    all_games = games_for_date(conn, date_str)

# Split by algo
devig_recs = [r for r in all_recs if r.get('algo', 'devig') == 'devig']
model_recs = [r for r in all_recs if r.get('algo') == 'model_v1']

# Real (actionable) vs shadow within devig
devig_real   = [r for r in devig_recs if r['is_shadow'] == 0]
devig_shadow = [r for r in devig_recs if r['is_shadow'] == 1]

games_w_recs = {r['game_pk'] for r in all_recs}

# Build lookup: (game_pk, market, side) -> True for devig recs — used for BOTH ALGOS badge
devig_keys = {
    (r['game_pk'], r['market'], r['side'])
    for r in devig_recs
}

# ============================================================================ #
# SECTION: Algo 1 — Market Consensus (devig)
# ============================================================================ #

st.markdown("""
<div class="algo-section-header">
  <div class="algo-section-label">ALGO 1 &middot; MARKET CONSENSUS</div>
</div>
""", unsafe_allow_html=True)

if devig_real:
    for rec in devig_real:
        st.markdown(play_card(rec), unsafe_allow_html=True)
        col_log, col_spacer = st.columns([2, 8])
        with col_log:
            btn_label = f'LOG BET > {market_label(rec["market"], rec["side"], rec.get("line")).upper()}'
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
    n_games   = len(all_games)
    next_slot = _next_slot()
    _sub = f'{n_games} game{"s" if n_games != 1 else ""} scanned -- market efficient'
    empty_state('No qualifying edges', _sub, f'NEXT SCAN: {next_slot}')

# Shadow ledger (devig)
with st.expander(f'ALGO 1 SHADOW LEDGER -- {len(devig_shadow)} CONTROL GROUP PLAYS (NOT ADVISED)'):
    st.markdown('<div class="shadow-header">RED SIGNAL - TRACKED FOR CALIBRATION ONLY</div>',
                unsafe_allow_html=True)
    if devig_shadow:
        for rec in devig_shadow:
            st.markdown(play_card(rec), unsafe_allow_html=True)
    else:
        st.markdown(
            '<span style="font-size:12px;font-family:\'JetBrains Mono\',monospace;'
            'color:#8B92A8;">NO SHADOW PLAYS THIS DATE</span>',
            unsafe_allow_html=True
        )

# ============================================================================ #
# SECTION: Algo 2 -- Predictive Model (model_v1)
# ============================================================================ #

st.markdown("""
<div class="algo-section-header">
  <div class="algo-section-label model-label">
    ALGO 2 &middot; PREDICTIVE MODEL
    <span class="algo-unproven-tag">UNPROVEN -- $0 STAKES</span>
  </div>
</div>
""", unsafe_allow_html=True)

model_ml_recs   = [r for r in model_recs if r['market'] in ('moneyline', 'total', 'spread')]
model_f5_recs   = [r for r in model_recs if r['market'] in ('f5_moneyline', 'f5_total')]
model_yrfi_recs = [r for r in model_recs if r['market'] in ('yrfi', 'nrfi')]

_MONO = "font-family:'JetBrains Mono',monospace;"

def _sub_head(label: str) -> None:
    st.markdown(
        f'<div style="{_MONO}font-size:9px;font-weight:600;letter-spacing:0.14em;'
        f'text-transform:uppercase;color:#6B7280;border-bottom:1px solid #1E2430;'
        f'padding-bottom:4px;margin:14px 0 8px 0;">{label}</div>',
        unsafe_allow_html=True,
    )

# ML picks
if model_ml_recs:
    for rec in model_ml_recs:
        key = (rec['game_pk'], rec['market'], rec['side'])
        has_devig = key in devig_keys
        st.markdown(model_play_card(rec, has_devig_match=has_devig), unsafe_allow_html=True)
elif not model_f5_recs and not model_yrfi_recs:
    empty_state(
        'No model signals today',
        'Model requires confirmed starters with sufficient IP data (>=10 IP).',
        'ALGO 2 PREVIEW - Pythagorean model with FIP-based pitcher quality and park factors'
    )

# F5 picks
_sub_head('F5 PICKS')
if model_f5_recs:
    for rec in model_f5_recs:
        key = (rec['game_pk'], rec['market'], rec['side'])
        has_devig = key in devig_keys
        st.markdown(model_play_card(rec, has_devig_match=has_devig), unsafe_allow_html=True)
else:
    st.markdown(
        f'<span style="{_MONO}font-size:11px;color:#6B7280;">'
        f'No F5 picks today — {len(all_games)} game{"s" if len(all_games) != 1 else ""} analyzed</span>',
        unsafe_allow_html=True,
    )

# YRFI/NRFI picks
_sub_head('YRFI / NRFI PICKS')
if model_yrfi_recs:
    for rec in model_yrfi_recs:
        key = (rec['game_pk'], rec['market'], rec['side'])
        has_devig = key in devig_keys
        st.markdown(model_play_card(rec, has_devig_match=has_devig), unsafe_allow_html=True)
else:
    st.markdown(
        f'<span style="{_MONO}font-size:11px;color:#6B7280;">'
        f'No YRFI/NRFI picks today — {len(all_games)} game{"s" if len(all_games) != 1 else ""} analyzed</span>',
        unsafe_allow_html=True,
    )

# ============================================================================ #
# SECTION: Full slate -- games without recommendations
# ============================================================================ #

no_rec = [g for g in all_games if g['game_pk'] not in games_w_recs]
with st.expander(f'FULL SLATE -- {len(no_rec)} GAME{"S" if len(no_rec) != 1 else ""} WITHOUT RECOMMENDATIONS'):
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
