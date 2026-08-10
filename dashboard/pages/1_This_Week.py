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
    page_header, BOT_COLORS,
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

    for bot_key, display_name in BOT_DISPLAY_NAMES.items():
        bot_recs = [r for r in recs if r['bot_key'] == bot_key]
        picks    = [r for r in bot_recs if not r['is_fade']]
        fades    = [r for r in bot_recs if r['is_fade']]
        accent   = BOT_COLORS.get(bot_key, '#8B92A8')

        st.markdown(f"""
<div class="bot-section-header">
  <div class="bot-section-label" style="color:{accent};border-color:{accent}33;">
    {display_name.upper()} &middot; {len(picks)} pick{'s' if len(picks) != 1 else ''}, {len(fades)} fade{'s' if len(fades) != 1 else ''}
  </div>
</div>
""", unsafe_allow_html=True)

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
