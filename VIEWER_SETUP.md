# Set up Portfolio Pulse — no coding, no terminal, just clicks

Get free 24/7 alerts on Telegram for every stock you own: exchange filings
(orders, results, dividends), verified news, and death/golden-cross signals.
Total cost: **₹0/month**. Time: **~25 minutes**, all in your browser and phone.

You'll create 3 free accounts (GitHub, Supabase, Telegram bot) — think of them
as: the engine, the memory, and the messenger. Your stock data stays entirely
in YOUR accounts. Nobody else — including the maker of this tool — can see it.

> Not investment advice. This tool reports public facts about stocks you choose.

---

## Step 1 — Get your own copy of the engine (5 min)

1. Create a free account at **github.com** (just email + password)
2. Open this project's page → click **Fork** (top right) → **Create fork**.
   You now own a copy of the entire system.
3. In YOUR fork, click the **Actions** tab → press the green
   **"I understand my workflows, go ahead and enable them"** button.
4. Click each of **fast-poll**, **dma-scan**, and **morning-auth** in the left
   sidebar — if any shows a *"This workflow was disabled"* banner, click
   **Enable workflow** (no banner = already on).
5. **About the first day:** when the setup-check in Step 5 passes, it starts
   the automatic polling immediately (you'll see a run named **pulse-loop**
   working in the Actions tab — that's normal, it's the engine idling between
   polls). No waiting, nothing else to press. The **Run workflow** button on
   *fast-poll* remains available for an instant on-demand poll any time.

## Step 2 — Create the memory (5 min)

1. Sign up free at **supabase.com** → **New project**
   (any name; region *Mumbai* if offered; set any strong password and forget it)
2. Left sidebar → **SQL Editor** → open the file `migrations/supabase_schema.sql`
   from your fork (view it on GitHub, click "Copy raw file") → paste → **Run**.
   "Success. No rows returned" = perfect.
3. Collect two values (keep the tab open, you'll paste them in Step 4):
   - **Project Settings (gear) → General** → copy the **Project ID**.
     Your URL is: `https://<that-id>.supabase.co`
   - **Project Settings → API Keys → "Legacy anon, service_role" tab** →
     reveal + copy the **service_role** key (the one marked *secret* —
     NOT the "anon" one)

## Step 3 — Create the messenger (5 min, on your phone)

1. In Telegram, search **@BotFather** → send `/newbot` → give it any name,
   then any username ending in `bot`. **Copy the token** it replies with.
2. Search **@userinfobot** → send it anything → **copy your numeric id**.
3. Open your new bot's chat and **press START** (important!).

## Step 4 — Give the engine its keys (5 min)

In your GitHub fork: **Settings → Secrets and variables → Actions →
New repository secret**. Add these four, one at a time (exact names):

| Name | Value |
|---|---|
| `SUPABASE_URL` | `https://<your-project-id>.supabase.co` |
| `SUPABASE_KEY` | the service_role key |
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | from userinfobot |

## Step 5 — Press the "did I do it right?" button (2 min)

**Actions tab → setup-check → Run workflow → Run workflow.**
Wait ~1 minute. If everything's right, **your bot messages you
"🎉 Setup complete"**. If not, open the run — it says exactly which piece to
fix, in plain English. Fix it, run again.

## Step 6 — Add your stocks (2 min, on your phone)

- **Zerodha user?** Send `/connect` to your bot → tap the "Login with Kite"
  link it returns (tap it right away — it expires in minutes) → send `/sync`.
  Your holdings import automatically.
- **Any other broker (or no broker)?** Just send `/add RELIANCE`,
  `/add tata motors`, etc. Same alerts, you type the list once.

**That's it.** The system now runs itself — every ~10 minutes, 8 AM to
midnight IST. First alerts arrive whenever your companies next file something.

---

### Good to know
- **The engine polls every ~10 minutes** (8 AM–midnight IST) — the setup-check
  starts a self-sustaining loop that keeps this cadence automatically. Replies
  and alerts arrive within one cycle; nothing is ever lost, only batched.
- **Getting updates later:** when this project improves, your fork shows a
  "Sync fork" button on its main page — one click brings the new version in.
  You never need to touch a terminal or any access token for this.
- Send `/help` to your bot for all commands (`/list`, `/remove`, `/newlist`,
  `/holdings`, `/dma`…).
- **Zerodha sessions expire every morning** (SEBI rule) — the bot automatically
  sends you a fresh login link. Even if you ignore it, all filing/news/cross
  alerts continue; only the holdings-sync pauses.
- Optional upgrades (still free/cheap): the dashboard below, on-the-dot timing
  (cron-job.org pinger — see SETUP_GUIDE.md), and AI summaries on each alert
  (an Anthropic API key added as an `ANTHROPIC_API_KEY` secret).

---

## Optional — your dashboard (10 min, browser only)

A dark "trading terminal" view of everything: Cross Radar (which stocks are
close to a death/golden cross and how close), P&L per broker, per-stock event
history, and the alert feed. Runs free in the cloud; open it from any device.

1. Go to **share.streamlit.io** → **Continue with GitHub** (same account as
   your fork) → authorize
2. **Create app** → *Deploy a public app from GitHub* →
   - Repository: your fork (`<your-username>/portfolio-pulse`)
   - Branch: `main`
   - Main file path: `portfolio_pulse/dashboard/app.py`
3. Before deploying, open **Advanced settings → Secrets** and paste (with YOUR
   two values — same ones you gave GitHub in Step 4):
   ```toml
   PP_STORE_BACKEND = "supabase"
   SUPABASE_URL = "https://xxxx.supabase.co"
   SUPABASE_KEY = "your service_role key"
   ```
4. **Deploy** → after ~2 minutes you get a permanent link like
   `https://something.streamlit.app` — bookmark it on your phone.

The dashboard only *reads* your database, so it always shows exactly what the
alert engine knows. (Prefer running it on your own computer instead? See the
`Start Dashboard` launchers in the repo — requires Python and a local `.env`;
that's the tinkerer's path.)
