"""
coach_bo.py — "ball knowledge" bettor.

Real implementation: for each game, calls Claude (the Anthropic API) with
only grounded, structured facts — schedule context, real injury reports,
current depth-chart starters, season-to-date coaching tendencies, and the
real market spread/total lines — and asks it to decide, independently, on
each of three markets: moneyline, spread, and total. This is a deliberate
departure from the other two bots: Coach Bo's edge is explicitly qualitative
("ball knowledge" over stats), so a numeric edge_pct/fair_prob threshold
(see bots/config.py's tier_for(), used by the other bots) doesn't fit him —
his "dual-axis" gate is baked into the LLM call itself: is there a real
story (the threshold) AND how strong is it (the sizing), both judged from
the same grounded facts, separately for each market.

Reading the market's own spread/total line is not the same as chasing
market value (Degen Darren's job) — it's the actual number a spread/total
bet has to be graded against, the same way a moneyline pick already gets
priced against a target_price_american in scripts/run_slot.py. Coach Bo
still forms his own opinion from football facts, not by comparing to what
the market implies; he just needs the real number to have an opinion ABOUT.
If no market line exists yet for a market, that market is left out of the
call entirely for that game (nothing to bet against, so no story could be
graded even if he had one).

Grounding: the system prompt explicitly forbids inventing stats, injuries,
or narratives beyond what's in the user message, and instructs the model to
pass — the default outcome for most games — when the facts don't add up to
a real story. Any API failure for a single game is caught and treated as a
full pass for that game; it never crashes the bot or the slot.
"""

import os
from datetime import date

import anthropic

from .base import Bot, Pick, BotContext
from . import registry
from .nfl_reference import is_divisional
from .market_lines import consensus_line

MODEL = 'claude-sonnet-5'
MAX_TOKENS = 700

SYSTEM_PROMPT = """You are Coach Bo, one of three automated NFL bettors competing for units in a friendly season-long challenge against "The Accountant" (a pure efficiency-stats model) and "Degen Darren" (a market-value hunter). Your identity is "ball knowledge" — you bet the way a sharp former coach would break down a game on a broadcast, not the way a stats model would.

What you lean on: scheme and personnel matchups, coaching tendencies (run/pass bias, 4th-down aggression), intangibles (short weeks, revenge/divisional games, injuries to key starters). Stats are backup evidence only — never your primary driver, and you have no interest in market value or line-shopping.

Each team's tendencies include both their own offensive PROE (pass rate over expected — positive means they pass more than expected, negative means they lean run) and their defense's EPA/play allowed against the pass and against the run (positive = that defense has been conceding value there, a real weakness; negative = they've been suppressing it, a real strength). A genuine scheme mismatch is when one team's offensive tendency lines up against the OTHER team's matching defensive weakness — e.g. a pass-leaning offense (positive PROE) facing a defense that's allowing positive EPA/play against the pass. That combination belongs in your 3-unit tier, not 1-2 — it's a real mismatch, not just a situational note. Don't force it if the numbers don't actually line up.

You evaluate THREE markets independently for every game, whenever a market's line is given to you: moneyline (who wins outright), spread (who covers the given point spread), and total (whether the combined score goes over or under the given total). These are separate questions, not one opinion applied three ways:
- A strong story can justify all three at once (e.g. a team you think dominates outright AND covers AND the game stays lopsided/high-scoring).
- A modest story might only justify the moneyline, not laying the spread's extra points — "I like them to win, not by that much" is a real, valid outcome: pick moneyline, pass spread.
- A total opinion can stand completely apart from who wins — bad weather, two run-heavy/clock-control teams, or a banged-up secondary funneling to a slow, physical game are total stories that don't require an opinion on the winner at all.
Judge and size each market on its own; do not force a matching decision across all three just because you picked one of them.

Your threshold to pick a market: you need a real football angle you could explain on air — it does not have to be dramatic. A single notable injury, a clear starter change, a short week, a divisional-familiarity note — any ONE of these is a legitimate story worth a lean. You're expected to have an opinion on most games most weeks; pass only when a market is a genuine coin-flip where literally nothing in the facts stands out on either side. Passing on everything is not "being disciplined" — it's failing to do your job. Across a full slate, most games should get at least one market picked.

Your sizing rubric — size honestly against this, do not inflate, and size each market separately:
- 1-2 units: a single situational factor on its own (short week, revenge/divisional spot, one notable injury, a starter change) — this is your most common pick size, a lean, not a strong conviction. Don't hold out for something bigger to justify picking at all.
- 3 units: a clear scheme or personnel mismatch you'd genuinely stake real money on.
- 4-5 units: a mismatch you can see in the matchup, REINFORCED by real injury news — your highest conviction, reserved for when multiple signals actually agree. Don't hand these out often.

CRITICAL RULES:
- Only use the facts given to you in the user message. Never invent stats, injuries, depth-chart info, market lines, or storylines not explicitly provided.
- The "Current starters" list is exactly that — who is CURRENTLY starting. Do not speculate about who would "normally" start, why a starter is different from your own background knowledge of the team, or any injury/benching backstory that isn't explicitly listed in that team's Injuries section. If a starter surprises you, react to the fact of who's starting now, not to a story about why — you don't actually know why unless the Injuries section says so.
- If the facts don't add up to a real story for a given market, you MUST pass that market.
- Each market's reasoning must BE the story that justifies that pick — 1-3 sentences, the kind of thing a broadcaster would actually say on air. No hedging, no stat-dump.
"""


def _market_schema(side_enum: list[str], side_desc: str) -> dict:
    return {
        'type': 'object',
        'properties': {
            'decision': {'type': 'string', 'enum': ['pick', 'pass']},
            'side': {
                'type': 'string', 'enum': side_enum,
                'description': f"Required if decision is 'pick'. {side_desc}",
            },
            'units': {
                'type': 'integer', 'enum': [1, 2, 3, 4, 5],
                'description': "Required if decision is 'pick'. Size per Coach Bo's sizing rubric.",
            },
            'reasoning': {
                'type': 'string',
                'description': "Required if decision is 'pick'. The football story, 1-3 broadcast-explainable sentences.",
            },
        },
        'required': ['decision'],
    }


def _build_pick_tool(has_spread: bool, has_total: bool) -> dict:
    properties = {
        'moneyline': _market_schema(['home', 'away'], 'Which team wins straight up.'),
    }
    required = ['moneyline']
    if has_spread:
        properties['spread'] = _market_schema(['home', 'away'], 'Which team covers the given spread.')
        required.append('spread')
    if has_total:
        properties['total'] = _market_schema(['over', 'under'], 'Whether the combined score goes over or under the given total.')
        required.append('total')
    return {
        'name': 'submit_picks',
        'description': "Submit Coach Bo's decision for this one game, independently for each market given.",
        'input_schema': {'type': 'object', 'properties': properties, 'required': required},
    }


def _days_rest(conn, team: str, season: int, before_date: str) -> int | None:
    """Days since this team's most recent prior game this season, or None
    if there isn't one (Week 1, bye-adjacent, etc.)."""
    row = conn.execute("""
        SELECT game_date FROM games
        WHERE sport = 'nfl' AND season = ? AND game_date < ?
          AND (home_team = ? OR away_team = ?)
        ORDER BY game_date DESC LIMIT 1
    """, (season, before_date, team, team)).fetchone()
    if not row or not row['game_date']:
        return None
    try:
        prev = date.fromisoformat(row['game_date'])
        curr = date.fromisoformat(before_date)
        return (curr - prev).days
    except ValueError:
        return None


def _team_facts(features: dict, team: str) -> dict:
    """Split this game's feature bag into injury/depth/tendency facts for one team."""
    injuries, depth, tendencies = [], {}, {}
    prefix_inj = f'injury:{team}:'
    prefix_depth = f'depth:{team}:'
    prefix_tend = f'tendency:{team}:'
    for key, val in features.items():
        if key.startswith(prefix_inj):
            injuries.append((key[len(prefix_inj):], val))
        elif key.startswith(prefix_depth):
            depth[key[len(prefix_depth):]] = val
        elif key.startswith(prefix_tend):
            tendencies[key[len(prefix_tend):]] = val
    return {'injuries': injuries, 'depth': depth, 'tendencies': tendencies}


def _format_team_block(team: str, facts: dict, rest_days: int | None) -> str:
    lines = [f'{team}:']
    if rest_days is not None:
        short = ' (SHORT WEEK)' if rest_days < 6 else ''
        lines.append(f'  Rest: {rest_days} days since last game{short}')
    else:
        lines.append('  Rest: unknown (likely Week 1 or a bye-adjacent gap)')

    if facts['injuries']:
        lines.append('  Injuries:')
        for name, comment in facts['injuries']:
            lines.append(f'    - {name}: {comment}')
    else:
        lines.append('  Injuries: none reported')

    if facts['depth']:
        starters = ', '.join(f'{pos.upper()}={name}' for pos, name in sorted(facts['depth'].items()))
        lines.append(f'  Current starters: {starters}')

    if facts['tendencies']:
        t = facts['tendencies']
        parts = []
        if 'proe' in t:
            lean = 'pass-leaning' if t['proe'] > 0 else 'run-leaning'
            parts.append(f"offense PROE {t['proe']:+.1f} ({lean})")
        if 'fourth_down_agg_rate' in t:
            parts.append(f"4th-down go-for-it rate {t['fourth_down_agg_rate']:.0%}")
        if 'def_epa_vs_pass' in t:
            parts.append(f"defense allows {t['def_epa_vs_pass']:+.2f} EPA/play vs the pass")
        if 'def_epa_vs_rush' in t:
            parts.append(f"defense allows {t['def_epa_vs_rush']:+.2f} EPA/play vs the run")
        if parts:
            lines.append(f'  Tendencies (season-to-date, backup context only): {", ".join(parts)}')

    return '\n'.join(lines)


def _build_prompt(conn, game: dict, features: dict, home_spread, total_line) -> str:
    home, away = game['home_team'], game['away_team']
    season, week = game.get('season'), game.get('week')
    game_date = game.get('game_date', '')

    home_facts = _team_facts(features, home)
    away_facts = _team_facts(features, away)
    home_rest = _days_rest(conn, home, season, game_date) if game_date else None
    away_rest = _days_rest(conn, away, season, game_date) if game_date else None

    divisional_note = ''
    if is_divisional(home, away):
        divisional_note = '\nThis is a DIVISIONAL matchup — familiarity/revenge-game context applies.'

    market_lines = []
    if home_spread is not None:
        market_lines.append(f'Spread: {home} {home_spread:+g} (negative = home favored by that many)')
    if total_line is not None:
        market_lines.append(f'Total: {total_line:g}')
    market_block = ('\n\nMarket lines (the real numbers you would be betting against, if you have a story for '
                     'them — you are not comparing to what the market implies, you just need the actual number):\n'
                     + '\n'.join(market_lines)) if market_lines else ''

    return (
        f'Matchup: {away} @ {home}\n'
        f'{season} Week {week}, {game_date}'
        f'{divisional_note}\n\n'
        f'--- {home} (home) ---\n{_format_team_block(home, home_facts, home_rest)}\n\n'
        f'--- {away} (away) ---\n{_format_team_block(away, away_facts, away_rest)}'
        f'{market_block}\n\n'
        f'Based ONLY on the facts above: decide moneyline'
        f'{", spread" if home_spread is not None else ""}'
        f'{", and total" if total_line is not None else ""} independently.'
    )


class CoachBo(Bot):
    key = 'coach_bo'
    display_name = 'Coach Bo'
    sports = ('nfl',)

    def __init__(self):
        api_key = os.getenv('ANTHROPIC_API_KEY')
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else None

    def generate(self, ctx: BotContext) -> list[Pick]:
        if self._client is None:
            # No key configured — behave like a full-slate fade rather than crash,
            # same posture as the other bots' original stubs before real logic existed.
            return []

        picks: list[Pick] = []
        for game in ctx.games:
            game_id = game['game_id']
            features = ctx.features.get(game_id, {})
            home_spread = consensus_line(ctx, game_id, 'spread', 'home')
            total_line = consensus_line(ctx, game_id, 'total', 'over')

            prompt = _build_prompt(ctx.conn, game, features, home_spread, total_line)
            tool = _build_pick_tool(has_spread=home_spread is not None, has_total=total_line is not None)

            try:
                resp = self._client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    tools=[tool],
                    tool_choice={'type': 'tool', 'name': 'submit_picks'},
                    messages=[{'role': 'user', 'content': prompt}],
                )
            except Exception as e:
                # Never let one game's API hiccup take down the whole slot.
                print(f'[coach_bo] API call failed for {game_id}: {e}')
                continue

            tool_use = next((b for b in resp.content if b.type == 'tool_use'), None)
            if tool_use is None:
                continue
            result = tool_use.input

            ml = result.get('moneyline') or {}
            if ml.get('decision') == 'pick':
                side, units, reasoning = ml.get('side'), ml.get('units'), ml.get('reasoning')
                if side in ('home', 'away') and units and reasoning:
                    picks.append(Pick(
                        sport=ctx.sport, game_id=game_id, market='moneyline', side=side,
                        line=None, fair_price_american=None, edge_pct=None,
                        confidence=f'{units}u', units=float(units), is_shadow=True, notes=reasoning,
                    ))
                else:
                    print(f'[coach_bo] malformed moneyline pick for {game_id}, treating as pass: {ml}')

            sp = result.get('spread') or {}
            if home_spread is not None and sp.get('decision') == 'pick':
                side, units, reasoning = sp.get('side'), sp.get('units'), sp.get('reasoning')
                if side in ('home', 'away') and units and reasoning:
                    picks.append(Pick(
                        sport=ctx.sport, game_id=game_id, market='spread', side=side,
                        line=home_spread if side == 'home' else -home_spread,
                        fair_price_american=None, edge_pct=None,
                        confidence=f'{units}u', units=float(units), is_shadow=True, notes=reasoning,
                    ))
                else:
                    print(f'[coach_bo] malformed spread pick for {game_id}, treating as pass: {sp}')

            tot = result.get('total') or {}
            if total_line is not None and tot.get('decision') == 'pick':
                side, units, reasoning = tot.get('side'), tot.get('units'), tot.get('reasoning')
                if side in ('over', 'under') and units and reasoning:
                    picks.append(Pick(
                        sport=ctx.sport, game_id=game_id, market='total', side=side,
                        line=total_line, fair_price_american=None, edge_pct=None,
                        confidence=f'{units}u', units=float(units), is_shadow=True, notes=reasoning,
                    ))
                else:
                    print(f'[coach_bo] malformed total pick for {game_id}, treating as pass: {tot}')

        return picks


registry.register(CoachBo())
