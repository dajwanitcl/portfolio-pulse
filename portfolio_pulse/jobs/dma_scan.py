"""EOD DMA scan (~18:45 IST on trading days): death/golden cross detection.

For each tracked symbol: load daily closes (yfinance) + cross-check the latest
print against Kite; evaluate the 50/200 relation against the stored state; on a
state transition, fire one alert. A SUSPECT price cross-check holds the alert
(state is still advanced so we don't spam once data reconciles).
"""

from __future__ import annotations

from portfolio_pulse import config
from portfolio_pulse.broker import get_broker
from portfolio_pulse.jobs._common import deliver
from portfolio_pulse.signals import dma, prices
from portfolio_pulse.store import get_store


def run(force: bool = False) -> dict:
    store = get_store()
    if not force and not config.is_trading_day():
        return {"skipped": "not a trading day"}
    # Once per day: the heartbeat calls this on every tick after 18:45 IST and
    # the backup cron may fire too — the first caller wins, the rest no-op.
    today = config.now_ist().date().isoformat()
    if not force and store.get_meta("dma_scan_date") == today:
        return {"skipped": "already ran today"}
    store.set_meta("dma_scan_date", today)

    kite = get_broker(store)  # Kite API or MCP client; may be None
    counts = {"scanned": 0, "alerts": 0, "suspect_held": 0}

    for symbol in store.all_symbols():
        counts["scanned"] += 1
        check = prices.load_with_crosscheck(symbol, kite)
        if check.closes.empty:
            continue

        prev = store.get_dma_state(symbol)
        prev_relation = prev["relation"] if prev else None
        signal, new_relation, m = dma.evaluate(symbol, check.closes, prev_relation)

        # Advance stored state + metrics every scan (the dashboard's cross-
        # proximity radar reads these), so we alert only on transitions but
        # always display fresh gap/projection numbers.
        store.upsert_dma_state(
            symbol, m["sma50"], m["sma200"], new_relation, m["gap_pct"],
            m["projected_days"],
        )
        if not signal:
            continue

        # A SUSPECT price cross-check holds the alert (state already advanced).
        if check.qc_status == "SUSPECT":
            counts["suspect_held"] += 1
            continue

        deliver(
            store, symbol=symbol, alert_type=signal.alert_type,
            title=signal.title,
            source_text=f"{signal.detail} Price cross-check: {check.note}.",
            source_url="", source_type="Signal", base_qc=check.qc_status,
            do_summarize=False,
        )
        counts["alerts"] += 1
    return counts


if __name__ == "__main__":
    print(run())
