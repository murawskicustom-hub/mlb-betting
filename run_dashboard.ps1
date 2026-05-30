Push-Location $PSScriptRoot
& ".\venv\Scripts\streamlit.exe" run dashboard\app.py --browser.gatherUsageStats false
Pop-Location
