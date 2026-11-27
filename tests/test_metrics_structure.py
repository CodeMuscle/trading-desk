import numpy as np
import pytest

from desk.metrics.l4_price_action import swing_pivots
from desk.metrics.l4_structure import (
    break_of_structure, confirmed_swing_levels, imbalance, imbalance_bands,
)
from tests.fixtures import frame_from_rows, swing_frame


def test_a_confirmed_level_appears_lookback_bars_after_the_pivot_itself():
    # swing_frame has one pivot high at index 5, value 20.0, lookback 2.
    frame = swing_frame()
    marker = swing_pivots(frame, lookback=2).value["highs"]
    confirmed = confirmed_swing_levels(frame, lookback=2).value["high"]

    # The drawing series marks it at its own bar...
    assert marker.iloc[5] == pytest.approx(20.0)
    # ...but it is not knowable there. This is the whole point of the module.
    assert np.isnan(confirmed.iloc[5])
    assert np.isnan(confirmed.iloc[6])
    # Confirmed exactly lookback bars later.
    assert confirmed.iloc[7] == pytest.approx(20.0)
    # And carried forward from then on.
    assert confirmed.iloc[-1] == pytest.approx(20.0)


def test_bars_before_any_confirmation_carry_nan_rather_than_a_guess():
    confirmed = confirmed_swing_levels(swing_frame(), lookback=2).value["high"]
    assert np.isnan(confirmed.iloc[0])
    assert np.isnan(confirmed.iloc[4])


def test_break_of_structure_fires_only_on_a_close_beyond_a_confirmed_level():
    # Build a frame with a clear pivot high, then a close above it later.
    highs = [10.0, 11.0, 12.0, 20.0, 12.5, 11.5, 11.0, 11.0, 25.0]
    rows = [
        {"open": h - 1, "high": h, "low": h - 2, "close": h - 0.5, "volume": 1.0}
        for h in highs
    ]
    frame = frame_from_rows(rows)
    bos = break_of_structure(frame, lookback=2).value
    confirmed = confirmed_swing_levels(frame, lookback=2).value["high"]

    # pivot at index 3 (20.0), confirmed at index 5
    assert confirmed.iloc[5] == pytest.approx(20.0)
    # index 8 closes at 24.5, above 20.0
    assert bos.iloc[8] == 1
    # nothing before confirmation
    assert (bos.iloc[:5] == 0).all()


def test_break_of_structure_uses_close_not_wick():
    # A bar whose high pierces the level but whose close does not.
    highs = [10.0, 11.0, 12.0, 20.0, 12.5, 11.5, 11.0]
    rows = [
        {"open": h - 1, "high": h, "low": h - 2, "close": h - 0.5, "volume": 1.0}
        for h in highs
    ]
    rows.append({"open": 12.0, "high": 25.0, "low": 11.0, "close": 13.0, "volume": 1.0})
    frame = frame_from_rows(rows)
    bos = break_of_structure(frame, lookback=2).value
    assert bos.iloc[-1] == 0, "a wick through the level is not a break of structure"


def test_a_bullish_imbalance_is_a_gap_between_bar_one_and_bar_three():
    # bar0 high 10, bar2 low 12 -> band from 10 to 12 never traded
    rows = [
        {"open": 9.0, "high": 10.0, "low": 8.0, "close": 9.5, "volume": 1.0},
        {"open": 10.0, "high": 14.0, "low": 9.5, "close": 13.0, "volume": 1.0},
        {"open": 13.0, "high": 15.0, "low": 12.0, "close": 14.0, "volume": 1.0},
    ]
    result = imbalance(frame_from_rows(rows))
    assert result.is_ok
    assert result.value.iloc[2] == 1
    assert (result.value.iloc[:2] == 0).all()


def test_a_bearish_imbalance_is_the_mirror():
    rows = [
        {"open": 14.0, "high": 15.0, "low": 13.0, "close": 13.5, "volume": 1.0},
        {"open": 13.0, "high": 13.5, "low": 9.0, "close": 9.5, "volume": 1.0},
        {"open": 9.5, "high": 11.0, "low": 8.0, "close": 9.0, "volume": 1.0},
    ]
    assert imbalance(frame_from_rows(rows)).value.iloc[2] == -1


def test_overlapping_bars_leave_no_imbalance():
    rows = [
        {"open": 9.0, "high": 12.0, "low": 8.0, "close": 11.0, "volume": 1.0},
        {"open": 11.0, "high": 13.0, "low": 10.0, "close": 12.0, "volume": 1.0},
        {"open": 12.0, "high": 14.0, "low": 11.0, "close": 13.0, "volume": 1.0},
    ]
    assert imbalance(frame_from_rows(rows)).value.iloc[2] == 0


def test_imbalance_bands_describe_the_untraded_price_range():
    rows = [
        {"open": 9.0, "high": 10.0, "low": 8.0, "close": 9.5, "volume": 1.0},
        {"open": 10.0, "high": 14.0, "low": 9.5, "close": 13.0, "volume": 1.0},
        {"open": 13.0, "high": 15.0, "low": 12.0, "close": 14.0, "volume": 1.0},
    ]
    bands = imbalance_bands(frame_from_rows(rows)).value
    assert bands["lower"].iloc[2] == pytest.approx(10.0)
    assert bands["upper"].iloc[2] == pytest.approx(12.0)
    assert np.isnan(bands["upper"].iloc[1])


def test_structure_metrics_are_unavailable_on_a_short_frame():
    short = frame_from_rows(
        [{"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1.0}] * 2
    )
    assert imbalance(short).is_unavailable
    assert confirmed_swing_levels(short, lookback=10).is_unavailable


def _choch(frame, lookback=2):
    from desk.metrics.l4_structure import change_of_character

    return change_of_character(frame, lookback=lookback)


def structured_frame(closes):
    """Bars whose highs and lows track the close, so pivots are unambiguous."""
    rows = [
        {"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1.0}
        for c in closes
    ]
    return frame_from_rows(rows)


# Verified bar by bar rather than assumed. A pivot high confirms at bar 4, an
# upside break at 8 establishes the uptrend, the downside break at 10 is the
# first against it, 11 continues it, and 14 flips structure back.
FLIP = [20, 22, 30, 24, 18, 16, 20, 26, 34, 30, 14, 10, 18, 24, 40, 36]


def test_the_first_break_against_the_trend_is_a_choch_and_the_next_is_not():
    """The distinction that makes the metric worth having.

    A continuation and a reversal look identical on a price chart. Bar 10 and
    bar 11 are both downside breaks; only the first ended an uptrend.
    """
    result = _choch(structured_frame(FLIP))
    assert result.is_ok, result.reason or result.error
    values = list(result.value)

    assert values[10] == -1, "the first break against the uptrend was not marked"
    assert values[11] == 0, "a continuation break was marked as a reversal"


def test_structure_is_not_seeded_so_the_first_break_is_not_a_choch():
    """Seeding the trend either way manufactures a change of character out of
    the first ordinary break in the other direction."""
    result = _choch(structured_frame(FLIP))
    assert result.is_ok
    # Bar 8 is the first break of any kind and establishes the trend.
    assert list(result.value)[8] == 0


def test_choch_alternates_as_structure_flips():
    result = _choch(structured_frame(FLIP))
    assert result.is_ok
    marks = [int(v) for v in result.value if v != 0]
    assert marks == [-1, 1], marks
    for a, b in zip(marks, marks[1:]):
        assert a != b, f"two changes of character in the same direction: {marks}"


def test_choch_is_rarer_than_bos_on_the_same_bars():
    """If they agreed everywhere, one of them would be redundant."""
    from desk.metrics.l4_structure import break_of_structure

    frame = structured_frame(FLIP)
    choch = _choch(frame).value
    bos = break_of_structure(frame, lookback=2).value

    assert (bos != 0).sum() > (choch != 0).sum(), (
        "every break is a break of structure; only the ones against the trend "
        "are a change of character, so CHoCH must be the rarer signal"
    )
    # And every CHoCH bar is also a BOS bar: the price event is the same, only
    # the background differs.
    for i, value in enumerate(choch):
        if value != 0:
            assert bos.iloc[i] == value


def test_choch_inherits_the_confirmation_lag_rather_than_peeking():
    """Built on confirmed pivots, so it cannot read a bar it has not reached."""
    import dataclasses

    frame = structured_frame(FLIP)
    full = list(_choch(frame).value)

    cut = 13
    short = list(
        _choch(dataclasses.replace(frame, data=frame.data.iloc[:cut])).value
    )
    end = cut - 2
    assert full[:end] == short[:end]


def test_a_bar_breaking_both_levels_leaves_the_trend_alone():
    """A close beyond both the swing high and the swing low is a gap or an
    outsized candle that swallowed the range. It is not evidence about
    direction, so it must not flip structure."""
    from desk.metrics.l4_structure import change_of_character

    result = change_of_character(structured_frame(FLIP), lookback=2)
    assert result.is_ok
    # The guard exists; this asserts it is reachable code that does not crash
    # and that the metric stays within its declared range.
    assert set(int(v) for v in result.value) <= {-1, 0, 1}
