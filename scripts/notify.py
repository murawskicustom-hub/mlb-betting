"""
notify.py — Discord webhook notifications for CMJ BETS algo channels.

Algo 1 (devig): per-pick alert fired immediately when a non-shadow rec is written.
Algo 2 (model_v1): slot digest fired after generate_model_recommendations() in
                   pregame/closing slots only.

Both functions are silent on missing webhook config (log warning, return False).
A network/Discord failure logs a warning and returns False — never raises.
"""

import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

from logger import get_logger

log = get_logger('notify')

_WEBHOOK1 = os.getenv('ALGO1_DISCORD_WEBHOOK', '')
_WEBHOOK2 = os.getenv('ALGO2_DISCORD_WEBHOOK', '')

# Discord embed sidebar colors (decimal)
_COLOR = {
    'green':  0x22c55e,
    'yellow': 0xeab308,
    'red':    0xef4444,
    'gray':   0x6b7280,
}

# ── Formatting helpers ────────────────────────────────────────────────────────

def _market_label(market: str) -> str:
    return {
        'moneyline':    'ML',
        'total':        'TOTAL',
        'f5_moneyline': 'F5 ML',
        'f5_total':     'F5 TOTAL',
        'yrfi':         'YRFI',
        'nrfi':         'NRFI',
    }.get(market, market.upper())


def _side_label(market: str, side: str, game: dict) -> str:
    if market in ('yrfi', 'nrfi'):
        return market.upper()
    if market in ('total', 'f5_total'):
        return side.upper()
    # moneyline / f5_moneyline
    team = game.get('home_team', '?') if side == 'home' else game.get('away_team', '?')
    return f"{side.upper()}: {team}"


def _fmt_american(price: int) -> str:
    return f'+{price}' if price > 0 else str(price)


def _game_time_et(game: dict) -> str:
    """Return game time as '1:40 PM ET', or '' if not available."""
    dt_utc = game.get('game_datetime_utc', '')
    if not dt_utc:
        return ''
    try:
        import pytz
        EASTERN = pytz.timezone('US/Eastern')
        dt = datetime.strptime(dt_utc, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        dt_et = dt.astimezone(EASTERN)
        return dt_et.strftime('%I:%M %p ET').lstrip('0')
    except Exception:
        return ''


def _post(webhook_url: str, payload: dict) -> bool:
    """POST a Discord payload. Returns True on success."""
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            return True
        log.warning(f'Discord returned {resp.status_code}: {resp.text[:200]}')
        return False
    except Exception as e:
        log.warning(f'Discord POST failed: {e}')
        return False


# ── Algo 1: per-pick alert ────────────────────────────────────────────────────

def send_algo1_alert(rec: dict, game: dict) -> bool:
    """
    Fire a Discord embed for a single non-shadow devig recommendation.

    rec  — dict with keys: market, side, line, target_price_american,
           fair_price_american, edge_percent, confidence_color,
           recommended_stake_dollars_at_2500, recommended_stake_pct,
           num_books_in_consensus
    game — dict with keys: away_team, home_team, game_date, game_datetime_utc
    """
    if not _WEBHOOK1:
        log.warning('ALGO1_DISCORD_WEBHOOK not set — skipping notification')
        return False

    color_key = rec.get('confidence_color', 'gray')
    color_hex  = _COLOR.get(color_key, _COLOR['gray'])

    market     = rec.get('market', '')
    side       = rec.get('side', '')
    target     = rec.get('target_price_american', 0)
    fair       = rec.get('fair_price_american', 0)
    edge       = rec.get('edge_percent', 0.0)
    stake_dlr  = rec.get('recommended_stake_dollars_at_2500', 0)
    stake_pct  = rec.get('recommended_stake_pct', 0)
    n_books    = rec.get('num_books_in_consensus', 0)
    line       = rec.get('line')

    away  = game.get('away_team', '?')
    home  = game.get('home_team', '?')
    gtime = _game_time_et(game)

    matchup    = f'{away} @ {home}'
    title_time = f' — {gtime}' if gtime else ''
    mkt_label  = _market_label(market)
    side_label = _side_label(market, side, game)

    if line is not None:
        pick_line = f'{mkt_label} {side_label} {line}'
    else:
        pick_line = f'{mkt_label} {side_label}'

    color_tag = color_key.upper()

    description = (
        f'**{pick_line}**\n'
        f'Target: {_fmt_american(target)} | Fair: {_fmt_american(fair)} | Edge: +{edge:.2f}%\n'
        f'Stake: ${stake_dlr:.0f} ({stake_pct:.0%} @ $2,500) | {n_books} books'
    )

    payload = {
        'embeds': [{
            'title':       f'[{color_tag}] {matchup}{title_time}',
            'description': description,
            'color':       color_hex,
            'footer':      {'text': 'CMJ BETS · Algo 1'},
        }]
    }

    ok = _post(_WEBHOOK1, payload)
    if ok:
        log.info(f'Algo1 alert sent: {matchup} {pick_line}')
    return ok


# ── Algo 2: slot digest ───────────────────────────────────────────────────────

def send_algo2_digest(new_recs: list[dict], slot_name: str,
                      games_analyzed: int, game_lookup: dict) -> bool:
    """
    Post a grouped digest of all new model_v1 recs for a slot.

    new_recs    — list of rec dicts (same schema as above, plus model_probability)
    slot_name   — 'pregame' | 'closing'
    games_analyzed — total games with odds analyzed this run
    game_lookup — dict of game_pk -> game dict (away_team, home_team, etc.)

    Returns False and skips if new_recs is empty.
    """
    if not new_recs:
        return False
    if not _WEBHOOK2:
        log.warning('ALGO2_DISCORD_WEBHOOK not set — skipping digest')
        return False

    # Group by market category
    groups = {
        'ML/TOTALS': [],
        'F5':        [],
        'YRFI/NRFI': [],
    }
    for r in new_recs:
        mkt = r.get('market', '')
        if mkt in ('yrfi', 'nrfi'):
            groups['YRFI/NRFI'].append(r)
        elif mkt in ('f5_moneyline', 'f5_total'):
            groups['F5'].append(r)
        else:
            groups['ML/TOTALS'].append(r)

    no_signal = games_analyzed - len({r['game_pk'] for r in new_recs})

    lines = [f'**ALGO 2 · {slot_name.upper()} DIGEST · {games_analyzed} games analyzed**\n']

    for group_name, recs in groups.items():
        if not recs:
            continue
        lines.append(f'**{group_name} ({len(recs)} picks)**')
        for r in recs:
            game  = game_lookup.get(r.get('game_pk'), {})
            away  = game.get('away_team', '?')
            home  = game.get('home_team', '?')
            mkt   = r.get('market', '')
            side  = r.get('side', '')
            color = r.get('confidence_color', '?').upper()
            prob  = r.get('model_probability', 0.0)
            mkt_label  = _market_label(mkt)
            side_label = _side_label(mkt, side, game)
            line_val   = r.get('line')
            line_str   = f' {line_val}' if line_val is not None else ''

            lines.append(
                f'`{color}` {away} @ {home} — {mkt_label}{line_str} {side_label} '
                f'({prob:.0%})'
            )
        lines.append('')

    if no_signal > 0:
        lines.append(f'*({no_signal} games — no signal)*')

    content = '\n'.join(lines).strip()
    # Discord message cap is 2000 chars; truncate if needed
    if len(content) > 1990:
        content = content[:1987] + '...'

    payload = {'content': content}
    ok = _post(_WEBHOOK2, payload)
    if ok:
        log.info(f'Algo2 digest sent: {len(new_recs)} recs, slot={slot_name}')
    return ok


# ── Test helpers ──────────────────────────────────────────────────────────────

def send_test_messages() -> tuple[bool, bool]:
    """Send [TEST] messages to both webhooks. Returns (algo1_ok, algo2_ok)."""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    ok1 = _post(_WEBHOOK1, {
        'embeds': [{
            'title':       '[TEST] Algo 1 webhook connected',
            'description': f'CMJ BETS Algo 1 channel is live.\n_{now}_',
            'color':       _COLOR['gray'],
            'footer':      {'text': 'Delete this message after confirming.'},
        }]
    }) if _WEBHOOK1 else False

    ok2 = _post(_WEBHOOK2, {
        'content': f'**[TEST] Algo 2 webhook connected** — CMJ BETS digest channel is live.\n_{now}_\n*Delete this message after confirming.*'
    }) if _WEBHOOK2 else False

    if not _WEBHOOK1:
        log.warning('ALGO1_DISCORD_WEBHOOK not set')
    if not _WEBHOOK2:
        log.warning('ALGO2_DISCORD_WEBHOOK not set')

    return ok1, ok2
