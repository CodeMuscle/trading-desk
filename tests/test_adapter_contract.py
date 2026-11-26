"""The contract every adapter must satisfy, in every milestone."""

from __future__ import annotations

import pandas as pd

from desk.capabilities import AdapterInfo, Capability

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


def adapter_contract(adapter) -> None:
    info = adapter.info()
    assert isinstance(info, AdapterInfo)
    assert info.venue, "an adapter must name its venue"
    assert Capability.OHLCV in info.capabilities, "every adapter must provide OHLCV"

    frame = adapter.fetch_ohlcv(adapter.probe_symbol, "1h", 50)

    assert isinstance(frame, pd.DataFrame)
    assert list(frame.columns) == REQUIRED_COLUMNS, (
        f"columns must be exactly {REQUIRED_COLUMNS}, got {list(frame.columns)}"
    )
    assert isinstance(frame.index, pd.DatetimeIndex), "index must be a DatetimeIndex"
    assert frame.index.tz is not None, "index must be timezone-aware"
    assert str(frame.index.tz) == "UTC", "index must be UTC"
    assert frame.index.is_monotonic_increasing, "index must be sorted ascending"
    assert not frame.index.has_duplicates, "index must have no duplicate timestamps"
    assert len(frame) > 0, "a contract probe must return at least one bar"
    assert not frame[REQUIRED_COLUMNS].isna().any().any(), (
        "an adapter must not emit NaN. A gap stays a gap: drop the bar instead "
        "of forward-filling it."
    )
    assert (frame["high"] >= frame["low"]).all(), "high must be >= low on every bar"
    assert (frame["high"] >= frame[["open", "close"]].max(axis=1)).all()
    assert (frame["low"] <= frame[["open", "close"]].min(axis=1)).all()


class FakeAdapter:
    """A minimal conforming adapter, so the contract is tested without a network."""

    probe_symbol = "FAKE/USD"

    def info(self) -> AdapterInfo:
        return AdapterInfo(venue="fake", capabilities=frozenset({Capability.OHLCV}))

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        idx = pd.date_range("2026-01-01", periods=limit, freq="1h", tz="UTC")
        rows = []
        for i in range(limit):
            o = 100.0 + i
            c = o + 0.5
            rows.append({"open": o, "high": c + 1, "low": o - 1, "close": c, "volume": 10.0})
        return pd.DataFrame(rows, index=idx)


def test_the_contract_accepts_a_conforming_adapter():
    adapter_contract(FakeAdapter())


def test_the_contract_rejects_a_forward_filled_gap():
    class FillsGaps(FakeAdapter):
        def fetch_ohlcv(self, symbol, timeframe, limit):
            frame = super().fetch_ohlcv(symbol, timeframe, limit)
            frame.iloc[3, frame.columns.get_loc("close")] = float("nan")
            return frame

    try:
        adapter_contract(FillsGaps())
    except AssertionError as exc:
        assert "must not emit NaN" in str(exc)
    else:
        raise AssertionError("the contract should have rejected a NaN close")


def test_the_contract_rejects_a_naive_index():
    class NaiveIndex(FakeAdapter):
        def fetch_ohlcv(self, symbol, timeframe, limit):
            frame = super().fetch_ohlcv(symbol, timeframe, limit)
            frame.index = frame.index.tz_localize(None)
            return frame

    try:
        adapter_contract(NaiveIndex())
    except AssertionError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("the contract should have rejected a naive index")
