"""
bot_profile.py — shared analytics block for one bot or Admin: headline
tiles, breakdown tabs, cumulative units chart, pick ledger.

Used by pages/2_Performance.py (per-bot/admin branch of its VIEW selector)
and each dedicated pages/{N}_{BotName}.py bio page, so this layout is
defined once. key_prefix keeps widget keys unique across pages that both
render a profile in the same browser session (Streamlit session_state is
shared app-wide, not per-page).
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from datetime import date, timedelta

from components.metrics import (
    bot_summary, bot_breakdown, unit_trend_by_week, pick_ledger,
    admin_summary, admin_breakdown, admin_unit_trend_by_week, admin_pick_ledger,
    ADMIN_KEY,
)
from components.formatters import fmt_pct
from components.styles import empty_state, section_head, plotly_dark

MONO = "font-family:'JetBrains Mono',monospace;"
_NO_DATA = 'No graded data yet'
_NO_DATA_SUB = 'Populates once picks are graded.'


def render_bio(bio: dict, display_name: str, accent: str) -> None:
    """Render a bot's bio card: tagline, what it leans on, pick threshold, sizing table."""
    sizing_rows = ''.join(
        f'<tr><td style="{MONO}color:{accent};font-weight:700;padding:4px 14px 4px 0;'
        f'white-space:nowrap;">{units}</td>'
        f'<td style="font-size:13px;color:#C7CCDA;padding:4px 0;">{desc}</td></tr>'
        for units, desc in bio['sizing']
    )
    st.markdown(f"""
<div class="empty-state" style="align-items:flex-start;max-height:none;">
  <div class="empty-state-glyph" style="color:{accent};">◆</div>
  <div class="empty-state-body">
    <div class="empty-state-lead" style="font-size:18px;">{display_name}</div>
    <div class="empty-state-sub" style="margin-bottom:10px;">{bio['tagline']}</div>
    <div style="font-size:13px;color:#C7CCDA;margin-bottom:8px;"><strong>Leans on:</strong> {bio['leans_on']}</div>
    <div style="font-size:13px;color:#C7CCDA;margin-bottom:12px;"><strong>Pick threshold:</strong> {bio['threshold']}</div>
    <table style="border-collapse:collapse;">{sizing_rows}</table>
  </div>
</div>
""", unsafe_allow_html=True)


def render_profile(conn, sport: str, bot_key: str, display_name: str, accent: str,
                   key_prefix: str = '') -> None:
    """Render headline tiles, breakdown tabs, cumulative chart, and pick
    ledger for one bot (bot_key = a real registered bot key) or Admin
    (bot_key = ADMIN_KEY)."""
    is_admin_view = bot_key == ADMIN_KEY
    summary = admin_summary(conn, sport) if is_admin_view else bot_summary(conn, sport, bot_key)

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
            _breakdown_table(admin_breakdown(conn, sport, 'market'), 'MARKET')
        with tab2:
            _breakdown_table(admin_breakdown(conn, sport, 'book'), 'BOOK')
    else:
        tab1, tab2 = st.tabs(['BY MARKET', 'BY CONFIDENCE'])
        with tab1:
            _breakdown_table(bot_breakdown(conn, sport, bot_key, 'market'), 'MARKET')
        with tab2:
            _breakdown_table(bot_breakdown(conn, sport, bot_key, 'confidence'), 'CONFIDENCE')

    st.markdown('<br>', unsafe_allow_html=True)

    # ── Cumulative units chart ─────────────────────────────────────────────
    section_head('CUMULATIVE UNITS P/L BY WEEK')
    trend = admin_unit_trend_by_week(conn, sport) if is_admin_view else unit_trend_by_week(conn, sport, bot_key)
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
        ledger_start = st.date_input('From', value=_default_start, key=f'{key_prefix}_ledger_from',
                                     label_visibility='collapsed')
    with fc2:
        ledger_end = st.date_input('To', value=_today, key=f'{key_prefix}_ledger_to',
                                   label_visibility='collapsed')
    show_fades = False
    if not is_admin_view:
        with fc3:
            show_fades = st.checkbox('Include fades', value=False, key=f'{key_prefix}_ledger_fades')

    if is_admin_view:
        raw = admin_pick_ledger(conn, sport,
                                start_date=ledger_start.isoformat(),
                                end_date=ledger_end.isoformat())
    else:
        raw = pick_ledger(conn, sport, bot_key,
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
