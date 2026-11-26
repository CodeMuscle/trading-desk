"""An OHLCV frame that knows when it was fetched and what its venue can do.

Staleness is carried on the frame rather than computed ad hoc by callers,
because a metric over stale data has to be able to label itself stale without
asking anyone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime

import pandas as pd

from desk.capabilities import Capability

PRICE_COLUMNS = ["open", "high", "low", "close", "volume"]

_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
_TIMEFRAME_RE = re.compile(r"^(\d+)([mhdw])$")


@dataclass(frozen=True, slots=True)
class OHLCVFrame:
    data: pd.DataFrame
    venue: str
    symbol: str
    timeframe: str
    as_of: datetime
    capabilities: frozenset[Capability]

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        missing = [c for c in PRICE_COLUMNS if c not in self.data.columns]
        if missing:
            raise ValueError(f"frame is missing columns: {missing}")
        if self.data[PRICE_COLUMNS].isna().any().any():
            raise ValueError(
                "frame contains NaN. A gap stays a gap: drop the bar rather than "
                "forward-filling it."
            )

    def timeframe_seconds(self) -> int:
        match = _TIMEFRAME_RE.match(self.timeframe.lower())
        if not match:
            raise ValueError(
                f"unparseable timeframe {self.timeframe!r}; expected forms like "
                "1m, 15m, 4h, 1d, 1w"
            )
        count, unit = match.groups()
        return int(count) * _UNIT_SECONDS[unit]

    def is_stale(self, now: datetime, tolerance_multiple: float = 2.0) -> bool:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        age = (now - self.as_of).total_seconds()
        return age > self.timeframe_seconds() * tolerance_multiple

    def has(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def tail(self, n: int) -> OHLCVFrame:
        return replace(self, data=self.data.tail(n))
