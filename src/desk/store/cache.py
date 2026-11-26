"""Parquet on disk, plus a warm in-memory registry keyed the same way.

The warm registry exists so recomputing a metric does not pay a disk read. The
frame load is the slow part; numpy over an in-memory frame is not.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from desk.capabilities import Capability
from desk.store.frame import OHLCVFrame

Key = tuple[str, str, str]


def _safe(part: str) -> str:
    """Make a symbol safe for a filename without losing information.

    BTC/USDT becomes BTC-USDT. A slash would otherwise create an unintended
    directory level, which breaks enumeration.
    """
    return part.replace("/", "-").replace(":", "_")


class FrameStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._warm: dict[Key, OHLCVFrame] = {}

    def _paths(self, venue: str, symbol: str, timeframe: str) -> tuple[Path, Path]:
        directory = self.root / _safe(venue)
        stem = f"{_safe(symbol)}__{_safe(timeframe)}"
        return directory / f"{stem}.parquet", directory / f"{stem}.meta.json"

    def put(self, frame: OHLCVFrame) -> None:
        data_path, meta_path = self._paths(frame.venue, frame.symbol, frame.timeframe)
        data_path.parent.mkdir(parents=True, exist_ok=True)
        frame.data.to_parquet(data_path, engine="pyarrow")
        meta_path.write_text(
            json.dumps(
                {
                    "venue": frame.venue,
                    "symbol": frame.symbol,
                    "timeframe": frame.timeframe,
                    "as_of": frame.as_of.isoformat(),
                    "capabilities": sorted(c.value for c in frame.capabilities),
                }
            )
        )
        self._warm[(frame.venue, frame.symbol, frame.timeframe)] = frame

    def get(self, venue: str, symbol: str, timeframe: str) -> OHLCVFrame | None:
        key: Key = (venue, symbol, timeframe)
        if key in self._warm:
            return self._warm[key]

        data_path, meta_path = self._paths(venue, symbol, timeframe)
        if not data_path.exists() or not meta_path.exists():
            return None

        meta = json.loads(meta_path.read_text())
        frame = OHLCVFrame(
            data=pd.read_parquet(data_path, engine="pyarrow"),
            venue=meta["venue"],
            symbol=meta["symbol"],
            timeframe=meta["timeframe"],
            as_of=datetime.fromisoformat(meta["as_of"]),
            capabilities=frozenset(Capability(c) for c in meta["capabilities"]),
        )
        self._warm[key] = frame
        return frame

    def warm_keys(self) -> list[Key]:
        return list(self._warm)

    def clear_warm(self) -> None:
        self._warm.clear()
