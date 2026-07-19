"""Fetch Zerodha holdings and build the symbol -> company-name map.

The name map is the bridge that lets NSE filing items (keyed by company name) be
matched to the user's positions (keyed by trading symbol). It's derived from the
Kite NSE instruments dump, cached to disk for the trading day to avoid re-pulling
a large payload on every run.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from portfolio_pulse import config

_INSTRUMENTS_CACHE = os.path.join(
    os.path.dirname(config.SQLITE_PATH), "instruments_nse.json"
)


def fetch_holdings(kite) -> list[dict[str, Any]]:
    """Normalize kite.holdings() into store-ready rows."""
    rows: list[dict[str, Any]] = []
    for h in kite.holdings():
        rows.append({
            "symbol": h.get("tradingsymbol", "").strip().upper(),
            "name": "",  # filled from the instruments map by callers if needed
            "qty": h.get("quantity", 0) or h.get("opening_quantity", 0),
            "avg_price": h.get("average_price", 0),
            "last_price": h.get("last_price", 0),
        })
    return [r for r in rows if r["symbol"]]


def _load_instruments_cache(max_age_days: int = 30) -> Optional[dict[str, str]]:
    """Return cached {symbol: name} if the cache is recent enough, else None.

    Company names change rarely, so a stale-ish cache is far better than none —
    without it, NSE filing matching (which keys on company names) degrades to
    ticker-only. 30 days keeps set-and-forget mode working between syncs.
    """
    try:
        with open(_INSTRUMENTS_CACHE, encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    try:
        from datetime import date
        age = (config.now_ist().date() - date.fromisoformat(blob.get("date", ""))).days
    except ValueError:
        return None
    if age > max_age_days:
        return None
    return blob.get("map", {})


def _save_instruments_cache(mapping: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(_INSTRUMENTS_CACHE), exist_ok=True)
    tmp = _INSTRUMENTS_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"date": config.now_ist().date().isoformat(), "map": mapping}, fh)
    os.replace(tmp, _INSTRUMENTS_CACHE)


def merge_names(names: dict[str, str]) -> None:
    """Merge {SYMBOL: company name} entries into the cache (MCP search results).

    Lets the MCP path (which has no bulk instruments dump) enrich the same cache
    the filings matcher reads, refreshing its date stamp in the process.
    """
    if not names:
        return
    current = _load_instruments_cache(max_age_days=365) or {}
    current.update({s.upper(): n for s, n in names.items() if n})
    _save_instruments_cache(current)


def symbol_name_map(kite, symbols: Optional[list[str]] = None) -> dict[str, str]:
    """Return {SYMBOL: company name} for NSE equities.

    Uses the day-cached instruments dump when available; otherwise pulls
    kite.instruments('NSE') once and caches it. If `symbols` is given, the result
    is restricted to those (falling back to the symbol itself when unknown).
    """
    mapping = _load_instruments_cache()
    if mapping is None and kite is not None:
        mapping = {}
        try:
            for inst in kite.instruments("NSE"):
                ts = (inst.get("tradingsymbol") or "").strip().upper()
                nm = (inst.get("name") or "").strip()
                if ts and nm:
                    mapping[ts] = nm
            _save_instruments_cache(mapping)
        except Exception:
            mapping = mapping or {}
    mapping = mapping or {}

    if symbols is None:
        return mapping
    return {s.upper(): mapping.get(s.upper(), s.upper()) for s in symbols}


def sync(store, kite) -> int:
    """Fetch holdings, enrich names, and sync into the store. Returns count.

    Works with either broker client (Kite Connect API or Kite MCP). The MCP
    client has no bulk instruments dump, so it enriches company names via
    per-symbol search (company_names) and merges them into the shared cache the
    NSE-filings matcher reads. Auto-promotes each holding to watchlist
    kind='holding' (done inside the store).
    """
    rows = fetch_holdings(kite)
    syms = [r["symbol"] for r in rows]
    if hasattr(kite, "company_names"):  # MCP path
        merge_names(kite.company_names(syms))
    names = symbol_name_map(kite, syms)
    for r in rows:
        r["name"] = names.get(r["symbol"], r["symbol"])
    return store.sync_holdings(rows)
