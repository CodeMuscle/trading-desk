"""L4 price-action metrics, transcribed from the user's Pine script.

Source: reference/pine/Forex_Confluence_AutoTrade_Pro.pine

These definitions are the user's own and are authoritative. The candle ratios
(2x and 0.6x) and the pivot lookback semantics are not to be adjusted toward
some other book's version of the same pattern.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from desk.result import MetricResult, failed, ok, unavailable
from desk.store.frame import OHLCVFrame


def _stale(frame: OHLCVFrame) -> bool:
    try:
        return frame.is_stale(now=datetime.now(timezone.utc))
    except ValueError:
        return False


def _parts(frame: OHLCVFrame):
    d = frame.data
    body = (d["close"] - d["open"]).abs()
    rng = d["high"] - d["low"]
    upper = d["high"] - d[["close", "open"]].max(axis=1)
    lower = d[["close", "open"]].min(axis=1) - d["low"]
    return d, body, rng, upper, lower


def bullish_engulfing(frame: OHLCVFrame) -> MetricResult:
    """close > open and close[1] < open[1] and close > open[1] and open < close[1]"""
    d = frame.data
    if len(d) < 2:
        return unavailable("needs at least 2 bars, frame has fewer")
    o, c = d["open"], d["close"]
    hit = (c > o) & (c.shift(1) < o.shift(1)) & (c > o.shift(1)) & (o < c.shift(1))
    return ok(hit.fillna(False).astype(bool), stale=_stale(frame))


def bearish_engulfing(frame: OHLCVFrame) -> MetricResult:
    """close < open and close[1] > open[1] and close < open[1] and open > close[1]"""
    d = frame.data
    if len(d) < 2:
        return unavailable("needs at least 2 bars, frame has fewer")
    o, c = d["open"], d["close"]
    hit = (c < o) & (c.shift(1) > o.shift(1)) & (c < o.shift(1)) & (o > c.shift(1))
    return ok(hit.fillna(False).astype(bool), stale=_stale(frame))


def hammer(frame: OHLCVFrame) -> MetricResult:
    """range > 0 and lowerWick > body * 2 and upperWick < body * 0.6 and close > open"""
    d, body, rng, upper, lower = _parts(frame)
    hit = (rng > 0) & (lower > body * 2) & (upper < body * 0.6) & (d["close"] > d["open"])
    return ok(hit.astype(bool), stale=_stale(frame))


def shooting_star(frame: OHLCVFrame) -> MetricResult:
    """range > 0 and upperWick > body * 2 and lowerWick < body * 0.6 and close < open"""
    d, body, rng, upper, lower = _parts(frame)
    hit = (rng > 0) & (upper > body * 2) & (lower < body * 0.6) & (d["close"] < d["open"])
    return ok(hit.astype(bool), stale=_stale(frame))


def swing_pivots(frame: OHLCVFrame, lookback: int = 10) -> MetricResult:
    """Pine's ta.pivothigh(high, n, n) and ta.pivotlow(low, n, n).

    A bar is a pivot high when its high is strictly greater than the highs of
    the n bars before it and the n bars after it. NaN at a bar means that bar
    is not a pivot, which is information about the series rather than a missing
    measurement, so the result is still ok.
    """
    if lookback <= 0:
        return failed(ValueError(f"lookback must be positive, got {lookback}"))

    d = frame.data
    window = lookback * 2 + 1
    if len(d) < window:
        return unavailable(
            f"needs at least {window} bars for lookback {lookback}, frame has {len(d)}"
        )

    high, low = d["high"].to_numpy(), d["low"].to_numpy()
    highs = np.full(len(d), np.nan)
    lows = np.full(len(d), np.nan)

    for i in range(lookback, len(d) - lookback):
        left_h, right_h = high[i - lookback : i], high[i + 1 : i + 1 + lookback]
        if high[i] > left_h.max() and high[i] > right_h.max():
            highs[i] = high[i]
        left_l, right_l = low[i - lookback : i], low[i + 1 : i + 1 + lookback]
        if low[i] < left_l.min() and low[i] < right_l.min():
            lows[i] = low[i]

    return ok(
        {
            "highs": pd.Series(highs, index=d.index),
            "lows": pd.Series(lows, index=d.index),
        },
        stale=_stale(frame),
        lookback=lookback,
    )
