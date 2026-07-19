"""Broker connectivity. Two interchangeable paths, one interface:

  * Kite MCP (default)  — Zerodha's hosted MCP server; no API app/key needed.
  * Kite Connect API    — optional; used automatically when configured.

`get_broker(store)` returns whichever client currently has a live session (API
first, then MCP), or None. Both expose `.holdings()` and `.ltp()`, which is all
the rest of the codebase uses — everything downstream is broker-path agnostic.
"""

from __future__ import annotations

from portfolio_pulse.broker import kite_auth


def get_broker(store):
    """An authenticated broker client (API or MCP), or None if neither is live."""
    kite = kite_auth.get_client(store)
    if kite is not None:
        return kite
    from portfolio_pulse.broker.kite_mcp import KiteMCPClient

    mcp = KiteMCPClient(store)
    if mcp.session_id and mcp.logged_in():
        return mcp
    return None
