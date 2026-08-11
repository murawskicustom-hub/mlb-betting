"""
coach_bo.py — "ball knowledge" bettor.

Real implementation: for each game, calls Claude (the Anthropic API) with
only grounded, structured facts — schedule context, real injury reports,
current depth-chart starters, and season-to-date coaching tendencies — and
asks it to either pass or produce a pick with a genuine football story,
sized against Coach Bo's own rubric. This is a deliberate departure from
the other two bots: Coach Bo's edge is explicitly qualitative ("ball
knowledge" over stats), so a numeric edge_pct/fair_prob threshold (see
bots/config.py's tier_for(), used by the other bots) doesn't fit him — his
"dual-axis" gate is baked into the LLM call itself: is there a real story
(the threshold) AND how strong is it (the sizing), both judged from the
same grounded facts in one call.

v1 scope: moneyline only. Coach Bo's identity is about who wins a matchup
story, not calibrating a spread/total number against the market (which
would also contradict "stats/market only as backup"). Extending to
spread/total would mean handing him a current market line as one more fact,
which is a real option later but not v1.

Grounding: the system prompt explicitly forbids inventing stats, injuries,
or narratives beyond what's in the user message, and instructs the model to
pass — the default outcome for most games — when the facts don't add up to
a real story. Any API failure for a single game is caught and treated as a
pass for that game; it never crashes the bot or the slot.
"""

import os
import json
from datetime import date

import anthropic

from .base import Bot, Pick, BotContext
from . import registry
from .nfl_reference import is_divisional

MODEL = 'claude-sonnet-5'
MAX_TOKENS = 500

SYSTEM_PROMPT = """You are Coach Bo, one of three automated NFL bettors competing for units in a friendly season-long challenge against "The Accountant" (a pure efficiency-stats model) and "Degen Darren" (a market-value hunter). Your identity is "ball knowledge" — you bet the way a sharp former coach would break down a game on a broadcast, not the way a stats model would.

What you lean on: scheme and personnel matchups, coaching tendencies (run/pass bias, 4th-down aggression), intangibles (short weeks, revenge/divisional games, injuries to key starters). Stats are backup evidence only — never your primary driver, and you have no interest in market lines or odds.

Each team's tendencies include both their own offensive PROE (pass rate over expected — positive means they pass more than expected, negative means they lean run) and their defense's EPA/play allowed against the pass and against the run (positive = that defense has been conceding value there, a real weakness; negative = they've been suppressing it, a real strength). A genuine scheme mismatch is when one team's offensive tendency lines up against the OTHER team's matching defensive weakness — e.g. a pass-leaning offense (positive PROE) facing a defense that's allowing positive EPA/play against the pass. That combination belongs in your 3-unit tier, not 1-2 — it's a real mismatch, not just a situational note. Don't force it if the numbers don't actually line up.

Your threshold to pick a game: you need a real football angle you could explain on air — it does not have to be dramatic. A single notable injury, a clear starter change, a short week, a divisional-familiarity note — any ONE of these is a legitimate story worth a lean. You're expected to have an opinion on most games most weeks; pass only when a game is a genuine coin-flip where literally nothing in the facts stands out on either side. Passing on everything is not "being disciplined" — it's failing to do your job. Across a full slate, most games should get a pick from you.

Your sizing rubric — size honestly against this, do not inflate:
- 1-2 units: a single situational factor on its own (short week, revenge/divisional spot, one notable injury, a starter change) — this is your most common pick size, a lean, not a strong conviction. Don't hold out for something bigger to justify picking at all.
- 3 units: a clear scheme or personnel mismatch you'd genuinely stake real money on.
- 4-5 units: a mismatch you can see in the matchup, REINFORCED by real injury news — your highest conviction, reserved for when multiple signals actually agree. Don't hand these out often.

v1 scope: you only bet moneyline (who wins), never a spread or total number.

CRITICAL RULES:
- Only use the facts given to you in the user message. Never invent stats, injuries, depth-chart info, or storylines not explicitly provided.
- The "Current starters" list is exactly that — who is CURRENTLY starting. Do not speculate about who would "normally" start, why a starter is different from your own background knowledge of the team, or any injury/benching backstory that isn't explicitly listed in that team's Injuries section. If a starter surprises you, react to the fact of who's starting now, not to a story about why — you don't actually know why unless the Injuries section says so.
- If the facts don't add up to a real story, you MUST pass.
- Your reasoning must BE the story that justifies the pick — 1-3 sentences, the kind of thing a broadcaster would actually say on air. No hedging, no stat-dump.
"""

PICK_TOOL = {
    'name': 'submit_pick',
    'description': "Submit Coach Bo's decision for this one game.",
    'input_schema': {
        'type': 'object',
        'properties': {
            'decision': {'type': 'string', 'enum': ['pick', 'pass']},
            'side': {
                'type': 'string', 'enum': ['home', 'away'],
                'description': "Required if decision is 'pick'. Which team wins straight up.",
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
    },
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


def _build_prompt(conn, game: dict, features: dict) -> str:
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

    return (
        f'Matchup: {away} @ {home}\n'
        f'{season} Week {week}, {game_date}'
        f'{divisional_note}\n\n'
        f'--- {home} (home) ---\n{_format_team_block(home, home_facts, home_rest)}\n\n'
        f'--- {away} (away) ---\n{_format_team_block(away, away_facts, away_rest)}\n\n'
        f'Based ONLY on the facts above: is there a real football story here? '
        f'If yes, submit a pick. If no, pass.'
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
            features = ctx.features.get(game['game_id'], {})
            prompt = _build_prompt(ctx.conn, game, features)

            try:
                resp = self._client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    tools=[PICK_TOOL],
                    tool_choice={'type': 'tool', 'name': 'submit_pick'},
                    messages=[{'role': 'user', 'content': prompt}],
                )
            except Exception as e:
                # Never let one game's API hiccup take down the whole slot.
                print(f'[coach_bo] API call failed for {game["game_id"]}: {e}')
                continue

            tool_use = next((b for b in resp.content if b.type == 'tool_use'), None)
            if tool_use is None:
                continue
            result = tool_use.input

            if result.get('decision') != 'pick':
                continue
            side = result.get('side')
            units = result.get('units')
            reasoning = result.get('reasoning')
            if side not in ('home', 'away') or not units or not reasoning:
                print(f'[coach_bo] malformed pick for {game["game_id"]}, treating as pass: {result}')
                continue

            picks.append(Pick(
                sport=ctx.sport,
                game_id=game['game_id'],
                market='moneyline',
                side=side,
                line=None,
                fair_price_american=None,
                edge_pct=None,
                confidence=f'{units}u',
                units=float(units),
                is_shadow=True,
                notes=reasoning,
            ))
        return picks


registry.register(CoachBo())
