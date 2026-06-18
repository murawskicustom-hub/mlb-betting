"""
2_Performance.py — System performance analytics.
Redesigned: understand the system in 10 seconds — headline, breakdown, trend, calibration.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from datetime import date, timedelta

from database import get_connection, init_db
from components.auth import require_login
from components.metrics import (
    unit_summary, unit_breakdown, unit_trend_by_day,
    calibration_by_prob_bucket, yrfi_calibration, head_to_head_recs,
    pick_ledger,
)
from components.formatters import fmt_pct
from components.styles import (
    inject_custom_css, empty_state, section_head, plotly_dark, page_header,
    C_ACCENT, C_YELLOW, C_RED_MUTED, C_MODEL,
)

st.set_page_config(page_title='Performance — MLB Betting', page_icon='o', layout='wide',
                   initial_sidebar_state='expanded')
inject_custom_css()
require_login()   # password gate — nothing below renders until authenticated
init_db()

page_header('PERFORMANCE', 'System performance analytics')

MONO = "font-family:'JetBrains Mono',monospace;"
_NO_DATA = 'No graded data yet'
_NO_DATA_SUB = 'Populates once picks are graded — meaningful after ~30 graded picks.'


# ── Engine selector ───────────────────────────────────────────────────────────
algo_label = st.radio(
    'ENGINE',
    options=['ALGO 1 — MARKET DEVIG', 'ALGO 2 — PREDICTIVE MODEL', 'HEAD TO HEAD'],
    horizontal=True,
    label_visibility='visible',
)

with get_connection() as conn:

    # ── Head-to-Head branch ───────────────────────────────────────────────────
    if 'HEAD TO HEAD' in algo_label:
        section_head('HEAD TO HEAD — BOTH ALGOS, SAME PICK')
        st.markdown(
            f'<span style="{MONO}font-size:11px;color:#8B92A8;">'
            'Games where Algo 1 (devig) and Algo 2 (model) independently rec\'d the same market/side.</span>',
            unsafe_allow_html=True,
        )
        st.markdown('<br>', unsafe_allow_html=True)

        h2h = head_to_head_recs(conn)
        if not h2h:
            empty_state(
                'No head-to-head picks yet',
                'Appears when both algos independently flag the same market/side on the same game. '
                'Convergence is a strong signal — expect 5–15% overlap once data accumulates.',
            )
        else:
            rows = []
            for r in h2h:
                rows.append({
                    'GAME':     f'{r["away_team"]} @ {r["home_team"]}',
                    'DATE':     r['game_date'],
                    'MARKET':   r['market'].upper(),
                    'SIDE':     r['side'].upper(),
                    'A1 EDGE':  f'{r["algo1_edge"]:+.1f}%' if r['algo1_edge'] is not None else '--',
                    'A2 EDGE':  f'{r["algo2_edge"]:+.1f}%' if r['algo2_edge'] is not None else '--',
                    'RESULT':   (r['result'] or '--').upper(),
                    'A1 CLV':   fmt_pct(r['algo1_clv']) if r['algo1_clv'] is not None else '--',
                    'A2 CLV':   fmt_pct(r['algo2_clv']) if r['algo2_clv'] is not None else '--',
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            st.markdown(
                f'<span style="{MONO}font-size:10px;color:#8B92A8;">{len(h2h)} shared picks graded</span>',
                unsafe_allow_html=True,
            )

    # ── Algo 1 / Algo 2 branch ────────────────────────────────────────────────
    else:
        algo = 'devig' if 'ALGO 1' in algo_label else 'model_v1'
        bar_color = C_MODEL if algo == 'model_v1' else C_ACCENT

        if algo == 'model_v1':
            st.markdown(
                f'<span style="{MONO}font-size:11px;color:{C_MODEL};">'
                'PAPER TRADING · UNPROVEN · $0 REAL STAKES — tracked in units only (1u = $25 reference). '
                'Calibration requires 60-90 days of data.</span>',
                unsafe_allow_html=True,
            )

        summary = unit_summary(conn, algo)

        # ── Section 2: Headline card ──────────────────────────────────────────
        st.markdown('<br>', unsafe_allow_html=True)
        col1, col2, col3 = st.columns(3)

        with col1:
            u = summary['total_units']
            sign = '+' if u >= 0 else ''
            color = C_ACCENT if u >= 0 else C_RED_MUTED
            st.markdown(
                f'<div style="{MONO}font-size:30px;font-weight:700;color:{color};line-height:1.1;">'
                f'{sign}{u:.1f}u</div>'
                f'<div style="{MONO}font-size:10px;color:#8B92A8;letter-spacing:.06em;margin-top:4px;">'
                f'UNITS P/L &nbsp;·&nbsp; {summary["graded"]} graded</div>',
                unsafe_allow_html=True,
            )

        with col2:
            w, l = summary['wins'], summary['losses']
            wr = w / (w + l) * 100 if (w + l) > 0 else 0.0
            color = C_ACCENT if wr >= 52 else (C_YELLOW if wr >= 48 else C_RED_MUTED)
            st.markdown(
                f'<div style="{MONO}font-size:30px;font-weight:700;color:{color};line-height:1.1;">'
                f'{wr:.1f}%</div>'
                f'<div style="{MONO}font-size:10px;color:#8B92A8;letter-spacing:.06em;margin-top:4px;">'
                f'WIN RATE &nbsp;·&nbsp; {w}W {l}L {summary["pushes"]}P</div>',
                unsafe_allow_html=True,
            )

        with col3:
            clv = summary['avg_clv']
            if clv is not None and summary['graded'] >= 10:
                sign = '+' if clv >= 0 else ''
                color = C_ACCENT if clv >= 0 else C_RED_MUTED
                interp = 'beats close' if clv >= 0 else 'below close'
                st.markdown(
                    f'<div style="{MONO}font-size:30px;font-weight:700;color:{color};line-height:1.1;">'
                    f'{sign}{clv:.1f}%</div>'
                    f'<div style="{MONO}font-size:10px;color:#8B92A8;letter-spacing:.06em;margin-top:4px;">'
                    f'AVG CLV &nbsp;·&nbsp; {interp}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div style="{MONO}font-size:30px;font-weight:700;color:#8B92A8;line-height:1.1;">--</div>'
                    f'<div style="{MONO}font-size:10px;color:#8B92A8;letter-spacing:.06em;margin-top:4px;">'
                    f'AVG CLV &nbsp;·&nbsp; calibrating</div>',
                    unsafe_allow_html=True,
                )

        # ROI subline
        if summary['roi_pct'] is not None and summary['total_staked'] > 0:
            sign = '+' if summary['roi_pct'] >= 0 else ''
            color = C_ACCENT if summary['roi_pct'] >= 0 else C_RED_MUTED
            st.markdown(
                f'<p style="{MONO}font-size:12px;color:{color};margin:8px 0 0 2px;">'
                f'ROI {sign}{summary["roi_pct"]:.1f}%'
                f'&nbsp;·&nbsp;{summary["total_staked"]:.0f}u staked total</p>',
                unsafe_allow_html=True,
            )
        st.markdown('<br>', unsafe_allow_html=True)

        # ── Section 3: Breakdown tabs ─────────────────────────────────────────
        section_head('B — BREAKDOWN')

        def _breakdown_table(rows, label_col):
            if not rows:
                empty_state(_NO_DATA, _NO_DATA_SUB)
                return
            table_rows = []
            for r in rows:
                picks = r['picks']
                w, l, p = r['wins'], r['losses'], r['pushes']
                wr = w / (w + l) * 100 if (w + l) > 0 else None
                staked = r['units_staked']
                roi = r['unit_profit'] / staked * 100 if staked > 0 else None
                table_rows.append({
                    label_col:  str(r['group_key'] or 'UNKNOWN').upper(),
                    'PICKS':    picks,
                    'W/L/P':    f'{w}/{l}/{p}',
                    'WIN%':     f'{wr:.1f}%' if wr is not None else '--',
                    'UNITS':    f'{r["unit_profit"]:+.2f}u',
                    'AVG CLV':  fmt_pct(r['avg_clv']) if r['avg_clv'] is not None else '--',
                    'ROI':      f'{roi:+.1f}%' if roi is not None else '--',
                })
            st.dataframe(pd.DataFrame(table_rows), hide_index=True, use_container_width=True)

        # Define third tab content
        if algo == 'model_v1':
            tab3_label = 'BY MODEL PROBABILITY'
            conf_col = (
                "CASE WHEN model_probability >= 0.75 THEN '75%+'"
                " WHEN model_probability >= 0.70 THEN '70-75%'"
                " WHEN model_probability >= 0.65 THEN '65-70%'"
                " ELSE '<65%' END"
            )
        else:
            tab3_label = 'BY CONSENSUS DEPTH'
            conf_col = (
                "CASE WHEN num_books_in_consensus <= 4 THEN '3-4 books'"
                " WHEN num_books_in_consensus <= 6 THEN '5-6 books'"
                " ELSE '7+ books' END"
            )

        tab1, tab2, tab3 = st.tabs(['BY COLOR', 'BY MARKET', tab3_label])

        with tab1:
            _breakdown_table(unit_breakdown(conn, algo, 'confidence_color'), 'COLOR')
        with tab2:
            _breakdown_table(unit_breakdown(conn, algo, 'market'), 'MARKET')
        with tab3:
            _breakdown_table(unit_breakdown(conn, algo, conf_col),
                             'MODEL PROB' if algo == 'model_v1' else 'CONSENSUS DEPTH')
            if algo == 'devig':
                st.markdown(
                    f'<span style="{MONO}font-size:10px;color:#8B92A8;">'
                    'Consensus depth = number of books contributing to the vig-free fair line.</span>',
                    unsafe_allow_html=True,
                )

        st.markdown('<br>', unsafe_allow_html=True)

        # ── Section 4: Cumulative units chart ─────────────────────────────────
        section_head('C — CUMULATIVE UNITS P/L')

        trend = unit_trend_by_day(conn, algo)
        if not trend:
            empty_state(_NO_DATA, 'Chart populates once picks are graded.')
        else:
            df = pd.DataFrame(trend)
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df['date'], y=df['cum_units'],
                name='Actual (weighted stakes)',
                line=dict(color=bar_color, width=2),
                fill='tozeroy',
                fillcolor='rgba(0,212,170,0.08)' if algo == 'devig' else 'rgba(139,92,246,0.08)',
            ))
            fig.add_trace(go.Scatter(
                x=df['date'], y=df['cum_flat'],
                name='Flat 1u baseline',
                line=dict(color='#8B92A8', width=1, dash='dash'),
            ))
            fig.add_hline(y=0, line_width=1, line_color='#334155', line_dash='dot')
            fig.update_layout(
                yaxis_title='Cumulative units',
                legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='left', x=0),
            )
            plotly_dark(fig, height=260)
            st.plotly_chart(fig, use_container_width=True)
            st.markdown(
                f'<span style="{MONO}font-size:10px;color:#8B92A8;">'
                'Flat baseline = 1u on every pick regardless of color. '
                'Actual = 2u green / 1u yellow / 0.5u red+shadow.</span>',
                unsafe_allow_html=True,
            )

        # ── Section 5: Calibration (Algo 2 only) ─────────────────────────────
        if algo == 'model_v1':
            st.markdown('<br>', unsafe_allow_html=True)
            section_head('D — CALIBRATION CHECK')
            st.markdown(
                f'<span style="{MONO}font-size:11px;color:{C_MODEL};">'
                'Pythagorean model_probability vs actual win rate by probability bucket. '
                'Need ~50+ picks per bucket for a reliable read.</span>',
                unsafe_allow_html=True,
            )
            st.markdown('<br>', unsafe_allow_html=True)

            calib = calibration_by_prob_bucket(conn)
            if not calib:
                empty_state(_NO_DATA, _NO_DATA_SUB)
            else:
                rows = []
                for r in calib:
                    pred = r['avg_pred_prob']
                    actual = r['actual_win_rate']
                    gap = (actual - pred) * 100 if pred and actual else None
                    rows.append({
                        'BUCKET':       r['bucket'],
                        'PRED WIN%':    f'{pred*100:.1f}%' if pred else '--',
                        'ACTUAL WIN%':  f'{actual*100:.1f}%' if actual else '--',
                        'GAP':          fmt_pct(gap) if gap is not None else '--',
                        'SAMPLE':       r['n'],
                    })
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                st.markdown(
                    f'<span style="{MONO}font-size:10px;color:#8B92A8;">'
                    'GAP = actual − predicted. Negative = model over-confident. '
                    'Positive = model under-confident.</span>',
                    unsafe_allow_html=True,
                )

            # ── Section E: YRFI/NRFI calibration ─────────────────────────────
            st.markdown('<br>', unsafe_allow_html=True)
            section_head('E — YRFI/NRFI CALIBRATION')
            st.markdown(
                f'<span style="{MONO}font-size:11px;color:{C_MODEL};">'
                'First-inning run model calibration — YRFI and NRFI picks only. '
                'Buckets populate once graded data accumulates.</span>',
                unsafe_allow_html=True,
            )
            st.markdown('<br>', unsafe_allow_html=True)

            yrfi_calib = yrfi_calibration(conn)
            if not yrfi_calib:
                empty_state(_NO_DATA, 'Populates once YRFI/NRFI picks are graded.')
            else:
                rows = []
                for r in yrfi_calib:
                    pred   = r['avg_pred_prob']
                    actual = r['actual_win_rate']
                    gap    = (actual - pred) * 100 if pred and actual else None
                    rows.append({
                        'BUCKET':      r['bucket'],
                        'MARKET':      (r['market'] or '').upper(),
                        'PRED WIN%':   f'{pred*100:.1f}%' if pred else '--',
                        'ACTUAL WIN%': f'{actual*100:.1f}%' if actual else '--',
                        'GAP':         fmt_pct(gap) if gap is not None else '--',
                        'SAMPLE':      r['n'],
                    })
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        # ── Section F: Pick ledger ────────────────────────────────────────────
        st.markdown('<br>', unsafe_allow_html=True)
        section_head('F — PICK LEDGER')

        # Filter controls
        _today = date.today()
        _default_start = _today - timedelta(days=30)

        fc1, fc2, fc3, fc4, fc5, fc6 = st.columns([2, 2, 2, 2, 2, 1])
        with fc1:
            ledger_start = st.date_input('From', value=_default_start, key='ledger_from',
                                         label_visibility='collapsed')
        with fc2:
            ledger_end = st.date_input('To', value=_today, key='ledger_to',
                                       label_visibility='collapsed')
        with fc3:
            color_opts = ['green', 'yellow', 'red']
            sel_colors = st.multiselect('Color', color_opts, default=color_opts,
                                        key='ledger_colors', label_visibility='collapsed',
                                        placeholder='Color')
        with fc4:
            mkt_opts = ['moneyline', 'total', 'spread', 'f5_moneyline', 'f5_total', 'yrfi', 'nrfi']
            sel_mkts = st.multiselect('Market', mkt_opts, default=mkt_opts,
                                      key='ledger_mkts', label_visibility='collapsed',
                                      placeholder='Market')
        with fc5:
            res_opts = ['win', 'loss', 'push', 'void', 'pending']
            sel_res  = st.multiselect('Result', res_opts, default=res_opts,
                                      key='ledger_res', label_visibility='collapsed',
                                      placeholder='Result')
        with fc6:
            if st.button('Reset', key='ledger_reset'):
                for k in ('ledger_from', 'ledger_to', 'ledger_colors', 'ledger_mkts', 'ledger_res'):
                    if k in st.session_state:
                        del st.session_state[k]
                st.rerun()

        # Pull base data
        raw = pick_ledger(conn, algo,
                          start_date=ledger_start.isoformat(),
                          end_date=ledger_end.isoformat())

        # ── Market display label ──────────────────────────────────────────────
        _MKT_LABEL = {
            'moneyline': 'ML', 'total': 'Total', 'spread': 'Spread',
            'f5_moneyline': 'F5 ML', 'f5_total': 'F5 Total',
            'yrfi': 'YRFI', 'nrfi': 'NRFI',
        }

        def _pick_str(mkt, side, line):
            if mkt == 'moneyline':
                return side.upper() if side else '—'
            if mkt in ('yrfi', 'nrfi'):
                return mkt.upper()
            if mkt in ('total', 'f5_total'):
                line_s = f' {line}' if line is not None else ''
                return f'{side.upper()}{line_s}'
            if mkt in ('spread', 'f5_moneyline'):
                line_s = f' {line:+g}' if line is not None else ''
                return f'{side.upper()}{line_s}'
            return side or '—'

        def _fmt_am(price):
            if price is None: return '—'
            return f'+{price}' if price >= 0 else str(price)

        def _fmt_result_key(result):
            return result if result else 'pending'

        # Build display rows
        display_rows = []
        for r in raw:
            res_key = _fmt_result_key(r.get('result'))
            display_rows.append({
                '_color':       r['confidence_color'] or 'red',
                '_market':      r['market'] or '',
                '_result':      res_key,
                '_unit_profit': r['unit_profit'],
                'Date':     r['game_date'][5:].replace('-', '/').lstrip('0') if r['game_date'] else '—',
                'Game':     f"{r['away_team']} @ {r['home_team']}",
                'Market':   _MKT_LABEL.get(r['market'], r['market'] or '—'),
                'Pick':     _pick_str(r['market'], r['side'], r['line']),
                'Odds':     _fmt_am(r['target_price_american']),
                'Fair':     _fmt_am(r['fair_price_american']),
                'Edge%':    f"{r['edge_percent']:.1f}%" if (r['edge_percent'] and r['edge_percent'] != 0) else '—',
                'Color':    (r['confidence_color'] or 'red').upper(),
                'CLV%':     f"{r['clv_percent']:+.1f}%" if r['clv_percent'] is not None else '—',
                'Result':   res_key.upper(),
                'Units':    f"{r['unit_profit']:+.2f}u" if r['unit_profit'] is not None else '—',
            })

        df_all = pd.DataFrame(display_rows) if display_rows else pd.DataFrame(
            columns=['_color', '_market', '_result', '_unit_profit',
                     'Date', 'Game', 'Market', 'Pick', 'Odds', 'Fair',
                     'Edge%', 'Color', 'CLV%', 'Result', 'Units'])

        # Apply low-cardinality filters in pandas
        if not df_all.empty:
            if sel_colors:
                df_all = df_all[df_all['_color'].isin(sel_colors)]
            if sel_mkts:
                df_all = df_all[df_all['_market'].isin(sel_mkts)]
            if sel_res:
                df_all = df_all[df_all['_result'].isin(sel_res)]

        display_cols = ['Date', 'Game', 'Market', 'Pick', 'Odds', 'Fair',
                        'Edge%', 'Color', 'CLV%', 'Result', 'Units']

        if df_all.empty:
            empty_state(
                'No picks for this filter',
                'Adjust the date range or filters above.',
            )
        else:
            st.dataframe(
                df_all[display_cols],
                hide_index=True,
                use_container_width=True,
                column_config={
                    'Date':   st.column_config.TextColumn('Date',   width='small'),
                    'Game':   st.column_config.TextColumn('Game',   width='medium'),
                    'Market': st.column_config.TextColumn('Market', width='small'),
                    'Pick':   st.column_config.TextColumn('Pick',   width='small'),
                    'Odds':   st.column_config.TextColumn('Odds',   width='small'),
                    'Fair':   st.column_config.TextColumn('Fair',   width='small'),
                    'Edge%':  st.column_config.TextColumn('Edge%',  width='small'),
                    'Color':  st.column_config.TextColumn('Color',  width='small'),
                    'CLV%':   st.column_config.TextColumn('CLV%',   width='small'),
                    'Result': st.column_config.TextColumn('Result', width='small'),
                    'Units':  st.column_config.TextColumn('Units',  width='small'),
                },
            )

            # Summary footer — updates with filters
            shown     = len(df_all)
            total_raw = len(display_rows)
            graded    = df_all[df_all['_result'].isin(['win', 'loss', 'push'])]
            wins      = int((graded['_result'] == 'win').sum())
            losses    = int((graded['_result'] == 'loss').sum())
            wr_str    = f'{wins/(wins+losses)*100:.1f}%' if (wins + losses) > 0 else '—'
            unit_vals = df_all['_unit_profit'].dropna()
            unit_sum  = unit_vals.sum()
            unit_str  = f'{unit_sum:+.2f}u' if len(unit_vals) > 0 else '—'

            st.markdown(
                f'<span style="{MONO}font-size:10px;color:#8B92A8;">'
                f'Showing {shown} of {total_raw} picks'
                f'&nbsp;·&nbsp;Units: {unit_str}'
                f'&nbsp;·&nbsp;Win rate: {wr_str}'
                f'&nbsp;({wins}W {losses}L)</span>',
                unsafe_allow_html=True,
            )
