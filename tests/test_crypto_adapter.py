import pytest

from desk.adapters.crypto import CryptoAdapter
from desk.capabilities import Capability
from tests.test_adapter_contract import adapter_contract


class FakeCcxt:
    """Mimics the ccxt return shape: [ms_timestamp, o, h, l, c, v]."""

    id = "binance"

    def __init__(self, rows=None, repeat_last=False):
        if rows is None:
            start = 1_767_225_600_000  # 2026-01-01T00:00:00Z in ms
            rows = [
                [start + i * 3_600_000, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0]
                for i in range(60)
            ]
        self.rows = rows
        self.repeat_last = repeat_last
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, limit=None):
        self.calls.append((symbol, timeframe, limit))
        rows = list(self.rows)
        if self.repeat_last and rows:
            rows.append(list(rows[-1]))
            return rows
        return rows[: limit or len(rows)]


def test_the_crypto_adapter_satisfies_the_shared_contract():
    adapter_contract(CryptoAdapter(client=FakeCcxt()))


def test_crypto_declares_the_book_and_withholds_what_it_does_not_have():
    info = CryptoAdapter(client=FakeCcxt()).info()
    assert info.has(Capability.OHLCV)
    assert info.has(Capability.AGGRESSOR_TICKS)
    assert info.has(Capability.L2_DEPTH)
    assert not info.has(Capability.SESSION_GRID)
    assert not info.has(Capability.FUNDAMENTALS)
    assert not info.has(Capability.PARTICIPANT_OI)
    assert not info.has(Capability.BROKER_PAPER_API)
    assert info.session_open is None


def test_millisecond_timestamps_become_a_utc_index():
    frame = CryptoAdapter(client=FakeCcxt()).fetch_ohlcv("BTC/USDT", "1h", 10)
    assert str(frame.index.tz) == "UTC"
    assert frame.index[0].year == 2026
    assert len(frame) == 10


def test_a_duplicate_timestamp_is_dropped_keeping_the_last():
    client = FakeCcxt(repeat_last=True)
    frame = CryptoAdapter(client=client).fetch_ohlcv("BTC/USDT", "1h", 60)
    assert not frame.index.has_duplicates


def test_fetch_frame_wraps_the_data_with_capabilities_and_an_as_of():
    adapter = CryptoAdapter(client=FakeCcxt())
    frame = adapter.fetch_frame("BTC/USDT", "1h", 30)
    assert frame.venue == "binance"
    assert frame.symbol == "BTC/USDT"
    assert frame.timeframe == "1h"
    assert frame.as_of.tzinfo is not None
    assert frame.has(Capability.L2_DEPTH)
    assert len(frame.data) == 30


def test_an_empty_response_raises_rather_than_returning_an_empty_frame():
    with pytest.raises(ValueError, match="no bars"):
        CryptoAdapter(client=FakeCcxt(rows=[])).fetch_ohlcv("BTC/USDT", "1h", 10)
