from datetime import datetime, timezone

import pandas as pd

from desk.capabilities import Capability
from desk.store.cache import FrameStore
from desk.store.frame import OHLCVFrame


def make_frame(bars: int = 20, symbol: str = "BTC/USDT") -> OHLCVFrame:
    idx = pd.date_range("2026-01-01", periods=bars, freq="1h", tz="UTC")
    data = pd.DataFrame(
        {
            "open": [100.0 + i for i in range(bars)],
            "high": [101.0 + i for i in range(bars)],
            "low": [99.0 + i for i in range(bars)],
            "close": [100.5 + i for i in range(bars)],
            "volume": [10.0] * bars,
        },
        index=idx,
    )
    return OHLCVFrame(
        data=data, venue="binance", symbol=symbol, timeframe="1h",
        as_of=datetime(2026, 1, 2, tzinfo=timezone.utc),
        capabilities=frozenset({Capability.OHLCV, Capability.L2_DEPTH}),
    )


def test_a_put_then_get_round_trips_the_data_and_the_metadata(tmp_path):
    store = FrameStore(root=tmp_path)
    original = make_frame()
    store.put(original)
    store.clear_warm()

    got = store.get("binance", "BTC/USDT", "1h")
    assert got is not None
    # check_freq=False deliberately. Parquet does not preserve
    # DatetimeIndex.freq, and restoring it is neither possible nor desirable:
    # pd.infer_freq returns None on a gapped index, and real market data has
    # gaps, which the engine explicitly keeps rather than filling. The frame's
    # `timeframe` field already carries the spacing authoritatively, and
    # nothing in src/ reads .freq.
    pd.testing.assert_frame_equal(got.data, original.data, check_freq=False)
    assert got.venue == "binance"
    assert got.symbol == "BTC/USDT"
    assert got.timeframe == "1h"
    assert got.as_of == original.as_of
    assert got.capabilities == original.capabilities


def test_a_miss_returns_none_rather_than_raising(tmp_path):
    store = FrameStore(root=tmp_path)
    assert store.get("binance", "NOPE/USDT", "1h") is None


def test_the_warm_registry_serves_the_second_read_without_touching_disk(tmp_path):
    store = FrameStore(root=tmp_path)
    store.put(make_frame())

    first = store.get("binance", "BTC/USDT", "1h")
    assert ("binance", "BTC/USDT", "1h") in store.warm_keys()

    for path in tmp_path.rglob("*.parquet"):
        path.unlink()

    second = store.get("binance", "BTC/USDT", "1h")
    assert second is not None
    pd.testing.assert_frame_equal(second.data, first.data, check_freq=False)


def test_a_symbol_with_a_slash_does_not_create_a_nested_directory(tmp_path):
    store = FrameStore(root=tmp_path)
    store.put(make_frame(symbol="BTC/USDT"))
    files = list(tmp_path.rglob("*.parquet"))
    assert len(files) == 1
    assert "/" not in files[0].name
    assert files[0].parent.parent == tmp_path


def test_putting_the_same_key_twice_overwrites_rather_than_appending(tmp_path):
    store = FrameStore(root=tmp_path)
    store.put(make_frame(bars=20))
    store.put(make_frame(bars=5))
    store.clear_warm()
    got = store.get("binance", "BTC/USDT", "1h")
    assert got is not None
    assert len(got.data) == 5
