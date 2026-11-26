"""The one interface every market adapter implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

from desk.capabilities import AdapterInfo


@runtime_checkable
class MarketAdapter(Protocol):
    probe_symbol: str

    def info(self) -> AdapterInfo:
        """Declare the venue and what this adapter can provide."""
        ...

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Return exactly the columns open, high, low, close, volume.

        The index is a UTC-localized DatetimeIndex, sorted ascending, with no
        duplicates. Missing bars are absent from the index. Never forward-filled.
        """
        ...
