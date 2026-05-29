import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv
import requests

from database import init_db, get_connection
from logger import get_logger
from team_mapping import translate

# Load .env from project root (one level above scripts/)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

ODDS_API_KEY = os.getenv('ODDS_API_KEY')
ODDS_ENDPOINT = 'https://api.the-odds-api.com/v4/sports/baseball_mlb/odds'

# Map Odds API market keys to our normalized market names
MARKET_MAP = {
    'h2h':     'moneyline',
    'totals':  'total',
    'spreads': 'spread',
}

log = get_logger('pull_odds')


def american_to_decimal(american: int) -> float:
    if american >= 0:
        return round((american / 100) + 1, 6)
    else:
        return round((100 / abs(american)) + 1, 6)


def fetch_odds():
    """Call The Odds API. Returns (response_json, headers) or (None, None)."""
    if not ODDS_API_KEY:
        log.error('ODDS_API_KEY not set — check your .env file')
        sys.exit(1)

    params = {
        'apiKey':      ODDS_API_KEY,
        'regions':     'us',
        'markets':     'h2h,totals,spreads',
        'oddsFormat':  'american',
        'dateFormat':  'iso',
    }
    try:
        resp = requests.get(ODDS_ENDPOINT, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json(), resp.headers
    except requests.RequestException as e:
        log.error(f'Odds API request failed: {e}')
        return None, None


def build_game_lookup(conn):
    """
    Return a dict keyed by (home_team_mlb, away_team_mlb, game_date) -> game_pk
    for all games in the DB that are Scheduled or upcoming.
    We include a date range wide enough to cover any timezone edge cases.
    """
    rows = conn.execute(
        "SELECT game_pk, home_team, away_team, game_date FROM games"
    ).fetchall()
    lookup = {}
    for r in rows:
        key = (r['home_team'], r['away_team'], r['game_date'])
        lookup[key] = r['game_pk']
    return lookup


def commence_to_eastern_date(commence_time: str) -> str:
    """Convert ISO UTC string from Odds API to Eastern date (YYYY-MM-DD)."""
    import pytz
    EASTERN = pytz.timezone('US/Eastern')
    dt = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
    return dt.astimezone(EASTERN).strftime('%Y-%m-%d')


def match_game(odds_game, lookup, unmatched):
    """
    Attempt to match an Odds API game object to a game_pk in our DB.
    Returns game_pk or None.
    """
    odds_home = odds_game.get('home_team', '')
    odds_away = odds_game.get('away_team', '')
    commence  = odds_game.get('commence_time', '')

    mlb_home = translate(odds_home)
    mlb_away = translate(odds_away)

    if mlb_home is None:
        log.warning(f'Unknown team name from Odds API (home): "{odds_home}" — add to team_mapping.py')
        unmatched.append({'home': odds_home, 'away': odds_away, 'reason': f'unknown home team "{odds_home}"'})
        return None
    if mlb_away is None:
        log.warning(f'Unknown team name from Odds API (away): "{odds_away}" — add to team_mapping.py')
        unmatched.append({'home': odds_home, 'away': odds_away, 'reason': f'unknown away team "{odds_away}"'})
        return None

    game_date = commence_to_eastern_date(commence)
    key = (mlb_home, mlb_away, game_date)
    game_pk = lookup.get(key)

    if game_pk is None:
        reason = f'no DB row for {mlb_away} @ {mlb_home} on {game_date}'
        log.warning(f'Could not match Odds API game to DB: {reason}')
        unmatched.append({'home': odds_home, 'away': odds_away, 'reason': reason})
        return None

    return game_pk


def extract_outcomes(bookmaker, game_pk, snapshot_time_utc):
    """
    Yield one snapshot-row dict per outcome across all markets for one bookmaker.
    """
    book_key  = bookmaker['key']
    api_last  = bookmaker.get('last_update', '')

    for market in bookmaker.get('markets', []):
        api_market = market.get('key')
        our_market = MARKET_MAP.get(api_market)
        if our_market is None:
            log.warning(f'Unrecognised market key "{api_market}" from book {book_key} — skipped')
            continue

        outcomes = market.get('outcomes', [])

        # For moneyline (h2h) the outcome names are team names; map to 'home'/'away'
        # For totals the outcome names are 'Over'/'Under'
        # For spreads the outcome names are team names; map to 'home'/'away'
        for outcome in outcomes:
            name  = outcome.get('name', '')
            price = outcome.get('price')
            point = outcome.get('point')   # present for totals & spreads

            if price is None:
                continue

            # Normalise outcome_type
            if our_market == 'moneyline':
                # We need home/away — we'll tag at insert time using game context
                outcome_type = name  # stored temporarily; resolved below
            elif our_market == 'total':
                outcome_type = name.lower()  # 'over' or 'under'
            else:  # spread
                outcome_type = name  # team name; resolved below

            yield {
                'game_pk':             game_pk,
                'book':                book_key,
                'market':              our_market,
                '_outcome_name':       name,   # raw, used for home/away resolution
                'outcome_type':        outcome_type,
                'line':                point,
                'price_american':      int(price),
                'price_decimal':       american_to_decimal(int(price)),
                'snapshot_time_utc':   snapshot_time_utc,
                'api_last_update_utc': api_last,
            }


def resolve_home_away(rows, odds_home, odds_away, mlb_home, mlb_away):
    """
    For moneyline and spread rows, replace the raw team-name outcome_type
    with 'home' or 'away'.
    """
    for row in rows:
        if row['market'] in ('moneyline', 'spread'):
            raw = row['_outcome_name']
            if raw == mlb_home or raw == odds_home:
                row['outcome_type'] = 'home'
            elif raw == mlb_away or raw == odds_away:
                row['outcome_type'] = 'away'
            else:
                log.warning(
                    f"outcome name '{raw}' didn't match home '{mlb_home}' "
                    f"or away '{mlb_away}' — stored raw"
                )
        del row['_outcome_name']
    return rows


def log_pull(conn, pull_time_utc, games_returned, remaining, used, success, error=None):
    conn.execute("""
        INSERT INTO odds_pulls
            (pull_time_utc, endpoint, games_returned, requests_remaining, requests_used, success, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (pull_time_utc, ODDS_ENDPOINT, games_returned, remaining, used, 1 if success else 0, error))


def main():
    init_db()

    pull_time_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    log.info('Fetching MLB odds from The Odds API...')
    data, headers = fetch_odds()

    remaining = int(headers.get('x-requests-remaining', -1)) if headers else -1
    used      = int(headers.get('x-requests-used', -1))      if headers else -1

    if data is None:
        with get_connection() as conn:
            log_pull(conn, pull_time_utc, 0, remaining, used, False, 'API request failed')
        sys.exit(1)

    log.info(f'API returned {len(data)} games. Requests remaining: {remaining}')

    unmatched    = []
    total_rows   = 0
    matched_pks  = set()

    with get_connection() as conn:
        lookup = build_game_lookup(conn)

        snapshot_rows = []

        for odds_game in data:
            odds_home = odds_game.get('home_team', '')
            odds_away = odds_game.get('away_team', '')

            game_pk = match_game(odds_game, lookup, unmatched)
            if game_pk is None:
                continue

            matched_pks.add(game_pk)

            mlb_home = translate(odds_home)
            mlb_away = translate(odds_away)

            game_rows = []
            for bookmaker in odds_game.get('bookmakers', []):
                game_rows.extend(extract_outcomes(bookmaker, game_pk, pull_time_utc))

            game_rows = resolve_home_away(game_rows, odds_home, odds_away, mlb_home, mlb_away)
            snapshot_rows.extend(game_rows)

        # Bulk insert all snapshot rows
        for row in snapshot_rows:
            conn.execute("""
                INSERT INTO odds_snapshots
                    (game_pk, book, market, outcome_type, line,
                     price_american, price_decimal, snapshot_time_utc, api_last_update_utc)
                VALUES
                    (:game_pk, :book, :market, :outcome_type, :line,
                     :price_american, :price_decimal, :snapshot_time_utc, :api_last_update_utc)
            """, row)
            total_rows += 1

        log_pull(conn, pull_time_utc, len(data), remaining, used, True)

    log.info(
        f'Done. {len(data)} games from API, {len(matched_pks)} matched, '
        f'{len(unmatched)} unmatched, {total_rows} snapshot rows inserted.'
    )
    if unmatched:
        for u in unmatched:
            log.warning(f'  Unmatched: {u["away"]} @ {u["home"]} — {u["reason"]}')

    print(
        f'\nOdds pull complete:\n'
        f'  Games from API:      {len(data)}\n'
        f'  Matched to DB:       {len(matched_pks)}\n'
        f'  Unmatched:           {len(unmatched)}\n'
        f'  Snapshot rows saved: {total_rows}\n'
        f'  Requests remaining:  {remaining}\n'
    )


if __name__ == '__main__':
    main()
