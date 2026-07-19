"""Connect Zerodha over MCP and sync holdings — no API app/key needed.

Run:  python -m portfolio_pulse.jobs.mcp_sync

Flow: ensure an MCP session with mcp.kite.trade -> if not yet authorised, print
(and Telegram) the "Login with Kite" URL -> poll until the user taps it -> pull
holdings, enrich company names via instrument search, sync into the store.

Re-run whenever your portfolio changes. Between runs the system needs no broker
session at all: filings, news and DMA alerts keep flowing (see README
"set-and-forget"); while the MCP session stays alive (until Kite's daily expiry)
DMA alerts additionally get the second-source price verification.
"""

from __future__ import annotations

import time

from portfolio_pulse.broker import holdings
from portfolio_pulse.broker.kite_mcp import KiteMCPClient, MCPError
from portfolio_pulse.notify import telegram
from portfolio_pulse.store import get_store


def run(wait_minutes: float = 3.0, poll_seconds: float = 5.0) -> dict:
    store = get_store()
    mcp = KiteMCPClient(store)
    try:
        mcp.ensure_session()
    except MCPError as exc:
        return {"synced": 0, "reason": f"cannot reach MCP server: {exc}"}

    if not mcp.logged_in():
        try:
            url = mcp.login_url()
        except MCPError as exc:
            return {"synced": 0, "reason": f"login tool failed: {exc}"}
        print(f"\nAuthorise Zerodha access (opens Kite login):\n  {url}\n")
        telegram.send_message(
            "🔐 <b>Connect Zerodha (MCP)</b>\n"
            f'<a href="{url}">Login with Kite</a> to let Portfolio Pulse read '
            "your holdings. Read-only; no order access is ever used."
        )
        deadline = time.monotonic() + wait_minutes * 60
        while time.monotonic() < deadline:
            if mcp.logged_in():
                break
            time.sleep(poll_seconds)
        else:
            return {"synced": 0,
                    "reason": f"login not completed within {wait_minutes:g} min "
                              "— tap the link and re-run"}

    try:
        n = holdings.sync(store, mcp)
    except MCPError as exc:
        return {"synced": 0, "reason": f"holdings fetch failed: {exc}"}
    syms = [r["symbol"] for r in store.get_holdings()]
    msg = f"✅ Synced {n} holdings from Zerodha: {', '.join(syms) or '—'}"
    print(msg)
    telegram.send_message(msg)
    return {"synced": n, "symbols": syms}


if __name__ == "__main__":
    print(run())
