"""Upstox official MCP client (mcp.upstox.com) with standard MCP OAuth.

Upstox implements the MCP spec's authorization: OAuth 2.0 with dynamic client
registration, PKCE, and refresh tokens (verified live against their
/.well-known metadata). Flow:

  1. jobs/upstox_connect.py (run once, locally): registers a public client,
     opens the authorize URL, catches the code on a localhost callback,
     exchanges it, and stores {client_id, access/refresh tokens} in store meta.
  2. Headless runs (cloud): access token is used until expiry, then renewed via
     the refresh token. When refresh fails (Upstox/SEBI daily rules), the
     morning nudge asks the user to reconnect.

Tool names on the server are discovered at runtime (tools/list) and matched by
name heuristics, so minor server-side renames don't break the client. Upstox's
MCP is read-only by design — no order tools exist on their server at all.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import requests

from portfolio_pulse import config
from portfolio_pulse.broker.mcp_core import MCPError, MCPHTTPClient, NotLoggedIn

_META_KEY = "upstox_oauth"          # JSON: client_id, access_token, refresh_token, expires_at
_BASE = "https://mcp.upstox.com"
AUTHORIZE_ENDPOINT = f"{_BASE}/authorize"
TOKEN_ENDPOINT = f"{_BASE}/token"
REGISTER_ENDPOINT = f"{_BASE}/register"
MCP_ENDPOINT = f"{_BASE}/mcp"
REDIRECT_URI = "http://localhost:8756/callback"


# --------------------------------------------------------------------------- #
# OAuth helpers (used by the connect job and for headless refresh)
# --------------------------------------------------------------------------- #
def load_oauth(store) -> dict:
    raw = store.get_meta(_META_KEY)
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def save_oauth(store, data: dict) -> None:
    store.set_meta(_META_KEY, json.dumps(data))


def register_client() -> str:
    """Dynamic client registration (public client, PKCE). Returns client_id."""
    resp = requests.post(REGISTER_ENDPOINT, json={
        "client_name": "Portfolio Pulse",
        "redirect_uris": [REDIRECT_URI],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }, timeout=20)
    if resp.status_code >= 400:
        raise MCPError(f"registration failed {resp.status_code}: {resp.text[:200]}")
    return resp.json()["client_id"]


def exchange_code(store, client_id: str, code: str, verifier: str) -> dict:
    """Authorization-code exchange; persists and returns the token record."""
    resp = requests.post(TOKEN_ENDPOINT, data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT_URI, "client_id": client_id,
        "code_verifier": verifier,
    }, timeout=20)
    if resp.status_code >= 400:
        raise MCPError(f"token exchange failed {resp.status_code}: {resp.text[:200]}")
    tok = resp.json()
    rec = {
        "client_id": client_id,
        "access_token": tok.get("access_token", ""),
        "refresh_token": tok.get("refresh_token", ""),
        "expires_at": time.time() + float(tok.get("expires_in", 3600)),
    }
    save_oauth(store, rec)
    return rec


def refresh_access(store) -> Optional[dict]:
    """Renew the access token via refresh_token. None if impossible/refused."""
    rec = load_oauth(store)
    if not rec.get("refresh_token") or not rec.get("client_id"):
        return None
    try:
        resp = requests.post(TOKEN_ENDPOINT, data={
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
        save_oauth(store, rec)
        return rec
    except requests.RequestException:
        return None


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class UpstoxMCPClient(MCPHTTPClient):
    session_meta_key = "upstox_mcp_session"

    def __init__(self, store=None, url: str | None = None):
        super().__init__(store, url or config.UPSTOX_MCP_URL)
        self._tools: Optional[list[dict]] = None

    def _auth_headers(self) -> dict[str, str]:
        if self.store is None:
            return {}
        rec = load_oauth(self.store)
        if not rec.get("access_token"):
            return {}
        if time.time() > rec.get("expires_at", 0) - 60:
            rec = refresh_access(self.store) or rec
        return {"Authorization": f"Bearer {rec.get('access_token', '')}"}

    def connected(self) -> bool:
        """True if we hold a working (or refreshable) access token."""
        try:
            self.ensure_session()
            self.list_tools()
            return True
        except (NotLoggedIn, MCPError):
            return False

    # -- tool discovery ------------------------------------------------------
    def _tool_named(self, *keywords: str) -> Optional[dict]:
        if self._tools is None:
            self._tools = self.list_tools()
        for t in self._tools:
            name = t.get("name", "").lower()
            if all(k in name for k in keywords):
                return t
        return None

    def _first_param(self, tool: dict, *hints: str) -> Optional[str]:
        props = (tool.get("inputSchema") or {}).get("properties") or {}
        for hint in hints:
            for p in props:
                if hint in p.lower():
                    return p
        return next(iter(props), None)

    # ---- KiteConnect-compatible surface ------------------------------------
    def holdings(self) -> list[dict[str, Any]]:
        """Holdings normalized to KiteConnect-ish field names."""
        tool = self._tool_named("holding") or self._tool_named("portfolio")
        if not tool:
            raise MCPError("no holdings tool found on Upstox MCP")
        data = self._json(self.call_tool(tool["name"]))
        if isinstance(data, dict):
            for key in ("holdings", "data", "items", "result"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        if not isinstance(data, list):
            raise MCPError(f"unexpected holdings shape: {type(data).__name__}")
        out = []
        for h in data:
            if not isinstance(h, dict):
                continue
            out.append({
                "tradingsymbol": (h.get("tradingsymbol") or h.get("trading_symbol")
                                  or h.get("symbol") or ""),
                "quantity": h.get("quantity", 0),
                "average_price": h.get("average_price", h.get("avg_price", 0)),
                "last_price": h.get("last_price", h.get("ltp", 0)),
                "company_name": h.get("company_name", h.get("name", "")),
            })
        return out

    def ltp(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        """Best-effort LTP keyed like KiteConnect ('NSE:SYM'). Raises on mismatch
        (callers degrade to SINGLE-SOURCE, never a wrong number)."""
        tool = self._tool_named("ltp") or self._tool_named("quote") \
            or self._tool_named("market", "price")
        if not tool:
            raise MCPError("no quote tool found on Upstox MCP")
        param = self._first_param(tool, "instrument", "symbol")
        if not param:
            raise MCPError("quote tool has no usable parameter")
        # Upstox instrument keys use 'NSE_EQ|SYMBOL' style; try both spellings.
        symbols = [i.split(":", 1)[-1] for i in instruments]
        for candidates in ([f"NSE_EQ|{s}" for s in symbols], symbols, instruments):
            try:
                data = self._json(self.call_tool(tool["name"], {param: candidates}))
            except MCPError:
                continue
            out: dict[str, dict[str, Any]] = {}
            flat = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(flat, dict):
                for key, val in flat.items():
                    price = None
                    if isinstance(val, dict):
                        price = val.get("last_price") or val.get("ltp")
                    elif isinstance(val, (int, float)):
                        price = val
                    if price is not None:
                        sym = key.split("|")[-1].split(":")[-1].upper()
                        out[f"NSE:{sym}"] = {"last_price": float(price)}
            if out:
                return out
        raise MCPError("could not map Upstox quote reply to prices")
