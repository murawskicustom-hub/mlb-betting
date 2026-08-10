"""
formatters.py — string formatting helpers used across dashboard pages.
"""

from datetime import datetime
import pytz

EASTERN = pytz.timezone('US/Eastern')


def fmt_american(price) -> str:
    """Format American odds: +130, -110, or '—' if None."""
    if price is None:
        return '—'
    return f'+{price}' if price >= 0 else str(price)


def fmt_dollars(amount, always_sign=False) -> str:
    """Format a dollar amount: '$1,234.56', or '—' if None."""
    if amount is None:
        return '—'
    sign = '+' if always_sign and amount > 0 else ''
    return f'{sign}${amount:,.2f}'


def fmt_pct(value, decimals=2) -> str:
    """Format a percentage: '+1.45%', or '—' if None."""
    if value is None:
        return '—'
    sign = '+' if value > 0 else ''
    return f'{sign}{value:.{decimals}f}%'


def fmt_clv(value) -> str:
    """Format CLV with sign, or '—' if None."""
    return fmt_pct(value)


def utc_to_eastern(utc_str: str) -> datetime | None:
    """Parse an ISO UTC string and return an Eastern-tz datetime, or None.

    ESPN's timestamps omit seconds ('...T00:20Z'); MLB Stats API/most other
    sources include them ('...T00:20:00Z') — try both.
    """
    if not utc_str:
        return None
    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%MZ'):
        try:
            dt = datetime.strptime(utc_str, fmt).replace(tzinfo=pytz.utc)
            return dt.astimezone(EASTERN)
        except (ValueError, TypeError):
            continue
    return None


def _no_pad(dt, directive: str) -> str:
    """Return strftime result with leading zero stripped — cross-platform."""
    return dt.strftime(directive).lstrip('0') or '0'


def fmt_game_time(utc_str: str) -> str:
    """Return '7:05 PM ET' style string from UTC ISO timestamp."""
    dt = utc_to_eastern(utc_str)
    if dt is None:
        return 'TBD'
    return f"{_no_pad(dt, '%I')}:{dt.strftime('%M %p')} ET"


def fmt_date_friendly(dt) -> str:
    """'Saturday, May 30, 2026' — cross-platform, no leading zero on day."""
    return f"{dt.strftime('%A, %B')} {dt.day}, {dt.year}"


def fmt_date_short(dt) -> str:
    """'May 30' — cross-platform."""
    return f"{dt.strftime('%B')} {dt.day}"


def fmt_datetime_et(utc_str: str) -> str:
    """'May 30, 2026  7:05 PM ET' from UTC ISO string."""
    dt = utc_to_eastern(utc_str)
    if dt is None:
        return '—'
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}  {_no_pad(dt, '%I')}:{dt.strftime('%M %p')} ET"


def fmt_game_label(away: str, home: str, utc_str: str = None) -> str:
    """'NYY @ BAL  7:05 PM ET'"""
    time_part = f'  {fmt_game_time(utc_str)}' if utc_str else ''
    return f'{away} @ {home}{time_part}'


def market_label(market: str, side: str, line) -> str:
    """Human-readable market+side+line string."""
    if not market or not side:
        return market or '—'
    if market == 'moneyline':
        return f'ML {side.capitalize()}'
    elif market == 'total':
        line_str = f' {line}' if line is not None else ''
        return f'Total {side.capitalize()}{line_str}'
    elif market == 'spread':
        line_str = f' {line:+g}' if line is not None else ''
        return f'Spread {side.capitalize()}{line_str}'
    return market
