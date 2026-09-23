"""
2_Performance.py — season standings across all bots + Admin.
Per-bot deep dives now live on their own dedicated pages (see
pages/3_Coach_Bo.py etc.); this page keeps the season-wide comparison plus
a quick-switch view for anyone who wants a bot's numbers without leaving.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st
import pandas as pd

from database import get_connection, init_db
from components.auth import require_login
from components.metrics import season_standings, BOT_DISPLAY_NAMES, ADMIN_KEY, ADMIN_DISPLAY_NAME
from components.formatters import fmt_pct
from components.bot_profile import render_profile
from components.styles import inject_custom_css, section_head, page_header, BOT_COLORS, ADMIN_COLOR
from calibration import compute_report as compute_calibration_report

SPORT = 'nfl'

st.set_page_config(page_title='Performance — 3 Bettors', page_icon='📊', layout='wide',
                   initial_sidebar_state='expanded')
inject_custom_css()
require_login()   # password gate — nothing below renders until authenticated
init_db()

page_header('PERFORMANCE', 'Season standings & per-bot analytics')

view = st.radio(
    'VIEW',
    options=(['SEASON STANDINGS', 'ACCURACY'] + [n.upper() for n in BOT_DISPLAY_NAMES.values()]
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

    # ── Accuracy / calibration branch ─────────────────────────────────────────
    elif view == 'ACCURACY':
        report = compute_calibration_report(conn, SPORT)
        section_head(f"CALIBRATION CHECK — WEEK {report['week']}")
        st.caption(
            'Does a bigger unit size actually win more often? And did any bot fail to '
            'decide on a game that already kicked off this week?'
        )

        any_silent = False
        for bot_key, b in report['bots'].items():
            accent = BOT_COLORS.get(bot_key, '#8B92A8')
            st.markdown(
                f'<div class="bot-section-header"><div class="bot-section-label" '
                f'style="color:{accent};border-color:{accent}33;">{b["display_name"].upper()}</div></div>',
                unsafe_allow_html=True,
            )
            if b['total_graded'] == 0:
                st.markdown(
                    '<span style="font-size:12px;font-family:\'JetBrains Mono\',monospace;'
                    'color:#8B92A8;">No graded picks yet this season.</span>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"**{b['total_graded']} graded** &middot; {b['win_rate']:.1%} win rate &middot; "
                    f"{b['total_units']:+.2f}u"
                )
                tier_rows = [
                    {'UNITS': f"{t['units']:.0f}u", 'N': t['n'],
                     'WIN%': f"{t['win_rate']:.0%}", 'PROFIT': f"{t['profit']:+.2f}u"}
                    for t in b['by_tier']
                ]
                st.dataframe(pd.DataFrame(tier_rows), hide_index=True, use_container_width=True)
                if b['inverted']:
                    st.warning('⚠️ Tier inversion — a bigger unit size is winning less often than a smaller one.')

            if b['silent_games']:
                any_silent = True
                games_str = ', '.join(f"{g['away_team']}@{g['home_team']}" for g in b['silent_games'])
                st.error(f"🚨 SILENT on {len(b['silent_games'])} already-started game(s): {games_str}")

        if not any_silent:
            st.success('✅ No silent bots this week.')

    # ── Per-bot / Admin branch ────────────────────────────────────────────────
    else:
        is_admin_view = view == ADMIN_DISPLAY_NAME.upper()
        if is_admin_view:
            bot_key, display_name, accent = ADMIN_KEY, ADMIN_DISPLAY_NAME, ADMIN_COLOR
        else:
            bot_key = next(k for k, v in BOT_DISPLAY_NAMES.items() if v.upper() == view)
            display_name = BOT_DISPLAY_NAMES[bot_key]
            accent = BOT_COLORS.get(bot_key, '#8B92A8')

        st.markdown('<br>', unsafe_allow_html=True)
        render_profile(conn, SPORT, bot_key, display_name, accent, key_prefix='perf')
