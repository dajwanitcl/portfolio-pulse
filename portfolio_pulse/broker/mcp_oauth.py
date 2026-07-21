"""Generic OAuth-secured MCP broker client (Dhan, Groww — and any future broker
that implements the MCP spec's authorization).

Everything is discovery-driven at runtime: the server's
/.well-known/oauth-protected-resource names its authorization server, whose own
/.well-known/oauth-authorization-server yields the authorize/token/register
endpoints — so no per-broker endpoint hardcoding beyond the MCP URL itself.
Dynamic client registration + PKCE + refresh tokens, exactly the flow proven
live with Upstox. Tool names are discovered per-server and matched by name
heuristics. Read-only use: only profile/holdings/quote-style tools are called.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional
from urllib.parse import urljoin

import requests

from portfolio_pulse.broker.mcp_core import MCPError, MCPHTTPClient, NotLoggedIn

REDIRECT_URI = "http://localhost:8756/callback"

BROKER_MCP_URLS = {
    "dhan": "https://mcp.dhan.co/mcp",
    "groww": "https://mcp.groww.in/mcp/",
}


def discover(mcp_url: str) -> dict:
    """Resolve authorize/token/register endpoints for an MCP resource URL."""
    from urllib.parse import urlparse

    p = urlparse(mcp_url)
    root = f"{p.scheme}://{p.netloc}"
    auth_servers = [root]
    try:
        meta = requests.get(f"{root}/.well-known/oauth-protected-resource",
                            timeout=15).json()
        auth_servers = meta.get("authorization_servers") or [root]
    except Exception:
        pass
    last_err = "no authorization server metadata found"
    for server in auth_servers + [root]:
        try:
            m = requests.get(urljoin(server if server.endswith("/") else server + "/",
                                     ".well-known/oauth-authorization-server"),
                             timeout=15).json()
            if m.get("authorization_endpoint") and m.get("token_endpoint"):
                return {
                    "authorize": m["authorization_endpoint"],
                    "token": m["token_endpoint"],
                    "register": m.get("registration_endpoint", ""),
                }
        except Exception as exc:
            last_err = str(exc)[:120]
    raise MCPError(f"OAuth discovery failed for {mcp_url}: {last_err}")


class OAuthMCPClient(MCPHTTPClient):
    """MCP client whose auth is a bearer token managed via OAuth + refresh."""

    def __init__(self, store, broker: str, url: Optional[str] = None):
        self.broker = broker
        self._oauth_key = f"{broker}_oauth"
        self.session_meta_key = f"{broker}_mcp_session"
        super().__init__(store, url or BROKER_MCP_URLS[broker])

    # -- token plumbing -------------------------------------------------------
    def load_oauth(self) -> dict:
        raw = self.store.get_meta(self._oauth_key) if self.store else None
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    def save_oauth(self, rec: dict) -> None:
        self.store.set_meta(self._oauth_key, json.dumps(rec))

    def refresh_access(self) -> Optional[dict]:
        rec = self.load_oauth()
        if not rec.get("refresh_token") or not rec.get("client_id"):
            return None
        try:
            resp = requests.post(rec.get("token_endpoint", ""), data={
                "grant_type": "refresh_token",
                "refresh_token": rec["refresh_token"],
                "client_id": rec["client_id"],
            }, timeout=20)
            if resp.status_code >= 400:
                return None
            tok = resp.json()
            rec["access_token"] = tok.get("access_token", rec["access_token"])
            rec["refresh_token"] = tok.get("refresh_token", rec["refresh_token"])
            rec["expires_at"] = time.time() + float(tok.get("expires_in", 3600))
            self.save_oauth(rec)
            return rec
        except requests.RequestException:
            return None

    def _auth_headers(self) -> dict[str, str]:
        if self.store is None:
            return {}
        rec = self.load_oauth()
        if not rec.get("access_token"):
            return {}
        if time.time() > rec.get("expires_at", 0) - 60:
            rec = self.refresh_access() or rec
        return {"Authorization": f"Bearer {rec.get('access_token', '')}"}

    def connected(self) -> bool:
        if not self.load_oauth().get("access_token"):
            return False
        try:
            self.ensure_session()
            self.list_tools()
            return True
        except (NotLoggedIn, MCPError):
            return False

    # -- tool discovery + KiteConnect-compatible surface ----------------------
    _tools_cache: Optional[list[dict]] = None

    def _tool_named(self, *keywords: str) -> Optional[dict]:
        if self._tools_cache is None:
            self._tools_cache = self.list_tools()
        for t in self._tools_cache:
            name = t.get("name", "").lower()
            if all(k in name for k in keywords):
                return t
        return None

    def holdings(self) -> list[dict[str, Any]]:
        tool = self._tool_named("holding") or self._tool_named("portfolio")
        if not tool:
            raise MCPError(f"no holdings tool on {self.broker} MCP")
        data = self._json(self.call_tool(tool["name"]))
        if isinstance(data, dict):
            for key in ("holdings", "data", "items", "result", "payload"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        if not isinstance(data, list):
            raise MCPError(f"unexpected holdings shape from {self.broker}")
        out = []
        for h in data:
            if not isinstance(h, dict):
                continue
            out.append({
                "tradingsymbol": (h.get("tradingsymbol") or h.get("trading_symbol")
                                  or h.get("tradingSymbol") or h.get("symbol")
                                  or h.get("nseTradingSymbol") or ""),
                "quantity": h.get("quantity", h.get("qty", h.get("netQty", 0))),
                "average_price": h.get("average_price",
                                       h.get("avg_price", h.get("avgPrice", 0))),
                "last_price": h.get("last_price", h.get("ltp", h.get("lastPrice", 0))),
                "company_name": h.get("company_name", h.get("companyName",
                                      h.get("name", ""))),
            })
        return [r for r in out if r["tradingsymbol"]]

    def ltp(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        raise MCPError(f"{self.broker} quote mapping not verified yet")
