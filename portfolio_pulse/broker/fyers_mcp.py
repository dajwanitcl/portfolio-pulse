"""Fyers official MCP client (mcp.fyers.in — verified live: open handshake,
39 tools including get_holdings).

Fyers' auth flow is discovered at connect time: the server accepts the MCP
handshake without credentials, and calling a protected tool tells us what it
wants (a login-style tool, or bearer auth on 401). jobs/broker_connect.py walks
whichever it turns out to be. Read-only use only — order tools are never called.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from portfolio_pulse import config
from portfolio_pulse.broker.mcp_core import MCPError, MCPHTTPClient, NotLoggedIn

FYERS_MCP_URL = "https://mcp.fyers.in/mcp"


class FyersMCPClient(MCPHTTPClient):
    session_meta_key = "fyers_mcp_session"

    def __init__(self, store=None, url: Optional[str] = None):
        super().__init__(store, url or getattr(config, "FYERS_MCP_URL", FYERS_MCP_URL))
        self._tools_cache: Optional[list[dict]] = None

    def _tool_named(self, *keywords: str) -> Optional[dict]:
        if self._tools_cache is None:
            self._tools_cache = self.list_tools()
        for t in self._tools_cache:
            name = t.get("name", "").lower()
            if all(k in name for k in keywords):
                return t
        return None

    def login_url(self) -> Optional[str]:
        """If the server exposes a login/auth tool, call it and extract a URL."""
        tool = (self._tool_named("login") or self._tool_named("auth")
                or self._tool_named("connect"))
        if not tool:
            return None
        text = self.call_tool(tool["name"])
        m = re.search(r"https?://\S+", text)
        return m.group(0).rstrip(").,]") if m else None

    def logged_in(self) -> bool:
        try:
            self.call_tool("get_funds")
            return True
        except (NotLoggedIn, MCPError):
            return False

    def holdings(self) -> list[dict[str, Any]]:
        data = self._json(self.call_tool("get_holdings"))
        if isinstance(data, dict):
            for key in ("holdings", "data", "items", "result", "netPositions"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        if not isinstance(data, list):
            raise MCPError("unexpected holdings shape from Fyers")
        out = []
        for h in data:
            if not isinstance(h, dict):
                continue
            sym = (h.get("symbol") or h.get("tradingsymbol") or "")
            # Fyers symbols look like 'NSE:RELIANCE-EQ' — normalise to bare ticker.
            sym = sym.split(":")[-1].replace("-EQ", "").strip().upper()
            out.append({
                "tradingsymbol": sym,
                "quantity": h.get("quantity", h.get("qty", 0)),
                "average_price": h.get("costPrice", h.get("avg_price", 0)),
                "last_price": h.get("ltp", h.get("last_price", 0)),
                "company_name": h.get("description", h.get("name", "")),
            })
        return [r for r in out if r["tradingsymbol"]]

    def ltp(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        raise MCPError("Fyers quote mapping not verified yet")
