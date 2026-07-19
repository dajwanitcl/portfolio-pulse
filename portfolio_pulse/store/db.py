"""Shared state store behind a small repository API.

Two backends satisfy the same `Store` interface:
  * SQLiteStore  — default; zero-setup, offline, used for local dev + verification.
                   Mirrors the SQLite cache pattern already used in Market Move.
  * SupabaseStore — hosted Postgres for production, where the poller (GitHub
                   Actions) and the dashboard (Streamlit Cloud) run on different
                   hosts and must share one DB. Added at deploy time (Phase 8);
                   the interface below is what it must implement.

Design choices:
  * `mark_seen(guid, ...)` is the dedup primitive — returns True only the first
    time a feed item's GUID is recorded, so callers alert exactly once.
  * `dma_state` persists the last SMA relation per symbol so cross alerts fire
    only on a state transition, never repeatedly.
  * Timestamps are stored as ISO-8601 UTC strings for portability across backends.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional, Protocol

from portfolio_pulse import config


# --------------------------------------------------------------------------- #
# Row types (lightweight, backend-agnostic)
# --------------------------------------------------------------------------- #
@dataclass
class WatchItem:
    symbol: str
    name: str
    kind: str  # "holding" | "watch"
    added_at: str


@dataclass
class Alert:
    id: Optional[int]
    symbol: str
    alert_type: str  # filing | news | dma_forming | dma_confirmed | golden_cross
    title: str
    summary: str
    impact: str
    source_url: str
    source_type: str  # "Exchange Filing" | "News: <publisher>" | "Signal"
    qc_status: str    # CONFIRMED | PARTIAL | SUSPECT | INSUFFICIENT
    created_at: str
    delivered: bool


def guid_hash(*parts: str) -> str:
    """Stable dedup key from any identifying strings (link/title/pubdate)."""
    joined = "\x1f".join(p.strip() for p in parts if p)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def _iso(dt: datetime | None = None) -> str:
    return (dt or config.utc_now()).isoformat()


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
class Store(Protocol):
    # watchlist / holdings
    def add_watch(self, symbol: str, name: str, kind: str = "watch") -> bool: ...
    def remove_watch(self, symbol: str) -> bool: ...
    def clear_watch(self) -> int: ...
    def list_watch(self, kind: Optional[str] = None) -> list[WatchItem]: ...
    def all_symbols(self) -> list[str]: ...
    def sync_holdings(self, rows: Iterable[dict[str, Any]]) -> int: ...
    def get_holdings(self) -> list[dict[str, Any]]: ...

    # dedup
    def mark_seen(self, guid: str, symbol: str, source_type: str,
                  title: str, url: str, published_at: Optional[str]) -> bool: ...
    def is_seen(self, guid: str) -> bool: ...

    # alerts
    def record_alert(self, alert: Alert) -> int: ...
    def mark_delivered(self, alert_id: int) -> None: ...
    def list_alerts(self, limit: int = 50, symbol: Optional[str] = None) -> list[Alert]: ...

    # dma state
    def get_dma_state(self, symbol: str) -> Optional[dict[str, Any]]: ...
    def upsert_dma_state(self, symbol: str, sma50: float, sma200: float,
                         relation: str, gap_pct: float,
                         projected_days: Optional[float]) -> None: ...

    # kite token
    def save_token(self, access_token: str, public_token: str = "") -> None: ...
    def load_token(self) -> Optional[dict[str, Any]]: ...

    # generic key-value (e.g. Telegram update offset)
    def get_meta(self, key: str) -> Optional[str]: ...
    def set_meta(self, key: str, value: str) -> None: ...


# --------------------------------------------------------------------------- #
# SQLite backend
# --------------------------------------------------------------------------- #
_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    symbol      TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'watch',
    added_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holdings_snapshot (
    symbol      TEXT PRIMARY KEY,
    qty         REAL NOT NULL DEFAULT 0,
    avg_price   REAL NOT NULL DEFAULT 0,
    last_price  REAL NOT NULL DEFAULT 0,
    synced_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_items (
    guid         TEXT PRIMARY KEY,
    symbol       TEXT,
    source_type  TEXT,
    title        TEXT,
    url          TEXT,
    published_at TEXT,
    ingested_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    alert_type  TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    impact      TEXT NOT NULL DEFAULT '',
    source_url  TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    qc_status   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    delivered   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS dma_state (
    symbol         TEXT PRIMARY KEY,
    sma50          REAL,
    sma200         REAL,
    relation       TEXT,
    gap_pct        REAL,
    projected_days REAL,
    updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_token (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    access_token TEXT NOT NULL,
    public_token TEXT NOT NULL DEFAULT '',
    issued_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_symbol ON alerts(symbol);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);
"""


class SQLiteStore:
    """File-backed store. Safe for the single-writer poller + read-only dashboard."""

    def __init__(self, path: str | None = None):
        self.path = path or config.SQLITE_PATH
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # -- watchlist / holdings -------------------------------------------------
    def add_watch(self, symbol: str, name: str = "", kind: str = "watch") -> bool:
        symbol = symbol.strip().upper()
        conn = self._connect()
        try:
            cur = conn.execute(
                """INSERT INTO watchlist(symbol, name, kind, added_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET
                       name = CASE WHEN excluded.name != '' THEN excluded.name
                                   ELSE watchlist.name END,
                       kind = excluded.kind""",
                (symbol, name, kind, _iso()),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def remove_watch(self, symbol: str) -> bool:
        symbol = symbol.strip().upper()
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def clear_watch(self) -> int:
        """Empty the manual watchlist (kind='watch' only — holdings are untouched)."""
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM watchlist WHERE kind = 'watch'")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def list_watch(self, kind: Optional[str] = None) -> list[WatchItem]:
        conn = self._connect()
        try:
            if kind:
                rows = conn.execute(
                    "SELECT * FROM watchlist WHERE kind = ? ORDER BY symbol", (kind,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM watchlist ORDER BY symbol").fetchall()
            return [WatchItem(r["symbol"], r["name"], r["kind"], r["added_at"]) for r in rows]
        finally:
            conn.close()

    def all_symbols(self) -> list[str]:
        return [w.symbol for w in self.list_watch()]

    def sync_holdings(self, rows: Iterable[dict[str, Any]]) -> int:
        """Replace the holdings snapshot and ensure each holding is in the watchlist
        as kind='holding'. Returns the number of holdings written."""
        rows = list(rows)
        conn = self._connect()
        try:
            conn.execute("DELETE FROM holdings_snapshot")
            n = 0
            for r in rows:
                sym = str(r["symbol"]).strip().upper()
                conn.execute(
                    """INSERT INTO holdings_snapshot(symbol, qty, avg_price, last_price, synced_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (sym, float(r.get("qty", 0)), float(r.get("avg_price", 0)),
                     float(r.get("last_price", 0)), _iso()),
                )
                conn.execute(
                    """INSERT INTO watchlist(symbol, name, kind, added_at)
                       VALUES (?, ?, 'holding', ?)
                       ON CONFLICT(symbol) DO UPDATE SET
                           kind = 'holding',
                           name = CASE WHEN excluded.name != '' THEN excluded.name
                                       ELSE watchlist.name END""",
                    (sym, str(r.get("name", "")), _iso()),
                )
                n += 1
            conn.commit()
            return n
        finally:
            conn.close()

    def get_holdings(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM holdings_snapshot ORDER BY symbol"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # -- dedup ----------------------------------------------------------------
    def mark_seen(self, guid: str, symbol: str, source_type: str,
                  title: str, url: str, published_at: Optional[str]) -> bool:
        """Record a feed item. Returns True if newly seen, False if a duplicate."""
        conn = self._connect()
        try:
            try:
                conn.execute(
                    """INSERT INTO seen_items(guid, symbol, source_type, title, url,
                                              published_at, ingested_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (guid, symbol, source_type, title, url, published_at, _iso()),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False  # PRIMARY KEY collision => already seen
        finally:
            conn.close()

    def is_seen(self, guid: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM seen_items WHERE guid = ?", (guid,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    # -- alerts ---------------------------------------------------------------
    def record_alert(self, alert: Alert) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                """INSERT INTO alerts(symbol, alert_type, title, summary, impact,
                                      source_url, source_type, qc_status, created_at, delivered)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (alert.symbol, alert.alert_type, alert.title, alert.summary,
                 alert.impact, alert.source_url, alert.source_type, alert.qc_status,
                 alert.created_at or _iso(), int(alert.delivered)),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def mark_delivered(self, alert_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute("UPDATE alerts SET delivered = 1 WHERE id = ?", (alert_id,))
            conn.commit()
        finally:
            conn.close()

    def list_alerts(self, limit: int = 50, symbol: Optional[str] = None) -> list[Alert]:
        conn = self._connect()
        try:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM alerts WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                    (symbol.strip().upper(), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            return [
                Alert(r["id"], r["symbol"], r["alert_type"], r["title"], r["summary"],
                      r["impact"], r["source_url"], r["source_type"], r["qc_status"],
                      r["created_at"], bool(r["delivered"]))
                for r in rows
            ]
        finally:
            conn.close()

    # -- dma state ------------------------------------------------------------
    def get_dma_state(self, symbol: str) -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM dma_state WHERE symbol = ?", (symbol.strip().upper(),)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def upsert_dma_state(self, symbol: str, sma50: float, sma200: float,
                         relation: str, gap_pct: float,
                         projected_days: Optional[float]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO dma_state(symbol, sma50, sma200, relation, gap_pct,
                                         projected_days, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET
                       sma50=excluded.sma50, sma200=excluded.sma200,
                       relation=excluded.relation, gap_pct=excluded.gap_pct,
                       projected_days=excluded.projected_days,
                       updated_at=excluded.updated_at""",
                (symbol.strip().upper(), sma50, sma200, relation, gap_pct,
                 projected_days, _iso()),
            )
            conn.commit()
        finally:
            conn.close()

    # -- kite token -----------------------------------------------------------
    def save_token(self, access_token: str, public_token: str = "") -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO auth_token(id, access_token, public_token, issued_at)
                   VALUES (1, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       access_token=excluded.access_token,
                       public_token=excluded.public_token,
                       issued_at=excluded.issued_at""",
                (access_token, public_token, _iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def load_token(self) -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM auth_token WHERE id = 1").fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # -- meta key-value -------------------------------------------------------
    def get_meta(self, key: str) -> Optional[str]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None
        finally:
            conn.close()

    def set_meta(self, key: str, value: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO meta(key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
_INSTANCE: Store | None = None


def get_store() -> Store:
    """Return the process-wide store for the configured backend."""
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE
    if config.STORE_BACKEND == "supabase":
        from portfolio_pulse.store.supabase_store import SupabaseStore
        if not config.SUPABASE_URL or not config.SUPABASE_KEY:
            raise RuntimeError(
                "PP_STORE_BACKEND=supabase but SUPABASE_URL/SUPABASE_KEY are unset."
            )
        _INSTANCE = SupabaseStore()
        return _INSTANCE
    _INSTANCE = SQLiteStore()
    return _INSTANCE
