"""Write run_dashboard.bat with CRLF line endings."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
bat = root / 'run_dashboard.bat'

lines = [
    '@echo off',
    'cd /d "%~dp0"',
    '',
    'echo [1/3] Clearing pycache...',
    'venv\\Scripts\\python.exe scripts\\clear_pycache.py',
    '',
    'echo [2/3] Running smoke test...',
    'venv\\Scripts\\python.exe scripts\\smoke_test.py',
    'if errorlevel 1 (',
    '    echo.',
    '    echo *** SMOKE TEST FAILED -- dashboard NOT launched ***',
    '    echo Fix the errors above, then re-run run_dashboard.bat',
    '    echo.',
    '    pause',
    '    exit /b 1',
    ')',
    '',
    'echo [3/3] Smoke test passed. Launching dashboard...',
    'start "" venv\\Scripts\\streamlit.exe run dashboard\\app.py --browser.gatherUsageStats false',
    'timeout /t 3 /nobreak >nul',
    'start http://localhost:8501',
]

bat.write_bytes(('\r\n'.join(lines)).encode('ascii'))
print(f'Written: {bat}')
