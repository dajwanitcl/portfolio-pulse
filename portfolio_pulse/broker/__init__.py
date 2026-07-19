"""Broker connectivity. Multiple brokers, one interface.

Every client — Kite Connect API, Kite MCP, Upstox MCP — exposes `.holdings()`
and `.ltp()`, which is all the rest of the codebase uses. `get_live_brokers()`
returns every currently-authorised connection as (name, client) pairs;
`get_broker()` returns the first live one (enough for price cross-checks).
"""

from __future__ import annotations

from portfolio_pulse.broker import kite_auth


def get_live_brokers(store) -> list[tuple[str, object]]:
    """All currently-authorised broker connections, most-established first."""
    live: list[tuple[str, object]] = []

    kite_api = kite_auth.get_client(store)
    if kite_api is not None:
        live.append(("zerodha", kite_api))
    else:
        from portfolio_pulse.broker.kite_mcp import KiteMCPClient

        kite = KiteMCPClient(store)
        if kite.session_id and kite.logged_in():
            live.append(("zerodha", kite))

    from portfolio_pulse.broker.upstox_mcp import UpstoxMCPClient, load_oauth

    if load_oauth(store).get("access_token"):
        upstox = UpstoxMCPClient(store)
        if upstox.connected():
            live.append(("upstox", upstox))

    return live


def get_broker(store):
    """First live broker client, or None. Callers that only need quotes/prices
    can use any live connection; holdings sync should iterate get_live_brokers."""
    live = get_live_brokers(store)
    return live[0][1] if live else None
