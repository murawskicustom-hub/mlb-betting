"""
1_This_Week.py — this week's picks and fades, one section per bot.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st
import pandas as pd

from database import get_connection, init_db
from components.auth import require_login
from components.metrics import week_recs, games_for_week, current_season_week, BOT_DISPLAY_NAMES
from components.formatters import fmt_game_time
from components.styles import (
    inject_custom_css, pick_card, fade_row, empty_state, section_head,
    page_header, BOT_COLORS, week_summary_bar,
)

SPORT = 'nfl'

st.set_page_config(page_title='This Week — 3 Bettors', page_icon='🏈', layout='wide',
                   initial_sidebar_state='expanded')
inject_custom_css()
require_login()   # password gate — nothing below renders until authenticated
init_db()

with get_connection() as conn:
    default_season, default_week = current_season_week(conn, SPORT)

# ── Week nav ─────────────────────────────────────────────────────────────────
col_season, col_week, col_spacer = st.columns([1, 1, 4])
with col_season:
    season = st.number_input('Season', value=default_season, step=1, format='%d')
with col_week:
    week = st.number_input('Week', value=default_week, min_value=1, max_value=22, step=1, format='%d')

page_header('THIS WEEK', f'NFL {int(season)} — Week {int(week)}')

with get_connection() as conn:
    recs  = week_recs(conn, SPORT, int(season), int(week))
    games = games_for_week(conn, SPORT, int(season), int(week))

if not games:
    empty_state(
        'No games loaded for this week',
        'Run tuesday_research (or pull_schedule_nfl.py) to pull the schedule first.',
    )
else:
    game_lookup = {g['game_id']: g for g in games}

    total_picks = sum(1 for r in recs if not r['is_fade'])
    total_fades = sum(1 for r in recs if r['is_fade'])
    week_summary_bar(total_picks, total_fades, len(games))
    st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)

    # One tab per bot rather than stacking all three sections — a full week
    # (16 games x 3 bots x up to 3 markets each) can mean 100+ pick cards, and
    # tabs keep only one bot's list on screen at a time instead of a single
    # long scroll. Within each tab, picks sort by units descending so the
    # bot's highest-conviction calls surface first.
    tab_labels = [
        f"{BOT_DISPLAY_NAMES[bot_key]} ({len([r for r in recs if r['bot_key'] == bot_key and not r['is_fade']])})"
        for bot_key in BOT_DISPLAY_NAMES
    ]
    tabs = st.tabs(tab_labels)

    for tab, (bot_key, display_name) in zip(tabs, BOT_DISPLAY_NAMES.items()):
        with tab:
            bot_recs = [r for r in recs if r['bot_key'] == bot_key]
            picks    = sorted((r for r in bot_recs if not r['is_fade']),
                              key=lambda r: r['units'] or 0, reverse=True)
            fades    = [r for r in bot_recs if r['is_fade']]
            accent   = BOT_COLORS.get(bot_key, '#8B92A8')

            if picks:
                for rec in picks:
                    st.markdown(pick_card(rec, display_name, accent), unsafe_allow_html=True)
            else:
                st.markdown(
                    '<span style="font-size:12px;font-family:\'JetBrains Mono\',monospace;'
                    'color:#8B92A8;">No picks yet this week.</span>',
                    unsafe_allow_html=True,
                )

            if fades:
                with st.expander(f'{len(fades)} FADE{"S" if len(fades) != 1 else ""} — GAMES SAT OUT'):
                    for f in fades:
                        game = game_lookup.get(f['game_id'])
                        if game:
                            st.markdown(fade_row(game, display_name), unsafe_allow_html=True)

    # ── Full slate reference table ────────────────────────────────────────────
    st.markdown('<br>', unsafe_allow_html=True)
    section_head(f'FULL SLATE — {len(games)} GAME{"S" if len(games) != 1 else ""}')
    rows = []
    for g in games:
        rows.append({
            'GAME':   f"{g['away_team']} @ {g['home_team']}",
            'KICKOFF': fmt_game_time(g['start_utc']),
            'VENUE':  g.get('venue') or '—',
            'STATUS': g.get('status', '').replace('STATUS_', ''),
        })
    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
