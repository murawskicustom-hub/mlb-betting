@echo off
cd /d "%~dp0"

rem Stage 4: dashboard reads/writes Postgres (Neon) so it shows the same data the
rem cloud job writes. SQLite is frozen as the rollback snapshot.
set DB_BACKEND=postgres

echo [1/3] Clearing pycache...
venv\Scripts\python.exe scripts\clear_pycache.py

echo [2/3] Running smoke test...
venv\Scripts\python.exe scripts\smoke_test.py
if errorlevel 1 (
    echo.
    echo *** SMOKE TEST FAILED -- dashboard NOT launched ***
    echo Fix the errors above, then re-run run_dashboard.bat
    echo.
    pause
    exit /b 1
)

echo [3/3] Smoke test passed. Killing any existing Streamlit processes...
taskkill /F /IM streamlit.exe >nul 2>&1
taskkill /F /IM python.exe /FI "COMMANDLINE eq *streamlit*" >nul 2>&1
timeout /t 1 /nobreak >nul

echo [3/3] Launching dashboard...
start "" venv\Scripts\streamlit.exe run dashboard\app.py --browser.gatherUsageStats false
timeout /t 3 /nobreak >nul
start http://localhost:8501