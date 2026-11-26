"""Hand-built frames with known features, so expected values are verifiable.

Every number these produce can be checked with a calculator. That matters more
than agreeing with another library, because a library can be wrong in the same
direction as the code under test.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from desk.capabilities import Capability
from desk.store.frame import OHLCVFrame

CRYPTO_CAPS = frozenset({Capability.OHLCV, Capability.AGGRESSOR_TICKS, Capability.L2_DEPTH})
OHLCV_ONLY = frozenset({Capability.OHLCV})


def frame_from_rows(
    rows: list[dict[str, float]],
    *,
    timeframe: str = "1h",
    capabilities: frozenset[Capability] = CRYPTO_CAPS,
    start: str = "2026-01-01",
) -> OHLCVFrame:
    freq = timeframe.replace("m", "min")
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    data = pd.DataFrame(rows, index=idx)
    for column in ("open", "high", "low", "close", "volume"):
        if column not in data.columns:
            raise ValueError(f"fixture rows must include {column}")
    return OHLCVFrame(
        data=data[["open", "high", "low", "close", "volume"]].astype("float64"),
        venue="fixture",
        symbol="FIX/USD",
        timeframe=timeframe,
        # as_of is now, not a fixed past date. A fixed date would make every
        # fixture frame stale, which would both mark unrelated metrics stale
        # and make the staleness test vacuous.
        as_of=datetime.now(timezone.utc),
        capabilities=capabilities,
    )


def flat_closes(closes: list[float], **kwargs) -> OHLCVFrame:
    rows = [{"open": c, "high": c, "low": c, "close": c, "volume": 1.0} for c in closes]
    return frame_from_rows(rows, **kwargs)


def bullish_engulfing_frame() -> OHLCVFrame:
    return frame_from_rows(
        [
            {"open": 100.0, "high": 101.0, "low": 95.0, "close": 96.0, "volume": 1.0},
            {"open": 95.5, "high": 103.0, "low": 95.0, "close": 102.0, "volume": 1.0},
        ]
    )


def hammer_frame() -> OHLCVFrame:
    """body=1, lowerWick=4 (> 2x body), upperWick=0.2 (< 0.6x body), close > open."""
    return frame_from_rows(
        [{"open": 100.0, "high": 101.2, "low": 96.0, "close": 101.0, "volume": 1.0}]
    )


def shooting_star_frame() -> OHLCVFrame:
    """body=1, upperWick=4 (> 2x body), lowerWick=0.2 (< 0.6x body), close < open."""
    return frame_from_rows(
        [{"open": 101.0, "high": 105.0, "low": 99.8, "close": 100.0, "volume": 1.0}]
    )


def swing_frame() -> OHLCVFrame:
    """A single unambiguous pivot high at index 5 with lookback 2."""
    highs = [10.0, 11.0, 12.0, 13.0, 14.0, 20.0, 14.5, 13.5, 12.5, 11.5, 10.5]
    rows = [
        {"open": h - 1, "high": h, "low": h - 2, "close": h - 0.5, "volume": 1.0}
        for h in highs
    ]
    return frame_from_rows(rows)
