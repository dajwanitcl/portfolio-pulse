# Setup Guide — Portfolio Pulse

You can run everything locally with just Python (SQLite). Cloud + Telegram +
holdings require a few free accounts. Do them in this order; each step unlocks
more of the system and is independently testable.

## 0. Local install (5 min)
```bash
cd "Portfolio Pulse"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run portfolio_pulse/dashboard/app.py   # opens the dashboard
```
The dashboard runs on SQLite with no credentials. Add symbols under **Watchlist**,
then `python -m portfolio_pulse.jobs.fast_poll` to pull filings/news for them.

## 1. Telegram (10 min) — get alerts on your phone
1. In Telegram, message **@BotFather** → `/newbot` → follow prompts → copy the
   **bot token**.
2. Message **@userinfobot** (or your new bot) to get your numeric **chat id**.
3. Put both in `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
4. Test: `python -c "from portfolio_pulse.notify.telegram import send_message; send_message('Pulse test ✅')"`
5. Commands work once the poller runs: `/add INFY`, `/list`, `/remove INFY`, `/holdings`.

## 2. Anthropic (5 min) — richer, still-guarded summaries
1. Get an API key at console.anthropic.com.
2. `ANTHROPIC_API_KEY=...` in `.env`. Without it, alerts still send using the
   verbatim filing/news headline (never fabricated) — the key just adds concise
   summaries + an impact reading.

## 3. Zerodha connection (2 min) — holdings via MCP, no API app needed
The default broker path is Zerodha's official **Kite MCP server** — zero setup:
```bash
python -m portfolio_pulse.jobs.mcp_sync
```
It prints (and Telegrams, if configured) a **Login with Kite** link. Tap it,
authorise, and your holdings sync into the store — quantities, average prices,
and company names (used to match NSE filings). Re-run any time your portfolio
changes. Access is read-only; order tools are never called.

<details><summary>Alternative: Kite Connect API (optional)</summary>

1. Create an app at **developers.kite.trade** (personal plan; portfolio APIs free).
2. Set the app's Redirect URL to your dashboard URL (local:
   `http://localhost:8501/`, or your Streamlit Cloud URL).
3. `KITE_API_KEY`, `KITE_API_SECRET` in `.env`. When set, the API path takes
   precedence over MCP, and the dashboard/morning-nudge login flow applies.
</details>

> **Why a daily tap?** Kite tokens expire every morning (SEBI rule). Filings, news,
> and DMA alerts keep working even if you skip the login — only holdings auto-sync
> and the price cross-check pause until you re-auth.

### Set-and-forget mode (no daily login at all)
If your holdings rarely change, you don't need the daily tap:
1. Log in once so holdings sync (or skip Kite entirely and `/add` each stock).
2. Set `PP_AUTH_NUDGE=off` (locally in `.env`; on GitHub add a repository
   **variable** `PP_AUTH_NUDGE=off` under Settings → Secrets and variables →
   Actions → Variables) to silence the morning reminder.
3. Everything runs hands-free. DMA alerts show `single source` instead of
   `verified` (no Kite quote to cross-check). When you do change your portfolio,
   just tap the login link on the dashboard once and it re-syncs.

## 4. Supabase (15 min) — shared cloud store for 24/7 operation
1. Create a free project at **supabase.com**.
2. In the SQL editor, paste and run **`migrations/supabase_schema.sql`**.
3. Copy the **Project URL** and the **service_role key** (Settings → API).
4. Set in `.env`: `PP_STORE_BACKEND=supabase`, `SUPABASE_URL`, `SUPABASE_KEY`.
5. Test: `python -m portfolio_pulse.jobs.fast_poll` should write to Supabase.

## 5. GitHub Actions (15 min) — the always-on poller
1. Push this repo to GitHub. **Use a public repo** for unlimited Actions minutes
   (all secrets live in GitHub Secrets, never in code).
2. Settings → Secrets and variables → Actions → add: `SUPABASE_URL`,
   `SUPABASE_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`,
   `KITE_API_KEY`, `KITE_API_SECRET`.
3. The three workflows in `.github/workflows/` run on schedule. Trigger any of
   them manually first via **Actions → (workflow) → Run workflow** to confirm.
4. Watch a run's logs; check Supabase `alerts` and your Telegram.

## 6. Streamlit Community Cloud (10 min) — hosted dashboard
1. At **share.streamlit.io**, deploy `portfolio_pulse/dashboard/app.py` from your repo.
2. In the app's **Secrets**, add the same keys as step 5 (TOML form:
   `SUPABASE_URL = "..."`). Set `PP_STORE_BACKEND = "supabase"`.
3. Update the Kite app's Redirect URL to the deployed dashboard URL.

## Verifying it end-to-end
- **Filings/news**: add a liquid symbol (e.g. `/add RELIANCE`), run `fast_poll`,
  expect a Telegram alert with a source link within a poll cycle when something
  is filed.
- **DMA**: `python -m portfolio_pulse.jobs.dma_scan` (add `force=True` off-hours).
  First run sets a baseline silently; alerts fire on later state transitions.
- **Dashboard**: holdings, DMA status, and the alert feed reflect the store live.
