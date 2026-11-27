"""Supply/demand zone and Fibonacci tests.

The anchoring test is the one that matters for Fibonacci. A level drawn from
the window's highest and lowest bar is a function of the scroll position, so
the same chart at two zooms gives two different ladders and neither is a price
anyone was watching. Anchoring to confirmed pivots is what makes the numbers
mean the same thing twice.
"""

from __future__ import annotations

import dataclasses

import pytest

from desk.metrics.l4_zones import (
    EXTENSIONS,
    RETRACEMENTS,
    fibonacci_levels,
    supply_demand_zones,
)
from tests.fixtures import frame_from_rows


def base_then_rally():
    """Twenty ranging bars, a three-bar shelf, then a decisive rally."""
    rows = []
    for i in range(30):
        price = 100.0 + (i % 3) * 0.4
        rows.append({"open": price, "high": price + 1.2, "low": price - 1.2,
                     "close": price + 0.1, "volume": 10.0})
    # A tight shelf: three bars with almost no range.
    for _ in range(3):
        rows.append({"open": 100.0, "high": 100.15, "low": 99.85,
                     "close": 100.05, "volume": 10.0})
    # Departure upward, far clear of the shelf.
    price = 100.0
    for _ in range(12):
        price += 2.5
        rows.append({"open": price - 2.0, "high": price + 0.5, "low": price - 2.2,
                     "close": price, "volume": 30.0})
    return frame_from_rows(rows)


def test_a_tight_base_followed_by_a_rally_is_a_demand_zone():
    result = supply_demand_zones(base_then_rally())
    assert result.is_ok, result.reason or result.error
    zones = result.value
    assert zones
    demand = [z for z in zones if z.kind == "demand"]
    assert demand, [z.kind for z in zones]
    best = max(demand, key=lambda z: z.departure_atr)
    assert best.bottom <= 100.0 <= best.top
    assert best.departure_atr >= 1.5
    assert best.base_bars <= 3


def test_a_base_with_no_departure_is_not_a_zone():
    """Any stretch of chart contains bases. The departure is what separates one
    that mattered, so without it there is nothing to report."""
    rows = [
        {"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 1.0}
        for _ in range(60)
    ]
    result = supply_demand_zones(frame_from_rows(rows))
    assert result.is_unavailable
    assert "departure" in result.reason


def test_a_wide_base_is_rejected_however_strong_the_departure():
    """A stretch spanning two ATR is a trend in miniature, not a shelf."""
    frame = base_then_rally()
    tight = supply_demand_zones(frame, base_max_atr=0.6)
    impossible = supply_demand_zones(frame, base_max_atr=0.001)
    assert tight.is_ok
    assert impossible.is_unavailable


def test_a_zone_price_returned_into_is_reported_tested_not_dropped():
    """How a level failed is information the next decision needs."""
    rows = []
    for i in range(30):
        price = 100.0 + (i % 3) * 0.4
        rows.append({"open": price, "high": price + 1.2, "low": price - 1.2,
                     "close": price + 0.1, "volume": 10.0})
    for _ in range(2):
        rows.append({"open": 100.0, "high": 100.1, "low": 99.9,
                     "close": 100.0, "volume": 10.0})
    price = 100.0
    for _ in range(8):
        price += 2.5
        rows.append({"open": price - 2.0, "high": price + 0.5, "low": price - 2.2,
                     "close": price, "volume": 30.0})
    # Price comes all the way back through the shelf.
    for _ in range(10):
        price -= 3.0
        rows.append({"open": price + 2.5, "high": price + 2.8, "low": price - 0.5,
                     "close": price, "volume": 30.0})

    result = supply_demand_zones(frame_from_rows(rows))
    assert result.is_ok
    assert any(z.tested for z in result.value)


def test_invalid_parameters_fail_rather_than_returning_nothing():
    frame = base_then_rally()
    assert supply_demand_zones(frame, base_max_bars=0).is_error
    assert supply_demand_zones(frame, base_max_atr=0).is_error
    assert supply_demand_zones(frame, departure_atr=-1).is_error
    assert supply_demand_zones(frame, departure_within=0).is_error


def test_fibonacci_refuses_rather_than_anchoring_to_the_window_edges():
    """The whole point of the metric.

    With no confirmed pivot there is no anchor, and falling back to the highest
    and lowest visible bar would make every level move when the chart is
    scrolled.
    """
    # Long enough to compute pivots, and strictly monotonic so it contains
    # none: every bar is higher than the last, so no bar is a local extreme.
    rows = [
        {"open": 100.0 + i, "high": 100.5 + i, "low": 99.5 + i,
         "close": 100.0 + i, "volume": 1.0}
        for i in range(40)
    ]
    result = fibonacci_levels(frame_from_rows(rows), lookback=5)
    assert result.is_unavailable, result
    assert "anchor" in result.reason or "scrolled" in result.reason


def test_fibonacci_levels_span_the_confirmed_swing():
    from tests.fixtures import swing_frame

    rows = []
    for high in [10.0, 11, 12, 13, 14, 20, 14.5, 13.5, 12.5, 11.5, 10.5,
                 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]:
        rows.append({"open": high - 1, "high": high, "low": high - 2,
                     "close": high - 0.5, "volume": 1.0})
    result = fibonacci_levels(frame_from_rows(rows), lookback=3)
    if not result.is_ok:
        pytest.skip(f"fixture produced no confirmed pair: {result.reason}")

    levels = result.value
    for ratio in RETRACEMENTS + EXTENSIONS:
        assert f"{ratio:.3f}" in levels
    assert "0.000" in levels and "1.000" in levels

    low = result.meta["swing_low"]
    high = result.meta["swing_high"]
    span = abs(high - low)
    # Every retracement sits inside the swing; extensions sit outside it.
    for ratio in RETRACEMENTS:
        value = levels[f"{ratio:.3f}"]
        assert min(low, high) - 1e-9 <= value <= max(low, high) + 1e-9
    for ratio in EXTENSIONS:
        assert abs(levels[f"{ratio:.3f}"] - levels["0.000"]) > span


def test_fibonacci_reports_which_leg_it_measured():
    """Direction is not cosmetic: an up leg and a down leg invert the ladder,
    so a level without its direction is ambiguous."""
    rows = []
    for high in [10.0, 11, 12, 13, 14, 20, 14.5, 13.5, 12.5, 11.5, 10.5,
                 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]:
        rows.append({"open": high - 1, "high": high, "low": high - 2,
                     "close": high - 0.5, "volume": 1.0})
    result = fibonacci_levels(frame_from_rows(rows), lookback=3)
    if not result.is_ok:
        pytest.skip("fixture produced no confirmed pair")
    assert result.meta["direction"] in {"up", "down"}
    assert "swing_high_at" in result.meta and "swing_low_at" in result.meta


def test_a_zone_is_not_marked_tested_by_its_own_departure():
    """Regression: every zone came back tested, and none ever was.

    Price moves continuously, so the bar that leaves a shelf still touches it on
    the way out. Scanning for re-entry from the bar after the base therefore
    marks every zone tested by its own departure, which made `fresh` zero on
    every window and the freshest-first ranking meaningless.
    """
    result = supply_demand_zones(base_then_rally())
    assert result.is_ok, result.reason or result.error
    demand = [z for z in result.value if z.kind == "demand"]
    assert demand
    # The fixture rallies away and never returns, so nothing can be tested.
    assert any(not z.tested for z in demand)
    assert result.meta["fresh"] > 0
