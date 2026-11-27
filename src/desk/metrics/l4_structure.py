"""Market structure: confirmed swing levels, break of structure, imbalance.

The central distinction in this module is between a pivot's *location* and the
moment it becomes *knowable*.

`swing_pivots` in l4_price_action marks a pivot at its own bar, which is what a
chart wants to draw. A signal must not read that series: a pivot at bar i is
only confirmable once `lookback` further bars have printed, so reading it at
bar i means knowing the future.

Pine gets this right by construction. `ta.pivothigh(high, n, n)` returns its
value at the bar where confirmation happens, n bars after the pivot, so the
user's `lastSwingHigh` is always a level that could legitimately have been
known. `confirmed_swing_levels` reproduces that, and `break_of_structure`
is built on it rather than on the raw pivot markers.
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


def confirmed_swing_levels(frame: OHLCVFrame, lookback: int = 10) -> MetricResult:
    """The most recent swing high and low *knowable* at each bar.

    A pivot at bar p is confirmed at bar p + lookback. From that bar onward the
    level is carried forward until a newer pivot confirms. Bars before the first
    confirmation carry NaN, meaning "no level known yet", which is the honest
    answer rather than a guess.
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
    n = len(d)
    last_high = np.full(n, np.nan)
    last_low = np.full(n, np.nan)

    known_high = np.nan
    known_low = np.nan
    for i in range(n):
        # A pivot at bar i - lookback becomes confirmable exactly now.
        p = i - lookback
        if p >= lookback:
            left_h, right_h = high[p - lookback : p], high[p + 1 : p + 1 + lookback]
            if high[p] > left_h.max() and high[p] > right_h.max():
                known_high = high[p]
            left_l, right_l = low[p - lookback : p], low[p + 1 : p + 1 + lookback]
            if low[p] < left_l.min() and low[p] < right_l.min():
                known_low = low[p]
        last_high[i] = known_high
        last_low[i] = known_low

    return ok(
        {
            "high": pd.Series(last_high, index=d.index),
            "low": pd.Series(last_low, index=d.index),
        },
        stale=_stale(frame),
        lookback=lookback,
    )


def break_of_structure(frame: OHLCVFrame, lookback: int = 10) -> MetricResult:
    """The user's Pine rule: close beyond the last confirmed swing.

        breakoutUp   = not na(lastSwingHigh) and close > lastSwingHigh
        breakoutDown = not na(lastSwingLow)  and close < lastSwingLow

    Close, not wick. Built on confirmed levels, so it carries no lookahead.
    Returns +1 for an upside break, -1 for a downside break, 0 otherwise.
    """
    levels = confirmed_swing_levels(frame, lookback)
    if not levels.is_ok:
        return levels

    close = frame.data["close"]
    swing_high = levels.value["high"]
    swing_low = levels.value["low"]

    direction = pd.Series(0, index=frame.data.index, dtype="int64")
    direction[swing_high.notna() & (close > swing_high)] = 1
    direction[swing_low.notna() & (close < swing_low)] = -1
    return ok(direction, stale=_stale(frame), lookback=lookback)


def imbalance(frame: OHLCVFrame) -> MetricResult:
    """A three-bar gap, known elsewhere as a fair value gap.

    Bullish when bar i's low sits above bar i-2's high: price moved through
    that band without trading in it. Bearish is the mirror.

    The band is only complete once bar i has closed, so the signal belongs to
    bar i and reads nothing forward. Returns +1 bullish, -1 bearish, 0 none.
    """
    d = frame.data
    if len(d) < 3:
        return unavailable("needs at least 3 bars, frame has fewer")

    high, low = d["high"], d["low"]
    bullish = low > high.shift(2)
    bearish = high < low.shift(2)

    direction = pd.Series(0, index=d.index, dtype="int64")
    direction[bullish.fillna(False)] = 1
    direction[bearish.fillna(False)] = -1
    return ok(direction, stale=_stale(frame))


def imbalance_bands(frame: OHLCVFrame) -> MetricResult:
    """The price band each imbalance leaves behind, for drawing.

    upper and lower are NaN on bars with no imbalance. A chart fills between
    them; a signal uses `imbalance` instead.
    """
    d = frame.data
    if len(d) < 3:
        return unavailable("needs at least 3 bars, frame has fewer")

    direction_result = imbalance(frame)
    if not direction_result.is_ok:
        return direction_result
    direction = direction_result.value

    upper = pd.Series(np.nan, index=d.index)
    lower = pd.Series(np.nan, index=d.index)

    bull = direction == 1
    upper[bull] = d["low"][bull]
    lower[bull] = d["high"].shift(2)[bull]

    bear = direction == -1
    upper[bear] = d["low"].shift(2)[bear]
    lower[bear] = d["high"][bear]

    return ok({"upper": upper, "lower": lower}, stale=_stale(frame))


def change_of_character(frame: OHLCVFrame, lookback: int = 10) -> MetricResult:
    """The first break against the prevailing structure.

    A break of structure and a change of character are the same event read
    against different backgrounds. In an uptrend, taking out the last swing
    high continues what was already happening and is a BOS; taking out the last
    swing low is the first evidence the uptrend has stopped working, and that is
    the CHoCH. Downtrend mirrors it. The distinction is the whole value: a
    continuation and a reversal look identical on a price chart and mean
    opposite things.

    The term is Wyckoff's, for the shift from markdown into accumulation, and
    predates its borrowing by the smart-money literature. It is included here on
    that reading rather than the ICT one.

    Structure is tracked from confirmed pivots only, so this inherits the fixed
    confirmation lag and carries no lookahead. Direction is:

        +1  bullish CHoCH: a downtrend broke its last swing high
        -1  bearish CHoCH: an uptrend broke its last swing low
         0  no change of character at this bar

    Only the *first* break against the trend is reported. Once structure has
    flipped, subsequent breaks in the new direction are continuation and belong
    to `break_of_structure`; marking every one of them a CHoCH would turn the
    signal into a second, noisier copy of it.
    """
    levels = confirmed_swing_levels(frame, lookback)
    if not levels.is_ok:
        return levels

    close = frame.data["close"].to_numpy(dtype="float64")
    highs = levels.value["high"].to_numpy(dtype="float64")
    lows = levels.value["low"].to_numpy(dtype="float64")

    direction = np.zeros(len(close), dtype="int64")
    # 0 until structure has declared itself. Seeding it to either trend would
    # manufacture a CHoCH out of the first ordinary break in the other.
    trend = 0

    for i in range(len(close)):
        high, low = highs[i], lows[i]
        broke_up = not np.isnan(high) and close[i] > high
        broke_down = not np.isnan(low) and close[i] < low

        if broke_up and broke_down:
            # A bar closing beyond both levels at once is a gap or an outsized
            # candle that swallowed the range. It is not evidence about
            # direction, so the trend is left as it was.
            continue
        if broke_up:
            if trend == -1:
                direction[i] = 1
            trend = 1
        elif broke_down:
            if trend == 1:
                direction[i] = -1
            trend = -1

    return ok(
        pd.Series(direction, index=frame.data.index),
        stale=_stale(frame),
        lookback=lookback,
    )
