"""A metric asked to run on an adapter that cannot feed it must refuse.

Crypto provides every capability M1's metrics need, so without a deliberately
impoverished adapter this path would ship untested and first be exercised by
NSE three milestones from now.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from desk.api.app import create_app
from desk.capabilities import AdapterInfo, Capability
from desk.metrics.registry import REGISTRY, MetricSpec
from desk.result import ok, unavailable
from desk.store.cache import FrameStore
from desk.store.frame import OHLCVFrame
from tests.fixtures import frame_from_rows


def nse_like_frame() -> OHLCVFrame:
    """OHLCV and a session grid, but no aggressor ticks and no L2 depth."""
    rows = [
        {"open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i,
         "close": 100.5 + i, "volume": 10.0}
        for i in range(60)
    ]
    frame = frame_from_rows(
        rows, capabilities=frozenset({Capability.OHLCV, Capability.SESSION_GRID})
    )
    return replace(frame, venue="nse")


class PoorAdapter:
    """Declares only what NSE actually has."""

    probe_symbol = "RELIANCE"

    def info(self) -> AdapterInfo:
        return AdapterInfo(
            venue="nse",
            capabilities=frozenset({Capability.OHLCV, Capability.SESSION_GRID}),
            session_open=time(9, 15),
        )

    def fetch_frame(self, symbol: str, timeframe: str, limit: int) -> OHLCVFrame:
        return nse_like_frame()

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        return nse_like_frame().data


def test_every_m1_metric_runs_on_an_ohlcv_only_adapter():
    # None of M1's eight metrics need anything beyond OHLCV, so all eight must
    # work on the impoverished adapter. If one fails here, its declared
    # requirements in the registry are wrong.
    frame = nse_like_frame()
    for name, spec in REGISTRY.items():
        missing = [c for c in spec.requires if not frame.has(c)]
        assert not missing, f"{name} declares {missing}, which M1 metrics should not need"
        result = spec.fn(frame, **spec.defaults)
        assert not result.is_error, f"{name} errored on an OHLCV-only frame: {result.error}"


def test_the_api_refuses_a_metric_whose_capability_is_absent(tmp_path):
    # Register a synthetic metric needing aggressor ticks, the way CVD will in a
    # later milestone, and confirm the API refuses it rather than computing one.
    def fake_cvd(frame, **_):
        return ok(frame.data["close"] * 0)  # never reached

    REGISTRY["fake_cvd"] = MetricSpec(
        fake_cvd, frozenset({Capability.AGGRESSOR_TICKS}), "series", {}
    )
    try:
        client = TestClient(create_app(store=FrameStore(root=tmp_path), adapter=PoorAdapter()))
        response = client.get(
            "/metric/fake_cvd",
            params={"symbol": "RELIANCE", "timeframe": "1h", "limit": 50},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "unavailable"
        assert "aggressor_ticks" in body["reason"]
        assert body["points"] == []
    finally:
        REGISTRY.pop("fake_cvd", None)


def test_no_metric_ever_returns_nan_as_a_value():
    frame = nse_like_frame()
    for name, spec in REGISTRY.items():
        result = spec.fn(frame, **spec.defaults)
        if not result.is_ok:
            continue
        if spec.sparse:
            continue  # declared sparse: NaN means "no feature here", not a gap
        if spec.returns in {"scalars", "profile"}:
            continue  # frame-level descriptors, not per-bar series
        values = (
            result.value.values()
            if spec.returns == "dict_of_series"
            else [result.value]
        )
        for series in values:
            assert not series.isna().any(), f"{name} emitted NaN; use unavailable() instead"


def test_sparseness_is_declared_deliberately_and_stays_rare():
    # Growing this set is a reviewed decision. NaN meaning "no feature at this
    # bar" is legitimate for pivots and bands; NaN meaning "I could not compute
    # this" is the thing the whole result type exists to prevent.
    sparse = {name for name, spec in REGISTRY.items() if spec.sparse}
    assert sparse == {
        "swing_pivots", "confirmed_swing_levels", "imbalance_bands",
        "rsi", "atr",  # warmup: the Wilder seed needs `length` bars to exist
    }


def test_unavailable_requires_a_reason_so_the_ui_can_explain_itself():
    r = unavailable("adapter has no aggressor-tagged ticks")
    assert r.reason
    with pytest.raises(ValueError):
        unavailable("   ")
