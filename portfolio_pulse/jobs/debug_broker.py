"""Diagnostic: show exactly what happens when THIS machine talks to the broker
MCP endpoints. Run via the debug-broker workflow to see the cloud runner's view.
Prints status/errors only — never token values.
"""

from __future__ import annotations

import requests

from portfolio_pulse import config
from portfolio_pulse.store import get_store


def probe(label: str, url: str, headers: dict) -> None:
    try:
        resp = requests.post(url, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "portfolio-pulse", "version": "0.1.0"}},
        }, headers=headers, timeout=20)
        body = resp.text[:200].replace("\n", " ")
        print(f"{label}: HTTP {resp.status_code} | server={resp.headers.get('server','?')} "
              f"| body: {body}")
    except Exception as exc:
        print(f"{label}: EXCEPTION {type(exc).__name__}: {str(exc)[:200]}")


def run() -> None:
    base = {"Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"}
    print("=== Kite MCP reachability from this machine ===")
    probe("default UA (python-requests)", config.KITE_MCP_URL, base)
    probe("browser UA", config.KITE_MCP_URL, {**base, **config.HTTP_HEADERS,
                                              "Content-Type": "application/json",
                                              "Accept": "application/json, text/event-stream"})

    print("=== Stored-session probe (get_profile) ===")
    try:
        from portfolio_pulse.broker.kite_mcp import KiteMCPClient

        mcp = KiteMCPClient(get_store())
        print("session id present:", bool(mcp.session_id))
        text = mcp.call_tool("get_profile")
        print("get_profile OK:", text[:80])
    except Exception as exc:
        print(f"get_profile FAILED: {type(exc).__name__}: {str(exc)[:300]}")


if __name__ == "__main__":
    run()
