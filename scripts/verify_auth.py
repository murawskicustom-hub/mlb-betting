"""
verify_auth.py — prove both password gates block every page independently.

Uses Streamlit's AppTest to run each page headlessly and assert:
  1. With APP_PASSWORD configured but NOT authenticated, EVERY page (incl.
     deep-linked sub-pages) halts at the login screen and renders no real content.
  2. A wrong main password is rejected and does not authenticate.
  3. The correct main password authenticates and lets non-admin pages render.
  4. With APP_PASSWORD missing, the page shows a config error (no default).
  5. Admin-only pages (My Bets, Settings) stay gated behind a SECOND password
     (ADMIN_PASSWORD) even after the main login succeeds; non-admin pages do not.
  6. Wrong/correct/missing ADMIN_PASSWORD behave the same way as the main gate.

Run: python scripts/verify_auth.py   (exits non-zero on any failure)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dashboard'))

from streamlit.testing.v1 import AppTest

DASH = Path(__file__).resolve().parents[1] / 'dashboard'
PAGES = [
    ('Home (app.py)',   DASH / 'app.py'),
    ('This Week',       DASH / 'pages' / '1_This_Week.py'),
    ('Performance',     DASH / 'pages' / '2_Performance.py'),
    ('Coach Bo',        DASH / 'pages' / '3_Coach_Bo.py'),
    ('The Accountant',  DASH / 'pages' / '4_The_Accountant.py'),
    ('Degen Darren',    DASH / 'pages' / '5_Degen_Darren.py'),
    ('My Bets',         DASH / 'pages' / '6_My_Bets.py'),
    ('Settings',        DASH / 'pages' / '7_Settings.py'),
]
ADMIN_PAGES = {'My Bets', 'Settings'}

PASSWORD = 'correct horse battery staple'
ADMIN_PASSWORD = 'admin horse battery staple'

failures = []


def _authed(at):
    return ('authenticated' in at.session_state
            and at.session_state['authenticated'] is True)


def _is_admin(at):
    return ('is_admin' in at.session_state
            and at.session_state['is_admin'] is True)


def _login_shown(at):
    return any('Enter password to continue' in (m.value or '') for m in at.markdown)


def _admin_gate_shown(at):
    return any('Admin only' in (m.value or '') for m in at.markdown)


def check(name, cond):
    print(f'  {"OK  " if cond else "FAIL"} {name}')
    if not cond:
        failures.append(name)


# 1. Every page blocks when not authenticated -----------------------------------
print('--- (1) every page gated when NOT authenticated ---')
for label, path in PAGES:
    at = AppTest.from_file(str(path), default_timeout=30)
    at.secrets['APP_PASSWORD'] = PASSWORD
    at.secrets['ADMIN_PASSWORD'] = ADMIN_PASSWORD
    at.run()
    gated = (
        not at.exception
        and not _authed(at)
        and _login_shown(at)
        and len(at.text_input) == 1          # only the password field rendered
    )
    check(f'{label}: blocked at login, no content', gated)

# 2. Wrong password rejected ----------------------------------------------------
print('\n--- (2) wrong main password rejected ---')
at = AppTest.from_file(str(PAGES[0][1]), default_timeout=30)
at.secrets['APP_PASSWORD'] = PASSWORD
at.run()
at.text_input[0].set_value('not the password').run()
at.button[0].click().run()
check('wrong password -> not authenticated',
      not _authed(at))
check('wrong password -> error shown',
      any('Incorrect password' in (e.value or '') for e in at.error))

# 3. Correct password authenticates non-admin pages ------------------------------
print('\n--- (3) correct main password unlocks non-admin pages ---')
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

# 5. Admin-only pages stay gated behind a second password ------------------------
print('\n--- (5) admin-only pages require ADMIN_PASSWORD even after main login ---')
for label, path in PAGES:
    at = AppTest.from_file(str(path), default_timeout=30)
    at.secrets['APP_PASSWORD'] = PASSWORD
    at.secrets['ADMIN_PASSWORD'] = ADMIN_PASSWORD
    at.session_state['authenticated'] = True
    at.run()
    if label in ADMIN_PAGES:
        check(f'{label}: admin gate shown (not yet unlocked)',
              not at.exception and not _is_admin(at) and _admin_gate_shown(at))
    else:
        check(f'{label}: renders without admin gate',
              not at.exception and not _admin_gate_shown(at))

# 6. Wrong/correct/missing admin password behave like the main gate --------------
print('\n--- (6) admin password wrong/correct/missing ---')
admin_page = next(p for label, p in PAGES if label == 'My Bets')

at = AppTest.from_file(str(admin_page), default_timeout=30)
at.secrets['APP_PASSWORD'] = PASSWORD
at.secrets['ADMIN_PASSWORD'] = ADMIN_PASSWORD
at.session_state['authenticated'] = True
at.run()
at.text_input[0].set_value('not the admin password').run()
at.button[0].click().run()
check('wrong admin password -> not admin', not _is_admin(at))
check('wrong admin password -> error shown',
      any('Incorrect admin password' in (e.value or '') for e in at.error))

at = AppTest.from_file(str(admin_page), default_timeout=30)
at.secrets['APP_PASSWORD'] = PASSWORD
at.secrets['ADMIN_PASSWORD'] = ADMIN_PASSWORD
at.session_state['authenticated'] = True
at.run()
at.text_input[0].set_value(ADMIN_PASSWORD).run()
at.button[0].click().run()
check('correct admin password -> is_admin', _is_admin(at))
check('correct admin password -> admin gate gone', not _admin_gate_shown(at))

at = AppTest.from_file(str(admin_page), default_timeout=30)
at.secrets['APP_PASSWORD'] = PASSWORD
at.session_state['authenticated'] = True
at.run()  # no ADMIN_PASSWORD secret set
check('missing ADMIN_PASSWORD -> not admin', not _is_admin(at))
check('missing ADMIN_PASSWORD -> config error shown',
      any('ADMIN_PASSWORD is not configured' in (e.value or '') for e in at.error))

print('\n' + '=' * 55)
if failures:
    print('AUTH VERIFICATION FAILED:')
    for f in failures:
        print('  -', f)
    sys.exit(1)
print('AUTH VERIFICATION PASSED — all pages gated, wrong/correct/missing handled.')
print('=' * 55)
