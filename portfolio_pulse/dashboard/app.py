"""Portfolio Pulse dashboard (Streamlit).

Read-mostly companion to the Telegram alerts: holdings, watchlist manager, the
live alert feed with QC badges + source links, and per-stock DMA status. Also
serves as the Kite token-capture callback: when Kite redirects back with
?request_token=..., this page exchanges it and stores the daily access token.

Runs locally against SQLite, or on Streamlit Community Cloud against Supabase.
On Cloud, put credentials in st.secrets; the block below mirrors them into env
BEFORE importing the app package (config reads env at import time).
"""

from __future__ import annotations

import os

import streamlit as st

# --- bridge Streamlit Cloud secrets -> environment (must precede pp imports) ---
for _key in ("PP_STORE_BACKEND", "SUPABASE_URL", "SUPABASE_KEY",
             "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY",
             "KITE_API_KEY", "KITE_API_SECRET"):
    try:
        if _key in st.secrets and not os.environ.get(_key):
            os.environ[_key] = str(st.secrets[_key])
    except Exception:
        pass  # no secrets.toml locally — env vars are used instead

from portfolio_pulse import config          # noqa: E402
from portfolio_pulse.broker import kite_auth  # noqa: E402
from portfolio_pulse.store import get_store  # noqa: E402

st.set_page_config(page_title="Portfolio Pulse", page_icon="📡", layout="wide")

_QC_COLOR = {
    "CONFIRMED": "#1a7f37", "SINGLE-SOURCE": "#9a6700", "PARTIAL": "#9a6700",
    "INSUFFICIENT": "#9a6700", "SUSPECT": "#cf222e", "NO-DATA": "#57606a",
}
_TYPE_LABEL = {
    "filing": "Exchange Filing", "news": "News", "dma_forming": "DMA forming",
    "dma_confirmed": "Death cross", "golden_cross": "Golden cross",
}


def _store():
    return get_store()


def _handle_token_callback(store) -> None:
    """If Kite redirected here with a request_token, exchange and store it."""
    params = st.query_params
    token = params.get("request_token")
    if not token:
        return
    try:
        kite_auth.exchange_request_token(token, store)
        st.success("Kite session refreshed for today. You can close this tab.")
    except Exception as exc:
        st.error(f"Could not exchange request token: {exc}")
    finally:
        st.query_params.clear()


def _mcp_session_live() -> bool:
    """Probe the MCP session. ~0.5s network call each render — cheap enough, and
    deliberately uncached: a cached False would wrongly show 'Stale' for minutes
    right after the user logs in (and vice versa after expiry)."""
    try:
        from portfolio_pulse.broker.kite_mcp import KiteMCPClient

        mcp = KiteMCPClient(get_store())
        return bool(mcp.session_id) and mcp.logged_in()
    except Exception:
        return False


def _status_bar(store) -> None:
    api_fresh = kite_auth.token_is_fresh(store)
    mcp_live = False if api_fresh else _mcp_session_live()
    holds = store.get_holdings()
    alerts = store.list_alerts(limit=1)
    c1, c2, c3, c4 = st.columns(4)
    label = "Fresh ✅ (API)" if api_fresh else ("Fresh ✅ (MCP)" if mcp_live else "Stale ⚠️")
    c1.metric("Broker session", label)
    c2.metric("Holdings", len(holds))
    c3.metric("Watchlist", len(store.list_watch("watch")))
    c4.metric("Latest alert", alerts[0].created_at[:16].replace("T", " ") if alerts else "—")
    if not api_fresh and not mcp_live:
        if config.KITE_API_KEY:
            try:
                url = kite_auth.build_login_url()
                st.warning(f"Broker session expired. [Log in to Kite]({url}) to refresh holdings & price cross-checks.")
                return
            except Exception:
                pass
        st.warning(
            "Broker session expired. Run `python -m portfolio_pulse.jobs.mcp_sync` "
            "and tap the Login-with-Kite link to reconnect. Filings, news and DMA "
            "alerts continue meanwhile."
        )


def _holdings_section(store) -> None:
    st.subheader("Holdings")
    rows = store.get_holdings()
    if not rows:
        st.info("No holdings synced yet. They sync automatically after the morning Kite login.")
        return
    st.dataframe(
        [{"Symbol": r["symbol"], "Qty": r["qty"], "Avg": r["avg_price"],
          "Last": r["last_price"],
          "P&L %": round((r["last_price"] - r["avg_price"]) / r["avg_price"] * 100, 2)
          if r["avg_price"] else None}
         for r in rows],
        use_container_width=True, hide_index=True,
    )


def _watchlist_section(store) -> None:
    st.subheader("Watchlist")
    with st.form("add_watch", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        sym = c1.text_input("Add symbol (NSE)", placeholder="e.g. INFY", label_visibility="collapsed")
        if c2.form_submit_button("Add", use_container_width=True) and sym.strip():
            store.add_watch(sym.strip(), kind="watch")
            st.rerun()
    watch = store.list_watch("watch")
    if not watch:
        st.caption("No watchlist symbols yet. Add above, or use /add in Telegram.")
        return
    for w in watch:
        c1, c2 = st.columns([4, 1])
        c1.write(f"**{w.symbol}** — {w.name or '—'}")
        if c2.button("Remove", key=f"rm_{w.symbol}", use_container_width=True):
            store.remove_watch(w.symbol)
            st.rerun()


def _dma_section(store) -> None:
    st.subheader("DMA status")
    symbols = store.all_symbols()
    rows = []
    for s in symbols:
        d = store.get_dma_state(s)
        if not d:
            continue
        rows.append({
            "Symbol": s, "Relation": d.get("relation"),
            "50-DMA": round(d["sma50"], 2) if d.get("sma50") else None,
            "200-DMA": round(d["sma200"], 2) if d.get("sma200") else None,
            "Gap %": round((d.get("gap_pct") or 0) * 100, 2),
            "Proj. days": round(d["projected_days"], 1) if d.get("projected_days") else None,
            "Updated": (d.get("updated_at") or "")[:16].replace("T", " "),
        })
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption("DMA status appears after the first evening scan.")


def _alert_card(a) -> None:
    color = _QC_COLOR.get(a.qc_status, "#57606a")
    type_label = _TYPE_LABEL.get(a.alert_type, a.alert_type)
    when = a.created_at[:16].replace("T", " ")
    src = f'&nbsp;·&nbsp;<a href="{a.source_url}" target="_blank">source</a>' if a.source_url else ""
    body = a.summary if a.summary and a.summary != a.title else ""
    impact = f'<div style="color:#57606a;font-size:0.85em">{a.impact}</div>' if a.impact else ""
    st.markdown(
        f"""<div style="border:1px solid #d0d7de;border-left:4px solid {color};
        border-radius:8px;padding:10px 14px;margin-bottom:8px">
        <div style="font-size:0.8em;color:#57606a">{when} · {a.symbol} · {a.source_type or type_label}
        · <span style="color:{color}">{a.qc_status}</span>{src}</div>
        <div style="font-weight:600">{a.title}</div>
        <div>{body}</div>{impact}</div>""",
        unsafe_allow_html=True,
    )


def _alerts_section(store) -> None:
    st.subheader("Alert feed")
    symbols = ["(all)"] + store.all_symbols()
    c1, c2 = st.columns([1, 1])
    pick = c1.selectbox("Symbol", symbols, label_visibility="collapsed")
    limit = c2.slider("Show", 10, 200, 40, label_visibility="collapsed")
    alerts = store.list_alerts(limit=limit,
                              symbol=None if pick == "(all)" else pick)
    if not alerts:
        st.info("No alerts yet.")
        return
    for a in alerts:
        _alert_card(a)


def main() -> None:
    store = _store()
    _handle_token_callback(store)

    st.title("📡 Portfolio Pulse")
    st.caption("NSE filings, verified news, and 50/200-DMA crosses for your holdings "
               "& watchlist. Alerts also delivered to Telegram. Not investment advice.")
    _status_bar(store)
    st.divider()

    left, right = st.columns([2, 1])
    with left:
        _holdings_section(store)
        _dma_section(store)
        _alerts_section(store)
    with right:
        _watchlist_section(store)


if __name__ == "__main__":
    main()
else:
    main()
