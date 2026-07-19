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

    kite = get_broker(store)  # Kite API or MCP client; may be None
    counts = {"scanned": 0, "alerts": 0, "suspect_held": 0}

    for symbol in store.all_symbols():
        counts["scanned"] += 1
        check = prices.load_with_crosscheck(symbol, kite)
        if check.closes.empty:
            continue

        prev = store.get_dma_state(symbol)
        prev_relation = prev["relation"] if prev else None
        signal, new_relation = dma.evaluate(symbol, check.closes, prev_relation)

        # Advance stored state regardless, so we alert only on transitions.
        s50 = signal.sma50 if signal else _last_sma(check.closes, config.DMA_SHORT)
        s200 = signal.sma200 if signal else _last_sma(check.closes, config.DMA_LONG)
        gap = signal.gap_pct if signal else ((s50 - s200) / s200 if s200 else 0.0)
        store.upsert_dma_state(
            symbol, s50, s200, new_relation, gap,
            signal.projected_days if signal else None,
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


def _last_sma(closes, window: int) -> float:
    s = closes.rolling(window).mean().dropna()
    return float(s.iloc[-1]) if len(s) else 0.0


if __name__ == "__main__":
    print(run())
