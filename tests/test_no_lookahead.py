"""No metric may depend on a bar that has not happened yet.

The check: compute the metric over the full frame, then recompute it over a
truncated prefix of that frame. Values at the bars both runs share must be
identical. If a metric reads future bars, truncating changes its past, and this
test fails.

swing_pivots is the one deliberate exception and it is marked as such: a pivot
is only confirmable once `lookback` bars have printed after it, which is
lookahead by construction. The exception is recorded here rather than silently
tolerated, and the test still enforces that the metric is centred as documented.
"""

from __future__ import annotations

import pandas as pd
import pytest

from desk.metrics.registry import REGISTRY
from desk.store.frame import OHLCVFrame
from tests.fixtures import frame_from_rows

CONFIRMS_LATE = {"swing_pivots"}


def realistic_frame(bars: int = 200):
    rows = []
    price = 100.0
    for i in range(bars):
        drift = 0.6 if i < 120 else -0.4
        o = price
        c = o + drift + ((i * 37) % 11 - 5) * 0.25
        rows.append(
            {
                "open": o,
                "high": max(o, c) + 1.0 + (i % 5) * 0.2,
                "low": min(o, c) - 1.0 - (i % 7) * 0.15,
                "close": c,
                "volume": 100.0 + (i % 13) * 10,
            }
        )
        price = c
    return frame_from_rows(rows)


def series_of(result, kind: str) -> list[pd.Series]:
    if kind == "dict_of_series":
        return list(result.value.values())
    return [result.value]


@pytest.mark.parametrize("name", sorted(REGISTRY))
@pytest.mark.parametrize("cut", [140, 170, 190])
def test_metric_output_on_shared_bars_is_unchanged_by_truncation(name, cut):
    spec = REGISTRY[name]
    full = realistic_frame(200)
    short = OHLCVFrame(
        data=full.data.iloc[:cut],
        venue=full.venue,
        symbol=full.symbol,
        timeframe=full.timeframe,
        as_of=full.as_of,
        capabilities=full.capabilities,
    )

    if spec.returns in {"scalars", "profile", "zones"}:
        pytest.skip(
            f"{name} describes the whole frame, not each bar. Truncating gives a "
            "different frame and therefore a legitimately different answer, so "
            "this particular comparison has nothing to say about it. Zones get "
            "their own prefix test below, because the property does apply to "
            "them, just not bar by bar."
        )

    full_result = spec.fn(full, **spec.defaults)
    short_result = spec.fn(short, **spec.defaults)

    if not (full_result.is_ok and short_result.is_ok):
        pytest.skip(f"{name} is not computable on this fixture")

    # A metric that only confirms after N future bars cannot be compared on the
    # final N bars of the truncated run. Trim that tail before comparing.
    trim = spec.defaults.get("lookback", 0) if name in CONFIRMS_LATE else 0
    end = cut - trim

    for a, b in zip(series_of(full_result, spec.returns), series_of(short_result, spec.returns)):
        pd.testing.assert_series_equal(
            a.iloc[:end], b.iloc[:end], check_names=False, check_freq=False,
            obj=f"{name}: truncating at {cut} changed its output before bar {end}",
        )


def test_the_lookahead_exception_list_is_not_silently_growing():
    # If a future milestone adds a metric that needs this exception, it must be
    # a deliberate edit to this test, reviewed, not an accident.
    assert CONFIRMS_LATE == {"swing_pivots"}


def test_every_registered_metric_is_covered_by_this_test():
    assert set(REGISTRY) == {
        "ema", "trend_state", "session_vwap", "bullish_engulfing",
        "bearish_engulfing", "hammer", "shooting_star", "swing_pivots",
        "confirmed_swing_levels", "break_of_structure", "imbalance",
        "imbalance_bands", "volume_profile", "value_area",
        "rsi", "macd", "true_range", "atr",
        "supply_demand_zones", "fibonacci_levels", "change_of_character",
    }


def test_a_settled_zone_is_not_rescored_by_later_bars():
    """The property that bounding the departure window buys.

    Measuring a departure over every bar after the base makes a zone's score a
    function of the whole future: the same base scores differently today than it
    did last week, and a strategy reading it at bar i would be reading bars it
    could not have seen. With `departure_within`, a zone is settled at a fixed
    lag after its base and never moves again, which is the same confirmation
    rule a swing pivot follows.

    `tested` and `age_bars` are excluded on purpose. Both are as-of-now
    properties: whether price has since returned, and how long ago this was, are
    exactly the questions that should change as bars arrive.
    """
    from desk.metrics.l4_zones import supply_demand_zones

    full = realistic_frame(200)
    settled_by = 170
    window = 10

    short = OHLCVFrame(
        data=full.data.iloc[:settled_by],
        venue=full.venue, symbol=full.symbol, timeframe=full.timeframe,
        as_of=full.as_of, capabilities=full.capabilities,
    )

    long_result = supply_demand_zones(full, departure_within=window)
    short_result = supply_demand_zones(short, departure_within=window)
    if not (long_result.is_ok and short_result.is_ok):
        pytest.skip("no zones on this fixture")

    cutoff = full.data.index[settled_by - window - 1]

    def settled(result):
        return {
            z.end_index: (z.kind, z.top, z.bottom, z.departure_atr, z.base_bars)
            for z in result.value
            if z.end_index <= cutoff
        }

    in_short = settled(short_result)
    in_full = settled(long_result)
    shared = set(in_short) & set(in_full)
    assert shared, "fixture produced no zone settled before the cut"
    for key in shared:
        assert in_short[key] == in_full[key], (
            f"zone ending {key} was rescored by bars after it settled: "
            f"{in_short[key]} became {in_full[key]}"
        )
