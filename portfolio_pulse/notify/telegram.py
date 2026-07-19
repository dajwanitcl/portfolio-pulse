"""Telegram push + command handling.

Two responsibilities:
  * Push: send formatted alerts. Uses the plain Bot HTTP API via requests so it
    works from a stateless GitHub Actions run (no long-lived process needed).
  * Commands: /add /remove /list /holdings, drained with getUpdates each poll
    (offset persisted in the store's meta table). Only messages from the
    configured chat_id are honoured — everyone else is ignored.

Message formatting is separated from I/O (format_alert, parse_command,
handle_update are pure) so the logic is unit-tested without network or a token.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Optional

import requests

from portfolio_pulse import config
from portfolio_pulse.store.db import Alert

_API = "https://api.telegram.org/bot{token}/{method}"
_OFFSET_KEY = "telegram_update_offset"

_QC_BADGE = {
    "CONFIRMED": "✅ verified",
    "PARTIAL": "⚠️ headline only",
    "INSUFFICIENT": "⚠️ headline only",
    "SUSPECT": "⚠️ price unverified",
    "SINGLE-SOURCE": "ℹ️ single source",
}


# --------------------------------------------------------------------------- #
# Formatting (pure)
# --------------------------------------------------------------------------- #
def format_alert(alert: Alert) -> str:
    """Render an alert as Telegram HTML. Always includes a source link when present."""
    tag = html.escape(alert.source_type or "Alert")
    sym = html.escape(alert.symbol)
    title = html.escape(alert.title)
    lines = [f"<b>{sym}</b> — {tag}", title]
    if alert.summary and alert.summary != alert.title:
        lines.append("")
        lines.append(html.escape(alert.summary))
    if alert.impact:
        lines.append(f"<i>{html.escape(alert.impact)}</i>")
    badge = _QC_BADGE.get(alert.qc_status, alert.qc_status)
    if badge:
        lines.append(f"<code>{html.escape(badge)}</code>")
    if alert.source_url:
        lines.append(f'<a href="{html.escape(alert.source_url)}">Source</a>')
    return "\n".join(lines)


def parse_command(text: str) -> tuple[str, str]:
    """Split '/add INFY' -> ('add', 'INFY'). Strips a bot @mention suffix."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return "", ""
    parts = text[1:].split(maxsplit=1)
    cmd = parts[0].split("@", 1)[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    return cmd, arg


_HELP = (
    "Portfolio Pulse commands:\n"
    "/add NAME or SYMBOL — add a stock (e.g. /add tata motors, /add INFY)\n"
    "/remove SYMBOL — remove a watchlist stock\n"
    "/newlist — start a fresh, empty watchlist (holdings untouched)\n"
    "/list — show watchlist + holdings\n"
    "/holdings — show current holdings snapshot\n"
    "/connect [zerodha|upstox] — connect/reconnect a broker (MCP)\n"
    "/sync — refresh holdings from every connected broker"
)


def _resolve_stock(store, query: str) -> tuple[Optional[str], str]:
    """Resolve free text ('tata motors', 'INFY') to (SYMBOL, company name).

    Order: exact symbol in the local instruments cache -> live MCP instrument
    search (works while a broker session is up) -> bare-ticker fallback for
    single-word queries. Returns (None, '') when nothing safe was found.
    """
    q = query.strip()
    up = q.upper()

    from portfolio_pulse.broker.holdings import _load_instruments_cache, merge_names

    cache = _load_instruments_cache(max_age_days=365) or {}
    if up in cache:
        return up, cache[up]

    try:
        from portfolio_pulse.broker.kite_mcp import KiteMCPClient

        mcp = KiteMCPClient(store)
        if mcp.session_id:
            data = mcp._json(mcp.call_tool("search_instruments", {"query": q}))
            rows = data if isinstance(data, list) else next(
                (data[k] for k in ("instruments", "data", "items", "result")
                 if isinstance(data, dict) and isinstance(data.get(k), list)), [])
            best = None
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("exchange", "NSE").upper() not in ("NSE", ""):
                    continue
                ts = (row.get("tradingsymbol") or "").strip().upper()
                if not ts:
                    continue
                if ts == up:            # exact ticker typed
                    best = row
                    break
                if best is None:        # else first NSE hit for a name query
                    best = row
            if best:
                sym = best["tradingsymbol"].strip().upper()
                name = (best.get("name") or "").strip()
                if name:
                    merge_names({sym: name})  # so NSE filings match immediately
                return sym, name
    except Exception:
        pass  # search unavailable (no session / network) -> fall through

    if " " not in q and 2 <= len(up) <= 20:
        # Bare ticker with no broker session: resolve the company name from
        # yfinance (public). The name matters — NSE filing matching keys on it,
        # and a nameless stock gets no filing/news alerts (fails closed).
        try:
            import yfinance as yf

            info = yf.Ticker(f"{up}.NS").info or {}
            name = (info.get("longName") or info.get("shortName") or "").strip()
            if name:
                merge_names({up: name})
                return up, name
        except Exception:
            pass
        return up, ""  # ticker accepted; name backfills when a session is live
    return None, ""


def _broker_connect_reply(store, which: str = "zerodha") -> str:
    """Connect/reconnect instructions per broker. Used by /connect [broker]."""
    which = (which or "zerodha").strip().lower()

    if which in ("upstox", "u"):
        from portfolio_pulse.broker.upstox_mcp import UpstoxMCPClient, load_oauth

        if load_oauth(store).get("access_token") and UpstoxMCPClient(store).connected():
            return "Upstox already connected. Send /sync to refresh holdings."
        return (
            "🔐 <b>Connect Upstox</b> (read-only, official Upstox MCP):\n"
            "Upstox uses a browser OAuth flow, so run this once on your computer:\n"
            "<code>python -m portfolio_pulse.jobs.upstox_connect</code>\n"
            "It opens the Upstox login, then syncs automatically. After that the "
            "cloud renews the session by itself whenever Upstox allows."
        )

    from portfolio_pulse.broker.kite_mcp import KiteMCPClient, MCPError

    mcp = KiteMCPClient(store)
    try:
        mcp.ensure_session()
        if mcp.logged_in():
            return ("Zerodha already connected. Send /sync to refresh holdings.\n"
                    "Also have Upstox? Send /connect upstox")
        url = mcp.login_url()
    except MCPError as exc:
        return f"Could not reach the Kite MCP server: {exc}"
    return (
        "🔐 <b>Connect Zerodha</b> (read-only, via official Kite MCP):\n"
        f'<a href="{url}">Login with Kite</a>\n'
        "⏱ The link expires in a few minutes — tap it right away, "
        "then send /sync to pull your holdings.\n"
        "<i>Also have Upstox? Send /connect upstox</i>"
    )


def _broker_sync_reply(store) -> str:
    """Refresh holdings from every live broker session. Used by /sync."""
    from portfolio_pulse.broker import get_live_brokers, holdings

    brokers = get_live_brokers(store)
    if not brokers:
        return ("No live broker session — reconnect first:\n"
                + _broker_connect_reply(store))
    parts = []
    for name, client in brokers:
        try:
            n = holdings.sync(store, client, broker=name)
            parts.append(f"{name}: {n}")
        except Exception as exc:  # broker hiccups must never crash the bot
            parts.append(f"{name}: failed ({str(exc)[:80]})")
    syms = [r["symbol"] for r in store.get_holdings()]
    return (f"✅ Synced — {', '.join(parts)}\n"
            f"Combined portfolio ({len(syms)}): {', '.join(syms) or '—'}")


def handle_update(update: dict, store) -> Optional[str]:
    """Process one getUpdates entry; return reply text (or None to stay silent).

    Ignores anything not from the configured chat_id (basic access control).
    """
    msg = update.get("message") or update.get("edited_message") or {}
    chat = str((msg.get("chat") or {}).get("id", ""))
    if config.TELEGRAM_CHAT_ID and chat != str(config.TELEGRAM_CHAT_ID):
        return None
    cmd, arg = parse_command(msg.get("text", ""))
    if not cmd:
        return None

    if cmd in ("start", "help"):
        return _HELP
    if cmd == "add":
        if not arg:
            return "Usage: /add NAME or SYMBOL (e.g. /add tata motors)"
        sym, name = _resolve_stock(store, arg)
        if not sym:
            return (f"Couldn't identify '{arg}'. Try the exact NSE symbol "
                    "(e.g. /add TATAMOTORS), or /connect the broker first so I "
                    "can search by company name.")
        existing = {w.symbol: w.kind for w in store.list_watch()}
        if existing.get(sym) == "holding":
            return f"{sym} is already tracked as a holding — no need to watchlist it."
        store.add_watch(sym, name, kind="watch")
        label = f"{sym} ({name})" if name else sym
        return f"Added {label} to your watchlist."
    if cmd == "remove":
        if not arg:
            return "Usage: /remove SYMBOL"
        ok = store.remove_watch(arg.split()[0])
        sym = arg.split()[0].upper()
        return f"Removed {sym}." if ok else f"{sym} was not in your watchlist."
    if cmd in ("newlist", "clearwatch"):
        n = store.clear_watch()
        return (f"🧹 Fresh watchlist started ({n} stock(s) removed). "
                "Add stocks with /add NAME — your holdings are unaffected.")
    if cmd == "list":
        watch = [w.symbol for w in store.list_watch("watch")]
        hold = [w.symbol for w in store.list_watch("holding")]
        return (f"Holdings ({len(hold)}): {', '.join(hold) or '—'}\n"
                f"Watchlist ({len(watch)}): {', '.join(watch) or '—'}")
    if cmd == "holdings":
        rows = store.get_holdings()
        if not rows:
            return "No holdings synced yet."
        return "\n".join(
            f"{r['symbol']}: {r['qty']:g} @ {r['avg_price']:g} (last {r['last_price']:g})"
            for r in rows
        )
    if cmd == "connect":
        return _broker_connect_reply(store, arg)
    if cmd == "sync":
        return _broker_sync_reply(store)
    return _HELP


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def send_message(text: str, chat_id: Optional[str] = None,
                 token: Optional[str] = None, disable_preview: bool = True) -> bool:
    """Send an HTML message. Returns True on success."""
    token = token or config.TELEGRAM_BOT_TOKEN
    chat_id = chat_id or config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False
    try:
        resp = requests.post(
            _API.format(token=token, method="sendMessage"),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": disable_preview},
            timeout=config.HTTP_TIMEOUT,
        )
        return resp.ok
    except requests.RequestException:
        return False


def send_alert(alert: Alert) -> bool:
    return send_message(format_alert(alert))


def drain_commands(store, token: Optional[str] = None) -> int:
    """Fetch and process pending commands via getUpdates. Returns count handled.

    The offset is persisted so each update is processed once across cron runs.
    """
    token = token or config.TELEGRAM_BOT_TOKEN
    if not token:
        return 0
    offset = store.get_meta(_OFFSET_KEY)
    params = {"timeout": 0}
    if offset:
        params["offset"] = int(offset) + 1
    try:
        resp = requests.get(
            _API.format(token=token, method="getUpdates"),
            params=params, timeout=config.HTTP_TIMEOUT,
        )
        updates = resp.json().get("result", []) if resp.ok else []
    except (requests.RequestException, ValueError):
        return 0

    handled = 0
    last_id = None
    for upd in updates:
        last_id = upd.get("update_id", last_id)
        reply = handle_update(upd, store)
        if reply:
            send_message(reply, token=token)
            handled += 1
    if last_id is not None:
        store.set_meta(_OFFSET_KEY, str(last_id))
    return handled
