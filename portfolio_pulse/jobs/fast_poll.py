"""Fast poll (every ~10 min, 08:00–24:00 IST): filings + news + command drain.

Idempotent by design: dedup lives in the store, so overlapping or repeated cron
runs never double-alert. Broker access is best-effort — if the Kite token is
stale, holdings sync and price cross-checks are skipped but filings/news still
flow (matched via the day-cached instruments name map).
"""

from __future__ import annotations

from portfolio_pulse.broker import get_broker, holdings
from portfolio_pulse.ingest import news_rss, nse_rss
from portfolio_pulse.jobs._common import deliver, tracked_symbol_names
from portfolio_pulse.notify import telegram
from portfolio_pulse.store import get_store


def run() -> dict:
    store = get_store()
    kite = get_broker(store)  # Kite API or MCP client; may be None

    # Opportunistically refresh holdings while we have a live session.
    synced = 0
    if kite is not None:
        try:
            synced = holdings.sync(store, kite)
        except Exception:
            synced = 0

    symbol_names = tracked_symbol_names(store, kite)
    counts = {"synced_holdings": synced, "filings": 0, "news": 0, "commands": 0}

    if symbol_names:
        for item in nse_rss.poll(store, symbol_names):
            deliver(
                store, symbol=item.symbol, alert_type="filing",
                title=f"{item.company}: {item.subject}" if item.subject else item.company,
                source_text=item.description, source_url=item.link,
                source_type=item.source_type,
            )
            counts["filings"] += 1

        for item in news_rss.poll(store, symbol_names):
            deliver(
                store, symbol=item.symbol, alert_type="news",
                title=item.title, source_text=item.description or item.title,
                source_url=item.link, source_type=item.source_type,
            )
            counts["news"] += 1

    counts["commands"] = telegram.drain_commands(store)
    return counts


if __name__ == "__main__":
    print(run())
