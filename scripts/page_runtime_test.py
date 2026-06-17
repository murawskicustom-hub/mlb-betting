"""
page_runtime_test.py -- Execute every dashboard page via Streamlit AppTest.
This runs each page's full Python render path and reports any exceptions.
Requires streamlit >= 1.28.
"""
import sys
from pathlib import Path

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
SCRIPTS_DIR   = PROJECT_ROOT / 'scripts'
DASHBOARD_DIR = PROJECT_ROOT / 'dashboard'

sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(DASHBOARD_DIR))

from streamlit.testing.v1 import AppTest

pages = [
    ('Home (app.py)',    str(DASHBOARD_DIR / 'app.py')),
    ('Today',           str(DASHBOARD_DIR / 'pages' / '1_Today.py')),
    ('Performance',     str(DASHBOARD_DIR / 'pages' / '2_Performance.py')),
    ('My Bets',         str(DASHBOARD_DIR / 'pages' / '3_My_Bets.py')),
    ('Settings',        str(DASHBOARD_DIR / 'pages' / '4_Settings.py')),
]

results = {}
for label, path in pages:
    try:
        at = AppTest.from_file(path, default_timeout=30)
        at.run()
        if at.exception:
            results[label] = 'FAIL -- exception: ' + str(at.exception)
        else:
            # Count rendered elements as a sanity check
            n = len(at.markdown) + len(at.metric) + len(at.dataframe)
            results[label] = 'PASS (' + str(n) + ' elements rendered)'
    except Exception as e:
        results[label] = 'FAIL -- ' + type(e).__name__ + ': ' + str(e)

print()
passed = 0
for label, status in results.items():
    tag = 'PASS' if status.startswith('PASS') else 'FAIL'
    if tag == 'PASS':
        passed += 1
    print('  ' + tag + '  ' + label + '  ' + status)

total = len(results)
failed = total - passed
print()
print('=' * 55)
print(('PASS  All ' + str(total) + ' pages passed.') if failed == 0
      else ('FAIL  ' + str(failed) + '/' + str(total) + ' pages FAILED.'))
print('=' * 55)
sys.exit(0 if failed == 0 else 1)
