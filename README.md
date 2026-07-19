# Portfolio Pulse

A 24/7 personal monitor for your **Zerodha (NSE)** portfolio. For every stock you
hold or watch, it tracks **NSE corporate filings** and **verified news**, detects
**50/200-DMA death & golden crosses**, and pushes source-cited alerts to
**Telegram**. A **Streamlit dashboard** is the companion view.

> **Not investment advice.** Impact labels are a mechanical reading of filing/news
> text, not a recommendation. The system never executes trades.

## The one rule: no fabricated facts
Every alert is traceable to a primary source. The summariser only *compresses*
text that was actually fetched — three code-level guardrails enforce this:
1. **Thin-source gate** — too little text ⇒ headline + link only, no LLM call.
2. **Numeric grounding** — any number in the summary must appear in the source,
   else the summary is discarded and the verbatim headline is sent.
3. **Source whitelist** — news is only accepted from trusted publishers; NSE
   filings come straight from the exchange's official RSS feeds (no scraping).

## Architecture
```
GitHub Actions (cron)                         Streamlit Cloud
 ├ morning_auth  08:15 IST  ─┐                 └ dashboard/app.py
 ├ fast_poll     */10 IST   ─┤  read/write        (reads + watchlist edits,
 └ dma_scan      18:45 IST  ─┘      ▼               Kite token callback)
                          Supabase (Postgres)  ◄──────────┘
        ▲ NSE RSS filings   ▲ Pulse/publisher news   ▲ yfinance + Kite quote
```
- **Broker**: Zerodha's official **Kite MCP server** (`mcp.kite.trade`) — holdings
  + price quotes with no API app/key; one "Login with Kite" tap authorises a
  session (`python -m portfolio_pulse.jobs.mcp_sync`). Kite Connect API remains
  an optional alternative. Read-only: order tools are never called.
- **Filings**: NSE official RSS (`nsearchives.nseindia.com/content/RSS/*.xml`).
- **News**: Pulse by Zerodha + whitelisted publisher RSS.
- **Prices**: yfinance `.NS` (primary) cross-checked against the Kite quote; a >1%
  disagreement marks the signal `SUSPECT` and the alert is held.
- **Summaries**: Claude Haiku 4.5, extractive + guarded.
- **Store**: SQLite locally, Supabase in production (shared by poller + dashboard).

## Module map
| Path | Responsibility |
|---|---|
| `config.py` | Feed URLs, thresholds, IST calendar, env plumbing |
| `store/db.py` · `store/supabase_store.py` | Repository API (SQLite / Supabase) |
| `broker/kite_mcp.py` · `broker/kite_auth.py` · `broker/holdings.py` | MCP client (default), optional API auth, holdings + symbol→name map |
| `ingest/nse_rss.py` · `ingest/news_rss.py` · `ingest/matching.py` | Feeds + dedup + matching |
| `signals/prices.py` · `signals/dma.py` | Price cross-check + cross detection |
| `summarize/guardrail.py` | Guarded extractive summariser |
| `notify/telegram.py` | Push + `/add /remove /list /holdings` |
| `jobs/*` | `fast_poll`, `dma_scan`, `morning_auth`, `mcp_sync` |
| `dashboard/app.py` | Streamlit dashboard + Kite token callback |

## Quick start (local, offline-friendly)
```bash
cd "Portfolio Pulse"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in what you have; SQLite needs nothing

# run the dashboard (SQLite backend by default)
streamlit run portfolio_pulse/dashboard/app.py

# run a poll once
python -m portfolio_pulse.jobs.fast_poll
```
Filings, news, and DMA detection run without any credentials (matching is weaker
without the Kite instruments name-map). Telegram/holdings/summaries activate as
you add each secret. See [SETUP_GUIDE.md](SETUP_GUIDE.md) for the full path.

## Hosting & cost
- **Poller**: GitHub Actions cron (free). Use a **public** repo for unlimited
  Actions minutes (secrets live in GitHub Secrets, never in code); a private repo
  can exceed the 2,000 free min/month at `*/10` — widen to `*/15` or go public.
  Cron can lag a few minutes and won't fire second-precise — fine for filings/EOD,
  not intraday ticks (out of scope). Fly.io free machine is the fallback if you
  later want tighter latency.
- **Store**: Supabase free tier. **Dashboard**: Streamlit Community Cloud (free).
- **APIs**: Kite personal plan free; NSE RSS + Pulse free; only Anthropic Haiku
  usage costs (a few dollars/month at personal volume).

## Known limits (v1)
- Daily Kite re-auth needs one manual tap (fully-automated TOTP login may violate
  Zerodha's terms — deliberately not built in).
- yfinance `.NS` can misprint; the Kite cross-check + `SUSPECT` hold mitigate it.
- NSE RSS content is used for personal, non-commercial monitoring only.
