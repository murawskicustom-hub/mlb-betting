@echo off
cd /d "%~dp0"
start "" venv\Scripts\streamlit.exe run dashboard\app.py --browser.gatherUsageStats false
timeout /t 3 /nobreak >nul
start http://localhost:8501
