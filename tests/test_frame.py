from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from desk.capabilities import Capability
from desk.store.frame import OHLCVFrame


def make_frame(bars: int = 10, timeframe: str = "1h") -> OHLCVFrame:
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
        data=data, venue="fake", symbol="FAKE/USD", timeframe=timeframe,
        as_of=idx[-1].to_pydatetime(),
        capabilities=frozenset({Capability.OHLCV}),
    )


def test_timeframe_seconds_parses_the_common_units():
    assert make_frame(timeframe="1m").timeframe_seconds() == 60
    assert make_frame(timeframe="15m").timeframe_seconds() == 900
    assert make_frame(timeframe="4h").timeframe_seconds() == 14400
    assert make_frame(timeframe="1d").timeframe_seconds() == 86400
    assert make_frame(timeframe="1w").timeframe_seconds() == 604800


def test_an_unparseable_timeframe_raises_rather_than_guessing():
    with pytest.raises(ValueError, match="timeframe"):
        make_frame(timeframe="fortnightly").timeframe_seconds()


def test_a_frame_is_fresh_within_two_bar_intervals():
    f = make_frame(timeframe="1h")
    assert not f.is_stale(now=f.as_of + timedelta(minutes=90))


def test_a_frame_is_stale_beyond_the_tolerance():
    f = make_frame(timeframe="1h")
    assert f.is_stale(now=f.as_of + timedelta(hours=3))


def test_the_staleness_tolerance_is_adjustable():
    f = make_frame(timeframe="1h")
    assert f.is_stale(now=f.as_of + timedelta(minutes=90), tolerance_multiple=1.0)


def test_capability_questions_are_answered_by_the_frame():
    f = make_frame()
    assert f.has(Capability.OHLCV)
    assert not f.has(Capability.AGGRESSOR_TICKS)


def test_tail_preserves_metadata_and_shortens_the_data():
    f = make_frame(bars=50)
    t = f.tail(10)
    assert len(t.data) == 10
    assert t.venue == f.venue
    assert t.symbol == f.symbol
    assert t.capabilities == f.capabilities
    assert t.as_of == f.as_of


def test_a_frame_rejects_a_naive_as_of():
    idx = pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC")
    data = pd.DataFrame(
        {"open": [1.0] * 3, "high": [2.0] * 3, "low": [0.5] * 3,
         "close": [1.5] * 3, "volume": [1.0] * 3},
        index=idx,
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        OHLCVFrame(data=data, venue="fake", symbol="X", timeframe="1h",
                   as_of=datetime(2026, 1, 1),
                   capabilities=frozenset({Capability.OHLCV}))


def test_a_frame_rejects_a_nan_in_the_price_columns():
    idx = pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC")
    data = pd.DataFrame(
        {"open": [1.0] * 3, "high": [2.0] * 3, "low": [0.5] * 3,
         "close": [1.5, float("nan"), 1.5], "volume": [1.0] * 3},
        index=idx,
    )
    with pytest.raises(ValueError, match="NaN"):
        OHLCVFrame(data=data, venue="fake", symbol="X", timeframe="1h",
                   as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
                   capabilities=frozenset({Capability.OHLCV}))
