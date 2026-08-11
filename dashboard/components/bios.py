"""
bios.py — static bio content for each bot, shown on their dedicated page.
Not DB-backed; this is narrative content from the 2026 rulebook.
"""

BOT_BIOS = {
    'coach_bo': {
        'tagline': '"Ball knowledge" first — scheme, personnel, and intangibles over raw stats.',
        'leans_on': (
            'Scheme/personnel matchups, coaching tendencies (run/pass bias, 4th-down '
            'aggression), intangibles (short weeks, revenge games, locker room storylines), '
            'injuries to key starters, weather — stats only as backup.'
        ),
        'threshold': (
            'Needs a real football story he could explain on a broadcast — a scheme '
            'mismatch, a personnel edge, a coach who’ll get exploited. No story, no pick. '
            'Doesn’t need heavy stat confirmation, but won’t bet on vibes alone.'
        ),
        'sizing': [
            ('1–2u', 'A single situational factor (short week, revenge spot).'),
            ('3u',        'A clear scheme/personnel mismatch he’d stake real money on.'),
            ('4–5u',  'A mismatch he can see on tape, reinforced by injury news — his highest conviction.'),
        ],
    },
    'the_accountant': {
        'tagline': 'Pure predictive model — bets his own number, indifferent to what the market thinks.',
        'leans_on': (
            'Efficiency metrics (EPA/play, success rate), situational base rates, '
            'injury-adjusted projections. No fandom, no gut, no narrative.'
        ),
        'threshold': (
            'Runs his model on every game and prop market and bets whenever it produces a '
            'confident enough projection — never compares that projection to the market '
            'looking for "value" (that’s Darren’s job). Passes only on a genuine statistical toss-up.'
        ),
        'sizing': [
            ('1u', 'Model leans a side, but the projected margin/probability is thin (near a coin flip).'),
            ('3u', 'A clear, well-supported projected outcome.'),
            ('5u', 'Rare — an unusually lopsided projection where every input agrees.'),
        ],
    },
    'degen_darren': {
        'tagline': 'Value hunter — chases market mispricing and unpriced news across every market.',
        'leans_on': (
            'Line movement (open vs. current), public-vs-sharp betting splits, injury/news '
            'not yet priced in, plus real NFL fandom. Bets the widest variety of markets.'
        ),
        'threshold': (
            'Bets when he thinks the market is wrong or slow — a perceived value gap, or news '
            'the line hasn’t adjusted to yet. Hates seeing red, so he’ll often take a smaller '
            '"safer" edge rather than skip a game — expect the highest pick volume of the three.'
        ),
        'sizing': [
            ('1–2u', 'A soft-number lean.'),
            ('3u',        'Clear line value plus a news edge.'),
            ('5u',        'A screaming, bet-it-before-it-moves spot — rare, since risk-aversion to red keeps him from maxing out often.'),
        ],
    },
}
