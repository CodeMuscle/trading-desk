"""Volume profile, point of control, and value area.

Dalton's auction framing: price spends most of its time where the most business
was done, and the edges of that region are where the market is most likely to
reject or accept a move. The value area is the narrowest price band containing
a given share of the session's volume, conventionally 70%.

This is a profile over the whole frame, not per session. A per-session profile
arrives with the NSE adapter in a later milestone, where a session grid exists
and means something; crypto trades continuously and has no session to anchor to.
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


def volume_profile(frame: OHLCVFrame, buckets: int = 48) -> MetricResult:
    """Volume distributed across price buckets.

    Each bar's volume is spread evenly across the buckets its high-low range
    touches. Spreading rather than assigning the whole bar to its close is the
    honest choice without tick data: a 4h bar that ranged 2% did not do all its
    business at the closing price, and pretending otherwise produces a profile
    with false precision.

    Returns bucket centres, their volumes, and the point of control.
    """
    if buckets < 2:
        return failed(ValueError(f"buckets must be at least 2, got {buckets}"))

    d = frame.data
    if len(d) < 2:
        return unavailable("needs at least 2 bars, frame has fewer")

    low, high = float(d["low"].min()), float(d["high"].max())
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return unavailable("price range is zero or not finite, so no profile exists")
    if float(d["volume"].sum()) <= 0.0:
        return unavailable("volume is zero across the frame, so no profile exists")

    edges = np.linspace(low, high, buckets + 1)
    centres = (edges[:-1] + edges[1:]) / 2.0
    totals = np.zeros(buckets)

    bar_low = d["low"].to_numpy()
    bar_high = d["high"].to_numpy()
    bar_vol = d["volume"].to_numpy()

    for lo, hi, vol in zip(bar_low, bar_high, bar_vol):
        first = int(np.searchsorted(edges, lo, side="right") - 1)
        last = int(np.searchsorted(edges, hi, side="left") - 1)
        first = max(0, min(first, buckets - 1))
        last = max(0, min(last, buckets - 1))
        span = last - first + 1
        totals[first : last + 1] += vol / span

    poc_index = int(np.argmax(totals))
    return ok(
        {
            "price": pd.Series(centres),
            "volume": pd.Series(totals),
        },
        stale=_stale(frame),
        poc=float(centres[poc_index]),
        buckets=buckets,
    )


def value_area(frame: OHLCVFrame, buckets: int = 48, share: float = 0.70) -> MetricResult:
    """The narrowest band around the point of control holding `share` of volume.

    Dalton's construction: start at the point of control and repeatedly take
    whichever neighbouring bucket holds more volume, until the accumulated share
    is reached. The result is value area high, value area low, and the POC.

    These are three numbers describing the whole frame, not a per-bar series,
    which is why the result is a dict of scalars rather than a Series.
    """
    if not 0.0 < share < 1.0:
        return failed(ValueError(f"share must be between 0 and 1, got {share}"))

    profile = volume_profile(frame, buckets)
    if not profile.is_ok:
        return profile

    price = profile.value["price"].to_numpy()
    volume = profile.value["volume"].to_numpy()
    total = float(volume.sum())
    target = total * share

    poc_index = int(np.argmax(volume))
    low_index = high_index = poc_index
    accumulated = float(volume[poc_index])

    while accumulated < target and (low_index > 0 or high_index < len(volume) - 1):
        below = volume[low_index - 1] if low_index > 0 else -1.0
        above = volume[high_index + 1] if high_index < len(volume) - 1 else -1.0
        if above >= below:
            high_index += 1
            accumulated += float(volume[high_index])
        else:
            low_index -= 1
            accumulated += float(volume[low_index])

    return ok(
        {
            "val": float(price[low_index]),
            "poc": float(price[poc_index]),
            "vah": float(price[high_index]),
            "share_captured": accumulated / total,
        },
        stale=_stale(frame),
        buckets=buckets,
        share=share,
    )
