"""50/200-DMA death-cross and golden-cross detection with state-transition dedup.

Relation state machine (persisted per symbol in `dma_state.relation`):

    above         : SMA50 >= SMA200, not projected to cross soon
    above_forming : SMA50 >= SMA200 but projected to cross DOWN within horizon
    below         : SMA50 <  SMA200, not projected to cross soon
    below_forming : SMA50 <  SMA200 but projected to cross UP within horizon

Alerts fire ONLY on a state change, so each event is announced once:
    above/above_forming -> below*        => 'dma_confirmed'  (death cross)
    below/below_forming -> above*        => 'golden_cross'   (golden cross)
    above               -> above_forming => 'dma_forming'    (death approaching)
    below               -> below_forming => 'dma_forming'    (golden approaching)

'Forming' uses the gap's recent slope (not a single day) to project the crossing,
which suppresses one-day noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from portfolio_pulse import config


@dataclass
class DmaSignal:
    symbol: str
    alert_type: str        # dma_confirmed | golden_cross | dma_forming
    direction: str         # death | golden
    title: str
    detail: str
    sma50: float
    sma200: float
    gap_pct: float
    projected_days: Optional[float]
    new_relation: str


def _project_days(gap_now: float, gap_prev: float) -> Optional[float]:
    """Trading days until the gap reaches zero at its recent slope, or None.

    gap = SMA50 - SMA200. A shrinking-magnitude gap projects a crossing; a
    widening gap returns None (not approaching).
    """
    slope = gap_now - gap_prev  # per-day change
    if slope == 0:
        return None
    days = -gap_now / slope
    return days if days > 0 else None


def evaluate(symbol: str, closes: pd.Series,
             prev_relation: Optional[str]
             ) -> tuple[Optional[DmaSignal], str, dict]:
    """Return (signal_or_None, new_relation, metrics) for the latest bar.

    `prev_relation` is the stored relation from the last run (None on first sight).
    A signal is returned only when the relation changes. `metrics` carries the
    current sma50/sma200/gap_pct/projected_days regardless of signalling, so the
    dashboard's cross-proximity radar stays fresh on every scan.
    """
    closes = closes.dropna()
    empty = {"sma50": 0.0, "sma200": 0.0, "gap_pct": 0.0, "projected_days": None}
    if len(closes) < config.DMA_LONG + config.DMA_SLOPE_LOOKBACK + 1:
        return None, prev_relation or "unknown", empty

    sma50 = closes.rolling(config.DMA_SHORT).mean()
    sma200 = closes.rolling(config.DMA_LONG).mean()
    gap = (sma50 - sma200).dropna()
    if len(gap) < config.DMA_SLOPE_LOOKBACK + 1:
        return None, prev_relation or "unknown", empty

    s50 = float(sma50.iloc[-1])
    s200 = float(sma200.iloc[-1])
    gap_now = float(gap.iloc[-1])
    gap_prev = float(gap.iloc[-1 - config.DMA_SLOPE_LOOKBACK])
    gap_pct = gap_now / s200 if s200 else 0.0

    above = gap_now >= 0
    # Slope measured over the lookback window, normalised to PER-DAY so the
    # projection is in trading days (the old code compared per-window slope
    # against a day threshold — a 5x unit mismatch).
    slope_per_day = (gap_now - gap_prev) / config.DMA_SLOPE_LOOKBACK
    proj = _project_days(gap_now, gap_now - slope_per_day)
    forming = proj is not None and proj <= config.DMA_FORMING_HORIZON_DAYS

    metrics = {"sma50": s50, "sma200": s200, "gap_pct": gap_pct,
               "projected_days": proj}

    if above:
        relation = "above_forming" if forming else "above"
    else:
        relation = "below_forming" if forming else "below"

    # First sight: adopt the relation silently (no alert without a prior baseline).
    if prev_relation in (None, "unknown"):
        return None, relation, metrics

    if relation == prev_relation:
        return None, relation, metrics

    prev_above = prev_relation.startswith("above")
    signal: Optional[DmaSignal] = None

    if prev_above and not above:
        signal = DmaSignal(
            symbol, "dma_confirmed", "death",
            f"Death cross confirmed on {symbol}",
            f"50-DMA ({s50:.2f}) crossed below 200-DMA ({s200:.2f}).",
            s50, s200, gap_pct, proj, relation,
        )
    elif (not prev_above) and above:
        signal = DmaSignal(
            symbol, "golden_cross", "golden",
            f"Golden cross confirmed on {symbol}",
            f"50-DMA ({s50:.2f}) crossed above 200-DMA ({s200:.2f}).",
            s50, s200, gap_pct, proj, relation,
        )
    elif prev_relation == "above" and relation == "above_forming":
        signal = DmaSignal(
            symbol, "dma_forming", "death",
            f"Death cross forming on {symbol}",
            f"50-DMA ({s50:.2f}) is converging on 200-DMA ({s200:.2f}); "
            f"projected to cross down in ~{proj:.0f} trading days.",
            s50, s200, gap_pct, proj, relation,
        )
    elif prev_relation == "below" and relation == "below_forming":
        signal = DmaSignal(
            symbol, "dma_forming", "golden",
            f"Golden cross forming on {symbol}",
            f"50-DMA ({s50:.2f}) is converging on 200-DMA ({s200:.2f}); "
            f"projected to cross up in ~{proj:.0f} trading days.",
            s50, s200, gap_pct, proj, relation,
        )
    # Transitions like above_forming->above (fizzled) update state without an alert.

    return signal, relation, metrics
