import pytest

from desk.result import MetricResult, failed, ok, unavailable


def test_ok_carries_value_and_is_not_stale_by_default():
    r = ok(42.0)
    assert r.is_ok
    assert not r.is_unavailable
    assert not r.is_error
    assert r.value == 42.0
    assert r.stale is False


def test_ok_can_be_marked_stale_and_keeps_its_value():
    r = ok(42.0, stale=True)
    assert r.is_ok
    assert r.stale is True
    assert r.value == 42.0


def test_unavailable_carries_a_reason_and_has_no_value():
    r = unavailable("adapter has no aggressor-tagged ticks")
    assert r.is_unavailable
    assert not r.is_ok
    assert r.reason == "adapter has no aggressor-tagged ticks"
    with pytest.raises(ValueError, match="has no value"):
        _ = r.value


def test_unavailable_requires_a_nonempty_reason():
    with pytest.raises(ValueError, match="reason"):
        unavailable("")


def test_failed_carries_the_error_and_has_no_value():
    r = failed(ZeroDivisionError("division by zero"))
    assert r.is_error
    assert not r.is_ok
    assert isinstance(r.error, ZeroDivisionError)
    with pytest.raises(ValueError, match="has no value"):
        _ = r.value


def test_result_is_frozen():
    r = ok(1.0)
    with pytest.raises(Exception):
        r.stale = True  # type: ignore[misc]


def test_nan_is_rejected_as_a_value():
    # A NaN standing in for "not supported here" is the exact failure this
    # type exists to prevent.
    import math

    with pytest.raises(ValueError, match="NaN"):
        ok(math.nan)
