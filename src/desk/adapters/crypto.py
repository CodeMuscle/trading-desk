"""ccxt-backed crypto adapter.

Crypto is the only market in this project where the full order book and real
aggressor-tagged ticks are free, so it declares capabilities no other adapter
will. The ccxt client is injected so the tests run offline: this class's job is
normalisation, and a fake exercises that completely.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pandas as pd

from desk.capabilities import AdapterInfo, Capability
from desk.store.frame import PRICE_COLUMNS, OHLCVFrame

CRYPTO_CAPABILITIES = frozenset(
    {Capability.OHLCV, Capability.AGGRESSOR_TICKS, Capability.L2_DEPTH}
)

PAGE_SIZE = 1000
"""Bars per exchange call. Binance, Kraken and most others cap here.

Asking for more in a single call does not error: the exchange silently returns
1000. That silence is the reason this constant exists rather than being
discovered when a backtest turns out to cover six weeks instead of a year."""

PAGE_MARGIN = 16
"""Extra bars requested beyond `limit`, to absorb clock skew and a partly
formed current bar. Trimmed off by the tail."""

MAX_PAGES = 60
"""A runaway guard, not a limit anyone should reach. 60 pages is 60,000 bars,
which is roughly seven years of hourly data."""

_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
_TIMEFRAME_RE = re.compile(r"^(\d+)([mhdw])$")


def timeframe_ms(timeframe: str) -> int:
    """Milliseconds per bar. Raises rather than guessing on an unknown unit."""
    match = _TIMEFRAME_RE.match(timeframe.lower())
    if not match:
        raise ValueError(
            f"unparseable timeframe {timeframe!r}; expected forms like 1m, 15m, 4h, 1d, 1w"
        )
    count, unit = match.groups()
    return int(count) * _UNIT_MS[unit]


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

    def _fetch_pages(self, symbol: str, timeframe: str, limit: int) -> list[list]:
        """Page forward until `limit` bars are collected or the data runs out.

        A single call is capped at PAGE_SIZE by the exchange, silently. Pages
        start from now minus the full span requested and walk forward, because
        that anchors the window to the present: the most recent bars are the
        ones a backtest most needs and the ones a user expects to see.
        """
        if limit <= PAGE_SIZE:
            return self.client.fetch_ohlcv(symbol, timeframe, limit=limit)

        step = timeframe_ms(timeframe)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        # Start a margin earlier than strictly needed. Anchoring at exactly
        # now - limit * step leaves the window one bar short whenever the
        # current bar is only partly formed, or whenever the local clock sits
        # slightly ahead of the exchange's. The tail is trimmed to `limit`
        # afterwards, so over-fetching costs one page at worst and under-
        # fetching silently returns a shorter backtest than was asked for.
        cursor = now_ms - (limit + PAGE_MARGIN) * step

        collected: list[list] = []
        for _ in range(MAX_PAGES):
            page = self.client.fetch_ohlcv(
                symbol, timeframe, since=cursor, limit=PAGE_SIZE
            )
            if not page:
                break
            collected.extend(page)

            last_ts = page[-1][0]
            if last_ts < cursor:
                # The exchange went backwards. Stop rather than loop forever.
                break
            cursor = last_ts + step

            if len(collected) >= limit or cursor >= now_ms:
                break
            if len(page) < PAGE_SIZE:
                # A short page means the history ended here.
                break
        return collected

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        rows = self._fetch_pages(symbol, timeframe, limit)
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
