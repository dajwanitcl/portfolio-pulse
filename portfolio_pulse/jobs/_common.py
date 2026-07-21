"""Shared helpers for the scheduled jobs: build symbol map, summarise, deliver."""

from __future__ import annotations

from typing import Any

from portfolio_pulse import config
from portfolio_pulse.broker import holdings
from portfolio_pulse.notify import telegram
from portfolio_pulse.store.db import Alert
from portfolio_pulse.summarize.guardrail import summarize


def tracked_symbol_names(store, kite) -> dict[str, str]:
    """{SYMBOL: company name} for every tracked symbol (holdings + watchlist).

    Company names drive NSE filing matching. Two layers, most durable first:
    the store's own watchlist names (persisted in the shared DB — survive
    stateless cloud runs where the local instruments cache doesn't), overlaid
    by the instruments map when a cache/broker session is available.
    """
    items = store.list_watch()
    if not items:
        return {}
    names = {w.symbol: (w.name or w.symbol) for w in items}
    mapped = holdings.symbol_name_map(kite, list(names))
    for sym, nm in mapped.items():
        if nm and nm != sym:  # overlay only real names, not symbol fallbacks
            names[sym] = nm
    return names


def deliver(store, *, symbol: str, alert_type: str, title: str,
            source_text: str, source_url: str, source_type: str,
            base_qc: str = "CONFIRMED", do_summarize: bool = True,
            impact: str = "", company: str = "", category: str = "",
            require_relevance: bool = False):
    """Summarise (guarded), persist, and push one alert. Returns the Alert,
    or None when `require_relevance` is set and the model judged the item to be
    about someone else (e.g. the company quoted as an analyst) — dropped, since
    the feed item is already marked seen by the caller.

    `base_qc` lets callers pass a stricter status (e.g. SUSPECT from a price
    cross-check); the summariser's own status only downgrades, never upgrades it.

    `do_summarize=False` is for our own deterministic messages (DMA signals): the
    provided `source_text` is used verbatim as the body — no LLM, nothing to
    hallucinate — and `base_qc` stands as the status.
    """
    if do_summarize:
        summary = summarize(source_text=source_text, headline=title,
                            company=company, category=category)
        if require_relevance and not summary.relevant:
            return None
        body, impact_note, qc = summary.text, summary.impact_note, \
            _worst_qc(base_qc, summary.qc_status)
    else:
        body, impact_note, qc = source_text, impact, base_qc
    alert = Alert(
        id=None, symbol=symbol, alert_type=alert_type, title=title,
        summary=body, impact=impact_note, source_url=source_url,
        source_type=source_type, qc_status=qc, created_at=config.utc_now().isoformat(),
        delivered=False,
    )
    alert.id = store.record_alert(alert)
    if telegram.send_alert(alert):
        store.mark_delivered(alert.id)
    return alert


# Lower rank = more trustworthy; the delivered alert takes the least-trusted of
# the price-check status and the summariser status.
_QC_RANK = {"CONFIRMED": 0, "SINGLE-SOURCE": 1, "PARTIAL": 2,
            "INSUFFICIENT": 3, "SUSPECT": 4, "NO-DATA": 5}


def _worst_qc(a: str, b: str) -> str:
    return a if _QC_RANK.get(a, 9) >= _QC_RANK.get(b, 9) else b
