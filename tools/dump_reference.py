"""Dump bars and metric output from a single frame, for tools/cross-check.mjs.

One fetch, deliberately. Fetching bars and metrics separately lets a newly
printed bar shift one series relative to the other, which makes every
comparison downstream wrong in a way that looks like a real disagreement.
"""

from __future__ import annotations

import json

from desk.adapters.crypto import CryptoAdapter
from desk.metrics.l1_momentum import atr, rsi
from desk.metrics.l1_trend import ema


def main() -> None:
    frame = CryptoAdapter().fetch_frame("BTC/USDT", "4h", 300)
    bars = [
        {
            "time": int(ts.timestamp()),
            "open": row.open, "high": row.high,
            "low": row.low, "close": row.close, "volume": row.volume,
        }
        for ts, row in frame.data.iterrows()
    ]
    nan_to_none = lambda s: [None if x != x else float(x) for x in s]  # noqa: E731

    with open("/tmp/bars.json", "w") as fh:
        json.dump(bars, fh)
    with open("/tmp/py.json", "w") as fh:
        json.dump(
            {
                "rsi14": nan_to_none(rsi(frame, 14).value),
                "atr14": nan_to_none(atr(frame, 14).value),
                "ema20": nan_to_none(ema(frame, 20).value),
            },
            fh,
        )
    print(f"dumped {len(bars)} bars and metrics from one frame")


if __name__ == "__main__":
    main()
