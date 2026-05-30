"""
3_My_Bets.py — Bet entry form and personal bet log.
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
from components.metrics import all_bets, upcoming_games_for_picker, unbet_recs_for_game
from components.formatters import fmt_american, fmt_dollars, fmt_pct, fmt_game_time

EASTERN = pytz.timezone('US/Eastern')

st.set_page_config(page_title='My Bets — MLB Betting', page_icon='💰', layout='wide')
init_db()
st.title('💰 My Bets')

# ── Section A: Bet entry form ─────────────────────────────────────────────────
st.subheader('Log a Bet')

with get_connection() as conn:
    games = upcoming_games_for_picker(conn)

game_options = {
    g['game_pk']: (
        f"{g['away_team']} @ {g['home_team']}"
        f"  —  {g['game_date']}  {fmt_game_time(g['game_datetime_utc'])}"
    )
    for g in games
}

prefill = st.session_state.pop('prefill_bet', {})

with st.form('bet_entry', clear_on_submit=True):
    # Game selector
    game_pks = list(game_options.keys())
    default_game_idx = 0
    if prefill.get('game_pk') in game_pks:
        default_game_idx = game_pks.index(prefill['game_pk'])
    selected_pk = st.selectbox(
        'Game',
        options=game_pks,
        format_func=lambda pk: game_options.get(pk, str(pk)),
        index=default_game_idx,
    )

    col1, col2 = st.columns(2)
    with col1:
        book = st.text_input('Book', value=st.session_state.get('last_book', 'hardrock'))
        market_choices = ['moneyline', 'total', 'spread']
        default_mkt = market_choices.index(prefill['market']) if prefill.get('market') in market_choices else 0
        market = st.selectbox('Market', market_choices, index=default_mkt)

    with col2:
        # Side options depend on market
        side_opts = {'moneyline': ['home', 'away'],
                     'total':     ['over', 'under'],
                     'spread':    ['home', 'away']}
        sides = side_opts.get(market, ['home', 'away'])
        default_side = sides.index(prefill['side']) if prefill.get('side') in sides else 0
        side = st.selectbox('Side', sides, index=default_side)
        line = st.number_input(
            'Line (totals/spreads only)',
            value=float(prefill.get('line') or 0.0),
            disabled=(market == 'moneyline'),
            format='%.1f',
        )

    col3, col4 = st.columns(2)
    with col3:
        price = st.number_input(
            'Actual Price (American)',
            value=int(prefill.get('price') or -110),
            step=1,
        )
        stake = st.number_input('Stake ($)', min_value=0.01, value=25.00, step=5.0, format='%.2f')

    with col4:
        placed_at_default = datetime.now(EASTERN).strftime('%Y-%m-%d %H:%M')
        placed_at_str = st.text_input('Placed at (YYYY-MM-DD HH:MM ET)', value=placed_at_default)

        # Linked recommendation picker
        with get_connection() as conn2:
            unbet = unbet_recs_for_game(conn2, selected_pk)
        rec_opts = {None: '— None (freelance bet) —'}
        rec_opts.update({
            r['id']: (f"Rec #{r['id']}  {r['market']}/{r['side']} "
                      f"{fmt_american(r['target_price_american'])} [{r['confidence_color']}]")
            for r in unbet
        })
        default_rec = prefill.get('rec_id')
        rec_keys = list(rec_opts.keys())
        rec_idx  = rec_keys.index(default_rec) if default_rec in rec_keys else 0
        linked_rec = st.selectbox(
            'Linked Recommendation (optional)',
            options=rec_keys,
            format_func=lambda k: rec_opts[k],
            index=rec_idx,
        )

    submitted = st.form_submit_button('✅ Log Bet')

if submitted:
    try:
        # Parse placed_at
        try:
            placed_et = EASTERN.localize(datetime.strptime(placed_at_str.strip(), '%Y-%m-%d %H:%M'))
        except ValueError:
            placed_et = datetime.now(EASTERN)
        placed_utc = placed_et.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        line_val = None if market == 'moneyline' else float(line)
        rec_id   = linked_rec if linked_rec is not None else None

        with get_connection() as conn:
            conn.execute("""
                INSERT INTO personal_bets
                    (game_pk, placed_at_utc, book, market, side, line,
                     actual_price_american, stake_dollars, recommendation_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (selected_pk, placed_utc, book, market, side, line_val,
                  int(price), float(stake), rec_id))

        st.session_state['last_book'] = book
        st.toast(f'✅ Bet logged: {game_options.get(selected_pk, "")} | {market}/{side} {fmt_american(int(price))} ${stake:.2f}', icon='✅')
        st.rerun()
    except Exception as e:
        st.error(f'Error logging bet: {e}')

# ── Section B: Bet log ────────────────────────────────────────────────────────
st.subheader('Bet Log')

with get_connection() as conn:
    bets = all_bets(conn)

if not bets:
    st.info('No bets logged yet. Use the form above to record your first bet.')
else:
    df_bets = pd.DataFrame(bets)

    # Filters
    fc1, fc2, fc3 = st.columns(3)
    statuses  = ['All'] + sorted({b['result'] or 'Pending' for b in bets})
    books_all = ['All'] + sorted({b['book'] for b in bets})
    sel_status = fc1.selectbox('Status', statuses, key='flt_status')
    sel_book   = fc2.selectbox('Book',   books_all, key='flt_book')

    filtered = bets
    if sel_status != 'All':
        filtered = [b for b in filtered
                    if (b['result'] or 'Pending') == sel_status]
    if sel_book != 'All':
        filtered = [b for b in filtered if b['book'] == sel_book]

    rows = []
    for b in filtered:
        rows.append({
            'Date':       b['placed_at_utc'][:10],
            'Game':       f"{b['away_team']} @ {b['home_team']}",
            'Market':     f"{b['market']}/{b['side']}" + (f" {b['line']}" if b['line'] else ''),
            'Price':      fmt_american(b['actual_price_american']),
            'Stake':      fmt_dollars(b['stake_dollars']),
            'Status':     b['result'].capitalize() if b['result'] else 'Pending',
            'Payout':     fmt_dollars(b['payout_dollars']),
            'P/L':        fmt_dollars(b['profit_loss_dollars'], always_sign=True),
            'CLV %':      fmt_pct(b['clv_percent']),
            'Linked Rec': f'#{b["recommendation_id"]}' if b['recommendation_id'] else '—',
            '_pending':   b['result'] is None,
        })

    df_display = pd.DataFrame(rows)

    def style_bets(df):
        def row_bg(row):
            if row['_pending']:
                return ['background-color: #F5F5F5'] * len(row)
            status = row['Status']
            color = {'Win': '#E8F5E9', 'Loss': '#FFEBEE', 'Push': '#FFF9C4', 'Void': '#F3E5F5'}.get(status, '')
            return [f'background-color: {color}'] * len(row)
        return df.style.apply(row_bg, axis=1)

    display_cols = ['Date', 'Game', 'Market', 'Price', 'Stake', 'Status', 'Payout', 'P/L', 'CLV %', 'Linked Rec']
    st.dataframe(
        style_bets(df_display[display_cols + ['_pending']]).hide(axis='index'),
        width='stretch', hide_index=True,
    )
    st.caption(f'Showing {len(filtered)} of {len(bets)} bets.')
