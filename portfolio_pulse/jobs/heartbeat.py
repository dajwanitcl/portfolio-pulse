"""One heartbeat to rule them all — runs on every ~10-minute tick.

Always polls (filings + news + Telegram commands), and additionally fires the
daily jobs when their time-of-day arrives, so they no longer depend on their
own GitHub crons (unreliable on forks: day-one lag, jitter, 60-day pause):

  * >= 08:15 IST  ->  morning broker-session check / login nudge
  * >= 18:45 IST  ->  DMA death/golden-cross scan

The daily jobs guard themselves with a once-per-day marker in the store, so a
tick every 10 minutes (or their backup crons ALSO firing) never double-runs
them. Called by the pulse-loop and by the fast-poll workflow alike.
"""

from __future__ import annotations

from portfolio_pulse import config
from portfolio_pulse.jobs import dma_scan, fast_poll, morning_auth


def run() -> dict:
    out: dict = {"fast_poll": fast_poll.run()}
    now = config.now_ist()
    minutes = now.hour * 60 + now.minute
    if minutes >= 8 * 60 + 15:
        out["morning_auth"] = morning_auth.run()
    if minutes >= 18 * 60 + 45:
        out["dma_scan"] = dma_scan.run()
    return out


if __name__ == "__main__":
    print(run())
