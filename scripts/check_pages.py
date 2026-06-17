"""Quick HTTP status check for all dashboard pages."""
import urllib.request
import sys

base = 'http://localhost:8501'
pages = [
    ('Home (app.py)',    base + '/'),
    ('Today',           base + '/Today'),
    ('Performance',     base + '/Performance'),
    ('My_Bets',         base + '/My_Bets'),
    ('Settings',        base + '/Settings'),
    ('health',          base + '/healthz'),
]

failed = 0
for label, url in pages:
    try:
        resp = urllib.request.urlopen(url, timeout=8)
        body = resp.read(4096).decode('utf-8', errors='ignore')
        bad = 'Internal Server Error' in body or 'StreamlitAPIException' in body
        tag = 'ERROR' if bad else 'OK'
        print('  ' + str(resp.status) + '  ' + tag + '  ' + label)
        if bad:
            failed += 1
    except Exception as e:
        print('  FAIL  ' + label + '  -- ' + str(e))
        failed += 1

print()
print('Result: ' + ('ALL OK' if failed == 0 else str(failed) + ' FAILED'))
sys.exit(0 if failed == 0 else 1)
