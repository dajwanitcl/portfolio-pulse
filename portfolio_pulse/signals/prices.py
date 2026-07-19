"""Daily price series + an accuracy cross-check.

Free primary: yfinance `.NS` daily closes. yfinance has documented `.NS`
data-mismatch issues (yfinance#1326), tolerable for a *smoothed* 50/200 signal,
but before firing a cross alert we cross-check the latest close against the Kite
quote (free). Disagreement beyond tolerance => qc 'SUSPECT' and the caller holds
the alert. When Kite is unavailable we can't corroborate => 'SINGLE-SOURCE'
(still usable, but flagged), mirroring Market Move's QC vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from portfolio_pulse import config


def yf_symbol(symbol: str) -> str:
    """NSE trading symbol -> Yahoo ticker (e.g. 'RELIANCE' -> 'RELIANCE.NS')."""
    symbol = symbol.strip().upper()
    return symbol if symbol.endswith(".NS") else f"{symbol}.NS"


def daily_closes(symbol: str, days: int = config.PRICE_HISTORY_DAYS) -> pd.Series:
    """Adjusted daily closes as a float Series indexed by date (empty on failure)."""
    import yfinance as yf

    period_days = max(days + 30, 260)  # pad so 200-DMA is fully warmed
    try:
        df = yf.download(
            yf_symbol(symbol),
            period=f"{period_days}d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
    except Exception:
        return pd.Series(dtype="float64")
    if df is None or df.empty or "Close" not in df:
        return pd.Series(dtype="float64")
    close = df["Close"]
    if isinstance(close, pd.DataFrame):  # yfinance sometimes returns a 1-col frame
        close = close.iloc[:, 0]
    return close.dropna().astype("float64")


def kite_quote(symbol: str, kite=None) -> Optional[float]:
    """Latest price from Kite for cross-check, or None if unavailable.

    `kite` is an authenticated KiteConnect client (broker.kite_auth.get_client()).
    Passing None (no token / offline) simply skips the cross-check.
    """
    if kite is None:
        return None
    try:
        key = f"NSE:{symbol.strip().upper()}"
        data = kite.ltp([key])
        return float(data[key]["last_price"])
    except Exception:
        return None


@dataclass
class PriceCheck:
    closes: pd.Series
    latest_close: Optional[float]
    qc_status: str          # CONFIRMED | SUSPECT | SINGLE-SOURCE | NO-DATA
    note: str


def load_with_crosscheck(symbol: str, kite=None) -> PriceCheck:
    """Load closes and corroborate the latest print against Kite."""
    closes = daily_closes(symbol)
    if closes.empty:
        return PriceCheck(closes, None, "NO-DATA", "no yfinance data")
    latest = float(closes.iloc[-1])
    quote = kite_quote(symbol, kite)
    if quote is None:
        return PriceCheck(closes, latest, "SINGLE-SOURCE", "no Kite cross-check")
    diff = abs(latest - quote) / quote if quote else 1.0
    if diff > config.PRICE_CROSSCHECK_TOLERANCE:
        return PriceCheck(
            closes, latest, "SUSPECT",
            f"yfinance {latest:.2f} vs Kite {quote:.2f} differ {diff:.1%}",
        )
    return PriceCheck(closes, latest, "CONFIRMED", f"cross-checked ({diff:.2%})")
