"""
notify.py — Discord webhook notifications, one channel per bot.

Each bot gets its own webhook (env var below) rather than a shared channel
tagged by bot_key — per PLATFORM_HANDOFF.md this is cleaner for muting a
single noisy bot without silencing the other two.

send_slot_digest() is the primary entry point, fired once per lock slot from
run_slot.py. It lists both picks AND fades per bot, since the rulebook's fade
rule requires fades to be visible, not just silently absent.

Silent on missing webhook config (log warning, return False). A network/
Discord failure logs a warning and returns False — never raises, so a
webhook outage never crashes a pull.
"""

import os
from datetime import datetime, timezone

import requests
import pytz
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

from logger import get_logger

log = get_logger('notify')

EASTERN = pytz.timezone('US/Eastern')

BOT_WEBHOOKS = {
    'coach_bo':       os.getenv('COACH_BO_DISCORD_WEBHOOK', ''),
    'the_accountant': os.getenv('ACCOUNTANT_DISCORD_WEBHOOK', ''),
    'degen_darren':   os.getenv('DARREN_DISCORD_WEBHOOK', ''),
}

BOT_DISPLAY_NAMES = {
    'coach_bo':       'Coach Bo',
    'the_accountant': 'The Accountant',
    'degen_darren':   'Degen Darren',
}


def _market_label(market: str | None) -> str:
    if not market:
        return ''
    return {'moneyline': 'ML', 'total': 'TOTAL', 'spread': 'SPREAD'}.get(market, market.upper())


def _side_label(market: str, side: str, game: dict) -> str:
    if market == 'total':
        return side.upper()
    team = game.get('home_team', '?') if side == 'home' else game.get('away_team', '?')
    return f'{side.upper()}: {team}'


def _matchup(game: dict) -> str:
    return f'{game.get("away_team", "?")} @ {game.get("home_team", "?")}'


def _game_time_et(game: dict) -> str:
    """Return 'Wed 9/10 8:20 PM ET' style string from a games-row's start_utc,
    or 'TBD' if missing/unparseable. ESPN's timestamps omit seconds
    ('...T00:20Z'); handle both with/without seconds. Avoids the %-m/%-d/%-I
    strftime extensions (Linux-only — GitHub Actions runs fine, but they raise
    ValueError on Windows, where this also needs to run for local dev/testing)."""
    start_utc = game.get('start_utc', '')
    if not start_utc:
        return 'TBD'
    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%MZ'):
        try:
            dt_utc = datetime.strptime(start_utc, fmt).replace(tzinfo=timezone.utc)
            dt_et = dt_utc.astimezone(EASTERN)
            month = str(int(dt_et.strftime('%m')))
            day = str(int(dt_et.strftime('%d')))
            hour12 = str(int(dt_et.strftime('%I')))
            return f"{dt_et.strftime('%a')} {month}/{day} {hour12}:{dt_et.strftime('%M %p')} ET"
        except ValueError:
            continue
    return 'TBD'


def _post(webhook_url: str, payload: dict) -> bool:
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            return True
        log.warning(f'Discord returned {resp.status_code}: {resp.text[:200]}')
        return False
    except Exception as e:
        log.warning(f'Discord POST failed: {e}')
        return False


def send_bot_pick(bot_key: str, pick, game: dict) -> bool:
    """Fire a single-pick Discord embed for one bot's non-shadow recommendation."""
    webhook = BOT_WEBHOOKS.get(bot_key, '')
    if not webhook:
        log.warning(f'no webhook configured for bot "{bot_key}" — skipping notification')
        return False

    mkt_label  = _market_label(pick.market)
    side_label = _side_label(pick.market, pick.side, game)
    line_str   = f' {pick.line}' if pick.line is not None else ''
    edge_str   = f'{pick.edge_pct:+.1%}' if pick.edge_pct is not None else 'n/a'

    description = (
        f'🕐 {_game_time_et(game)}\n'
        f'**{mkt_label}{line_str} {side_label}**\n'
        f'Units: {pick.units:.1f}u | Confidence: {pick.confidence} | Edge: {edge_str}'
    )
    description += f'\nWhy: {pick.notes}' if pick.notes else '\nWhy: —'

    payload = {'embeds': [{
        'title': f'{_matchup(game)}',
        'description': description,
        'footer': {'text': f'3 Bettors - {BOT_DISPLAY_NAMES.get(bot_key, bot_key)}'},
    }]}
    ok = _post(webhook, payload)
    if ok:
        log.info(f'{bot_key} pick sent: {_matchup(game)} {mkt_label}{line_str} {side_label}')
    return ok


def send_slot_digest(slot_name: str, picks_by_bot: dict, fades_by_bot: dict, game_lookup: dict) -> None:
    """
    Post one digest message per bot for this lock slot: its picks, plus which
    games it faded (so fades are visible in Discord too, not just the
    dashboard). No-op per bot if that bot's webhook isn't configured.
    """
    for bot_key, picks in picks_by_bot.items():
        webhook = BOT_WEBHOOKS.get(bot_key, '')
        if not webhook:
            log.warning(f'no webhook configured for bot "{bot_key}" — skipping digest')
            continue

        display = BOT_DISPLAY_NAMES.get(bot_key, bot_key)
        lines = [f'**{display} - {slot_name.upper()}**\n']

        if picks:
            for p in picks:
                game = game_lookup.get(p.game_id, {})
                mkt_label  = _market_label(p.market)
                side_label = _side_label(p.market, p.side, game)
                line_str   = f' {p.line}' if p.line is not None else ''
                shadow_tag = ' [shadow]' if p.is_shadow else ''
                reasoning  = p.notes if p.notes else '—'
                lines.append(
                    f'`{p.confidence}` {_matchup(game)} — {_game_time_et(game)}\n'
                    f'{mkt_label}{line_str} {side_label} · {p.units:.1f}u{shadow_tag}\n'
                    f'Why: {reasoning}'
                )
                lines.append('')  # blank line between picks for readability
        else:
            lines.append('_No picks this slot._')

        n_fades = fades_by_bot.get(bot_key, 0)
        if n_fades:
            faded_games = [
                _matchup(game_lookup[gid]) for gid, game in game_lookup.items()
                if gid not in {p.game_id for p in picks}
            ]
            lines.append('')
            lines.append(f'*Faded {n_fades} game(s): {", ".join(faded_games)}*')

        content = '\n'.join(lines).strip()
        if len(content) > 1990:
            content = content[:1987] + '...'

        ok = _post(webhook, {'content': content})
        if ok:
            log.info(f'{bot_key} digest sent: {len(picks)} pick(s), {n_fades} fade(s), slot={slot_name}')


def send_test_messages() -> dict:
    """Send [TEST] messages to every configured bot webhook. Returns {bot_key: ok}."""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    results = {}
    for bot_key, webhook in BOT_WEBHOOKS.items():
        if not webhook:
            log.warning(f'{bot_key}: webhook not set')
            results[bot_key] = False
            continue
        display = BOT_DISPLAY_NAMES.get(bot_key, bot_key)
        results[bot_key] = _post(webhook, {
            'content': f'**[TEST] {display} webhook connected** — 3 Bettors channel is live.\n_{now}_'
        })
    return results
