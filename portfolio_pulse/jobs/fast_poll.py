"""Fast poll (every ~10 min, 08:00–24:00 IST): filings + news + command drain.

Idempotent by design: dedup lives in the store, so overlapping or repeated cron
runs never double-alert. Broker access is best-effort — if the Kite token is
stale, holdings sync and price cross-checks are skipped but filings/news still
flow (matched via the day-cached instruments name map).
"""

from __future__ import annotations

from portfolio_pulse.broker import get_live_brokers, holdings
from portfolio_pulse.ingest import news_rss, nse_rss
from portfolio_pulse.jobs._common import deliver, tracked_symbol_names
from portfolio_pulse.notify import telegram
from portfolio_pulse.store import get_store


def run() -> dict:
    store = get_store()
    brokers = get_live_brokers(store)  # [] when every session is expired
    kite = brokers[0][1] if brokers else None  # any live client works for names

    # Opportunistically refresh holdings from every live broker.
    synced = 0
    for name, client in brokers:
        try:
            synced += holdings.sync(store, client, broker=name)
        except Exception:
            pass

    # Reconnection confirmation: a broker session appearing (after being absent)
    # gets an explicit ✅ on Telegram — so a silently-failed login tap is
    # distinguishable from a successful one by the absence of this message.
    live_names = [n for n, _ in brokers]
    prev = set(filter(None, (store.get_meta("live_brokers") or "").split(",")))
    for n in live_names:
        if n not in prev:
            telegram.send_message(
                f"✅ <b>{n.title()} connected</b> — holdings re-synced. "
                "You're fully live again."
            )
    store.set_meta("live_brokers", ",".join(live_names))
    if kite is not None:
        # Backfill company names for watchlist stocks added without one (e.g.
        # /add by bare ticker while offline) — names drive filing matching.
        if hasattr(kite, "company_names"):
            try:
                nameless = {w.symbol: w.kind for w in store.list_watch() if not w.name}
                if nameless:
                    found = kite.company_names(list(nameless))
                    holdings.merge_names(found)
                    for sym, nm in found.items():
                        store.add_watch(sym, nm, kind=nameless[sym])
            except Exception:
                pass

    symbol_names = tracked_symbol_names(store, kite)
    counts = {"synced_holdings": synced, "brokers": live_names,
              "filings": 0, "news": 0, "commands": 0}

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
