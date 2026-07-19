"""Zerodha Kite MCP client — broker connection with NO API app/key/secret.

Talks to Zerodha's official hosted MCP server (https://mcp.kite.trade/mcp,
streamable-HTTP JSON-RPC, verified live) exactly like an AI assistant would:

    initialize -> notifications/initialized -> tools/call

Auth is the "Login with Kite" flow: the `login` tool returns a URL, the user taps
it once, and the server-side session is then authorised. The MCP session id is
persisted in the store (meta key) so it survives across cron runs until Kite's
daily expiry — after which alerts keep flowing and only holdings/price
cross-checks pause (see set-and-forget mode in the README).

Read-only by design: although the server also exposes order-placement tools,
this client only ever calls login/profile/holdings/ltp/search_instruments.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

import requests

from portfolio_pulse import config

_SESSION_KEY = "kite_mcp_session"
_PROTOCOL = "2025-03-26"


class MCPError(RuntimeError):
    pass


class NotLoggedIn(MCPError):
    """The MCP session exists but hasn't completed the Kite login."""


class KiteMCPClient:
    """Minimal MCP client for the hosted Kite server.

    Duck-types the two KiteConnect methods the rest of the codebase uses
    (`holdings()`, `ltp()`), so `broker.holdings.sync` and
    `signals.prices.kite_quote` work unchanged with either client.
    """

    def __init__(self, store=None, url: Optional[str] = None):
        self.url = url or config.KITE_MCP_URL
        self.store = store
        self.session_id: Optional[str] = None
        self._rpc_id = 0
        if store is not None:
            self.session_id = store.get_meta(_SESSION_KEY)

    # ------------------------------------------------------------------ #
    # JSON-RPC plumbing
    # ------------------------------------------------------------------ #
    def _post(self, payload: dict, timeout: int = 30) -> Optional[dict]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        resp = requests.post(self.url, json=payload, headers=headers, timeout=timeout)
        sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
        if sid and sid != self.session_id:
            self.session_id = sid
            if self.store is not None:
                self.store.set_meta(_SESSION_KEY, sid)
        if resp.status_code >= 400:
            raise MCPError(f"MCP HTTP {resp.status_code}: {resp.text[:200]}")
        return self._parse_body(resp)

    @staticmethod
    def _parse_body(resp: requests.Response) -> Optional[dict]:
        """Handle both plain-JSON and SSE-framed responses."""
        text = resp.text or ""
        if not text.strip():
            return None
        if "text/event-stream" in resp.headers.get("Content-Type", ""):
            last = None
            for line in text.splitlines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data:
                        try:
                            msg = json.loads(data)
                            if "result" in msg or "error" in msg:
                                last = msg
                        except json.JSONDecodeError:
                            continue
            return last
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def _rpc(self, method: str, params: Optional[dict] = None) -> Any:
        self._rpc_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self._rpc_id, "method": method}
        if params is not None:
            payload["params"] = params
        msg = self._post(payload)
        if msg is None:
            raise MCPError(f"empty MCP response for {method}")
        if "error" in msg:
            raise MCPError(f"{method}: {msg['error'].get('message', msg['error'])}")
        return msg.get("result")

    def _notify(self, method: str) -> None:
        try:
            self._post({"jsonrpc": "2.0", "method": method})
        except MCPError:
            pass  # notifications are fire-and-forget

    # ------------------------------------------------------------------ #
    # Session
    # ------------------------------------------------------------------ #
    def connect(self) -> dict:
        """Fresh initialize handshake. Returns serverInfo."""
        self.session_id = None
        result = self._rpc("initialize", {
            "protocolVersion": _PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "portfolio-pulse", "version": "0.1.0"},
        })
        self._notify("notifications/initialized")
        return result.get("serverInfo", {})

    def ensure_session(self) -> None:
        """Reuse the persisted session if the server still accepts it, else reconnect."""
        if self.session_id:
            try:
                self._rpc("tools/list")
                return
            except MCPError:
                pass  # stale/unknown session -> fall through to a fresh handshake
        self.connect()

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #
    def call_tool(self, name: str, arguments: Optional[dict] = None) -> str:
        """Invoke one MCP tool and return its text content (raises on error)."""
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        chunks = [c.get("text", "") for c in result.get("content", [])
                  if c.get("type") == "text"]
        text = "\n".join(chunks).strip()
        if result.get("isError"):
            if re.search(r"log\s*in|login|session|authoris|authoriz", text, re.I):
                raise NotLoggedIn(text[:300])
            raise MCPError(text[:300])
        return text

    @staticmethod
    def _json(text: str) -> Any:
        """Extract the first JSON object/array embedded in a tool's text reply."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"[\[{].*[\]}]", text, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
        raise MCPError(f"unparseable tool reply: {text[:200]}")

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
        except NotLoggedIn:
            return False
        except MCPError:
            return False

    # ---- KiteConnect-compatible surface ------------------------------ #
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
