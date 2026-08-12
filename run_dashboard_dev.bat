@echo off
cd /d "C:\Users\Connor\Documents\mlb-betting"
venv\Scripts\streamlit.exe run dashboard\app.py --browser.gatherUsageStats false --server.headless true
