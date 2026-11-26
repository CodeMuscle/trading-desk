"""L1 trend and mean metrics.

Pure functions: frame in, MetricResult out. No I/O, no network, no state.
Lengths default to the user's own Pine script: 20 fast, 50 slow, 200 bias.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from desk.result import MetricResult, failed, ok, unavailable
from desk.store.frame import OHLCVFrame


def _stale(frame: OHLCVFrame) -> bool:
    try:
        return frame.is_stale(now=datetime.now(timezone.utc))
    except ValueError:
        return False


def ema(frame: OHLCVFrame, length: int = 20) -> MetricResult:
    if length <= 0:
        return failed(ValueError(f"ema length must be positive, got {length}"))
    if len(frame.data) < length:
        return unavailable(
            f"needs at least {length} bars, frame has {len(frame.data)}"
        )
    series = frame.data["close"].ewm(span=length, adjust=False).mean()
    return ok(series, stale=_stale(frame), length=length)


def trend_state(frame: OHLCVFrame, fast: int = 20, slow: int = 50) -> MetricResult:
    if fast >= slow:
        return failed(ValueError(f"fast ({fast}) must be shorter than slow ({slow})"))
    fast_result = ema(frame, fast)
    slow_result = ema(frame, slow)
    for result in (fast_result, slow_result):
        if result.is_unavailable or result.is_error:
            return result

    f, s = fast_result.value, slow_result.value
    state = pd.Series("flat", index=frame.data.index, dtype="object")
    state[f > s] = "up"
    state[f < s] = "down"
    return ok(state, stale=_stale(frame), fast=fast, slow=slow)


def session_vwap(frame: OHLCVFrame) -> MetricResult:
    data = frame.data
    volume = data["volume"]
    if float(volume.sum()) == 0.0:
        return unavailable("volume is zero across the frame, so VWAP is undefined")

    typical = (data["high"] + data["low"] + data["close"]) / 3.0
    session = pd.Series(data.index.tz_convert("UTC").date, index=data.index)

    pv = (typical * volume).groupby(session).cumsum()
    vv = volume.groupby(session).cumsum()
    series = (pv / vv).where(vv > 0)

    if series.isna().any():
        # A session whose cumulative volume is still zero has no VWAP yet.
        # Report rather than fill.
        return unavailable("a session in this frame has zero cumulative volume")
    return ok(series, stale=_stale(frame))
