#!/bin/bash
# Double-click to open the Portfolio Pulse dashboard in your browser.
# Reads the cloud database (Supabase), so it shows the same live state
# the Telegram alerts run on. Close the terminal window to stop it.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run — creating environment (one time, ~2 min)..."
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi
echo "Starting Portfolio Pulse dashboard — your browser will open shortly."
./.venv/bin/python -m streamlit run portfolio_pulse/dashboard/app.py
