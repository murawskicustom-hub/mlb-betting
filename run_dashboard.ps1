Push-Location $PSScriptRoot
Start-Process ".\venv\Scripts\streamlit.exe" -ArgumentList "run dashboard\app.py --browser.gatherUsageStats false"
Start-Sleep -Seconds 3
Start-Process "http://localhost:8501"
Pop-Location
