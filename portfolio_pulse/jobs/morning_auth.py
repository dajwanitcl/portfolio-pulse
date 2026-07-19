"""Morning re-auth nudge (~08:15 IST): prompt the user to refresh the Kite token.

Kite tokens expire daily, so once each trading morning we send the login link to
Telegram. The user taps it, authorises at Kite, and the redirect lands on the
token-capture callback (dashboard.callback / an edge function) which exchanges the
request_token and stores the fresh access token. If a fresh token already exists
(e.g. the user re-authed manually), we stay quiet.
"""

from __future__ import annotations

from portfolio_pulse.broker import kite_auth
from portfolio_pulse.notify import telegram
from portfolio_pulse.store import get_store


def run(force: bool = False) -> dict:
    from portfolio_pulse import config

    if not config.AUTH_NUDGE and not force:
        # Set-and-forget mode: user synced holdings once and opted out of the
        # daily reminder (PP_AUTH_NUDGE=off). All non-broker alerts still run.
        return {"sent": False, "reason": "nudge disabled (PP_AUTH_NUDGE=off)"}
    if not config.KITE_API_KEY and not force:
        # MCP-only mode: there is no API app to log into, so the daily API login
        # link would be invalid. Re-sync via `jobs.mcp_sync` whenever needed.
        return {"sent": False, "reason": "no Kite API key (MCP-only mode)"}
    store = get_store()
    if kite_auth.token_is_fresh(store) and not force:
        return {"sent": False, "reason": "token already fresh"}
    try:
        url = kite_auth.build_login_url()
    except Exception as exc:  # missing/invalid API key
        return {"sent": False, "reason": f"cannot build login url: {exc}"}
    msg = (
        "🔐 <b>Portfolio Pulse — daily Kite login</b>\n"
        "Your broker session expires each morning. Tap to re-authorise so holdings "
        "and price cross-checks keep working today:\n"
        f'<a href="{url}">Log in to Kite</a>\n'
        "<i>Filings, news and DMA alerts continue even if you skip this.</i>"
    )
    sent = telegram.send_message(msg)
    return {"sent": sent, "url": url}


if __name__ == "__main__":
    print(run())
