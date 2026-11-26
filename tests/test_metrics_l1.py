from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd
import pytest

from desk.metrics.l1_trend import ema, session_vwap, trend_state
from tests.fixtures import flat_closes, frame_from_rows


def test_ema_first_value_equals_the_first_close():
    result = ema(flat_closes([10.0, 20.0, 30.0]), length=3)
    assert result.is_ok
    assert result.value.iloc[0] == pytest.approx(10.0)


def test_ema_matches_a_hand_computed_series():
    # k = 2/(3+1) = 0.5 ; e0=10 ; e1=20*.5+10*.5=15 ; e2=30*.5+15*.5=22.5
    got = ema(flat_closes([10.0, 20.0, 30.0]), length=3).value
    assert got.iloc[0] == pytest.approx(10.0)
    assert got.iloc[1] == pytest.approx(15.0)
    assert got.iloc[2] == pytest.approx(22.5)


def test_ema_returns_a_series_indexed_like_the_frame():
    frame = flat_closes([1.0] * 30)
    got = ema(frame, length=10).value
    assert isinstance(got, pd.Series)
    assert got.index.equals(frame.data.index)


def test_ema_rejects_a_non_positive_length():
    frame = flat_closes([1.0] * 10)
    assert ema(frame, length=0).is_error
    assert ema(frame, length=-5).is_error


def test_ema_is_unavailable_when_there_are_fewer_bars_than_the_length():
    result = ema(flat_closes([1.0, 2.0, 3.0]), length=20)
    assert result.is_unavailable
    assert "bars" in result.reason


def test_a_fresh_frame_is_not_marked_stale():
    result = ema(flat_closes([1.0 + i for i in range(30)]), length=10)
    assert result.is_ok
    assert result.stale is False


def test_a_metric_over_a_stale_frame_is_marked_stale():
    frame = flat_closes([1.0 + i for i in range(30)])
    old = replace(frame, as_of=datetime(2020, 1, 1, tzinfo=timezone.utc))
    result = ema(old, length=10)
    assert result.is_ok
    assert result.stale is True


def test_trend_state_is_up_when_fast_is_above_slow():
    got = trend_state(flat_closes([float(i) for i in range(1, 80)]), fast=20, slow=50).value
    assert got.iloc[-1] == "up"


def test_trend_state_is_down_when_fast_is_below_slow():
    got = trend_state(flat_closes([float(i) for i in range(80, 1, -1)]), fast=20, slow=50).value
    assert got.iloc[-1] == "down"


def test_trend_state_is_flat_when_the_emas_are_equal():
    got = trend_state(flat_closes([50.0] * 80), fast=20, slow=50).value
    assert got.iloc[-1] == "flat"


def test_session_vwap_matches_a_hand_computed_value():
    # typical = (h+l+c)/3 ; bar1 (12+8+10)/3=10 vol100 -> 1000
    # bar2 (22+18+20)/3=20 vol300 -> 6000 ; vwap = 7000/400 = 17.5
    frame = frame_from_rows(
        [
            {"open": 9.0, "high": 12.0, "low": 8.0, "close": 10.0, "volume": 100.0},
            {"open": 19.0, "high": 22.0, "low": 18.0, "close": 20.0, "volume": 300.0},
        ]
    )
    got = session_vwap(frame).value
    assert got.iloc[0] == pytest.approx(10.0)
    assert got.iloc[1] == pytest.approx(17.5)


def test_session_vwap_resets_each_calendar_day():
    rows = [
        {"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1.0}
        for _ in range(48)
    ]
    rows[24] = {"open": 99.0, "high": 99.0, "low": 99.0, "close": 99.0, "volume": 1.0}
    got = session_vwap(frame_from_rows(rows)).value
    assert got.iloc[24] == pytest.approx(99.0)


def test_session_vwap_is_unavailable_when_volume_is_all_zero():
    frame = frame_from_rows(
        [{"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0} for _ in range(5)]
    )
    result = session_vwap(frame)
    assert result.is_unavailable
    assert "volume" in result.reason
