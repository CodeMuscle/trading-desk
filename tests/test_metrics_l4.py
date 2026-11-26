import numpy as np
import pytest

from desk.metrics.l4_price_action import (
    bearish_engulfing, bullish_engulfing, hammer, shooting_star, swing_pivots,
)
from tests.fixtures import (
    bullish_engulfing_frame, frame_from_rows, hammer_frame,
    shooting_star_frame, swing_frame,
)


def test_bullish_engulfing_fires_on_the_known_fixture():
    got = bullish_engulfing(bullish_engulfing_frame()).value
    assert bool(got.iloc[0]) is False  # first bar has no predecessor
    assert bool(got.iloc[1]) is True


def test_bullish_engulfing_does_not_fire_when_the_prior_bar_was_up():
    frame = frame_from_rows(
        [
            {"open": 95.0, "high": 101.0, "low": 94.0, "close": 100.0, "volume": 1.0},
            {"open": 95.5, "high": 103.0, "low": 95.0, "close": 102.0, "volume": 1.0},
        ]
    )
    assert bool(bullish_engulfing(frame).value.iloc[1]) is False


def test_bearish_engulfing_is_the_mirror_of_bullish():
    frame = frame_from_rows(
        [
            {"open": 96.0, "high": 101.0, "low": 95.0, "close": 100.0, "volume": 1.0},
            {"open": 100.5, "high": 101.0, "low": 94.0, "close": 95.5, "volume": 1.0},
        ]
    )
    assert bool(bearish_engulfing(frame).value.iloc[1]) is True


def test_hammer_fires_on_the_known_ratios():
    assert bool(hammer(hammer_frame()).value.iloc[0]) is True


def test_hammer_does_not_fire_when_the_upper_wick_is_too_long():
    frame = frame_from_rows(
        [{"open": 100.0, "high": 102.0, "low": 96.0, "close": 101.0, "volume": 1.0}]
    )
    assert bool(hammer(frame).value.iloc[0]) is False


def test_shooting_star_fires_on_the_known_ratios():
    assert bool(shooting_star(shooting_star_frame()).value.iloc[0]) is True


def test_a_doji_with_zero_range_fires_nothing_and_does_not_divide_by_zero():
    frame = frame_from_rows(
        [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0}]
    )
    assert bool(hammer(frame).value.iloc[0]) is False
    assert bool(shooting_star(frame).value.iloc[0]) is False


def test_swing_pivots_finds_the_single_known_pivot_high():
    frame = swing_frame()
    result = swing_pivots(frame, lookback=2)
    assert result.is_ok
    highs = result.value["highs"]
    assert highs.notna().sum() == 1
    assert highs.dropna().iloc[0] == pytest.approx(20.0)
    assert highs.dropna().index[0] == frame.data.index[5]


def test_swing_pivots_marks_non_pivot_bars_as_nan():
    highs = swing_pivots(swing_frame(), lookback=2).value["highs"]
    assert np.isnan(highs.iloc[0])
    assert np.isnan(highs.iloc[4])


def test_swing_pivots_is_unavailable_when_the_frame_is_shorter_than_the_window():
    frame = frame_from_rows(
        [{"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1.0}] * 4
    )
    result = swing_pivots(frame, lookback=10)
    assert result.is_unavailable
    assert "bars" in result.reason


def test_swing_pivots_rejects_a_non_positive_lookback():
    assert swing_pivots(swing_frame(), lookback=0).is_error
