"""
verify_auth.py — prove the password gate blocks every page independently.

Uses Streamlit's AppTest to run each page headlessly and assert:
  1. With the secret configured but NOT authenticated, EVERY page (incl. deep-
     linked sub-pages) halts at the login screen and renders no real content.
  2. A wrong password is rejected and does not authenticate.
  3. The correct password authenticates and lets the page render.
  4. With APP_PASSWORD missing, the page shows a config error (no default).

Run: python scripts/verify_auth.py   (exits non-zero on any failure)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dashboard'))

from streamlit.testing.v1 import AppTest

DASH = Path(__file__).resolve().parents[1] / 'dashboard'
PAGES = [
    ('Home (app.py)', DASH / 'app.py'),
    ('Today',         DASH / 'pages' / '1_Today.py'),
    ('Performance',   DASH / 'pages' / '2_Performance.py'),
    ('My Bets',       DASH / 'pages' / '3_My_Bets.py'),
    ('Settings',      DASH / 'pages' / '4_Settings.py'),
]
PASSWORD = 'correct horse battery staple'

failures = []


def _authed(at):
    return ('authenticated' in at.session_state
            and at.session_state['authenticated'] is True)


def _login_shown(at):
    return any('Enter password to continue' in (m.value or '') for m in at.markdown)


def check(name, cond):
    print(f'  {"OK  " if cond else "FAIL"} {name}')
    if not cond:
        failures.append(name)


# 1. Every page blocks when not authenticated -----------------------------------
print('--- (1) every page gated when NOT authenticated ---')
for label, path in PAGES:
    at = AppTest.from_file(str(path), default_timeout=30)
    at.secrets['APP_PASSWORD'] = PASSWORD
    at.run()
    gated = (
        not at.exception
        and not _authed(at)
        and _login_shown(at)
        and len(at.text_input) == 1          # only the password field rendered
    )
    check(f'{label}: blocked at login, no content', gated)

# 2. Wrong password rejected ----------------------------------------------------
print('\n--- (2) wrong password rejected ---')
at = AppTest.from_file(str(PAGES[0][1]), default_timeout=30)
at.secrets['APP_PASSWORD'] = PASSWORD
at.run()
at.text_input[0].set_value('not the password').run()
at.button[0].click().run()
check('wrong password -> not authenticated',
      not _authed(at))
check('wrong password -> error shown',
      any('Incorrect password' in (e.value or '') for e in at.error))

# 3. Correct password authenticates ---------------------------------------------
print('\n--- (3) correct password unlocks ---')
at = AppTest.from_file(str(PAGES[0][1]), default_timeout=30)
at.secrets['APP_PASSWORD'] = PASSWORD
at.run()
at.text_input[0].set_value(PASSWORD).run()
at.button[0].click().run()
check('correct password -> authenticated',
      _authed(at))
check('correct password -> login screen gone', not _login_shown(at))

# 4. Missing secret -> config error, no default ---------------------------------
print('\n--- (4) missing APP_PASSWORD -> config error ---')
at = AppTest.from_file(str(PAGES[0][1]), default_timeout=30)
at.run()  # no secret set
check('missing secret -> not authenticated',
      not _authed(at))
check('missing secret -> config error shown',
      any('APP_PASSWORD is not configured' in (e.value or '') for e in at.error))

print('\n' + '=' * 55)
if failures:
    print('AUTH VERIFICATION FAILED:')
    for f in failures:
        print('  -', f)
    sys.exit(1)
print('AUTH VERIFICATION PASSED — all pages gated, wrong/correct/missing handled.')
print('=' * 55)
