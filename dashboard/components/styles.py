"""
styles.py — All visual styling for the MLB Betting dashboard.

Design target: Binance data density + Robinhood page hierarchy.
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
C_RED        = '#F6465D'   # Binance red — negative values
C_RED_MUTED  = '#6E3B47'   # desaturated red — shadow/control, intentionally dim
C_TEXT       = '#E6E9EF'   # primary text
C_MUTED      = '#8B92A8'   # secondary / label text
C_LABEL      = '#6B7280'   # tile labels — clearly subordinate
C_GRID       = '#1A1F2E'   # chart gridlines
C_MODEL      = '#8B5CF6'   # purple — reserved accent

# One accent color per bot, used for the pick-card stripe/prices and charts.
BOT_COLORS = {
    'coach_bo':       '#00D4AA',   # teal
    'the_accountant': '#8B5CF6',   # purple
    'degen_darren':   '#FFB454',   # amber
}
ADMIN_COLOR = '#F6465D'   # red — visually distinct from all 3 bot accents
C_FADE = '#4B5262'   # muted gray — fades (no side taken)


# ── Main CSS injection ────────────────────────────────────────────────────────

def inject_custom_css():
    st.markdown(f"""
<style>
/* ── Font imports ─────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:ital,wght@0,400;0,500;0,600&display=swap');

/* ── Hide Streamlit branding — surgical targeting only ─── */
/* RULE: never hide stToolbar or any button broadly —       */
/* Streamlit 1.58+ puts the sidebar toggle inside stToolbar */
/* and inside header buttons; hiding them kills navigation. */
footer {{ visibility: hidden; }}
#MainMenu {{ visibility: hidden; }}
.stDeployButton {{ display: none !important; }}
[data-testid="stDecoration"] {{ display: none !important; }}
/* Hide only the app-menu dots and status widget, NOT the   */
/* full toolbar (sidebar toggle lives there in 1.58+).      */
[data-testid="stMainMenuDots"] {{ display: none !important; }}
[data-testid="stStatusWidget"] {{ display: none !important; }}
/* Collapse header height but keep overflow visible so the  */
/* sidebar toggle (rendered as a sibling, not child) shows. */
[data-testid="stHeader"] {{
    background: transparent !important;
    border-bottom: none !important;
    height: 0 !important;
    min-height: 0 !important;
    padding: 0 !important;
    overflow: visible !important;
}}

/* ── Kill Streamlit's generous default padding ────────── */
.block-container {{
    padding-top: 1.5rem !important;
    padding-bottom: 1rem !important;
    max-width: 1200px !important;
}}

/* ── Global typography ────────────────────────────────── */
html, body, [class*="css"] {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}}

/* ── Metric tile override (st.metric) ─────────────────── */
[data-testid="metric-container"] {{
    background: linear-gradient(180deg, #161B26 0%, #11151E 100%);
    border: 1px solid {C_BORDER};
    border-radius: 10px;
    padding: 14px 16px !important;
    position: relative;
    overflow: hidden;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
    transition: border-color 0.12s ease, transform 0.12s ease;
}}
[data-testid="metric-container"]:hover {{
    border-color: #2A3242;
    transform: translateY(-1px);
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
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: {C_LABEL} !important;
}}
[data-testid="stMetricValue"] {{
    font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace !important;
    font-size: 32px !important;
    font-weight: 600 !important;
    font-variant-numeric: tabular-nums;
    color: #FFFFFF !important;
    line-height: 1.1;
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
    color: {C_LABEL} !important;
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
    margin: 14px 0 !important;
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

[data-testid="stSidebarContent"]::before {{
    content: '3 BETTORS';
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

/* ── Status bar (thinner, full-bleed) ─────────────────── */
.status-bar {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: {C_SURFACE};
    border: 1px solid {C_BORDER};
    border-radius: 6px;
    padding: 0 16px;
    height: 34px;
    margin-bottom: 16px;
    font-size: 11px;
    letter-spacing: 0.05em;
}}
.status-left {{ display: flex; align-items: center; gap: 8px; }}
.status-right {{ display: flex; align-items: center; gap: 12px; color: {C_LABEL}; }}
.status-live-text {{ font-weight: 600; color: {C_ACCENT}; }}
.status-stale-text {{ font-weight: 600; color: {C_YELLOW}; }}
.status-sub {{ color: {C_LABEL}; }}
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

/* ── Page header ──────────────────────────────────────── */
.page-header {{
    margin-bottom: 16px;
}}
.page-header-overline {{
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: {C_ACCENT};
    margin-bottom: 4px;
    font-family: 'JetBrains Mono', monospace;
}}
.page-header-headline {{
    font-size: 24px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: #FFFFFF;
    line-height: 1.15;
}}

/* ── Custom metric grid tiles ─────────────────────────── */
.metrics-grid {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 10px;
    margin-bottom: 6px;
}}
.m-tile {{
    background: linear-gradient(180deg, #161B26 0%, #11151E 100%);
    border: 1px solid {C_BORDER};
    border-radius: 10px;
    padding: 14px 16px;
    position: relative;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
    transition: border-color 0.12s ease, transform 0.12s ease;
    cursor: default;
}}
.m-tile:hover {{
    border-color: #2A3242;
    transform: translateY(-1px);
}}
.m-tile.positive {{ border-top: 2px solid {C_ACCENT}; }}
.m-tile.negative {{ border-top: 2px solid {C_RED}; }}
.m-tile.neutral  {{ border-top: 2px solid {C_BORDER}; }}
.m-tile.empty    {{ opacity: 0.55; }}
.m-label {{
    font-size: 10px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: {C_LABEL};
    margin-bottom: 6px;
}}
.m-value {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 32px;
    font-weight: 600;
    color: #FFFFFF;
    font-variant-numeric: tabular-nums;
    line-height: 1.1;
}}
.m-value.accent   {{ color: {C_ACCENT}; }}
.m-value.negative {{ color: {C_RED}; }}
.m-value.yellow   {{ color: {C_YELLOW}; }}
.m-value.muted    {{ color: {C_MUTED}; }}
.m-delta {{
    font-size: 11px;
    color: {C_MUTED};
    margin-top: 4px;
    font-family: 'JetBrains Mono', monospace;
}}

/* ── Empty state — designed moments, not voids ────────── */
.empty-state {{
    background: linear-gradient(180deg, #161B26 0%, #11151E 100%);
    border: 1px solid {C_BORDER};
    border-radius: 10px;
    padding: 20px 28px;
    display: flex;
    align-items: center;
    gap: 20px;
    max-height: 120px;
    box-sizing: border-box;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
}}
.empty-state-glyph {{
    font-size: 22px;
    color: {C_ACCENT};
    opacity: 0.45;
    flex-shrink: 0;
    line-height: 1;
    font-family: 'JetBrains Mono', monospace;
}}
.empty-state-body {{
    min-width: 0;
}}
.empty-state-lead {{
    font-size: 15px;
    font-weight: 600;
    color: #FFFFFF;
    margin-bottom: 3px;
    letter-spacing: -0.01em;
}}
.empty-state-sub {{
    font-size: 13px;
    color: {C_MUTED};
    margin-bottom: 3px;
}}
.empty-state-preview {{
    font-size: 11px;
    font-family: 'JetBrains Mono', monospace;
    color: {C_LABEL};
    letter-spacing: 0.03em;
}}

/* ── Play cards (Today page) ──────────────────────────── */
.play-card {{
    display: flex;
    align-items: stretch;
    background: linear-gradient(180deg, #161B26 0%, #11151E 100%);
    border: 1px solid {C_BORDER};
    border-radius: 10px;
    margin-bottom: 10px;
    overflow: hidden;
    position: relative;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
    transition: border-color 0.12s ease, transform 0.12s ease;
}}
.play-card:hover {{
    border-color: #2A3242;
    transform: translateY(-1px);
}}
.card-stripe {{
    width: 4px;
    flex-shrink: 0;
    background: var(--accent, {C_BORDER});
}}

.card-body {{
    flex: 1;
    padding: 14px 18px;
    min-width: 0;
}}
.card-teams {{
    font-size: 16px;
    font-weight: 600;
    color: #FFFFFF;
    letter-spacing: 0.01em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}
.card-time {{
    font-size: 11px;
    color: {C_LABEL};
    margin-top: 2px;
    font-family: 'JetBrains Mono', monospace;
}}
.card-pitching {{
    font-size: 12px;
    color: {C_MUTED};
    margin-top: 6px;
}}
.card-market-tag {{
    display: inline-block;
    margin-top: 8px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 3px 8px;
    border-radius: 3px;
    background: rgba(255,255,255,0.04);
    color: var(--accent, {C_MUTED});
    border: 1px solid {C_BORDER};
}}

.card-prices {{
    padding: 14px 22px;
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
    color: var(--accent, #FFFFFF);
    font-variant-numeric: tabular-nums;
    line-height: 1;
}}

.price-fair {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: {C_LABEL};
    margin-top: 4px;
}}
.price-edge {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    font-weight: 600;
    margin-top: 6px;
    letter-spacing: 0.04em;
    color: var(--accent, {C_MUTED});
}}
.price-stake {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: {C_LABEL};
    margin-top: 3px;
}}
.price-books {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    color: {C_BORDER};
    margin-top: 5px;
    letter-spacing: 0.06em;
}}

.pick-notes {{
    font-size: 10px;
    font-family: 'JetBrains Mono', monospace;
    color: {C_LABEL};
    margin-top: 6px;
    line-height: 1.4;
    letter-spacing: 0.02em;
}}

/* ── Shadow (paper-tracked) badge ─────────────────────── */
.shadow-badge {{
    display: inline-block;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    padding: 2px 6px;
    border-radius: 3px;
    background: rgba(255,255,255,0.05);
    border: 1px solid {C_BORDER};
    color: {C_MUTED};
    margin-left: 8px;
    vertical-align: middle;
}}

/* ── Fade card + badge ─────────────────────────────────── */
.fade-row {{
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 14px;
    border: 1px dashed {C_BORDER};
    border-radius: 8px;
    margin-bottom: 6px;
    font-size: 12px;
    color: {C_MUTED};
}}
.fade-badge {{
    display: inline-block;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    padding: 2px 7px;
    border-radius: 3px;
    background: rgba(75,82,98,0.18);
    border: 1px solid {C_FADE};
    color: {C_FADE};
}}

/* ── Bot section divider ──────────────────────────────── */
.bot-section-header {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 20px 0 12px 0;
}}
.bot-section-label {{
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: {C_MUTED};
    border-bottom: 1px solid {C_BORDER};
    padding-bottom: 6px;
    flex: 1;
}}

/* ── Log bet button below card ────────────────────────── */
.log-btn-wrap {{ margin-top: -6px; margin-bottom: 16px; text-align: right; }}

/* ── Shadow ledger header ─────────────────────────────── */
.shadow-header {{
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: {C_RED_MUTED};
    margin-bottom: 10px;
}}

/* ── Section headings in terminal style ───────────────── */
.section-head {{
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: {C_MUTED};
    border-bottom: 1px solid {C_BORDER};
    padding-bottom: 6px;
    margin: 20px 0 10px 0;
}}

/* ══ DO NOT REMOVE — sidebar nav must survive all future styling passes ══════ */
/* Streamlit 1.58 collapses the sidebar via translateX/margin-left, not         */
/* display:none. Force it open permanently — this is a personal tool, always-  */
/* open sidebar is acceptable and far better than an inaccessible nav.          */
[data-testid="stSidebar"] {{
    display: flex !important;
    visibility: visible !important;
    min-width: 244px !important;
    width: 244px !important;
    transform: translateX(0) !important;
    margin-left: 0 !important;
    left: 0 !important;
    position: relative !important;
    flex-shrink: 0 !important;
}}
[data-testid="stSidebarCollapsedControl"] {{
    display: none !important;
}}
[data-testid="stSidebarNavCollapseButton"] {{
    display: none !important;
}}
[data-testid="stSidebarNavItems"],
[data-testid="stSidebarNav"],
[data-testid="stSidebarContent"] {{
    visibility: visible !important;
    display: block !important;
}}
/* ══════════════════════════════════════════════════════════════════════════ */
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
    return 'MORNING 7:00 AM ET'


def page_header(overline: str, headline: str) -> None:
    """Render the page header: small teal overline + large white headline."""
    st.markdown(f"""
<div class="page-header">
  <div class="page-header-overline">{overline}</div>
  <div class="page-header-headline">{headline}</div>
</div>
""", unsafe_allow_html=True)


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
    is_empty = value in ('—', '', 'NO DATA', None)
    tile_cls = f'{accent}' + (' empty' if is_empty else '')

    if accent == 'positive':
        val_class = 'accent'
    elif accent == 'negative':
        val_class = 'negative'
    else:
        val_class = 'muted' if is_empty else ''

    delta_html = f'<div class="m-delta">{delta}</div>' if delta else ''
    return f"""
<div class="m-tile {tile_cls}">
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


def empty_state(lead: str, sub: str = '', preview: str = '') -> None:
    """Render a designed empty-state card with glyph, headline, explanation, and feature preview."""
    sub_html     = f'<div class="empty-state-sub">{sub}</div>' if sub else ''
    preview_html = f'<div class="empty-state-preview">{preview}</div>' if preview else ''
    st.markdown(f"""
<div class="empty-state">
  <div class="empty-state-glyph">◇</div>
  <div class="empty-state-body">
    <div class="empty-state-lead">{lead}</div>
    {sub_html}
    {preview_html}
  </div>
</div>
""", unsafe_allow_html=True)


def pick_card(rec: dict, bot_display_name: str, accent_color: str) -> str:
    """Return HTML for one bot's pick card (any bot — accent color distinguishes them)."""
    from components.formatters import fmt_american, fmt_game_time, market_label

    teams    = f"{rec['away_team']} @ {rec['home_team']}"
    gametime = fmt_game_time(rec.get('start_utc', ''))
    mkt_str  = market_label(rec['market'], rec['side'], rec.get('line')).upper()

    target   = fmt_american(rec.get('target_price_american'))
    fair     = fmt_american(rec.get('fair_price_american'))
    edge     = rec.get('edge_percent')
    edge_str = (f'+{edge:.2f}%' if edge >= 0 else f'{edge:.2f}%') if edge is not None else '—'
    units    = rec.get('units') or 0
    confidence = rec.get('confidence') or f'{units}u'
    shadow_badge = '<span class="shadow-badge">SHADOW</span>' if rec.get('is_shadow') else ''
    notes    = rec.get('notes') or ''
    notes_html = f'<div class="pick-notes">{notes}</div>' if notes else ''

    return f"""
<div class="play-card" style="--accent:{accent_color}">
  <div class="card-stripe"></div>
  <div class="card-body">
    <div class="card-teams">{teams}{shadow_badge}</div>
    <div class="card-time">{gametime}</div>
    <div class="card-market-tag">{bot_display_name.upper()} &middot; {mkt_str}</div>
    {notes_html}
  </div>
  <div class="card-prices">
    <div class="price-target">{target}</div>
    <div class="price-fair">fair {fair}</div>
    <div class="price-edge">{edge_str} EDGE &middot; {confidence}</div>
    <div class="price-stake">{units}u</div>
  </div>
</div>"""


def fade_row(game: dict, bot_display_name: str) -> str:
    """Return HTML for one bot's fade (skip) on a game — visible, not silent."""
    from components.formatters import fmt_game_time
    teams    = f"{game['away_team']} @ {game['home_team']}"
    gametime = fmt_game_time(game.get('start_utc', ''))
    return f"""
<div class="fade-row">
  <span class="fade-badge">FADE</span>
  <span>{bot_display_name} sat out <strong>{teams}</strong></span>
  <span style="margin-left:auto;color:{C_LABEL};font-size:11px;">{gametime}</span>
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
