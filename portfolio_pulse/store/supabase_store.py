"""Supabase (hosted Postgres) backend — the production store.

Implements the exact same `Store` interface as SQLiteStore, over PostgREST via
supabase-py, so the poller (GitHub Actions) and the dashboard (Streamlit Cloud)
share one database. Run migrations/supabase_schema.sql once before first use.

Only credential-bearing hosts instantiate this; local development stays on SQLite.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from portfolio_pulse import config
from portfolio_pulse.store.db import Alert, WatchItem, _iso


class SupabaseStore:
    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        from supabase import create_client
        self.client = create_client(url or config.SUPABASE_URL,
                                    key or config.SUPABASE_KEY)

    def _t(self, name: str):
        return self.client.table(name)

    # -- watchlist / holdings -------------------------------------------------
    def add_watch(self, symbol: str, name: str = "", kind: str = "watch") -> bool:
        symbol = symbol.strip().upper()
        row = {"symbol": symbol, "kind": kind, "added_at": _iso()}
        if name:
            row["name"] = name
        self._t("watchlist").upsert(row, on_conflict="symbol").execute()
        return True

    def remove_watch(self, symbol: str) -> bool:
        symbol = symbol.strip().upper()
        res = self._t("watchlist").delete().eq("symbol", symbol).execute()
        return bool(res.data)

    def clear_watch(self) -> int:
        res = self._t("watchlist").delete().eq("kind", "watch").execute()
        return len(res.data or [])

    def list_watch(self, kind: Optional[str] = None) -> list[WatchItem]:
        q = self._t("watchlist").select("*").order("symbol")
        if kind:
            q = q.eq("kind", kind)
        rows = q.execute().data or []
        return [WatchItem(r["symbol"], r.get("name", ""), r["kind"], r["added_at"])
                for r in rows]

    def all_symbols(self) -> list[str]:
        rows = self._t("watchlist").select("symbol").execute().data or []
        return [r["symbol"] for r in rows]

    def sync_holdings(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        # Replace snapshot (PostgREST delete needs a filter that matches all rows).
        self._t("holdings_snapshot").delete().neq("symbol", "\x00").execute()
        n = 0
        for r in rows:
            sym = str(r["symbol"]).strip().upper()
            self._t("holdings_snapshot").upsert({
                "symbol": sym, "qty": float(r.get("qty", 0)),
                "avg_price": float(r.get("avg_price", 0)),
                "last_price": float(r.get("last_price", 0)), "synced_at": _iso(),
            }, on_conflict="symbol").execute()
            self._t("watchlist").upsert({
                "symbol": sym, "name": str(r.get("name", "")), "kind": "holding",
                "added_at": _iso(),
            }, on_conflict="symbol").execute()
            n += 1
        return n

    def get_holdings(self) -> list[dict[str, Any]]:
        return self._t("holdings_snapshot").select("*").order("symbol").execute().data or []

    # -- dedup ----------------------------------------------------------------
    def mark_seen(self, guid: str, symbol: str, source_type: str,
                  title: str, url: str, published_at: Optional[str]) -> bool:
        try:
            self._t("seen_items").insert({
                "guid": guid, "symbol": symbol, "source_type": source_type,
                "title": title, "url": url, "published_at": published_at,
                "ingested_at": _iso(),
            }).execute()
            return True
        except Exception as exc:  # unique-violation => already seen
            if "23505" in str(exc) or "duplicate" in str(exc).lower():
                return False
            raise

    def is_seen(self, guid: str) -> bool:
        res = self._t("seen_items").select("guid").eq("guid", guid).limit(1).execute()
        return bool(res.data)

    # -- alerts ---------------------------------------------------------------
    def record_alert(self, alert: Alert) -> int:
        res = self._t("alerts").insert({
            "symbol": alert.symbol, "alert_type": alert.alert_type,
            "title": alert.title, "summary": alert.summary, "impact": alert.impact,
            "source_url": alert.source_url, "source_type": alert.source_type,
            "qc_status": alert.qc_status, "created_at": alert.created_at or _iso(),
            "delivered": alert.delivered,
        }).execute()
        return int(res.data[0]["id"])

    def mark_delivered(self, alert_id: int) -> None:
        self._t("alerts").update({"delivered": True}).eq("id", alert_id).execute()

    def list_alerts(self, limit: int = 50, symbol: Optional[str] = None) -> list[Alert]:
        q = self._t("alerts").select("*").order("id", desc=True).limit(limit)
        if symbol:
            q = q.eq("symbol", symbol.strip().upper())
        rows = q.execute().data or []
        return [
            Alert(r["id"], r["symbol"], r["alert_type"], r["title"], r["summary"],
                  r["impact"], r["source_url"], r["source_type"], r["qc_status"],
                  r["created_at"], bool(r["delivered"]))
            for r in rows
        ]

    # -- dma state ------------------------------------------------------------
    def get_dma_state(self, symbol: str) -> Optional[dict[str, Any]]:
        res = self._t("dma_state").select("*").eq(
            "symbol", symbol.strip().upper()).limit(1).execute()
        return res.data[0] if res.data else None

    def upsert_dma_state(self, symbol: str, sma50: float, sma200: float,
                         relation: str, gap_pct: float,
                         projected_days: Optional[float]) -> None:
        self._t("dma_state").upsert({
            "symbol": symbol.strip().upper(), "sma50": sma50, "sma200": sma200,
            "relation": relation, "gap_pct": gap_pct,
            "projected_days": projected_days, "updated_at": _iso(),
        }, on_conflict="symbol").execute()

    # -- kite token -----------------------------------------------------------
    def save_token(self, access_token: str, public_token: str = "") -> None:
        self._t("auth_token").upsert({
            "id": 1, "access_token": access_token, "public_token": public_token,
            "issued_at": _iso(),
        }, on_conflict="id").execute()

    def load_token(self) -> Optional[dict[str, Any]]:
        res = self._t("auth_token").select("*").eq("id", 1).limit(1).execute()
        return res.data[0] if res.data else None

    # -- meta -----------------------------------------------------------------
    def get_meta(self, key: str) -> Optional[str]:
        res = self._t("meta").select("value").eq("key", key).limit(1).execute()
        return res.data[0]["value"] if res.data else None

    def set_meta(self, key: str, value: str) -> None:
        self._t("meta").upsert({"key": key, "value": value},
                              on_conflict="key").execute()
