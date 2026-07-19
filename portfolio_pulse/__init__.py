"""Portfolio Pulse — 24/7 Zerodha portfolio monitor.

Tracks NSE corporate filings + verified news and 50/200-DMA death/golden crosses
for a user's holdings and watchlist, pushing source-cited alerts to Telegram.

Design rule (non-negotiable): never surface a fact that isn't traceable to a
primary source. The LLM only compresses text that was actually fetched; it never
asserts, infers prices, or "recalls" facts. Every alert carries a source link.
"""

__version__ = "0.1.0"
