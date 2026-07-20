"""Portfolio Pulse dashboard (Streamlit) — state companion to the Telegram alerts.

Built around the three questions the alert stream can't answer at a glance:
  1. Cross Radar  — which stocks are close to a death/golden cross, and how close?
  2. Portfolio    — P&L per holding and for the whole book.
  3. Stock History — everything that happened to one stock over a chosen window.

Also serves as the Kite token-capture callback for the (optional) API auth path.
Runs locally against SQLite, or on Streamlit Community Cloud against Supabase.
On Cloud, st.secrets are mirrored into env BEFORE importing the app package
(config reads env at import time).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import streamlit as st

# Streamlit Cloud launches this file from its own subfolder, so the repo root
# (which holds the portfolio_pulse package) isn't importable without this.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

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

# Dark-surface status colors (status is never color-alone here: every badge and
# QC label pairs the color with its text/icon).
_QC_COLOR = {
    "CONFIRMED": "#34C08B", "SINGLE-SOURCE": "#E2B93B", "PARTIAL": "#E2B93B",
    "INSUFFICIENT": "#E2B93B", "SUSPECT": "#F87171", "NO-DATA": "#8B93A7",
}

_CSS = """
<style>
/* ---- hide Streamlit chrome for an app-like feel ---- */
#MainMenu, footer, header[data-testid="stHeader"] {visibility: hidden; height: 0;}
.block-container {padding-top: 2.2rem; max-width: 1200px;}

/* ---- typography ---- */
html, body, [class*="css"] {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
}
h1 {letter-spacing: -0.02em; font-weight: 750;}
h4 {letter-spacing: -0.01em; color: #C7CEDB;}

/* ---- metric tiles as cards ---- */
div[data-testid="stMetric"] {
  background: linear-gradient(180deg, #171F2E 0%, #131A27 100%);
  border: 1px solid #232D40;
  border-radius: 12px;
  padding: 14px 16px 10px 16px;
}
div[data-testid="stMetric"] label {color: #98A2B3 !important; font-size: 0.78rem;
  text-transform: uppercase; letter-spacing: 0.06em;}
div[data-testid="stMetricValue"] {
  font-variant-numeric: tabular-nums; font-weight: 650; font-size: 1.55rem;
}
div[data-testid="stMetricDelta"] {font-variant-numeric: tabular-nums;}

/* ---- tabs ---- */
button[data-baseweb="tab"] {
  font-weight: 600; letter-spacing: 0.01em; padding: 0.6rem 1.1rem;
}
div[data-baseweb="tab-highlight"] {background-color: #2DD4BF;}

/* ---- dataframes ---- */
div[data-testid="stDataFrame"] {
  border: 1px solid #232D40; border-radius: 12px; overflow: hidden;
}

/* ---- buttons & inputs ---- */
button[kind="secondaryFormSubmit"], .stButton button {
  border-radius: 10px; border: 1px solid #2A3548;
}
.stButton button:hover {border-color: #2DD4BF; color: #2DD4BF;}

/* ---- pulse dot in the title ---- */
.pulse-dot {
  display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  background: #2DD4BF; margin-right: 10px; vertical-align: middle;
  box-shadow: 0 0 0 rgba(45, 212, 191, 0.6); animation: pulse 2.2s infinite;
}
@keyframes pulse {
  0% {box-shadow: 0 0 0 0 rgba(45, 212, 191, 0.45);}
  70% {box-shadow: 0 0 0 12px rgba(45, 212, 191, 0);}
  100% {box-shadow: 0 0 0 0 rgba(45, 212, 191, 0);}
}
</style>
"""
_TYPE_LABEL = {
    "filing": "Exchange Filing", "news": "News", "dma_forming": "DMA forming",
    "dma_confirmed": "Death cross", "golden_cross": "Golden cross",
}

_RELATION = {
    # relation -> (badge, blurb, sort-bucket)  bucket: 0 = most urgent
    "above_forming": ("🟠 Death cross forming", "50-DMA closing in on 200-DMA from above", 0),
    "below_forming": ("🔵 Golden cross forming", "50-DMA closing in on 200-DMA from below", 1),
    "above": ("🟢 Above", "50-DMA above 200-DMA", 2),
    "below": ("🔴 Below", "50-DMA below 200-DMA (death cross in effect)", 3),
    "unknown": ("⚪ Insufficient history", "needs ~200 trading days of data", 4),
}


def _store():
    return get_store()


def _handle_token_callback(store) -> None:
    """If Kite (API path) redirected here with a request_token, exchange it."""
    token = st.query_params.get("request_token")
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
    """Probe the MCP session. ~0.5s network call each render — deliberately
    uncached: a cached value would show a wrong state for minutes around
    login/expiry transitions."""
    try:
        from portfolio_pulse.broker.kite_mcp import KiteMCPClient

        mcp = KiteMCPClient(get_store())
        return bool(mcp.session_id) and mcp.logged_in()
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Data shaping
# --------------------------------------------------------------------------- #
def _holdings_rows(store) -> list[dict]:
    rows = []
    for r in store.get_holdings():
        qty, avg, last = r["qty"], r["avg_price"], r["last_price"]
        invested = qty * avg
        value = qty * last
        rows.append({
            "Symbol": r["symbol"], "Qty": qty, "Avg ₹": round(avg, 2),
            "Last ₹": round(last, 2), "Invested ₹": round(invested, 0),
            "Value ₹": round(value, 0), "P&L ₹": round(value - invested, 0),
            "P&L %": round((last - avg) / avg * 100, 2) if avg else None,
        })
    return rows


def _broker_holdings(store) -> dict[str, list[dict]]:
    """Per-broker holdings straight from the raw per-broker records (no merging).

    The user chose separate broker sections over a combined book: a stock held
    at both brokers appears in each section with that broker's own qty/avg.
    """
    import json as _json

    out: dict[str, list[dict]] = {}
    for broker in ("zerodha", "upstox"):
        raw = store.get_meta(f"holdings:{broker}")
        if not raw:
            continue
        try:
            recs = _json.loads(raw)
        except _json.JSONDecodeError:
            continue
        rows = []
        for r in recs:
            qty = float(r.get("qty", 0))
            avg = float(r.get("avg_price", 0))
            last = float(r.get("last_price", 0))
            rows.append({
                "Symbol": r.get("symbol", ""), "Qty": qty, "Avg ₹": round(avg, 2),
                "Last ₹": round(last, 2), "Invested ₹": round(qty * avg, 0),
                "Value ₹": round(qty * last, 0), "P&L ₹": round(qty * (last - avg), 0),
                "P&L %": round((last - avg) / avg * 100, 2) if avg else None,
            })
        if rows:
            out[broker] = sorted(rows, key=lambda r: r["Symbol"])
    return out


def _radar_rows(store) -> list[dict]:
    rows = []
    for w in store.list_watch():
        d = store.get_dma_state(w.symbol)
        if not d:
            rows.append({"symbol": w.symbol, "relation": "unknown", "gap_pct": None,
                         "proj": None, "sma50": None, "sma200": None, "updated": "—",
                         "kind": w.kind})
            continue
        rows.append({
            "symbol": w.symbol, "relation": d.get("relation") or "unknown",
            "gap_pct": (d.get("gap_pct") or 0) * 100,
            "proj": d.get("projected_days"),
            "sma50": d.get("sma50"), "sma200": d.get("sma200"),
            "updated": (d.get("updated_at") or "")[:16].replace("T", " "),
            "kind": w.kind,
        })
    def sort_key(r):
        bucket = _RELATION.get(r["relation"], _RELATION["unknown"])[2]
        gap = abs(r["gap_pct"]) if r["gap_pct"] is not None else 999
        return (bucket, gap)
    return sorted(rows, key=sort_key)


def _parse_created(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #
def _status_bar(store) -> None:
    api_fresh = kite_auth.token_is_fresh(store)
    mcp_live = False if api_fresh else _mcp_session_live()
    holds = _holdings_rows(store)
    total_val = sum(r["Value ₹"] for r in holds)
    total_pnl = sum(r["P&L ₹"] for r in holds)
    forming = sum(1 for r in _radar_rows(store) if r["relation"].endswith("_forming"))
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Broker session",
              "Fresh ✅ (API)" if api_fresh else ("Fresh ✅ (MCP)" if mcp_live else "Expired ⚠️"))
    c2.metric("Portfolio value", f"₹{total_val:,.0f}")
    c3.metric("Total P&L", f"₹{total_pnl:,.0f}",
              delta=f"{(total_pnl / (total_val - total_pnl) * 100):.1f}%" if total_val != total_pnl else None)
    c4.metric("Tracked stocks", len(store.all_symbols()))
    c5.metric("Crosses forming", forming)
    if not api_fresh and not mcp_live:
        st.caption("⚠️ Broker session expired (daily SEBI rule). Alerts continue; "
                   "holdings refresh & price double-check resume after /connect "
                   "in Telegram or `python -m portfolio_pulse.jobs.mcp_sync`.")


def _broker_symbols(store) -> dict[str, set[str]]:
    """{broker: set of symbols held there} from the per-broker records."""
    import json as _json

    out: dict[str, set[str]] = {}
    for broker in ("zerodha", "upstox"):
        raw = store.get_meta(f"holdings:{broker}")
        if not raw:
            continue
        try:
            out[broker] = {str(r.get("symbol", "")).upper()
                           for r in _json.loads(raw) if r.get("symbol")}
        except _json.JSONDecodeError:
            continue
    return out


def _radar_table(rows: list[dict]) -> None:
    display = []
    for r in rows:
        badge, _, _ = _RELATION.get(r["relation"], _RELATION["unknown"])
        display.append({
            "Stock": r["symbol"],
            "Status": badge,
            "Gap %": round(r["gap_pct"], 2) if r["gap_pct"] is not None else None,
            # A projection years out is noise — only show near-term convergence.
            "≈ Days to cross": round(r["proj"], 1)
            if r["proj"] and r["proj"] <= 60 else None,
            "50-DMA": round(r["sma50"], 2) if r["sma50"] else None,
            "200-DMA": round(r["sma200"], 2) if r["sma200"] else None,
            "As of": r["updated"],
        })
    st.dataframe(
        display, use_container_width=True, hide_index=True,
        column_config={
            "Gap %": st.column_config.NumberColumn(
                help="(50-DMA − 200-DMA) / 200-DMA. Positive = above; the closer "
                     "to 0, the closer a crossover.", format="%.2f%%"),
            "≈ Days to cross": st.column_config.NumberColumn(
                help="Linear projection of the current gap trend; only shown "
                     "when the SMAs are converging."),
        },
    )


def _radar_tab(store) -> None:
    st.subheader("Cross Radar — by broker")
    st.caption("50-DMA vs 200-DMA per stock, closest-to-crossover on top within "
               "each section. Updated at the 18:45 IST scan.")
    rows = _radar_rows(store)
    if not rows:
        st.info("Nothing tracked yet — sync holdings or /add stocks in Telegram.")
        return

    # The urgent banner stays global — a forming death cross matters wherever held.
    urgent = [r for r in rows if r["relation"] == "above_forming"]
    if urgent:
        names = ", ".join(f"{r['symbol']} ({r['gap_pct']:.2f}%"
                          + (f", ~{r['proj']:.0f}d" if r["proj"] else "") + ")"
                          for r in urgent)
        st.warning(f"⚠️ Death cross forming: {names}")

    membership = _broker_symbols(store)
    shown: set[str] = set()
    for broker, label in (("zerodha", "Zerodha"), ("upstox", "Upstox")):
        syms = membership.get(broker, set())
        section = [r for r in rows if r["symbol"] in syms]
        if not section:
            continue
        forming = sum(1 for r in section if r["relation"].endswith("_forming"))
        st.markdown(f"#### {label} — {len(section)} stocks"
                    + (f" · {forming} forming" if forming else ""))
        _radar_table(section)
        shown |= {r["symbol"] for r in section}

    watch = [r for r in rows if r["kind"] == "watch"]
    if watch:
        plural = "stock" if len(watch) == 1 else "stocks"
        st.markdown(f"#### 👁 Watchlist — {len(watch)} {plural}")
        _radar_table(watch)
        shown |= {r["symbol"] for r in watch}

    leftovers = [r for r in rows if r["symbol"] not in shown]
    if leftovers:  # holdings whose broker record is missing (e.g. pre-migration)
        st.markdown(f"#### Other — {len(leftovers)}")
        _radar_table(leftovers)

    both = membership.get("zerodha", set()) & membership.get("upstox", set())
    if both:
        st.caption("Held at both brokers (appears in each section): "
                   + ", ".join(sorted(both)))
    st.caption("'Forming' means the moving averages are converging with a "
               f"projected cross within {config.DMA_FORMING_HORIZON_DAYS} "
               "trading days. Not investment advice.")


def _portfolio_tab(store) -> None:
    st.subheader("Holdings P&L — by broker")
    per_broker = _broker_holdings(store)
    if not per_broker:
        st.info("No holdings synced yet — use /connect then /sync in Telegram.")
        return
    labels = {"zerodha": "Zerodha", "upstox": "Upstox"}
    for broker, rows in per_broker.items():
        invested = sum(r["Invested ₹"] for r in rows)
        value = sum(r["Value ₹"] for r in rows)
        pnl = value - invested
        pct = (pnl / invested * 100) if invested else 0.0
        st.markdown(f"#### {labels.get(broker, broker.title())} — {len(rows)} stocks")
        c1, c2, c3 = st.columns(3)
        c1.metric("Invested", f"₹{invested:,.0f}")
        c2.metric("Value", f"₹{value:,.0f}")
        c3.metric("P&L", f"₹{pnl:,.0f}", delta=f"{pct:.1f}%")
        st.dataframe(
            rows, use_container_width=True, hide_index=True,
            column_config={
                "P&L %": st.column_config.NumberColumn(format="%.2f%%"),
            },
        )
    overlap = set.intersection(*[{r["Symbol"] for r in rows}
                                 for rows in per_broker.values()]) \
        if len(per_broker) > 1 else set()
    if overlap:
        st.caption("Held at both brokers (shown separately in each section): "
                   + ", ".join(sorted(overlap)))
    synced = store.get_holdings()
    if synced:
        st.caption(f"Last synced: {synced[0]['synced_at'][:16].replace('T',' ')} UTC "
                   "— refresh via /sync in Telegram after a broker login.")


def _history_tab(store) -> None:
    st.subheader("Stock History")
    symbols = store.all_symbols()
    if not symbols:
        st.info("Nothing tracked yet.")
        return
    c1, c2 = st.columns([2, 1])
    sym = c1.selectbox("Stock", symbols)
    window = c2.selectbox("Window", ["7 days", "30 days", "90 days", "All"], index=1)
    alerts = store.list_alerts(limit=500, symbol=sym)
    if window != "All":
        days = int(window.split()[0])
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        alerts = [a for a in alerts if _parse_created(a.created_at) >= cutoff]
    d = store.get_dma_state(sym)
    if d:
        badge, blurb, _ = _RELATION.get(d.get("relation") or "unknown",
                                        _RELATION["unknown"])
        gap = (d.get("gap_pct") or 0) * 100
        st.markdown(f"**{sym}** — {badge} · gap {gap:+.2f}% · {blurb}")
    if not alerts:
        st.info(f"No events for {sym} in the last {window.lower()} — "
                "that itself is worth knowing.")
        return
    st.caption(f"{len(alerts)} event(s)")
    for a in alerts:
        _alert_card(a)


def _alert_card(a) -> None:
    color = _QC_COLOR.get(a.qc_status, "#8B93A7")
    type_label = _TYPE_LABEL.get(a.alert_type, a.alert_type)
    when = a.created_at[:16].replace("T", " ")
    src = (f'&nbsp;·&nbsp;<a href="{a.source_url}" target="_blank" '
           f'style="color:#2DD4BF;text-decoration:none">source ↗</a>'
           if a.source_url else "")
    body = a.summary if a.summary and a.summary != a.title else ""
    impact = (f'<div style="color:#98A2B3;font-size:0.85em;margin-top:2px">'
              f'{a.impact}</div>' if a.impact else "")
    st.markdown(
        f"""<div style="background:#141B29;border:1px solid #232D40;
        border-left:3px solid {color};border-radius:10px;
        padding:12px 16px;margin-bottom:10px">
        <div style="font-size:0.78em;color:#8B93A7;letter-spacing:0.02em">
        {when} &nbsp;·&nbsp; <b style="color:#C7CEDB">{a.symbol}</b>
        &nbsp;·&nbsp; {a.source_type or type_label}
        &nbsp;·&nbsp; <span style="color:{color}">{a.qc_status}</span>{src}</div>
        <div style="font-weight:600;color:#E6EAF2;margin-top:4px">{a.title}</div>
        <div style="color:#C7CEDB">{body}</div>{impact}</div>""",
        unsafe_allow_html=True,
    )


def _feed_tab(store) -> None:
    st.subheader("Alert feed")
    limit = st.slider("Show latest", 10, 200, 50)
    alerts = store.list_alerts(limit=limit)
    if not alerts:
        st.info("No alerts yet.")
        return
    for a in alerts:
        _alert_card(a)


def _watchlist_tab(store) -> None:
    st.subheader("Watchlist")
    st.caption("Watchlist stocks get identical treatment to holdings: filings, "
               "news, and cross alerts. Manage here or via /add /remove in Telegram.")
    with st.form("add_watch", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        sym = c1.text_input("Add symbol (NSE)", placeholder="e.g. INFY",
                            label_visibility="collapsed")
        if c2.form_submit_button("Add", use_container_width=True) and sym.strip():
            store.add_watch(sym.strip(), kind="watch")
            st.rerun()
    watch = store.list_watch("watch")
    if not watch:
        st.caption("No watchlist stocks yet.")
        return
    for w in watch:
        c1, c2 = st.columns([4, 1])
        c1.write(f"**{w.symbol}** — {w.name or '(name resolves at next sync)'}")
        if c2.button("Remove", key=f"rm_{w.symbol}", use_container_width=True):
            store.remove_watch(w.symbol)
            st.rerun()


def main() -> None:
    try:
        store = _store()
        store.list_alerts(limit=1)  # connectivity probe — fail here, friendly
    except Exception as exc:
        st.markdown(_CSS, unsafe_allow_html=True)
        st.title("📡 Portfolio Pulse")
        st.error(
            "**Can't reach the database.** This almost always means the "
            "`SUPABASE_URL` or `SUPABASE_KEY` in this app's Secrets is wrong "
            "or points to a deleted project.\n\n"
            "**Fix:** Manage app (bottom right) → ⋮ → Settings → Secrets → "
            "paste your current Supabase Project URL and service_role key "
            "(Supabase dashboard → Project Settings) → Save."
        )
        st.caption(f"Technical detail: {type(exc).__name__}: {exc}")
        st.stop()
    _handle_token_callback(store)

    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(
        '<h1><span class="pulse-dot"></span>Portfolio Pulse</h1>',
        unsafe_allow_html=True,
    )
    st.caption("NSE filings · verified news · 50/200-DMA crosses — for your "
               "holdings & watchlist. Alerts on Telegram; this is the state view. "
               "Not investment advice.")
    _status_bar(store)
    st.divider()

    radar, portfolio, history, feed, watch = st.tabs(
        ["🎯 Cross Radar", "💼 Portfolio", "🕘 Stock History", "📨 Alert Feed", "👁 Watchlist"]
    )
    with radar:
        _radar_tab(store)
    with portfolio:
        _portfolio_tab(store)
    with history:
        _history_tab(store)
    with feed:
        _feed_tab(store)
    with watch:
        _watchlist_tab(store)


main()
