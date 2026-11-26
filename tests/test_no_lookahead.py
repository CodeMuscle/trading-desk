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

    if spec.returns in {"scalars", "profile"}:
        pytest.skip(
            f"{name} describes the whole frame, not each bar. Truncating gives a "
            "different frame and therefore a legitimately different answer, so "
            "the no-lookahead property has nothing to say about it."
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
    }
