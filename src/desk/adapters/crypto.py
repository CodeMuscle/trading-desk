"""ccxt-backed crypto adapter.

Crypto is the only market in this project where the full order book and real
aggressor-tagged ticks are free, so it declares capabilities no other adapter
will. The ccxt client is injected so the tests run offline: this class's job is
normalisation, and a fake exercises that completely.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from desk.capabilities import AdapterInfo, Capability
from desk.store.frame import PRICE_COLUMNS, OHLCVFrame

CRYPTO_CAPABILITIES = frozenset(
    {Capability.OHLCV, Capability.AGGRESSOR_TICKS, Capability.L2_DEPTH}
)


class CryptoAdapter:
    probe_symbol = "BTC/USDT"

    def __init__(self, exchange_id: str = "binance", client=None) -> None:
        if client is None:
            import ccxt

            client = getattr(ccxt, exchange_id)({"enableRateLimit": True})
        self.client = client
        self.exchange_id = getattr(client, "id", exchange_id)

    def info(self) -> AdapterInfo:
        return AdapterInfo(
            venue=self.exchange_id,
            capabilities=CRYPTO_CAPABILITIES,
            session_open=None,
        )

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        rows = self.client.fetch_ohlcv(symbol, timeframe, limit=limit)
        if not rows:
            raise ValueError(f"exchange returned no bars for {symbol} {timeframe}")

        frame = pd.DataFrame(rows, columns=["ts", *PRICE_COLUMNS])
        frame.index = pd.to_datetime(frame.pop("ts"), unit="ms", utc=True)
        frame.index.name = None
        frame = frame.astype({c: "float64" for c in PRICE_COLUMNS})

        # A repeated final bar is a normal artefact of polling a live candle.
        # Keep the last observation and drop the earlier duplicate; never merge.
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()

        # A gap stays a gap. Dropping an incomplete row is correct; filling it
        # would invent a candle.
        frame = frame.dropna(subset=PRICE_COLUMNS)
        return frame.tail(limit)

    def fetch_frame(self, symbol: str, timeframe: str, limit: int) -> OHLCVFrame:
        data = self.fetch_ohlcv(symbol, timeframe, limit)
        return OHLCVFrame(
            data=data,
            venue=self.exchange_id,
            symbol=symbol,
            timeframe=timeframe,
            as_of=datetime.now(timezone.utc),
            capabilities=CRYPTO_CAPABILITIES,
        )
