"""
run_slot.py — weekly orchestrator for the NFL multi-bot pipeline.

Usage: python run_slot.py <slot>
Slots: tuesday_research | thursday_lock | sunday_lock | monday_lock | tuesday_grade

Replaces the old daily morning/midday/pregame/closing cadence (run_scheduled_pull.py)
with the actual NFL weekly rhythm from the rulebook:
    Tue  -> tuesday_research  (pull full week: schedule/injuries/features, no bot run)
    Thu  -> thursday_lock     (lock TNF: latest injuries/lines, run bots, notify)
    Sun  -> sunday_lock       (lock all Sunday games)
    Mon  -> monday_lock       (lock MNF)
    Tue  -> tuesday_grade     (grade the finished week, advance to next week)

Current season/week are read from settings (nfl_current_season/nfl_current_week)
rather than derived from today's date — NFL week boundaries (byes, moved
games, playoffs) don't map onto a clean formula, so a manually-advanced
counter is far less fragile. tuesday_grade auto-advances the week counter.

This script is called directly by the venv's Python interpreter, so venv
activation is not needed. Sub-scripts are run with sys.executable so the same
code works on a laptop scheduler and in GitHub Actions.
"""

import sys
import os
import subprocess
import logging
from pathlib import Path
from datetime import date, datetime, timezone

# The orchestrator (scheduled runs + manual catch-ups) targets Postgres so the
# laptop and the GitHub Actions cloud job both write to the same Neon DB.
os.environ.setdefault('DB_BACKEND', 'postgres')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR  = PROJECT_ROOT / 'scripts'
LOGS_DIR     = PROJECT_ROOT / 'logs' / 'scheduled'
VENV_PYTHON  = sys.executable

sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(PROJECT_ROOT))

from database import get_connection, upsert_sql
from grader import grade_pending
from clv_calculator import compute_clv

# Import every bot module for its registration side effect.
import bots.coach_bo    # noqa: F401
import bots.the_accountant  # noqa: F401
import bots.degen_darren    # noqa: F401
from bots import registry
from bots.base import BotContext
from bots.config import BOT_CONFIG_VERSION

from notify import send_slot_digest

SPORT = 'nfl'
VALID_SLOTS = ('tuesday_research', 'thursday_lock', 'sunday_lock', 'monday_lock', 'tuesday_grade')


# ── settings helpers ─────────────────────────────────────────────────────────

def get_setting(conn, key: str, default=None):
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else default


def set_setting(conn, key: str, value, now_utc: str):
    conn.execute(
        upsert_sql('settings', ['key', 'value', 'updated_at_utc'], ['key']),
        (key, str(value), now_utc),
    )


def current_season_week(conn) -> tuple[int, int]:
    season = int(get_setting(conn, 'nfl_current_season', '2026'))
    week   = int(get_setting(conn, 'nfl_current_week', '1'))
    return season, week


# ── logging ───────────────────────────────────────────────────────────────────

def setup_slot_logger(slot: str, date_str: str) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f'{date_str}_{slot}.log'
    logger = logging.getLogger(f'orchestrator.{slot}')
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger

    fmt = logging.Formatter('%(asctime)s  %(levelname)-8s  %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    fh = logging.FileHandler(log_path, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


def run_script(script: str, args: list, log: logging.Logger) -> bool:
    cmd = [str(VENV_PYTHON), str(SCRIPTS_DIR / script)] + [str(a) for a in args]
    log.info(f'  -> Running: {script} {" ".join(str(a) for a in args)}')
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
        for line in result.stdout.splitlines():
            if line.strip():
                log.info(f'    {line}')
        for line in result.stderr.splitlines():
            if line.strip():
                log.info(f'    {line}')
        if result.returncode == 0:
            log.info(f'  OK  {script}')
            return True
        log.error(f'  FAIL  {script} exited with code {result.returncode}')
        return False
    except Exception as e:
        log.error(f'  FAIL  {script} raised an exception: {e}')
        return False


# ── slate scoping ────────────────────────────────────────────────────────────

def _weekday(date_str: str | None) -> int | None:
    """Monday=0 ... Sunday=6, or None if date_str is missing/unparseable."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').weekday()
    except ValueError:
        return None


# thursday_lock catches every pre-Sunday game (Tue/Wed/Thu/Fri), not just
# Thursday exactly — real schedules include odd-week quirks like a Wednesday
# season opener or a Thursday/Friday international game, and the rulebook's
# intent is "lock the early-week games before Sunday", not "only Thursday."
DAY_FILTERS = {
    'thursday_lock': lambda g: _weekday(g['game_date']) not in (5, 6, 0),  # not Sat/Sun/Mon
    'sunday_lock':   lambda g: _weekday(g['game_date']) == 6,
    'monday_lock':   lambda g: _weekday(g['game_date']) == 0,
}


def slate_for(conn, season: int, week: int, day_filter=None) -> list[dict]:
    rows = conn.execute(
        'SELECT * FROM games WHERE sport = ? AND season = ? AND week = ? ORDER BY start_utc',
        (SPORT, season, week),
    ).fetchall()
    games = [dict(r) for r in rows]
    if day_filter:
        games = [g for g in games if day_filter(g)]
    return games


def build_context(conn, games: list[dict]) -> BotContext:
    odds: dict[str, list[dict]] = {}
    features: dict[str, dict] = {}
    game_ids = [g['game_id'] for g in games]
    if game_ids:
        placeholders = ','.join('?' * len(game_ids))
        for r in conn.execute(f'SELECT * FROM odds_snapshots WHERE game_id IN ({placeholders})', game_ids):
            odds.setdefault(r['game_id'], []).append(dict(r))
        for r in conn.execute(f'SELECT * FROM features WHERE game_id IN ({placeholders})', game_ids):
            features.setdefault(r['game_id'], {})[r['key']] = (
                r['value'] if r['value'] is not None else r['value_text']
            )
    return BotContext(
        conn=conn,
        sport=SPORT,
        as_of_utc=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        games=games,
        odds=odds,
        features=features,
    )


# ── persistence: picks + explicit fade rows ─────────────────────────────────

def _resolve_target_price(ctx: BotContext, pick) -> int | None:
    """
    The price to grade the pick's payout against: the market's offered price
    for this exact (market, side) if a snapshot exists, else the bot's own
    fair_price_american (a market-less/shadow pick still needs *some* price
    to compute win/loss payout against).
    """
    for row in ctx.odds.get(pick.game_id, []):
        if row.get('market') == pick.market and row.get('outcome_type') == pick.side:
            price = row.get('price_american')
            if price is not None:
                return price
    return pick.fair_price_american


def persist_picks_and_fades(conn, bot, ctx: BotContext, picks: list, now_utc: str) -> tuple[int, int]:
    """
    Write one recommendations row per Pick, then one is_fade=1 row per game in
    the slate the bot did NOT pick. This is the orchestrator's job, not the
    bot's — see bots/base.py and PLATFORM_HANDOFF.md.
    """
    games = ctx.games
    picked_game_ids = {p.game_id for p in picks}

    for p in picks:
        target_price = _resolve_target_price(ctx, p)
        conn.execute("""
            INSERT INTO recommendations (
                bot_key, sport, game_id, generated_at_utc, market, side, line,
                target_price_american, fair_price_american, edge_percent, confidence, units,
                is_shadow, is_fade, notes, config_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """, (
            bot.key, p.sport, p.game_id, now_utc, p.market, p.side, p.line,
            target_price, p.fair_price_american, p.edge_pct, p.confidence, p.units,
            1 if p.is_shadow else 0, p.notes, BOT_CONFIG_VERSION.get(bot.key),
        ))

    fades = 0
    for g in games:
        if g['game_id'] in picked_game_ids:
            continue
        conn.execute("""
            INSERT INTO recommendations (bot_key, sport, game_id, generated_at_utc, is_fade, units)
            VALUES (?, ?, ?, ?, 1, 0)
        """, (bot.key, SPORT, g['game_id'], now_utc))
        fades += 1

    return len(picks), fades


# ── slot implementations ─────────────────────────────────────────────────────

def run_tuesday_research(conn, log: logging.Logger) -> dict:
    season, week = current_season_week(conn)
    log.info(f'tuesday_research: {season} week {week} — pulling full week')
    ok_sched  = run_script('pull_schedule_nfl.py', [season, week], log)
    ok_odds   = run_script('pull_odds_nfl.py', [season, week], log)
    ok_inj    = run_script('pull_injuries_nfl.py', [season, week], log)
    ok_feat   = run_script('pull_features_nfl.py', [season, week], log)
    ok_depth  = run_script('pull_depth_charts_nfl.py', [season, week], log)
    # Coaching tendencies (PROE, 4th-down aggression) are season-to-date and
    # slow-moving — pulled once a week here, not re-pulled at every lock slot.
    ok_tend   = run_script('pull_tendencies_nfl.py', [season, week], log)
    return {
        'schedule': ok_sched, 'odds': ok_odds, 'injuries': ok_inj,
        'features': ok_feat, 'depth_charts': ok_depth, 'tendencies': ok_tend,
    }


def _already_decided_game_ids(conn, bot_key: str, sport: str, game_ids: list[str]) -> set[str]:
    """Game ids this bot already has ANY recommendation row for (pick or
    fade) this run. Used to make lock slots idempotent: a retry (GitHub
    Actions hiccup, manual re-run) must not re-decide a game a bot already
    committed to — especially once a bot's generate() makes a live LLM call,
    where a re-run could produce a different, non-reproducible answer."""
    if not game_ids:
        return set()
    placeholders = ','.join('?' * len(game_ids))
    rows = conn.execute(
        f'SELECT DISTINCT game_id FROM recommendations '
        f'WHERE bot_key = ? AND sport = ? AND game_id IN ({placeholders})',
        [bot_key, sport, *game_ids],
    ).fetchall()
    return {r['game_id'] for r in rows}


# A real lock slot only ever fires within a few days of kickoff (thursday_lock
# for Tue-Fri games, sunday_lock the morning of Sunday games, monday_lock the
# afternoon of MNF). If the earliest game in scope is further out than this, the
# slot was manually dispatched against a future week's games — almost certainly
# to test the pipeline before the season has actually started, not to place a
# real weekly lock. Writing real recommendation rows in that case would count
# against a bot's official record before the challenge begins, and — because of
# the idempotency check above — would permanently block the real, up-to-date
# pick from ever being generated once the actual lock slot fires for that game.
LOCK_WINDOW_DAYS = 5


def _earliest_game_date(games: list[dict]) -> date | None:
    dates = [g['game_date'] for g in games if g.get('game_date')]
    if not dates:
        return None
    try:
        return min(date.fromisoformat(d) for d in dates)
    except ValueError:
        return None


def run_lock_slot(slot: str, conn, log: logging.Logger) -> dict:
    season, week = current_season_week(conn)
    day_filter = DAY_FILTERS.get(slot)

    # Refresh injuries/depth charts/odds for just the teams in scope before
    # locking. Depth charts are worth re-pulling here (not just Tuesday) since
    # a starter can change mid-week due to injury; tendencies are season-to-date
    # and don't need a mid-week refresh.
    run_script('pull_schedule_nfl.py', [season, week], log)
    teams_scope = slate_for(conn, season, week, day_filter)
    team_args = sorted({g['home_team'] for g in teams_scope} | {g['away_team'] for g in teams_scope})
    if team_args:
        run_script('pull_injuries_nfl.py', [season, week, *team_args], log)
        run_script('pull_depth_charts_nfl.py', [season, week, *team_args], log)
    run_script('pull_odds_nfl.py', [season, week], log)

    games = slate_for(conn, season, week, day_filter)
    if not games:
        log.warning(f'{slot}: no games in scope for {season} week {week}')
        return {'games': 0}

    earliest = _earliest_game_date(games)
    if earliest is not None:
        days_out = (earliest - datetime.now(timezone.utc).date()).days
        if days_out > LOCK_WINDOW_DAYS:
            log.warning(
                f'{slot}: earliest game is {days_out} day(s) out (> {LOCK_WINDOW_DAYS}) — '
                f'this looks like a pre-season pipeline test, not a real lock. Data was '
                f'refreshed above, but skipping bot picks and notify so nothing gets '
                f'written to the official record.'
            )
            return {'games': len(games), 'skipped_test_window': True, 'days_out': days_out}

    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    game_ids = [g['game_id'] for g in games]

    picks_by_bot: dict[str, list] = {}
    fades_by_bot: dict[str, int] = {}
    for bot in registry.bots_for_sport(SPORT):
        decided = _already_decided_game_ids(conn, bot.key, SPORT, game_ids)
        remaining = [g for g in games if g['game_id'] not in decided]
        if decided:
            log.info(f'  {bot.key}: {len(decided)} game(s) already decided this week — skipping re-pick')
        if not remaining:
            log.info(f'  {bot.key}: nothing new to decide this slot')
            continue

        bot_ctx = build_context(conn, remaining)
        picks = bot.generate(bot_ctx)
        n_picks, n_fades = persist_picks_and_fades(conn, bot, bot_ctx, picks, now_utc)
        picks_by_bot[bot.key] = picks
        fades_by_bot[bot.key] = n_fades
        log.info(f'  {bot.key}: {n_picks} pick(s), {n_fades} fade(s)')

    game_lookup = {g['game_id']: g for g in games}
    try:
        send_slot_digest(slot, picks_by_bot, fades_by_bot, game_lookup)
    except Exception as e:
        log.warning(f'  notify failed: {e}')

    return {'games': len(games), 'picks_by_bot': {k: len(v) for k, v in picks_by_bot.items()}}


def run_tuesday_grade(conn, log: logging.Logger) -> dict:
    grade_summary = grade_pending(conn)
    clv_summary = compute_clv(conn)
    log.info(
        f'  Grader: {grade_summary["rec_graded"]} recs graded '
        f'(W={grade_summary["rec_counts"]["win"]} L={grade_summary["rec_counts"]["loss"]} '
        f'P={grade_summary["rec_counts"].get("push",0)} V={grade_summary["rec_counts"].get("void",0)}), '
        f'{grade_summary["bets_graded"]} bets graded'
    )
    log.info(
        f'  CLV: recs filled={clv_summary["rec_filled"]} skipped={clv_summary["rec_skipped"]}, '
        f'bets filled={clv_summary["bet_filled"]} skipped={clv_summary["bet_skipped"]}'
    )

    # Advance to next week for the following slots, once this week is graded.
    season, week = current_season_week(conn)
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    set_setting(conn, 'nfl_current_week', week + 1, now_utc)
    log.info(f'  Advanced nfl_current_week: {week} -> {week + 1}')

    return {'grade': grade_summary, 'clv': clv_summary, 'advanced_to_week': week + 1}


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) != 2 or sys.argv[1] not in VALID_SLOTS:
        print(f'Usage: python run_slot.py <slot>')
        print(f'Valid slots: {", ".join(VALID_SLOTS)}')
        sys.exit(1)

    slot = sys.argv[1]
    now = datetime.now(timezone.utc)
    date_str = now.strftime('%Y-%m-%d')
    log = setup_slot_logger(slot, date_str)

    log.info('=' * 60)
    log.info(f'NFL orchestrator — slot: {slot.upper()}  |  {now.strftime("%Y-%m-%d %H:%M:%S UTC")}')
    log.info('=' * 60)

    ok = True
    with get_connection() as conn:
        if slot == 'tuesday_research':
            result = run_tuesday_research(conn, log)
            ok = all(result.values())
        elif slot == 'tuesday_grade':
            run_tuesday_grade(conn, log)
        else:
            run_lock_slot(slot, conn, log)

    log.info('-' * 60)
    log.info(f'Slot {slot.upper()} finished — {"OK" if ok else "SOME FAILURES"}')
    log.info('=' * 60)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
