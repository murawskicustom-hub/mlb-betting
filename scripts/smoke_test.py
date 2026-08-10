"""
smoke_test.py -- Import-level regression test for the dashboard.

Run after any change touching dashboard/ or scripts/:
    python scripts/smoke_test.py

Exit code 0 = all PASS. Exit code 1 = at least one FAIL.
"""

import sys
import ast
import importlib
import shutil
import traceback
from pathlib import Path

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
SCRIPTS_DIR   = PROJECT_ROOT / 'scripts'
DASHBOARD_DIR = PROJECT_ROOT / 'dashboard'

# Purge all __pycache__ before any imports so the test and any subsequent
# server restart both compile bytecode from the current source files.
# This is the only reliable way to prevent stale .pyc from giving a false PASS
# while the live server loads an older version of the same module.
_cleared = 0
for _d in PROJECT_ROOT.rglob('__pycache__'):
    shutil.rmtree(_d, ignore_errors=True)
    _cleared += 1
print(f'[smoke_test] cleared {_cleared} __pycache__ director{"ies" if _cleared != 1 else "y"}')

sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(DASHBOARD_DIR))

results = {}


def check(label, fn):
    try:
        fn()
        results[label] = True
        print('  PASS  ' + label)
    except Exception:
        results[label] = False
        print('  FAIL  ' + label)
        for line in traceback.format_exc().splitlines()[-5:]:
            print('        ' + line)


# ------------------------------------------------------------------
# 1. database init
# ------------------------------------------------------------------
print('\n--- database ---')
check('init_db()', lambda: __import__('database').init_db())


# ------------------------------------------------------------------
# 2. component modules
# ------------------------------------------------------------------
print('\n--- components ---')
for mod in ('components.metrics', 'components.styles',
            'components.formatters', 'components.nav_check'):
    check('import ' + mod, lambda m=mod: importlib.import_module(m))


# ------------------------------------------------------------------
# 3. scripts imported by pages / the orchestrator
# ------------------------------------------------------------------
print('\n--- scripts (used by dashboard / orchestrator) ---')
# run_slot.py is intentionally excluded here: it sets DB_BACKEND=postgres as a
# module-level side effect (via os.environ.setdefault), which would leak into
# every check that runs after it in this same process. It's exercised directly
# (python scripts/run_slot.py <slot>) instead.
for mod in ('devig', 'consensus', 'grader', 'clv_calculator', 'notify'):
    check('import ' + mod, lambda m=mod: importlib.import_module(m))

print('\n--- bots ---')
sys.path.insert(0, str(PROJECT_ROOT))
for mod in ('bots.base', 'bots.registry', 'bots.config',
            'bots.coach_bo', 'bots.the_accountant', 'bots.degen_darren'):
    check('import ' + mod, lambda m=mod: importlib.import_module(m))


# ------------------------------------------------------------------
# 4. Name-level audit
# ------------------------------------------------------------------
print('\n--- name-level import audit ---')

dashboard_files = [
    DASHBOARD_DIR / 'app.py',
    DASHBOARD_DIR / 'pages' / '1_This_Week.py',
    DASHBOARD_DIR / 'pages' / '2_Performance.py',
    DASHBOARD_DIR / 'pages' / '3_My_Bets.py',
    DASHBOARD_DIR / 'pages' / '4_Settings.py',
]

_mod_cache = {}

def _load_mod(mod_name):
    if mod_name not in _mod_cache:
        try:
            _mod_cache[mod_name] = importlib.import_module(mod_name)
        except Exception:
            _mod_cache[mod_name] = None
    return _mod_cache[mod_name]

target_prefixes = (
    'components.', 'database', 'settings',
    'devig', 'consensus', 'grader', 'clv_calculator',
)

for page_path in dashboard_files:
    src  = page_path.read_text(encoding='utf-8')
    tree = ast.parse(src)
    page_label = page_path.relative_to(PROJECT_ROOT).as_posix()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        mod_name = node.module or ''
        if not any(mod_name.startswith(p) for p in target_prefixes):
            continue
        mod = _load_mod(mod_name)
        if mod is None:
            check(
                page_label + ': import ' + mod_name,
                lambda mn=mod_name: (_ for _ in ()).throw(
                    ImportError('module ' + mn + ' failed to load')),
            )
            continue
        for alias in node.names:
            name  = alias.name
            label = page_label + ': from ' + mod_name + ' import ' + name
            check(label, lambda m=mod, n=name: getattr(m, n))


# ------------------------------------------------------------------
# 5. Summary
# ------------------------------------------------------------------
total  = len(results)
passed = sum(1 for v in results.values() if v)
failed = total - passed

# ------------------------------------------------------------------
# 5. Page runtime execution via AppTest
# ------------------------------------------------------------------
print('\n--- page runtime (AppTest) ---')

try:
    from streamlit.testing.v1 import AppTest

    page_files = [
        ('Home (app.py)',    DASHBOARD_DIR / 'app.py'),
        ('This Week',       DASHBOARD_DIR / 'pages' / '1_This_Week.py'),
        ('Performance',     DASHBOARD_DIR / 'pages' / '2_Performance.py'),
        ('My Bets',         DASHBOARD_DIR / 'pages' / '3_My_Bets.py'),
        ('Settings',        DASHBOARD_DIR / 'pages' / '4_Settings.py'),
    ]
    for label, path in page_files:
        def _run(p=path, lbl=label):
            at = AppTest.from_file(str(p), default_timeout=30)
            # Pre-authenticate past the password gate so the full page executes
            # (auth itself is covered separately by verify_auth.py).
            at.secrets['APP_PASSWORD'] = 'smoketest'
            at.session_state['authenticated'] = True
            at.run()
            if at.exception:
                raise RuntimeError('page raised: ' + str(at.exception))
        check('AppTest: ' + label, _run)

except ImportError:
    print('  SKIP  AppTest not available in this Streamlit version')


# ------------------------------------------------------------------
# 6. Summary
# ------------------------------------------------------------------
total  = len(results)
passed = sum(1 for v in results.values() if v)
failed = total - passed

print('\n' + '=' * 55)
if failed == 0:
    print('  PASS  All ' + str(total) + ' checks passed.')
else:
    print('  FAIL  ' + str(failed) + '/' + str(total) + ' checks FAILED.')
print('=' * 55)

sys.exit(0 if failed == 0 else 1)
