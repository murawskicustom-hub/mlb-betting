"""
styles.py — All visual styling for the MLB Betting dashboard.

Design target: premium dark-mode trading terminal.
Reference palette: TradingView dark + Bloomberg terminal density.
"""

import streamlit as st
from datetime import datetime, timedelta
import pytz

EASTERN = pytz.timezone('US/Eastern')

# ── Palette constants (also referenced in chart helpers) ──────────────────────
C_BG         = '#0B0E14'   # page background
C_SURFACE    = '#131722'   # card / sidebar surface
C_BORDER     = '#1E2430'   # hairline borders
C_ACCENT     = '#00D4AA'   # teal — primary signal, used sparingly
C_YELLOW     = '#FFB454'   # amber — yellow signal
C_RED_MUTED  = '#6E3B47'   # desaturated red — shadow/control, intentionally dim
C_TEXT       = '#E6E9EF'   # primary text
C_MUTED      = '#8B92A8'   # secondary / label text
C_GRID       = '#1A1F2E'   # chart gridlines

SLOT_TIMES = [
    ('morning',  7,  0),
    ('midday',  12,  0),
    ('pregame', 17,  0),
    ('closing', 23,  0),
]


# ── Main CSS injection ────────────────────────────────────────────────────────

def inject_custom_css():
    st.markdown(f"""
<style>
/* ── Font imports ─────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:ital,wght@0,400;0,500;0,600&display=swap');

/* ── Hide Streamlit branding — NOT the header bar itself ─ */
/* stHeader contains the sidebar toggle; hiding it removes  */
/* the only way to reopen the nav. Hide children instead.  */
footer {{ visibility: hidden; }}
#MainMenu {{ visibility: hidden; }}
.stDeployButton {{ display: none !important; }}
[data-testid="stDecoration"] {{ display: none !important; }}

/* Hide the app menu (⋮) and the top-right toolbar icons,  */
/* but NOT the sidebar chevron button.                     */
[data-testid="stToolbar"] {{ display: none !important; }}
[data-testid="stHeader"] button[kind="header"] {{ display: none !important; }}

/* Keep the header bar itself, but collapse its height so  */
/* it doesn't waste vertical space. The sidebar chevron    */
/* (data-testid="collapsedControl" / "stSidebarCollapsedControl") */
/* is rendered in the sidebar, not the header, so it's safe. */
[data-testid="stHeader"] {{
    background: transparent !important;
    border-bottom: none !important;
    height: 0 !important;
    min-height: 0 !important;
    padding: 0 !important;
    overflow: visible !important;
}}
/* The actual collapse toggle lives outside stHeader — leave it alone */

/* ── Global typography ────────────────────────────────── */
html, body, [class*="css"] {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}}

/* ── Metric tile override ─────────────────────────────── */
[data-testid="metric-container"] {{
    background: {C_SURFACE};
    border: 1px solid {C_BORDER};
    border-radius: 6px;
    padding: 16px 18px !important;
    position: relative;
    overflow: hidden;
}}
[data-testid="metric-container"]::before {{
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: {C_BORDER};
}}
[data-testid="stMetricLabel"] {{
    font-size: 10px !important;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: {C_MUTED} !important;
}}
[data-testid="stMetricValue"] {{
    font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace !important;
    font-size: 22px !important;
    font-weight: 500 !important;
    font-variant-numeric: tabular-nums;
    color: {C_TEXT} !important;
}}
[data-testid="stMetricDelta"] {{
    font-size: 11px !important;
    color: {C_MUTED} !important;
}}

/* ── Table styling ────────────────────────────────────── */
.stDataFrame {{
    font-size: 12px;
}}
.stDataFrame thead th {{
    font-size: 10px !important;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    color: {C_MUTED} !important;
    border-bottom: 1px solid {C_BORDER} !important;
    padding: 8px 10px !important;
    background: {C_SURFACE} !important;
}}
.stDataFrame tbody td {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    padding: 6px 10px !important;
    border-bottom: 1px solid {C_BORDER} !important;
    color: {C_TEXT};
}}
.stDataFrame tbody tr:hover td {{
    background: rgba(0,212,170,0.04) !important;
}}

/* ── Divider ──────────────────────────────────────────── */
hr {{
    border-color: {C_BORDER} !important;
    margin: 20px 0 !important;
}}

/* ── Expander ─────────────────────────────────────────── */
[data-testid="stExpander"] {{
    border: 1px solid {C_BORDER} !important;
    border-radius: 6px;
    background: {C_SURFACE};
}}
[data-testid="stExpander"] summary {{
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: {C_MUTED};
    padding: 10px 14px;
}}

/* ── Buttons ──────────────────────────────────────────── */
.stButton > button {{
    background: transparent;
    border: 1px solid {C_BORDER};
    color: {C_MUTED};
    font-size: 11px;
    letter-spacing: 0.06em;
    font-family: 'JetBrains Mono', monospace;
    border-radius: 4px;
    transition: all 0.15s ease;
}}
.stButton > button:hover {{
    border-color: {C_ACCENT};
    color: {C_ACCENT};
    background: rgba(0,212,170,0.06);
}}

/* ── Sidebar background & wordmark ───────────────────── */
[data-testid="stSidebar"] {{
    background: #0E1118 !important;
    border-right: 1px solid {C_BORDER};
}}
[data-testid="stSidebar"] > div:first-child {{
    padding-top: 20px !important;
}}

/* Wordmark injected via ::before on the sidebar content */
[data-testid="stSidebarContent"]::before {{
    content: 'CMJ BETS';
    display: block;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: {C_ACCENT};
    padding: 0 16px 18px 16px;
    border-bottom: 1px solid {C_BORDER};
    margin-bottom: 12px;
}}

/* ── Sidebar nav links ────────────────────────────────── */
[data-testid="stSidebarNav"] {{
    padding-top: 4px;
}}
[data-testid="stSidebarNav"] a {{
    font-size: 11px;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    color: {C_MUTED};
    font-weight: 500;
    padding: 8px 16px;
    border-radius: 4px;
    transition: color 0.12s ease, background 0.12s ease;
}}
[data-testid="stSidebarNav"] a:hover {{
    color: {C_TEXT};
    background: rgba(255,255,255,0.04);
}}
[data-testid="stSidebarNav"] a[aria-selected="true"] {{
    color: {C_ACCENT};
    font-weight: 600;
    background: rgba(0,212,170,0.07);
}}

/* ── Status bar ───────────────────────────────────────── */
.status-bar {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: {C_SURFACE};
    border: 1px solid {C_BORDER};
    border-radius: 6px;
    padding: 8px 16px;
    margin-bottom: 24px;
    font-size: 11px;
    letter-spacing: 0.05em;
}}
.status-left {{ display: flex; align-items: center; gap: 8px; }}
.status-right {{ display: flex; align-items: center; gap: 12px; color: {C_MUTED}; }}
.status-live-text {{ font-weight: 600; color: {C_ACCENT}; }}
.status-stale-text {{ font-weight: 600; color: {C_YELLOW}; }}
.status-sub {{ color: {C_MUTED}; }}
.status-divider {{ color: {C_BORDER}; margin: 0 4px; }}

@keyframes pulse-dot {{
    0%, 100% {{ opacity: 1; box-shadow: 0 0 0 2px rgba(0,212,170,0.3); }}
    50%       {{ opacity: 0.6; box-shadow: none; }}
}}
.dot-live {{
    display: inline-block; width: 7px; height: 7px;
    border-radius: 50%; background: {C_ACCENT};
    animation: pulse-dot 2s ease infinite;
}}
.dot-stale {{
    display: inline-block; width: 7px; height: 7px;
    border-radius: 50%; background: {C_YELLOW};
}}

/* ── Custom metric grid tiles ─────────────────────────── */
.metrics-grid {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 12px;
    margin-bottom: 8px;
}}
.m-tile {{
    background: {C_SURFACE};
    border: 1px solid {C_BORDER};
    border-top: 2px solid {C_BORDER};
    border-radius: 6px;
    padding: 16px 18px;
    position: relative;
}}
.m-tile.positive {{ border-top-color: {C_ACCENT}; }}
.m-tile.negative {{ border-top-color: {C_RED_MUTED}; }}
.m-tile.neutral  {{ border-top-color: {C_BORDER}; }}
.m-label {{
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: {C_MUTED};
    margin-bottom: 8px;
}}
.m-value {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 22px;
    font-weight: 500;
    color: {C_TEXT};
    font-variant-numeric: tabular-nums;
    line-height: 1.1;
}}
.m-value.accent {{ color: {C_ACCENT}; }}
.m-value.yellow {{ color: {C_YELLOW}; }}
.m-value.muted  {{ color: {C_MUTED}; }}
.m-delta {{
    font-size: 11px;
    color: {C_MUTED};
    margin-top: 5px;
    font-family: 'JetBrains Mono', monospace;
}}

/* ── Empty state terminal cards ───────────────────────── */
.empty-state {{
    background: {C_SURFACE};
    border: 1px solid {C_BORDER};
    border-radius: 6px;
    padding: 28px 32px;
    text-align: center;
    color: {C_MUTED};
}}
.empty-state-lead {{
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: {C_MUTED};
    margin-bottom: 8px;
}}
.empty-state-sub {{
    font-size: 12px;
    font-family: 'JetBrains Mono', monospace;
    color: {C_BORDER};
    margin-top: 6px;
    letter-spacing: 0.04em;
}}

/* ── Play cards (Today page) ──────────────────────────── */
.play-card {{
    display: flex;
    align-items: stretch;
    background: {C_SURFACE};
    border: 1px solid {C_BORDER};
    border-radius: 6px;
    margin-bottom: 12px;
    overflow: hidden;
    position: relative;
    transition: border-color 0.15s ease;
}}
.play-card:hover {{ border-color: #2A3248; }}
.card-stripe {{
    width: 4px;
    flex-shrink: 0;
    background: {C_BORDER};
}}
.play-card.sig-green .card-stripe {{ background: {C_ACCENT}; }}
.play-card.sig-green {{
    box-shadow: 0 0 24px rgba(0,212,170,0.07), inset 0 0 0 1px rgba(0,212,170,0.08);
}}
.play-card.sig-yellow .card-stripe {{ background: {C_YELLOW}; }}
.play-card.sig-red .card-stripe {{ background: {C_RED_MUTED}; opacity: 0.7; }}

.card-body {{
    flex: 1;
    padding: 16px 20px;
    min-width: 0;
}}
.card-teams {{
    font-size: 16px;
    font-weight: 600;
    color: {C_TEXT};
    letter-spacing: 0.01em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}
.card-time {{
    font-size: 11px;
    color: {C_MUTED};
    margin-top: 2px;
    font-family: 'JetBrains Mono', monospace;
}}
.card-pitching {{
    font-size: 12px;
    color: {C_MUTED};
    margin-top: 8px;
}}
.card-market-tag {{
    display: inline-block;
    margin-top: 10px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 3px 8px;
    border-radius: 3px;
    background: rgba(255,255,255,0.04);
    color: {C_MUTED};
    border: 1px solid {C_BORDER};
}}
.play-card.sig-green .card-market-tag {{ color: {C_ACCENT}; border-color: rgba(0,212,170,0.2); background: rgba(0,212,170,0.06); }}
.play-card.sig-yellow .card-market-tag {{ color: {C_YELLOW}; border-color: rgba(255,180,84,0.2); background: rgba(255,180,84,0.06); }}

.card-prices {{
    padding: 16px 24px;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    justify-content: center;
    min-width: 160px;
    flex-shrink: 0;
    border-left: 1px solid {C_BORDER};
}}
.price-target {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 28px;
    font-weight: 600;
    color: {C_TEXT};
    font-variant-numeric: tabular-nums;
    line-height: 1;
}}
.play-card.sig-green .price-target {{ color: {C_ACCENT}; }}
.play-card.sig-yellow .price-target {{ color: {C_YELLOW}; }}
.play-card.sig-red .price-target {{ color: {C_MUTED}; }}

.price-fair {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: {C_MUTED};
    margin-top: 4px;
}}
.price-edge {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    font-weight: 600;
    margin-top: 8px;
    letter-spacing: 0.04em;
}}
.play-card.sig-green .price-edge {{ color: {C_ACCENT}; }}
.play-card.sig-yellow .price-edge {{ color: {C_YELLOW}; }}
.play-card.sig-red .price-edge {{ color: {C_MUTED}; }}
.price-stake {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: {C_MUTED};
    margin-top: 3px;
}}
.price-books {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    color: {C_BORDER};
    margin-top: 6px;
    letter-spacing: 0.06em;
}}

/* ── Log bet button below card ────────────────────────── */
.log-btn-wrap {{ margin-top: -6px; margin-bottom: 18px; text-align: right; }}

/* ── Shadow ledger header ─────────────────────────────── */
.shadow-header {{
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: {C_RED_MUTED};
    margin-bottom: 12px;
}}

/* ── Section headings in terminal style ───────────────── */
.section-head {{
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: {C_MUTED};
    border-bottom: 1px solid {C_BORDER};
    padding-bottom: 8px;
    margin: 24px 0 16px 0;
}}
</style>
""", unsafe_allow_html=True)


# ── HTML component helpers ────────────────────────────────────────────────────

def _hours_since(utc_str: str) -> float | None:
    """Return hours since a UTC ISO timestamp, or None if unparseable."""
    if not utc_str:
        return None
    try:
        from datetime import timezone
        dt = datetime.strptime(utc_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except (ValueError, TypeError):
        return None


def _next_slot() -> str:
    """Return the label of the next scheduled pull slot."""
    now = datetime.now(EASTERN)
    for name, h, m in SLOT_TIMES:
        slot_today = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now < slot_today:
            label = slot_today.strftime('%I:%M %p ET').lstrip('0')
            return f'{name.upper()} {label}'
    # all today's slots passed — next is morning tomorrow
    return 'MORNING 7:00 AM ET'


def status_bar(last_snapshot_utc: str, requests_remaining, last_db_update_utc: str) -> None:
    """Render the full-width status bar strip at the top of the home page."""
    hours = _hours_since(last_snapshot_utc)
    live  = hours is not None and hours < 8

    if live:
        dot_class  = 'dot-live'
        label_html = f'<span class="status-live-text">SYSTEM LIVE</span>'
        sub        = f'<span class="status-sub">odds updated {hours:.1f}h ago</span>'
    else:
        dot_class  = 'dot-stale'
        age_str    = f'{hours:.1f}h ago' if hours is not None else 'UNKNOWN'
        label_html = f'<span class="status-stale-text">STALE</span>'
        sub        = f'<span class="status-sub">last odds pull {age_str}</span>'

    req  = str(requests_remaining) if requests_remaining is not None else '—'
    db_h = _hours_since(last_db_update_utc)
    db_s = f'{db_h:.1f}h ago' if db_h is not None else '—'

    st.markdown(f"""
<div class="status-bar">
  <div class="status-left">
    <span class="{dot_class}"></span>
    {label_html}
    {sub}
  </div>
  <div class="status-right">
    <span>API {req}/month</span>
    <span class="status-divider">·</span>
    <span>DB updated {db_s}</span>
  </div>
</div>
""", unsafe_allow_html=True)


def metric_tile(label: str, value: str, delta: str = '',
                accent: str = 'neutral') -> str:
    """Return HTML for one metric tile. accent: 'positive'|'negative'|'neutral'."""
    val_class = 'accent' if accent == 'positive' else ('muted' if accent == 'negative' else '')
    delta_html = f'<div class="m-delta">{delta}</div>' if delta else ''
    return f"""
<div class="m-tile {accent}">
  <div class="m-label">{label}</div>
  <div class="m-value {val_class}">{value}</div>
  {delta_html}
</div>"""


def metrics_row(tiles: list[str]) -> None:
    """Render a list of metric_tile HTML strings in a CSS grid."""
    st.markdown(
        f'<div class="metrics-grid">{"".join(tiles)}</div>',
        unsafe_allow_html=True
    )


def empty_state(lead: str, sub: str = '') -> None:
    """Render a terminal-voice empty state card."""
    sub_html = f'<div class="empty-state-sub">{sub}</div>' if sub else ''
    st.markdown(f"""
<div class="empty-state">
  <div class="empty-state-lead">{lead}</div>
  {sub_html}
</div>
""", unsafe_allow_html=True)


def play_card(rec: dict) -> str:
    """Return HTML for one play card."""
    from components.formatters import fmt_american, fmt_game_time, market_label

    color    = rec.get('confidence_color', 'red')
    sig_cls  = {'green': 'sig-green', 'yellow': 'sig-yellow', 'red': 'sig-red'}.get(color, 'sig-red')

    teams    = f"{rec['away_team']} @ {rec['home_team']}"
    gametime = fmt_game_time(rec.get('game_datetime_utc', ''))
    away_p   = rec.get('away_pitcher') or 'TBD'
    home_p   = rec.get('home_pitcher') or 'TBD'
    pitching = f'{away_p} vs {home_p}'
    mkt_str  = market_label(rec['market'], rec['side'], rec.get('line')).upper()

    target   = fmt_american(rec['target_price_american'])
    fair     = fmt_american(rec['fair_price_american'])
    edge     = rec.get('edge_percent', 0)
    edge_str = f'+{edge:.2f}%' if edge >= 0 else f'{edge:.2f}%'
    stake    = rec.get('recommended_stake_dollars_at_2500', 0)
    stake_str = f'${stake:.0f}' if stake else '—'
    books    = rec.get('num_books_in_consensus', 0)

    return f"""
<div class="play-card {sig_cls}">
  <div class="card-stripe"></div>
  <div class="card-body">
    <div class="card-teams">{teams}</div>
    <div class="card-time">{gametime}</div>
    <div class="card-pitching">{pitching}</div>
    <div class="card-market-tag">{mkt_str}</div>
  </div>
  <div class="card-prices">
    <div class="price-target">{target}</div>
    <div class="price-fair">fair {fair}</div>
    <div class="price-edge">{edge_str} EDGE</div>
    <div class="price-stake">stake {stake_str} @ $2500</div>
    <div class="price-books">{books} BOOKS</div>
  </div>
</div>"""


def plotly_dark(fig, height: int = 280):
    """Apply the dark terminal theme to a plotly figure in-place and return it."""
    fig.update_layout(
        paper_bgcolor = 'rgba(0,0,0,0)',
        plot_bgcolor  = 'rgba(0,0,0,0)',
        font          = dict(color=C_TEXT, family='Inter, sans-serif', size=11),
        xaxis         = dict(gridcolor=C_GRID, linecolor=C_BORDER, tickcolor=C_MUTED,
                             zeroline=False),
        yaxis         = dict(gridcolor=C_GRID, linecolor=C_BORDER, tickcolor=C_MUTED,
                             zeroline=False),
        margin        = dict(l=8, r=8, t=16, b=8),
        height        = height,
        showlegend    = False,
        hoverlabel    = dict(bgcolor=C_SURFACE, bordercolor=C_BORDER,
                             font_color=C_TEXT, font_family='JetBrains Mono'),
    )
    return fig


def section_head(text: str) -> None:
    """Render a terminal-style section heading."""
    st.markdown(f'<div class="section-head">{text}</div>', unsafe_allow_html=True)
