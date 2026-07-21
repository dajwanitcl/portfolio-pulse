"""Connect any supported broker over MCP — run locally, once per broker:

    python -m portfolio_pulse.jobs.broker_connect dhan
    python -m portfolio_pulse.jobs.broker_connect groww
    python -m portfolio_pulse.jobs.broker_connect fyers
    (zerodha uses mcp_sync; upstox uses upstox_connect — unchanged)

OAuth brokers (dhan, groww): full discovery -> dynamic registration -> PKCE ->
browser login -> localhost callback -> tokens stored; the cloud then renews via
refresh tokens. Fyers: handshake first, then whatever auth the server asks for
(login tool URL or bearer), reported interactively.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser

import requests

from portfolio_pulse.broker import holdings
from portfolio_pulse.broker.mcp_core import MCPError
from portfolio_pulse.store import get_store

_PORT = 8756
REDIRECT_URI = f"http://localhost:{_PORT}/callback"


class _Catcher(http.server.BaseHTTPRequestHandler):
    code = ""
    error = ""

    def do_GET(self):  # noqa: N802
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Catcher.code = (qs.get("code") or [""])[0]
        _Catcher.error = (qs.get("error") or [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write((
            "<h2>Portfolio Pulse — broker connected. Close this tab.</h2>"
            if _Catcher.code else
            f"<h2>Login failed: {_Catcher.error or 'no code returned'}</h2>"
        ).encode())

    def log_message(self, *a):
        pass


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def connect_oauth(broker: str, timeout_seconds: int = 300) -> dict:
    from portfolio_pulse.broker.mcp_oauth import OAuthMCPClient, discover

    store = get_store()
    client = OAuthMCPClient(store, broker)
    print(f"Discovering {broker} OAuth endpoints...")
    ep = discover(client.url)
    print("Registering client...")
    resp = requests.post(ep["register"], json={
        "client_name": "Portfolio Pulse",
        "redirect_uris": [REDIRECT_URI],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }, timeout=20)
    if resp.status_code >= 400:
        return {"connected": False,
                "reason": f"registration failed {resp.status_code}: {resp.text[:150]}"}
    client_id = resp.json()["client_id"]

    verifier, challenge = _pkce()
    auth_url = ep["authorize"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id,
        "redirect_uri": REDIRECT_URI, "state": secrets.token_urlsafe(16),
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    server = http.server.HTTPServer(("localhost", _PORT), _Catcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"\nOpening {broker} login (or open manually):\n  {auth_url}\n")
    webbrowser.open(auth_url)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and not (_Catcher.code or _Catcher.error):
        time.sleep(0.5)
    server.shutdown()
    if not _Catcher.code:
        return {"connected": False,
                "reason": _Catcher.error or "login not completed in time"}

    print("Exchanging code for tokens...")
    tok = requests.post(ep["token"], data={
        "grant_type": "authorization_code", "code": _Catcher.code,
        "redirect_uri": REDIRECT_URI, "client_id": client_id,
        "code_verifier": verifier,
    }, timeout=20)
    if tok.status_code >= 400:
        return {"connected": False,
                "reason": f"token exchange failed: {tok.text[:150]}"}
    t = tok.json()
    client.save_oauth({
        "client_id": client_id, "token_endpoint": ep["token"],
        "access_token": t.get("access_token", ""),
        "refresh_token": t.get("refresh_token", ""),
        "expires_at": time.time() + float(t.get("expires_in", 3600)),
    })
    if not client.connected():
        return {"connected": False, "reason": "tokens stored but MCP session failed"}
    tools = [x.get("name") for x in client.list_tools()]
    print(f"Connected. {broker} MCP exposes {len(tools)} tools.")
    n = holdings.sync(get_store(), client, broker=broker)
    print(f"Synced {n} {broker} holdings.")
    from portfolio_pulse.notify import telegram
    telegram.send_message(f"✅ {broker.title()} connected — {n} holdings synced.")
    return {"connected": True, "synced": n, "tools": tools}


def connect_fyers(timeout_seconds: int = 300) -> dict:
    from portfolio_pulse.broker.fyers_mcp import FyersMCPClient

    store = get_store()
    mcp = FyersMCPClient(store)
    mcp.ensure_session()
    if mcp.logged_in():
        n = holdings.sync(store, mcp, broker="fyers")
        return {"connected": True, "synced": n}
    try:
        url = mcp.login_url()
    except MCPError as exc:
        return {"connected": False, "reason": f"auth probe failed: {exc}"}
    if not url:
        try:
            mcp.call_tool("get_holdings")
        except MCPError as exc:
            return {"connected": False,
                    "reason": f"Fyers wants auth this client doesn't speak yet — "
                              f"server said: {str(exc)[:200]}"}
        return {"connected": False, "reason": "unexpected: no auth needed but not logged in"}
    print(f"\nOpen this Fyers login and approve access:\n  {url}\n")
    webbrowser.open(url)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if mcp.logged_in():
            n = holdings.sync(store, mcp, broker="fyers")
            from portfolio_pulse.notify import telegram
            telegram.send_message(f"✅ Fyers connected — {n} holdings synced.")
            return {"connected": True, "synced": n}
        time.sleep(5)
    return {"connected": False, "reason": "login not completed in time"}


def run(broker: str) -> dict:
    broker = broker.strip().lower()
    if broker in ("dhan", "groww"):
        return connect_oauth(broker)
    if broker == "fyers":
        return connect_fyers()
    return {"connected": False,
            "reason": "use jobs.mcp_sync for zerodha, jobs.upstox_connect for upstox"}


if __name__ == "__main__":
    print(run(sys.argv[1] if len(sys.argv) > 1 else ""))
