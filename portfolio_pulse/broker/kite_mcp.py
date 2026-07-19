"""Zerodha Kite MCP client — broker connection with NO API app/key/secret.

Thin subclass of the shared MCP core (broker/mcp_core.py). Kite's auth is its
own `login` tool: it returns a time-signed URL the user taps once; the session
id (persisted in store meta) is then authorised until Kite's daily expiry.

Read-only by design: although the server also exposes order-placement tools,
this client only ever calls login/profile/holdings/ltp/search_instruments.
"""

from __future__ import annotations

import re
from typing import Any

from portfolio_pulse import config
from portfolio_pulse.broker.mcp_core import MCPError, MCPHTTPClient, NotLoggedIn

__all__ = ["KiteMCPClient", "MCPError", "NotLoggedIn"]


class KiteMCPClient(MCPHTTPClient):
    session_meta_key = "kite_mcp_session"

    def __init__(self, store=None, url: str | None = None):
        super().__init__(store, url or config.KITE_MCP_URL)

    def login_url(self) -> str:
        """Start the Login-with-Kite flow; returns the URL for the user to tap."""
        text = self.call_tool("login")
        m = re.search(r"https?://\S+", text)
        if not m:
            raise MCPError(f"login tool returned no URL: {text[:200]}")
        return m.group(0).rstrip(").,]")

    def logged_in(self) -> bool:
        try:
            self.call_tool("get_profile")
            return True
        except (NotLoggedIn, MCPError):
            return False

    # ---- KiteConnect-compatible surface ------------------------------------
    def holdings(self) -> list[dict[str, Any]]:
        """Holdings as dicts with KiteConnect field names (tradingsymbol, ...)."""
        data = self._json(self.call_tool("get_holdings"))
        if isinstance(data, dict):
            for key in ("holdings", "data", "items", "result"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        if not isinstance(data, list):
            raise MCPError(f"unexpected holdings shape: {type(data).__name__}")
        return data

    def ltp(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        """{'NSE:RELIANCE': {'last_price': ...}} — mirrors KiteConnect.ltp()."""
        data = self._json(self.call_tool("get_ltp", {"instruments": instruments}))
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
            data = data["data"]
        out: dict[str, dict[str, Any]] = {}
        if isinstance(data, dict):
            for key, val in data.items():
                if isinstance(val, dict) and "last_price" in val:
                    out[key] = {"last_price": float(val["last_price"])}
                elif isinstance(val, (int, float)):
                    out[key] = {"last_price": float(val)}
        if not out:
            raise MCPError("get_ltp returned no usable prices")
        return out

    def company_names(self, symbols: list[str]) -> dict[str, str]:
        """{SYMBOL: company name} via search_instruments (best-effort)."""
        names: dict[str, str] = {}
        for sym in symbols:
            sym = sym.strip().upper()
            try:
                data = self._json(self.call_tool("search_instruments", {"query": sym}))
            except MCPError:
                continue
            rows = data if isinstance(data, list) else \
                next((data[k] for k in ("instruments", "data", "items", "result")
                      if isinstance(data, dict) and isinstance(data.get(k), list)), [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if (row.get("tradingsymbol", "").strip().upper() == sym
                        and row.get("exchange", "NSE").upper() in ("NSE", "")):
                    nm = (row.get("name") or "").strip()
                    if nm:
                        names[sym] = nm
                    break
        return names
