"""Morning re-auth nudge (~08:15 IST): auto-send a login link when the broker
session has expired (daily SEBI rule).

Two broker paths, checked in order:
  * Kite Connect API (if configured): its login URL is static, valid all day.
  * Kite MCP (default): a FRESH authorise link is minted at send time — these
    are time-signed and die within minutes, so the message says to tap right
    away and to use /connect for a new one if it has gone stale.

Quiet whenever a session (either path) is still live, or PP_AUTH_NUDGE=off.
"""

from __future__ import annotations

from portfolio_pulse.broker import kite_auth
from portfolio_pulse.notify import telegram
from portfolio_pulse.store import get_store


def run(force: bool = False) -> dict:
    from portfolio_pulse import config
    from portfolio_pulse.broker.kite_mcp import KiteMCPClient, MCPError

    if not config.AUTH_NUDGE and not force:
        # Set-and-forget mode: user opted out of the daily reminder
        # (PP_AUTH_NUDGE=off). All non-broker alerts still run.
        return {"sent": False, "reason": "nudge disabled (PP_AUTH_NUDGE=off)"}

    store = get_store()
    if kite_auth.token_is_fresh(store) and not force:
        return {"sent": False, "reason": "API token already fresh"}

    mcp = KiteMCPClient(store)
    if not force and mcp.session_id and mcp.logged_in():
        return {"sent": False, "reason": "MCP session still live"}

    # Session expired -> send a login link for whichever path is configured.
    if config.KITE_API_KEY:
        try:
            url = kite_auth.build_login_url()
            msg = (
                "🔐 <b>Portfolio Pulse — daily Kite login</b>\n"
                "Your broker session expired overnight. Tap to re-authorise so "
                "holdings and price cross-checks keep working today:\n"
                f'<a href="{url}">Log in to Kite</a>\n'
                "<i>Filings, news and DMA alerts continue even if you skip this.</i>"
            )
            sent = telegram.send_message(msg)
            return {"sent": sent, "path": "api", "url": url}
        except Exception:
            pass  # fall through to the MCP link

    try:
        mcp.ensure_session()
        url = mcp.login_url()
    except MCPError as exc:
        return {"sent": False, "reason": f"cannot mint MCP login link: {exc}"}
    msg = (
        "🔐 <b>Portfolio Pulse — broker session expired</b>\n"
        "Tap to reconnect Zerodha (read-only, via official Kite MCP):\n"
        f'<a href="{url}">Login with Kite</a>\n'
        "⏱ <b>This link dies in a few minutes.</b> Seeing it late? Just send "
        "/connect for a fresh one.\n"
        "<i>Filings, news and DMA alerts continue even if you skip this — "
        "reconnecting only refreshes holdings & price double-checks.</i>"
    )
    sent = telegram.send_message(msg)
    return {"sent": sent, "path": "mcp", "url": url}


if __name__ == "__main__":
    print(run())
