"""
pull_odds_nfl.py — pull NFL betting lines from The Odds API into odds_snapshots.

Usage:
    python pull_odds_nfl.py <season> <week>

Requires ODDS_API_KEY (see .env). The Odds API bills 1 credit per market per
region per call — regions=us + markets=h2h,totals,spreads = 3 credits per
pull. Free tier is 500 credits/month, so call this from lock slots (thursday/
sunday/monday), not on every research pass.

Falls back to a hand-edited data/manual_odds.csv when ODDS_API_KEY isn't set,
so the rest of the pipeline (bots -> persistence -> dashboard) stays testable
without a key. Team names come back as full names ("Seattle Seahawks"); they
are mapped to the same abbreviations pull_schedule_nfl.py already uses via
TEAM_NAME_TO_ABBR below.
"""

import os
import sys
import csv
import argparse
from datetime import datetime, timezone

import requests

from database import init_db, get_connection, upsert_sql
from logger import get_logger

ODDS_API_KEY  = os.getenv('ODDS_API_KEY')
ODDS_ENDPOINT = 'https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds'
MARKETS       = 'h2h,totals,spreads'
MARKET_MAP    = {'h2h': 'moneyline', 'totals': 'total', 'spreads': 'spread'}

MANUAL_ODDS_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'manual_odds.csv')

TEAM_NAME_TO_ABBR = {
    'Arizona Cardinals': 'ARI', 'Atlanta Falcons': 'ATL', 'Baltimore Ravens': 'BAL',
    'Buffalo Bills': 'BUF', 'Carolina Panthers': 'CAR', 'Chicago Bears': 'CHI',
    'Cincinnati Bengals': 'CIN', 'Cleveland Browns': 'CLE', 'Dallas Cowboys': 'DAL',
    'Denver Broncos': 'DEN', 'Detroit Lions': 'DET', 'Green Bay Packers': 'GB',
    'Houston Texans': 'HOU', 'Indianapolis Colts': 'IND', 'Jacksonville Jaguars': 'JAX',
    'Kansas City Chiefs': 'KC', 'Las Vegas Raiders': 'LV', 'Los Angeles Chargers': 'LAC',
    'Los Angeles Rams': 'LAR', 'Miami Dolphins': 'MIA', 'Minnesota Vikings': 'MIN',
    'New England Patriots': 'NE', 'New Orleans Saints': 'NO', 'New York Giants': 'NYG',
    'New York Jets': 'NYJ', 'Philadelphia Eagles': 'PHI', 'Pittsburgh Steelers': 'PIT',
    'San Francisco 49ers': 'SF', 'Seattle Seahawks': 'SEA', 'Tampa Bay Buccaneers': 'TB',
    'Tennessee Titans': 'TEN', 'Washington Commanders': 'WSH',
}

log = get_logger('pull_odds_nfl')


def american_to_decimal(american: int) -> float:
    if american >= 0:
        return round((american / 100) + 1, 6)
    return round((100 / abs(american)) + 1, 6)


def fetch_odds():
    """Call The Odds API. Returns (response_json, headers) or (None, None)."""
    params = {
        'apiKey': ODDS_API_KEY, 'regions': 'us', 'markets': MARKETS,
        'oddsFormat': 'american', 'dateFormat': 'iso',
    }
    try:
        resp = requests.get(ODDS_ENDPOINT, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json(), resp.headers
    except requests.RequestException as e:
        log.error(f'Odds API request failed: {e}')
        return None, None


def build_game_lookup(conn, season: int, week: int) -> dict:
    """(home_abbr, away_abbr) -> game_id for the given week's games."""
    rows = conn.execute(
        'SELECT game_id, home_team, away_team FROM games WHERE sport = ? AND season = ? AND week = ?',
        ('nfl', season, week),
    ).fetchall()
    return {(r['home_team'], r['away_team']): r['game_id'] for r in rows}


def log_pull(conn, pull_time_utc, source, success, remaining=None, used=None, error=None):
    conn.execute("""
        INSERT INTO pulls (pull_time_utc, sport, source, requests_remaining, requests_used, success, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (pull_time_utc, 'nfl', source, remaining, used, 1 if success else 0, error))


def extract_rows(odds_game: dict, game_id: str, snapshot_time_utc: str, home_abbr: str, away_abbr: str) -> list[dict]:
    rows = []
    for bookmaker in odds_game.get('bookmakers', []):
        book = bookmaker['key']
        for market in bookmaker.get('markets', []):
            our_market = MARKET_MAP.get(market.get('key'))
            if our_market is None:
                continue
            for outcome in market.get('outcomes', []):
                name  = outcome.get('name', '')
                price = outcome.get('price')
                point = outcome.get('point')
                if price is None:
                    continue

                if our_market == 'total':
                    outcome_type = name.lower()  # 'over' / 'under'
                else:  # moneyline / spread — outcome name is a team name
                    team_abbr = TEAM_NAME_TO_ABBR.get(name)
                    if team_abbr == home_abbr:
                        outcome_type = 'home'
                    elif team_abbr == away_abbr:
                        outcome_type = 'away'
                    else:
                        log.warning(f'outcome team "{name}" matched neither home ({home_abbr}) nor away ({away_abbr})')
                        continue

                rows.append({
                    'game_id': game_id, 'sport': 'nfl', 'book': book, 'market': our_market,
                    'outcome_type': outcome_type, 'line': point,
                    'price_american': int(price), 'price_decimal': american_to_decimal(int(price)),
                    'snapshot_time_utc': snapshot_time_utc,
                })
    return rows


def pull_odds_live(season: int, week: int) -> dict:
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    data, headers = fetch_odds()
    remaining = int(headers.get('x-requests-remaining', -1)) if headers else None
    used      = int(headers.get('x-requests-used', -1)) if headers else None

    with get_connection() as conn:
        if data is None:
            log_pull(conn, now_utc, 'the_odds_api', False, remaining, used, error='request failed')
            return {'rows_written': 0, 'games_matched': 0}

        lookup = build_game_lookup(conn, season, week)
        rows_written = 0
        matched = set()

        for odds_game in data:
            home_abbr = TEAM_NAME_TO_ABBR.get(odds_game.get('home_team', ''))
            away_abbr = TEAM_NAME_TO_ABBR.get(odds_game.get('away_team', ''))
            if home_abbr is None or away_abbr is None:
                continue
            game_id = lookup.get((home_abbr, away_abbr))
            if game_id is None:
                continue  # not in this week's slate — expected, API returns the whole season

            matched.add(game_id)
            for row in extract_rows(odds_game, game_id, now_utc, home_abbr, away_abbr):
                conn.execute("""
                    INSERT OR IGNORE INTO odds_snapshots
                        (game_id, sport, book, market, outcome_type, line,
                         price_american, price_decimal, snapshot_time_utc)
                    VALUES (:game_id, :sport, :book, :market, :outcome_type, :line,
                            :price_american, :price_decimal, :snapshot_time_utc)
                """, row)
                rows_written += 1

        log_pull(conn, now_utc, 'the_odds_api', True, remaining, used)

    log.info(f'{season} week {week}: {len(matched)} game(s) matched, {rows_written} snapshot row(s) written '
             f'(API credits remaining: {remaining})')
    return {'rows_written': rows_written, 'games_matched': len(matched), 'requests_remaining': remaining}


def pull_odds_manual_stub(season: int, week: int) -> dict:
    """Fallback used only when ODDS_API_KEY isn't set — see module docstring."""
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if not os.path.exists(MANUAL_ODDS_PATH):
        os.makedirs(os.path.dirname(MANUAL_ODDS_PATH), exist_ok=True)
        with open(MANUAL_ODDS_PATH, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['game_id', 'book', 'market', 'outcome_type', 'line', 'price_american'])
            f.write('# example: nfl:401872656,manual,moneyline,home,,-150\n')
        log.info(f'ODDS_API_KEY not set — wrote empty template to {MANUAL_ODDS_PATH}')

    rows_written = 0
    with get_connection() as conn:
        with open(MANUAL_ODDS_PATH, encoding='utf-8') as f:
            reader = csv.DictReader(row for row in f if row.strip() and not row.lstrip().startswith('#'))
            for r in reader:
                if not r.get('game_id'):
                    continue
                price = (r.get('price_american') or '').strip()
                line  = (r.get('line') or '').strip()
                conn.execute(
                    upsert_sql('odds_snapshots',
                               ['game_id', 'sport', 'book', 'market', 'outcome_type', 'line',
                                'price_american', 'price_decimal', 'snapshot_time_utc'],
                               ['game_id', 'book', 'market', 'outcome_type', 'snapshot_time_utc']),
                    (r['game_id'], 'nfl', r.get('book', 'manual'), r['market'], r['outcome_type'],
                     float(line) if line else None, int(price) if price else None, None, now_utc),
                )
                rows_written += 1
        log_pull(conn, now_utc, 'manual_odds_stub', True)

    log.info(f'manual odds stub: {rows_written} row(s) written from {os.path.basename(MANUAL_ODDS_PATH)}')
    return {'rows_written': rows_written, 'games_matched': 0}


def pull_odds(season: int, week: int) -> dict:
    init_db()
    if not ODDS_API_KEY:
        return pull_odds_manual_stub(season, week)
    return pull_odds_live(season, week)


def main():
    parser = argparse.ArgumentParser(description='Pull NFL odds from The Odds API into odds_snapshots.')
    parser.add_argument('season', type=int)
    parser.add_argument('week', type=int)
    args = parser.parse_args()

    result = pull_odds(args.season, args.week)
    print(f'Pulled NFL odds for {args.season} week {args.week}: {result}')
    sys.exit(0)


if __name__ == '__main__':
    main()
