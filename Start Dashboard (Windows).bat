@echo off
REM Double-click to open the Portfolio Pulse dashboard in your browser.
REM Requires Python 3.11+ from python.org (tick "Add python.exe to PATH"
REM during install) and a .env file in this folder (copy .env.example and
REM fill in your Supabase values).
cd /d "%~dp0"
if not exist .venv (
  echo First run - creating environment, this takes a couple of minutes...
  python -m venv .venv
  .venv\Scripts\pip install -q -r requirements.txt
)
echo Starting Portfolio Pulse dashboard - your browser will open shortly.
.venv\Scripts\python -m streamlit run portfolio_pulse\dashboard\app.py
pause
