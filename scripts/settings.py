"""
settings.py — read/write the settings table.
"""

from datetime import datetime, timezone


def get_setting(conn, key: str, default=None) -> str | None:
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row:
        return row[0]
    return default


def set_setting(conn, key: str, value: str) -> None:
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    conn.execute("""
        INSERT INTO settings (key, value, updated_at_utc)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                       updated_at_utc = excluded.updated_at_utc
    """, (key, value, now))


def get_bankroll(conn) -> float:
    val = get_setting(conn, 'bankroll_dollars', '2500')
    try:
        return float(val)
    except (TypeError, ValueError):
        return 2500.0


def set_bankroll(conn, value: float) -> None:
    set_setting(conn, 'bankroll_dollars', str(value))
