"""One-time migration: copy the local SQLite state into Supabase.

Run AFTER creating the Supabase project and running migrations/supabase_schema.sql,
with SUPABASE_URL + SUPABASE_KEY filled in .env:

    python -m portfolio_pulse.jobs.migrate_to_supabase

Copies: watchlist (holdings + watch), holdings snapshot, DMA states, seen-item
GUIDs (so the cloud poller doesn't re-alert old filings), alert history, the
broker MCP session, and the Telegram update offset. Idempotent — safe to re-run.
"""

from __future__ import annotations

from portfolio_pulse import config
from portfolio_pulse.store.db import SQLiteStore
from portfolio_pulse.store.supabase_store import SupabaseStore


def run() -> dict:
    if not config.SUPABASE_URL or not config.SUPABASE_KEY:
        return {"error": "SUPABASE_URL / SUPABASE_KEY not set in .env"}
    src = SQLiteStore()
    dst = SupabaseStore()
    counts: dict[str, int] = {}

    for w in src.list_watch():
        dst.add_watch(w.symbol, w.name, w.kind)
    counts["watchlist"] = len(src.list_watch())

    holdings = src.get_holdings()
    if holdings:
        dst.sync_holdings(
            {"symbol": h["symbol"], "name": "", "qty": h["qty"],
             "avg_price": h["avg_price"], "last_price": h["last_price"]}
            for h in holdings
        )
    counts["holdings"] = len(holdings)

    n = 0
    for sym in src.all_symbols():
        d = src.get_dma_state(sym)
        if d:
            dst.upsert_dma_state(sym, d.get("sma50") or 0, d.get("sma200") or 0,
                                 d.get("relation") or "unknown",
                                 d.get("gap_pct") or 0, d.get("projected_days"))
            n += 1
    counts["dma_states"] = n

    conn = src._connect()
    try:
        seen = conn.execute("SELECT * FROM seen_items").fetchall()
        alerts = conn.execute("SELECT * FROM alerts ORDER BY id").fetchall()
    finally:
        conn.close()
    for r in seen:
        dst.mark_seen(r["guid"], r["symbol"], r["source_type"], r["title"],
                      r["url"], r["published_at"])
    counts["seen_items"] = len(seen)

    from portfolio_pulse.store.db import Alert
    for r in alerts:
        dst.record_alert(Alert(None, r["symbol"], r["alert_type"], r["title"],
                               r["summary"], r["impact"], r["source_url"],
                               r["source_type"], r["qc_status"], r["created_at"],
                               bool(r["delivered"])))
    counts["alerts"] = len(alerts)

    tok = src.load_token()
    if tok:
        dst.save_token(tok["access_token"], tok.get("public_token", ""))
    for key in ("kite_mcp_session", "telegram_update_offset"):
        val = src.get_meta(key)
        if val:
            dst.set_meta(key, val)

    return counts


if __name__ == "__main__":
    print(run())
