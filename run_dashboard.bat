@echo off
cd /d "%~dp0"
venv\Scripts\streamlit.exe run dashboard\app.py --browser.gatherUsageStats false
