"""
3_My_Bets.py — bet entry form and personal bet log (units-native).
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st
import pandas as pd
from datetime import datetime, timezone
import pytz

from database import get_connection, init_db
from components.auth import require_login
from components.metrics import all_bets, upcoming_games_for_picker, unbet_recs_for_game
from components.formatters import fmt_american, fmt_pct, fmt_game_time
from components.styles import inject_custom_css, section_head, page_header

SPORT = 'nfl'
EASTERN = pytz.timezone('US/Eastern')

st.set_page_config(page_title='My Bets — 3 Bettors', page_icon='💰', layout='wide',
                   initial_sidebar_state='expanded')
inject_custom_css()
require_login()   # password gate — nothing below renders until authenticated
init_db()

page_header('MY BETS', 'Bet log & entry')

# ── Section A: Bet entry form ─────────────────────────────────────────────────
section_head('LOG A BET')

with get_connection() as conn:
    games = upcoming_games_for_picker(conn, SPORT)

game_options = {
    g['game_id']: (
        f"{g['away_team']} @ {g['home_team']}"
        f"  —  {g['game_date']}  {fmt_game_time(g['start_utc'])}"
    )
    for g in games
}

with st.form('bet_entry', clear_on_submit=True):
    game_ids = list(game_options.keys())
    selected_id = st.selectbox(
        'GAME',
        options=game_ids,
        format_func=lambda gid: game_options.get(gid, str(gid)),
    )

    col1, col2 = st.columns(2)
    with col1:
        book = st.text_input('BOOK', value=st.session_state.get('last_book', 'draftkings'))
        market = st.selectbox('MARKET', ['moneyline', 'total', 'spread'])
    with col2:
        side_opts = {'moneyline': ['home', 'away'], 'total': ['over', 'under'], 'spread': ['home', 'away']}
        sides = side_opts.get(market, ['home', 'away'])
        side = st.selectbox('SIDE', sides)
        line = st.number_input('LINE (totals/spreads)', value=0.0,
                               disabled=(market == 'moneyline'), format='%.1f')

    col3, col4 = st.columns(2)
    with col3:
        price = st.number_input('PRICE (AMERICAN)', value=-110, step=1)
        units = st.number_input('UNITS', min_value=0.5, value=1.0, step=0.5, format='%.1f')
    with col4:
        placed_at_str = st.text_input('PLACED AT (YYYY-MM-DD HH:MM ET)',
                                      value=datetime.now(EASTERN).strftime('%Y-%m-%d %H:%M'))
        with get_connection() as conn2:
            unbet = unbet_recs_for_game(conn2, selected_id) if selected_id else []
        rec_opts = {None: '— NONE (FREELANCE BET) —'}
        rec_opts.update({
            r['id']: f"#{r['id']} {r['bot_key']} {r['market'].upper()}/{r['side'].upper()} "
                     f"{fmt_american(r['target_price_american'])} [{r['confidence']}]"
            for r in unbet
        })
        linked_rec = st.selectbox('LINKED RECOMMENDATION (OPTIONAL)',
                                  options=list(rec_opts.keys()),
                                  format_func=lambda k: rec_opts[k])

    submitted = st.form_submit_button('✓ LOG BET')

if submitted:
    try:
        try:
            placed_et = EASTERN.localize(datetime.strptime(placed_at_str.strip(), '%Y-%m-%d %H:%M'))
        except ValueError:
            placed_et = datetime.now(EASTERN)
        placed_utc = placed_et.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        line_val = None if market == 'moneyline' else float(line)
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO bets
                    (sport, game_id, book, market, side, line, price_american, units,
                     placed_at_utc, recommendation_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (SPORT, selected_id, book, market, side, line_val,
                  int(price), float(units), placed_utc, linked_rec))
        st.session_state['last_book'] = book
        st.toast(
            f'BET LOGGED: {game_options.get(selected_id,"")} | '
            f'{market.upper()}/{side.upper()} {fmt_american(int(price))} {units}u',
            icon='✅'
        )
        st.rerun()
    except Exception as e:
        st.error(f'Error logging bet: {e}')

# ── Section B: Bet log ────────────────────────────────────────────────────────
section_head('BET LOG')

with get_connection() as conn:
    bets = all_bets(conn, SPORT)

if not bets:
    st.markdown(
        '<span style="font-family:\'JetBrains Mono\',monospace;font-size:12px;color:#8B92A8;">'
        'NO BETS LOGGED — use the form above to record your first bet.</span>',
        unsafe_allow_html=True
    )
else:
    fc1, fc2, _ = st.columns(3)
    statuses  = ['ALL'] + sorted({b['result'].upper() if b['result'] else 'PENDING' for b in bets})
    books_all = ['ALL'] + sorted({b['book'] for b in bets if b['book']})
    sel_status = fc1.selectbox('STATUS', statuses, key='flt_status')
    sel_book   = fc2.selectbox('BOOK',   books_all, key='flt_book')

    filtered = bets
    if sel_status != 'ALL':
        filtered = [b for b in filtered
                    if (b['result'].upper() if b['result'] else 'PENDING') == sel_status]
    if sel_book != 'ALL':
        filtered = [b for b in filtered if b['book'] == sel_book]

    rows = []
    for b in filtered:
        game = f"{b['away_team']} @ {b['home_team']}" if b.get('away_team') else '—'
        rows.append({
            'DATE':   b['placed_at_utc'][:10] if b['placed_at_utc'] else '—',
            'GAME':   game,
            'MARKET': f"{(b['market'] or '').upper()}/{(b['side'] or '').upper()}" + (f" {b['line']}" if b['line'] else ''),
            'PRICE':  fmt_american(b['price_american']),
            'UNITS':  f"{b['units']:.1f}u" if b['units'] else '—',
            'STATUS': b['result'].upper() if b['result'] else 'PENDING',
            'P/L':    f"{b['unit_profit']:+.2f}u" if b['unit_profit'] is not None else '—',
            'CLV':    fmt_pct(b['clv_percent']),
            'REC':    f"#{b['recommendation_id']}" if b['recommendation_id'] else '—',
        })

    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    st.markdown(
        f'<span style="font-size:11px;font-family:\'JetBrains Mono\',monospace;color:#8B92A8;">'
        f'{len(filtered)} OF {len(bets)} BETS</span>',
        unsafe_allow_html=True
    )
