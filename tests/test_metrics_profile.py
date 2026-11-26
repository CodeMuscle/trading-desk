import pytest

from desk.metrics.l2_profile import value_area, volume_profile
from tests.fixtures import frame_from_rows


def concentrated_frame():
    """Most volume traded in a tight band around 100, a little at the extremes."""
    rows = []
    for _ in range(20):
        rows.append({"open": 99.8, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 100.0})
    rows.append({"open": 90.0, "high": 90.5, "low": 89.5, "close": 90.0, "volume": 1.0})
    rows.append({"open": 110.0, "high": 110.5, "low": 109.5, "close": 110.0, "volume": 1.0})
    return frame_from_rows(rows)


def test_the_point_of_control_lands_where_the_volume_is():
    result = volume_profile(concentrated_frame(), buckets=40)
    assert result.is_ok
    assert result.meta["poc"] == pytest.approx(100.0, abs=0.6)


def test_profile_volume_sums_to_the_frames_volume():
    frame = concentrated_frame()
    result = volume_profile(frame, buckets=40)
    assert float(result.value["volume"].sum()) == pytest.approx(
        float(frame.data["volume"].sum())
    )


def test_a_bars_volume_is_spread_across_the_buckets_it_touches():
    # One bar spanning the whole range: its volume must be split, not heaped.
    frame = frame_from_rows(
        [
            {"open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0, "volume": 100.0},
            {"open": 105.0, "high": 106.0, "low": 104.0, "close": 105.0, "volume": 1.0},
        ]
    )
    volumes = volume_profile(frame, buckets=10).value["volume"]
    assert (volumes > 0).sum() >= 8, "a wide bar should touch most buckets"
    assert volumes.max() < 100.0, "no single bucket should hold the whole bar"


def test_the_value_area_brackets_the_point_of_control():
    result = value_area(concentrated_frame(), buckets=40, share=0.70)
    assert result.is_ok
    va = result.value
    assert va["val"] <= va["poc"] <= va["vah"]


def test_the_value_area_captures_at_least_the_requested_share():
    result = value_area(concentrated_frame(), buckets=40, share=0.70)
    assert result.value["share_captured"] >= 0.70


def test_a_tighter_share_gives_a_narrower_band():
    frame = concentrated_frame()
    narrow = value_area(frame, buckets=40, share=0.50).value
    wide = value_area(frame, buckets=40, share=0.90).value
    assert (wide["vah"] - wide["val"]) >= (narrow["vah"] - narrow["val"])


def test_a_flat_frame_has_no_profile_rather_than_a_fake_one():
    frame = frame_from_rows(
        [{"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 5.0}] * 5
    )
    result = volume_profile(frame)
    assert result.is_unavailable
    assert "range" in result.reason


def test_zero_volume_has_no_profile():
    frame = frame_from_rows(
        [{"open": 9.0, "high": 11.0, "low": 8.0, "close": 10.0, "volume": 0.0}] * 5
    )
    assert volume_profile(frame).is_unavailable


def test_an_invalid_share_is_an_error_not_an_unavailable():
    assert value_area(concentrated_frame(), share=1.5).is_error
    assert value_area(concentrated_frame(), share=0.0).is_error
