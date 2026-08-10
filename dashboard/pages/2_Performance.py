"""
2_Performance.py — season standings + per-bot performance analytics.
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
    season_standings, bot_summary, bot_breakdown, unit_trend_by_week, pick_ledger,
    admin_summary, admin_breakdown, admin_unit_trend_by_week, admin_pick_ledger,
    BOT_DISPLAY_NAMES, ADMIN_KEY, ADMIN_DISPLAY_NAME,
)
from components.formatters import fmt_pct
from components.styles import (
    inject_custom_css, empty_state, section_head, plotly_dark, page_header, BOT_COLORS, ADMIN_COLOR,
)

SPORT = 'nfl'

st.set_page_config(page_title='Performance — 3 Bettors', page_icon='📊', layout='wide',
                   initial_sidebar_state='expanded')
inject_custom_css()
require_login()   # password gate — nothing below renders until authenticated
init_db()

page_header('PERFORMANCE', 'Season standings & per-bot analytics')

MONO = "font-family:'JetBrains Mono',monospace;"
_NO_DATA = 'No graded data yet'
_NO_DATA_SUB = 'Populates once picks are graded.'

view = st.radio(
    'VIEW',
    options=(['SEASON STANDINGS'] + [n.upper() for n in BOT_DISPLAY_NAMES.values()]
             + [ADMIN_DISPLAY_NAME.upper()]),
    horizontal=True,
    label_visibility='visible',
)

with get_connection() as conn:

    # ── Season standings branch ───────────────────────────────────────────────
    if view == 'SEASON STANDINGS':
        section_head('MOST UNITS WON, SEASON-TO-DATE')
        standings = season_standings(conn, SPORT)
        rows = []
        for s in standings:
            w, l = s['wins'], s['losses']
            wr = f'{w/(w+l)*100:.1f}%' if (w + l) > 0 else '—'
            rows.append({
                'BOT':      s['display_name'],
                'UNITS':    f"{s['total_units']:+.2f}u",
                'GRADED':   s['graded'],
                'W/L/P':    f"{w}/{l}/{s['pushes']}",
                'WIN%':     wr,
                'AVG CLV':  fmt_pct(s['avg_clv']) if s['avg_clv'] is not None else '—',
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # ── Per-bot / Admin branch ────────────────────────────────────────────────
    else:
        is_admin_view = view == ADMIN_DISPLAY_NAME.upper()

        if is_admin_view:
            bot_key = ADMIN_KEY
            display_name = ADMIN_DISPLAY_NAME
            accent = ADMIN_COLOR
            summary = admin_summary(conn, SPORT)
        else:
            bot_key = next(k for k, v in BOT_DISPLAY_NAMES.items() if v.upper() == view)
            display_name = BOT_DISPLAY_NAMES[bot_key]
            accent = BOT_COLORS.get(bot_key, '#8B92A8')
            summary = bot_summary(conn, SPORT, bot_key)

        st.markdown('<br>', unsafe_allow_html=True)
        col1, col2, col3 = st.columns(3)

        with col1:
            u = summary['total_units']
            sign = '+' if u >= 0 else ''
            color = accent if u >= 0 else '#F6465D'
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
            color = accent if wr >= 52 else ('#FFB454' if wr >= 48 else '#6E3B47')
            st.markdown(
                f'<div style="{MONO}font-size:30px;font-weight:700;color:{color};line-height:1.1;">'
                f'{wr:.1f}%</div>'
                f'<div style="{MONO}font-size:10px;color:#8B92A8;letter-spacing:.06em;margin-top:4px;">'
                f'WIN RATE &nbsp;·&nbsp; {w}W {l}L {summary["pushes"]}P</div>',
                unsafe_allow_html=True,
            )
        with col3:
            clv = summary['avg_clv']
            if clv is not None and summary['graded'] >= 5:
                sign = '+' if clv >= 0 else ''
                color = accent if clv >= 0 else '#6E3B47'
                st.markdown(
                    f'<div style="{MONO}font-size:30px;font-weight:700;color:{color};line-height:1.1;">'
                    f'{sign}{clv:.1f}%</div>'
                    f'<div style="{MONO}font-size:10px;color:#8B92A8;letter-spacing:.06em;margin-top:4px;">'
                    f'AVG CLV</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div style="{MONO}font-size:30px;font-weight:700;color:#8B92A8;line-height:1.1;">--</div>'
                    f'<div style="{MONO}font-size:10px;color:#8B92A8;letter-spacing:.06em;margin-top:4px;">'
                    f'AVG CLV &nbsp;·&nbsp; calibrating</div>',
                    unsafe_allow_html=True,
                )

        if summary['roi_pct'] is not None and summary['total_staked'] > 0:
            sign = '+' if summary['roi_pct'] >= 0 else ''
            st.markdown(
                f'<p style="{MONO}font-size:12px;color:{accent};margin:8px 0 0 2px;">'
                f'ROI {sign}{summary["roi_pct"]:.1f}%'
                f'&nbsp;·&nbsp;{summary["total_staked"]:.0f}u staked total</p>',
                unsafe_allow_html=True,
            )
        st.markdown('<br>', unsafe_allow_html=True)

        # ── Breakdown ──────────────────────────────────────────────────────────
        section_head('BREAKDOWN')

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
                    label_col: str(r['group_key'] or 'UNKNOWN').upper(),
                    'PICKS':   picks,
                    'W/L/P':   f'{w}/{l}/{p}',
                    'WIN%':    f'{wr:.1f}%' if wr is not None else '--',
                    'UNITS':   f"{r['unit_profit']:+.2f}u",
                    'AVG CLV': fmt_pct(r['avg_clv']) if r['avg_clv'] is not None else '--',
                    'ROI':     f'{roi:+.1f}%' if roi is not None else '--',
                })
            st.dataframe(pd.DataFrame(table_rows), hide_index=True, use_container_width=True)

        if is_admin_view:
            tab1, tab2 = st.tabs(['BY MARKET', 'BY BOOK'])
            with tab1:
                _breakdown_table(admin_breakdown(conn, SPORT, 'market'), 'MARKET')
            with tab2:
                _breakdown_table(admin_breakdown(conn, SPORT, 'book'), 'BOOK')
        else:
            tab1, tab2 = st.tabs(['BY MARKET', 'BY CONFIDENCE'])
            with tab1:
                _breakdown_table(bot_breakdown(conn, SPORT, bot_key, 'market'), 'MARKET')
            with tab2:
                _breakdown_table(bot_breakdown(conn, SPORT, bot_key, 'confidence'), 'CONFIDENCE')

        st.markdown('<br>', unsafe_allow_html=True)

        # ── Cumulative units chart ─────────────────────────────────────────────
        section_head('CUMULATIVE UNITS P/L BY WEEK')
        trend = admin_unit_trend_by_week(conn, SPORT) if is_admin_view else unit_trend_by_week(conn, SPORT, bot_key)
        if not trend:
            empty_state(_NO_DATA, 'Chart populates once picks are graded.')
        else:
            df = pd.DataFrame(trend)
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df['label'], y=df['cum_units'],
                name='Cumulative units',
                line=dict(color=accent, width=2),
                fill='tozeroy',
            ))
            fig.add_hline(y=0, line_width=1, line_color='#334155', line_dash='dot')
            fig.update_layout(yaxis_title='Cumulative units')
            plotly_dark(fig, height=260)
            st.plotly_chart(fig, use_container_width=True)

        st.markdown('<br>', unsafe_allow_html=True)

        # ── Pick ledger ────────────────────────────────────────────────────────
        section_head('PICK LEDGER')

        _today = date.today()
        _default_start = _today - timedelta(days=90)

        fc1, fc2, fc3 = st.columns([2, 2, 2])
        with fc1:
            ledger_start = st.date_input('From', value=_default_start, key='ledger_from',
                                         label_visibility='collapsed')
        with fc2:
            ledger_end = st.date_input('To', value=_today, key='ledger_to',
                                       label_visibility='collapsed')
        show_fades = False
        if not is_admin_view:
            with fc3:
                show_fades = st.checkbox('Include fades', value=False, key='ledger_fades')

        if is_admin_view:
            raw = admin_pick_ledger(conn, SPORT,
                                    start_date=ledger_start.isoformat(),
                                    end_date=ledger_end.isoformat())
        else:
            raw = pick_ledger(conn, SPORT, bot_key,
                              start_date=ledger_start.isoformat(),
                              end_date=ledger_end.isoformat())
            if not show_fades:
                raw = [r for r in raw if not r['is_fade']]

        def _fmt_am(price):
            if price is None: return '—'
            return f'+{price}' if price >= 0 else str(price)

        display_rows = []
        for r in raw:
            display_rows.append({
                'Date':   r['game_date'][5:].replace('-', '/').lstrip('0') if r['game_date'] else '—',
                'Game':   f"{r['away_team']} @ {r['home_team']}",
                'Market': (r['market'] or 'FADE').upper() if not r['is_fade'] else 'FADE',
                'Side':   (r['side'] or '—').upper() if r['side'] else '—',
                'Odds':   _fmt_am(r['target_price_american']),
                'Conf':   r['confidence'] or '—',
                'Units':  f"{r['units']:.1f}u" if r['units'] else '—',
                'CLV%':   f"{r['clv_percent']:+.1f}%" if r['clv_percent'] is not None else '—',
                'Result': (r['result'] or 'pending').upper(),
                'P/L':    f"{r['unit_profit']:+.2f}u" if r['unit_profit'] is not None else '—',
            })

        if not display_rows:
            empty_state('No picks for this filter', 'Adjust the date range above.')
        else:
            st.dataframe(pd.DataFrame(display_rows), hide_index=True, use_container_width=True)
            st.markdown(
                f'<span style="{MONO}font-size:10px;color:#8B92A8;">{len(display_rows)} rows</span>',
                unsafe_allow_html=True,
            )
