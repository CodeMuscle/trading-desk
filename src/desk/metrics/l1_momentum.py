"""RSI, MACD and ATR, with the parameters the user's Pine script uses.

Source: reference/pine/Forex_Confluence_AutoTrade_Pro.pine
  rsi    = ta.rsi(close, 14)
  [macdLine, signalLine, hist] = ta.macd(close, 12, 26, 9)
  atrVal = ta.atr(14)

All three use Wilder's smoothing where Pine does, which is an EMA with
alpha = 1/length rather than the 2/(length+1) used by a conventional EMA.
Getting that wrong gives values that look plausible and drift from TradingView,
which is the worst kind of wrong.
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


def _wilder(series: pd.Series, length: int) -> pd.Series:
    """Wilder's smoothing, matching Pine's ta.rma exactly.

    Two details, both of which matter:

    1. alpha = 1/length, not the 2/(length+1) of a conventional EMA.
    2. The first value is a simple average of the first `length` observations,
       not the first observation alone.

    Point 2 is easy to miss and was wrong here initially. Seeding with the
    first value instead of an SMA produced an RSI that disagreed with the
    charting library by 5.6 points at bar 25, decaying to zero by bar 200.
    Harmless deep into a series, and not harmless at all for a backtest whose
    opening trades sit in that window, or for a confluence gate testing
    rsi > 50.

    Bars before the seed carry NaN, because the average genuinely does not
    exist yet. That is information, not a gap.
    """
    values = series.to_numpy(dtype="float64")
    out = np.full(len(values), np.nan)

    # Leading NaN is skipped rather than treated as zero. close.diff() has no
    # value at bar 0, and Pine's ta.sma returns na if any input is na, so its
    # seed spans the first `length` *real* observations. Filling that first
    # gap with a fabricated 0 drags the seed down and leaves a residual error
    # that takes ~200 bars to decay.
    valid = np.flatnonzero(~np.isnan(values))
    if valid.size < length:
        return pd.Series(out, index=series.index)

    seed_at = int(valid[length - 1])
    out[seed_at] = values[valid[:length]].mean()
    for i in range(seed_at + 1, len(values)):
        current = values[i]
        if np.isnan(current):
            out[i] = out[i - 1]
            continue
        out[i] = (out[i - 1] * (length - 1) + current) / length
    return pd.Series(out, index=series.index)


def rsi(frame: OHLCVFrame, length: int = 14) -> MetricResult:
    if length <= 0:
        return failed(ValueError(f"rsi length must be positive, got {length}"))
    if len(frame.data) < length + 1:
        return unavailable(
            f"needs at least {length + 1} bars, frame has {len(frame.data)}"
        )

    delta = frame.data["close"].diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    # gain/loss keep their NaN at bar 0 so _wilder can skip it, matching Pine.
    avg_gain = _wilder(gain, length)
    avg_loss = _wilder(loss, length)

    # Float division already produces the right answers at the edges, so no
    # sentinel is needed:
    #   gain > 0, loss = 0  -> rs = inf -> 100 - 100/inf = 100
    #   gain = 0, loss > 0  -> rs = 0   -> 100 - 100/1   = 0
    #   gain = 0, loss = 0  -> rs = nan, handled explicitly below
    # An earlier version replaced the zero divisor with pd.NA, which injects a
    # masked type into a float64 series that pandas 3 then refuses to cast.
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain.to_numpy() / avg_loss.to_numpy()
        values = 100.0 - (100.0 / (1.0 + rs))

    # No movement in either direction is neither overbought nor oversold.
    values = np.where((avg_gain.to_numpy() == 0.0) & (avg_loss.to_numpy() == 0.0), 50.0, values)

    out = pd.Series(values, index=frame.data.index, dtype="float64")
    return ok(out, stale=_stale(frame), length=length)


def macd(
    frame: OHLCVFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> MetricResult:
    """Returns the MACD line, its signal line, and the histogram.

    The user's confluence gate reads only the histogram's sign, but the other
    two are what a chart draws, so all three are returned.
    """
    if fast >= slow:
        return failed(ValueError(f"fast ({fast}) must be shorter than slow ({slow})"))
    if min(fast, slow, signal) <= 0:
        return failed(ValueError("macd lengths must all be positive"))
    if len(frame.data) < slow:
        return unavailable(f"needs at least {slow} bars, frame has {len(frame.data)}")

    close = frame.data["close"]
    macd_line = (
        close.ewm(span=fast, adjust=False).mean()
        - close.ewm(span=slow, adjust=False).mean()
    )
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return ok(
        {
            "macd": macd_line,
            "signal": signal_line,
            "hist": macd_line - signal_line,
        },
        stale=_stale(frame),
        fast=fast,
        slow=slow,
        signal=signal,
    )


def true_range(frame: OHLCVFrame) -> MetricResult:
    d = frame.data
    if len(d) < 2:
        return unavailable("needs at least 2 bars, frame has fewer")
    prev_close = d["close"].shift(1)
    tr = pd.concat(
        [
            d["high"] - d["low"],
            (d["high"] - prev_close).abs(),
            (d["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # The first bar has no previous close, so its true range is its own range.
    tr.iloc[0] = d["high"].iloc[0] - d["low"].iloc[0]
    return ok(tr, stale=_stale(frame))


def atr(frame: OHLCVFrame, length: int = 14) -> MetricResult:
    """Average true range, Wilder-smoothed, as Pine's ta.atr does.

    This is the number the user's risk rules are denominated in: a 1.5x ATR
    initial stop and a 2x ATR trail, so an ATR that disagrees with TradingView
    moves every stop in the system.
    """
    if length <= 0:
        return failed(ValueError(f"atr length must be positive, got {length}"))
    tr_result = true_range(frame)
    if not tr_result.is_ok:
        return tr_result
    if len(frame.data) < length:
        return unavailable(f"needs at least {length} bars, frame has {len(frame.data)}")
    return ok(_wilder(tr_result.value, length), stale=_stale(frame), length=length)
