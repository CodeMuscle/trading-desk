import pytest

from desk.metrics.l1_momentum import atr, macd, rsi, true_range
from tests.fixtures import flat_closes, frame_from_rows


def test_rsi_is_100_when_every_bar_closes_higher():
    got = rsi(flat_closes([float(i) for i in range(1, 60)]), length=14).value
    assert got.iloc[-1] == pytest.approx(100.0)


def test_rsi_is_0_when_every_bar_closes_lower():
    got = rsi(flat_closes([float(i) for i in range(60, 1, -1)]), length=14).value
    assert got.iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_rsi_is_50_on_a_perfectly_flat_series_rather_than_nan():
    got = rsi(flat_closes([50.0] * 40), length=14).value
    assert got.iloc[-1] == pytest.approx(50.0)
    # No NaN after the warmup window. Inside it the Wilder seed does not yet
    # exist, which is a different statement from "could not compute".
    assert not got.iloc[14:].isna().any()


def test_rsi_stays_inside_its_bounds_on_mixed_data():
    closes = [100.0 + ((i * 37) % 11 - 5) for i in range(80)]
    got = rsi(flat_closes(closes), length=14).value
    assert got.min() >= 0.0
    assert got.max() <= 100.0


def test_rsi_is_unavailable_without_enough_bars():
    result = rsi(flat_closes([1.0, 2.0, 3.0]), length=14)
    assert result.is_unavailable
    assert "bars" in result.reason


def test_macd_histogram_is_the_line_minus_its_signal():
    frame = flat_closes([100.0 + i * 0.5 for i in range(80)])
    got = macd(frame).value
    assert (got["hist"] - (got["macd"] - got["signal"])).abs().max() == pytest.approx(0.0)


def test_macd_histogram_is_positive_in_a_rising_market():
    frame = flat_closes([float(i) for i in range(1, 100)])
    assert macd(frame).value["hist"].iloc[-1] > 0


def test_macd_histogram_is_negative_in_a_falling_market():
    frame = flat_closes([float(i) for i in range(100, 1, -1)])
    assert macd(frame).value["hist"].iloc[-1] < 0


def test_macd_rejects_a_fast_length_at_or_above_slow():
    frame = flat_closes([1.0] * 80)
    assert macd(frame, fast=26, slow=26).is_error
    assert macd(frame, fast=30, slow=26).is_error


def test_true_range_takes_the_largest_of_the_three_candidates():
    # prev close 10, this bar high 20 low 18 -> |high - prevClose| = 10 wins
    frame = frame_from_rows(
        [
            {"open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "volume": 1.0},
            {"open": 18.5, "high": 20.0, "low": 18.0, "close": 19.0, "volume": 1.0},
        ]
    )
    got = true_range(frame).value
    assert got.iloc[1] == pytest.approx(10.0)


def test_the_first_bars_true_range_is_its_own_range():
    frame = frame_from_rows(
        [
            {"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1.0},
            {"open": 11.0, "high": 11.5, "low": 10.5, "close": 11.0, "volume": 1.0},
        ]
    )
    assert true_range(frame).value.iloc[0] == pytest.approx(3.0)


def test_atr_on_a_constant_range_equals_that_range():
    rows = [
        {"open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0, "volume": 1.0}
        for _ in range(60)
    ]
    got = atr(frame_from_rows(rows), length=14).value
    assert got.iloc[-1] == pytest.approx(4.0, abs=0.01)


def test_atr_is_never_negative():
    closes = [100.0 + ((i * 29) % 13 - 6) for i in range(80)]
    rows = [
        {"open": c, "high": c + 2, "low": c - 2, "close": c, "volume": 1.0}
        for c in closes
    ]
    got = atr(frame_from_rows(rows), length=14).value
    assert (got.dropna() >= 0).all()


def test_the_wilder_seed_is_an_sma_of_the_first_length_values():
    # This is the detail that makes RSI and ATR agree with TradingView.
    # Pine's ta.rma seeds with ta.sma(src, length); seeding with the first
    # observation instead produced a 5.6-point RSI error at bar 25 that only
    # decayed to zero around bar 200.
    rows = [
        {"open": 100.0, "high": 100.0 + i, "low": 100.0, "close": 100.0, "volume": 1.0}
        for i in range(1, 30)
    ]
    frame = frame_from_rows(rows)
    tr = true_range(frame).value
    got = atr(frame, length=14).value

    assert got.iloc[:13].isna().all(), "no average exists before the seed"
    assert got.iloc[13] == pytest.approx(tr.iloc[:14].mean()), "seed must be the SMA"
    # and from there, Wilder's recursion
    expected_14 = (got.iloc[13] * 13 + tr.iloc[14]) / 14
    assert got.iloc[14] == pytest.approx(expected_14)


def test_wilder_smoothing_differs_from_a_conventional_ema():
    # alpha = 1/14 vs 2/15. If these matched, the implementation would be
    # using the wrong smoothing and would drift from TradingView.
    from desk.metrics.l1_trend import ema

    rows = [
        {"open": 100.0 + i, "high": 103.0 + i, "low": 98.0 + i,
         "close": 100.0 + i, "volume": 1.0}
        for i in range(60)
    ]
    frame = frame_from_rows(rows)
    wilder_atr = atr(frame, length=14).value.iloc[-1]
    conventional = frame.data["close"].ewm(span=14, adjust=False).mean().iloc[-1]
    assert wilder_atr != pytest.approx(conventional)
