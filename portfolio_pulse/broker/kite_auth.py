"""Zerodha Kite Connect authentication with daily-token handling.

Kite access tokens expire every day ~6 AM IST (SEBI-mandated), so a 24/7 system
must re-authorise once per day. Flow:

  1. Morning job sends the login URL (build_login_url) to Telegram.
  2. User taps it, logs in at Kite, and is redirected to the app's registered
     redirect URL carrying `request_token`.
  3. A tiny callback (Streamlit page / edge function) calls
     exchange_request_token(request_token) -> access_token, stored in the DB.
  4. Poller jobs call get_client(); if the stored token is stale/missing they
     skip broker-dependent work gracefully (filings/news/DMA still run).

The API key/secret are read from the environment; the access token lives in the
store so every host (poller, dashboard) shares one session.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from portfolio_pulse import config


def _kite(api_key: Optional[str] = None):
    from kiteconnect import KiteConnect
    return KiteConnect(api_key=api_key or config.KITE_API_KEY)


def build_login_url() -> str:
    """The URL the user taps to authorise for the day."""
    return _kite().login_url()


def exchange_request_token(request_token: str, store) -> str:
    """Exchange a request_token for an access_token and persist it. Returns token."""
    kite = _kite()
    data = kite.generate_session(request_token, api_secret=config.KITE_API_SECRET)
    access_token = data["access_token"]
    store.save_token(access_token, data.get("public_token", ""))
    return access_token


def token_is_fresh(store, now: Optional[datetime] = None) -> bool:
    """True if a token exists and was issued on the current IST calendar day.

    Kite tokens die at ~6 AM IST; treating 'issued today (IST)' as fresh is the
    simple, correct rule for a once-daily re-auth.
    """
    rec = store.load_token()
    if not rec or not rec.get("access_token"):
        return False
    try:
        issued = datetime.fromisoformat(rec["issued_at"])
    except (ValueError, KeyError, TypeError):
        return False
    now = now or config.now_ist()
    issued_ist = issued.astimezone(config.IST)
    return issued_ist.date() == now.astimezone(config.IST).date()


def get_client(store):
    """Return an authenticated KiteConnect client, or None if no fresh token.

    Callers must treat None as 'broker temporarily unavailable' and continue with
    the non-broker parts of the pipeline.
    """
    if not token_is_fresh(store):
        return None
    rec = store.load_token()
    kite = _kite()
    kite.set_access_token(rec["access_token"])
    return kite
