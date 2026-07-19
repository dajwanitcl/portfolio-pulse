"""Connect Upstox over MCP OAuth — run this ONCE, locally:

    python -m portfolio_pulse.jobs.upstox_connect

It registers a client with mcp.upstox.com, opens the Upstox login page in your
browser, catches the redirect on localhost:8756, exchanges the code (PKCE), and
stores the tokens in the shared store. From then on the cloud renews access via
the refresh token; when Upstox forces a re-login (daily SEBI rules), the morning
nudge tells you to run this again.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import urllib.parse
import webbrowser

from portfolio_pulse.broker import upstox_mcp
from portfolio_pulse.broker.upstox_mcp import UpstoxMCPClient
from portfolio_pulse.store import get_store

_PORT = 8756


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class _Catcher(http.server.BaseHTTPRequestHandler):
    code: str = ""
    error: str = ""

    def do_GET(self):  # noqa: N802
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Catcher.code = (qs.get("code") or [""])[0]
        _Catcher.error = (qs.get("error") or [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        body = ("<h2>Portfolio Pulse — Upstox connected. You can close this tab.</h2>"
                if _Catcher.code else
                f"<h2>Login failed: {_Catcher.error or 'no code returned'}</h2>")
        self.wfile.write(body.encode())

    def log_message(self, *args):  # silence request logging
        pass


def run(timeout_seconds: int = 300) -> dict:
    store = get_store()
    print("Registering with Upstox MCP...")
    client_id = upstox_mcp.register_client()
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    auth_url = upstox_mcp.AUTHORIZE_ENDPOINT + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id,
        "redirect_uri": upstox_mcp.REDIRECT_URI, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })

    server = http.server.HTTPServer(("localhost", _PORT), _Catcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"\nOpening Upstox login (or open manually):\n  {auth_url}\n")
    webbrowser.open(auth_url)

    import time
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and not (_Catcher.code or _Catcher.error):
        time.sleep(0.5)
    server.shutdown()

    if not _Catcher.code:
        return {"connected": False,
                "reason": _Catcher.error or f"no login within {timeout_seconds}s"}

    print("Exchanging code for tokens...")
    upstox_mcp.exchange_code(store, client_id, _Catcher.code, verifier)

    mcp = UpstoxMCPClient(store)
    if not mcp.connected():
        return {"connected": False, "reason": "token stored but MCP session failed"}
    tools = [t.get("name") for t in mcp.list_tools()]
    print(f"Connected. Upstox MCP exposes {len(tools)} tools: {tools}")

    from portfolio_pulse.broker import holdings as h
    n = h.sync(store, mcp, broker="upstox")
    syms = sorted({r["symbol"] for r in store.get_holdings()})
    print(f"Synced {n} Upstox holdings. Combined portfolio now: {', '.join(syms)}")
    from portfolio_pulse.notify import telegram
    telegram.send_message(f"✅ Upstox connected — {n} holdings synced.")
    return {"connected": True, "synced": n, "tools": tools}


if __name__ == "__main__":
    print(run())
