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
from components.styles import inject_custom_css, section_head

EASTERN = pytz.timezone('US/Eastern')

st.set_page_config(page_title='My Bets — MLB Betting', page_icon='💰', layout='wide')
inject_custom_css()
init_db()

st.markdown('<div style="font-size:22px;font-weight:700;margin-bottom:20px;">My Bets</div>',
            unsafe_allow_html=True)

# ── Section A: Bet entry form ─────────────────────────────────────────────────
section_head('LOG A BET')

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
    game_pks = list(game_options.keys())
    default_game_idx = 0
    if prefill.get('game_pk') in game_pks:
        default_game_idx = game_pks.index(prefill['game_pk'])
    selected_pk = st.selectbox(
        'GAME',
        options=game_pks,
        format_func=lambda pk: game_options.get(pk, str(pk)),
        index=default_game_idx,
    )

    col1, col2 = st.columns(2)
    with col1:
        book = st.text_input('BOOK', value=st.session_state.get('last_book', 'hardrock'))
        market_choices = ['moneyline', 'total', 'spread']
        default_mkt = market_choices.index(prefill['market']) if prefill.get('market') in market_choices else 0
        market = st.selectbox('MARKET', market_choices, index=default_mkt)
    with col2:
        side_opts = {'moneyline': ['home', 'away'], 'total': ['over', 'under'], 'spread': ['home', 'away']}
        sides = side_opts.get(market, ['home', 'away'])
        default_side = sides.index(prefill['side']) if prefill.get('side') in sides else 0
        side = st.selectbox('SIDE', sides, index=default_side)
        line = st.number_input('LINE (totals/spreads)',
                               value=float(prefill.get('line') or 0.0),
                               disabled=(market == 'moneyline'), format='%.1f')

    col3, col4 = st.columns(2)
    with col3:
        price = st.number_input('ACTUAL PRICE (AMERICAN)', value=int(prefill.get('price') or -110), step=1)
        stake = st.number_input('STAKE ($)', min_value=0.01, value=25.00, step=5.0, format='%.2f')
    with col4:
        placed_at_str = st.text_input('PLACED AT (YYYY-MM-DD HH:MM ET)',
                                      value=datetime.now(EASTERN).strftime('%Y-%m-%d %H:%M'))
        with get_connection() as conn2:
            unbet = unbet_recs_for_game(conn2, selected_pk)
        rec_opts = {None: '— NONE (FREELANCE BET) —'}
        rec_opts.update({
            r['id']: f"REC #{r['id']}  {r['market'].upper()}/{r['side'].upper()} "
                     f"{fmt_american(r['target_price_american'])} [{r['confidence_color'].upper()}]"
            for r in unbet
        })
        rec_keys = list(rec_opts.keys())
        default_rec = prefill.get('rec_id')
        rec_idx = rec_keys.index(default_rec) if default_rec in rec_keys else 0
        linked_rec = st.selectbox('LINKED RECOMMENDATION (OPTIONAL)',
                                  options=rec_keys,
                                  format_func=lambda k: rec_opts[k],
                                  index=rec_idx)

    submitted = st.form_submit_button('✓ LOG BET')

if submitted:
    try:
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
        st.toast(
            f'BET LOGGED: {game_options.get(selected_pk,"")} | '
            f'{market.upper()}/{side.upper()} {fmt_american(int(price))} ${stake:.2f}',
            icon='✓'
        )
        st.rerun()
    except Exception as e:
        st.error(f'Error logging bet: {e}')

# ── Section B: Bet log ────────────────────────────────────────────────────────
section_head('BET LOG')

with get_connection() as conn:
    bets = all_bets(conn)

if not bets:
    st.markdown(
        '<span style="font-family:\'JetBrains Mono\',monospace;font-size:12px;color:#8B92A8;">'
        'NO BETS LOGGED — use the form above to record your first bet.</span>',
        unsafe_allow_html=True
    )
else:
    fc1, fc2, _ = st.columns(3)
    statuses  = ['ALL'] + sorted({b['result'].upper() if b['result'] else 'PENDING' for b in bets})
    books_all = ['ALL'] + sorted({b['book'] for b in bets})
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
        rows.append({
            'DATE':    b['placed_at_utc'][:10],
            'GAME':    f"{b['away_team']} @ {b['home_team']}",
            'MARKET':  f"{b['market'].upper()}/{b['side'].upper()}" + (f" {b['line']}" if b['line'] else ''),
            'PRICE':   fmt_american(b['actual_price_american']),
            'STAKE':   fmt_dollars(b['stake_dollars']),
            'STATUS':  b['result'].upper() if b['result'] else 'PENDING',
            'PAYOUT':  fmt_dollars(b['payout_dollars']),
            'P/L':     fmt_dollars(b['profit_loss_dollars'], always_sign=True),
            'CLV':     fmt_pct(b['clv_percent']),
            'REC':     f'#{b["recommendation_id"]}' if b['recommendation_id'] else '—',
            '_pending': b['result'] is None,
        })

    df = pd.DataFrame(rows)

    def _style(df):
        STATUS_BG = {
            'WIN':  'background-color:#0D1F1A;color:#00D4AA',
            'LOSS': 'background-color:#1A0E12;color:#8B92A8',
            'PUSH': 'background-color:#1A1800;color:#FFB454',
            'VOID': 'background-color:#151020;color:#8B92A8',
        }
        def row(r):
            if r['_pending']:
                return ['color:#8B92A8'] * len(r)
            s = r['STATUS']
            style = STATUS_BG.get(s, '')
            return [style] * len(r)
        return df.style.apply(row, axis=1)

    display = ['DATE','GAME','MARKET','PRICE','STAKE','STATUS','PAYOUT','P/L','CLV','REC']
    st.dataframe(
        _style(df[display + ['_pending']]).hide(axis='index'),
        width='stretch', hide_index=True,
    )
    st.markdown(
        f'<span style="font-size:11px;font-family:\'JetBrains Mono\',monospace;color:#8B92A8;">'
        f'{len(filtered)} OF {len(bets)} BETS</span>',
        unsafe_allow_html=True
    )
