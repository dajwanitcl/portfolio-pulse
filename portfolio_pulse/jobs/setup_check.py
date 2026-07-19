"""One-click setup verification — the 'did I do it right?' button.

Run from the GitHub Actions tab (workflow: setup-check). Checks each piece of a
fresh install in plain English and, if Telegram works, sends the success message
to the user's own bot — the proof moment that setup is complete.

Never prints secret values; only whether each piece works.
"""

from __future__ import annotations


def run() -> dict:
    from portfolio_pulse import config

    results: list[tuple[str, bool, str]] = []

    # 1. Database (Supabase in cloud mode)
    try:
        from portfolio_pulse.store import get_store

        store = get_store()
        store.set_meta("setup_check", "ok")
        db_ok = store.get_meta("setup_check") == "ok"
        results.append(("Database (Supabase)", db_ok,
                        "connected" if db_ok else "read-back failed"))
    except Exception as exc:
        store = None
        results.append(("Database (Supabase)", False,
                        f"{type(exc).__name__}: check SUPABASE_URL / SUPABASE_KEY "
                        "secrets (use the service_role key, not anon)"))

    # 2. Telegram
    tg_ok = False
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        results.append(("Telegram bot", False,
                        "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secret missing"))
    else:
        from portfolio_pulse.notify.telegram import send_message

        tg_ok = send_message("✅ Portfolio Pulse setup check: Telegram works!")
        results.append(("Telegram bot", tg_ok,
                        "test message sent — check your phone" if tg_ok else
                        "send failed: token or chat id wrong, or you haven't "
                        "pressed START on your bot"))

    # 3. NSE feed reachability (no credentials involved). The feed is often
    # EMPTY outside filing hours (NSE rotates it overnight) — that's normal,
    # so we test reachability, not content.
    try:
        import requests

        resp = requests.get(config.NSE_RSS_FEEDS["announcements"],
                            headers=config.HTTP_HEADERS, timeout=config.HTTP_TIMEOUT)
        reachable = resp.ok and b"<rss" in resp.content[:200]
        n = resp.content.count(b"<item>")
        results.append(("NSE filings feed", reachable,
                        f"reachable; {n} announcements right now "
                        "(0 is normal outside market/filing hours)"
                        if reachable else f"HTTP {resp.status_code}"))
    except Exception as exc:
        results.append(("NSE filings feed", False, str(exc)[:80]))

    # 4. Broker + tracked stocks (informational — not required to pass)
    broker_note = "not connected yet — send /connect to your bot (Zerodha) "\
                  "or add stocks with /add SYMBOL"
    tracked = 0
    if store is not None:
        try:
            from portfolio_pulse.broker import get_live_brokers

            live = [name for name, _ in get_live_brokers(store)]
            tracked = len(store.all_symbols())
            if live:
                broker_note = f"connected: {', '.join(live)}"
        except Exception:
            pass
        results.append(("Broker connection", True, broker_note))
        results.append(("Tracked stocks", True,
                        f"{tracked} being monitored" if tracked else
                        "none yet — /add or /connect from Telegram"))

    # Report
    core_ok = all(ok for name, ok, _ in results
                  if name in ("Database (Supabase)", "Telegram bot", "NSE filings feed"))
    print("\n===== PORTFOLIO PULSE SETUP CHECK =====")
    for name, ok, note in results:
        print(f"  {'✅' if ok else '❌'} {name}: {note}")
    print("=======================================")
    if core_ok and tg_ok:
        from portfolio_pulse.notify.telegram import send_message

        send_message(
            "🎉 <b>Setup complete — Portfolio Pulse is live!</b>\n"
            "From now on it runs by itself. Next steps:\n"
            "• /connect — link your Zerodha (holdings auto-sync)\n"
            "• /add SYMBOL — track any stock on any broker\n"
            "• /help — everything else\n"
            "Alerts arrive here automatically. Not investment advice."
        )
        print("ALL CORE CHECKS PASSED — success message sent to your Telegram.")
    else:
        print("Fix the ❌ items above, then run this workflow again.")
    return {"passed": core_ok}


if __name__ == "__main__":
    run()
