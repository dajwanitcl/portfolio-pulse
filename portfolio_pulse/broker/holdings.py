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
            # Upstox rows carry company_name inline; Kite rows get names later.
            "name": (h.get("company_name") or "").strip(),
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


def sync(store, kite, broker: str = "zerodha") -> int:
    """Fetch one broker's holdings and merge into the multi-broker snapshot.

    Per-broker raw rows are kept in store meta (`holdings:{broker}` as JSON) and
    the visible `holdings_snapshot` is the AGGREGATE across brokers: same stock
    held at two brokers gets total quantity and weighted average price. This
    needs no schema change, so it works identically on SQLite and Supabase.

    Company names: MCP clients enrich via per-symbol search or per-row
    company_name fields; results merge into the cache the NSE-filings matcher
    reads. Every holding is auto-promoted to watchlist kind='holding'.
    """
    import json as _json

    rows = fetch_holdings(kite)
    if not rows:
        # An empty holdings reply from a broker that previously had rows is far
        # more likely an API hiccup than a fully liquidated account — keep the
        # previous snapshot rather than wiping (and demoting) everything.
        prev = store.get_meta(f"holdings:{broker}")
        if prev and prev != "[]":
            return 0
    syms = [r["symbol"] for r in rows]

    # Name enrichment: per-row company_name (Upstox) first, then search (Kite MCP).
    inline = {r["symbol"]: r.get("name", "") for r in rows if r.get("name")}
    if inline:
        merge_names(inline)
    if hasattr(kite, "company_names"):
        missing = [s for s in syms if s not in inline]
        if missing:
            merge_names(kite.company_names(missing))
    names = symbol_name_map(kite, syms)
    for r in rows:
        r["name"] = r.get("name") or names.get(r["symbol"], r["symbol"])

    store.set_meta(f"holdings:{broker}", _json.dumps(rows))
    _write_aggregate(store)
    return len(rows)


_KNOWN_BROKERS = ("zerodha", "upstox")


def _write_aggregate(store) -> None:
    """Rebuild holdings_snapshot as the aggregate of all brokers' meta rows."""
    import json as _json

    merged: dict[str, dict] = {}
    for broker in _KNOWN_BROKERS:
        raw = store.get_meta(f"holdings:{broker}")
        if not raw:
            continue
        try:
            rows = _json.loads(raw)
        except _json.JSONDecodeError:
            continue
        for r in rows:
            sym = str(r.get("symbol", "")).strip().upper()
            if not sym:
                continue
            qty = float(r.get("qty", 0))
            avg = float(r.get("avg_price", 0))
            cur = merged.setdefault(sym, {"symbol": sym, "name": r.get("name", ""),
                                          "qty": 0.0, "cost": 0.0, "last_price": 0.0})
            cur["qty"] += qty
            cur["cost"] += qty * avg
            cur["last_price"] = float(r.get("last_price", 0)) or cur["last_price"]
            if not cur["name"]:
                cur["name"] = r.get("name", "")
    out = []
    for r in merged.values():
        out.append({"symbol": r["symbol"], "name": r["name"], "qty": r["qty"],
                    "avg_price": (r["cost"] / r["qty"]) if r["qty"] else 0.0,
                    "last_price": r["last_price"]})
    store.sync_holdings(out)

    # A stock sold everywhere (gone from every broker after settlement) demotes
    # from 'holding' to 'watch': it leaves the P&L view but KEEPS all its
    # alerts — selling a stock rarely means you stop caring about it. /remove
    # drops it entirely if the user wants silence.
    if merged:
        for w in store.list_watch("holding"):
            if w.symbol not in merged:
                store.add_watch(w.symbol, w.name, kind="watch")
