"""The single place a metric's name, function and required capabilities meet.

Having one registry is what makes the degradation contract and the no-lookahead
property test enforceable over every metric rather than over a hand-maintained
list that drifts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from desk.capabilities import Capability
from desk.metrics.l1_trend import ema, session_vwap, trend_state
from desk.metrics.l1_momentum import atr, macd, rsi, true_range
from desk.metrics.l2_profile import value_area, volume_profile
from desk.metrics.l4_zones import fibonacci_levels, supply_demand_zones
from desk.metrics.l4_structure import (
    break_of_structure,
    change_of_character,
    confirmed_swing_levels,
    imbalance,
    imbalance_bands,
)
from desk.metrics.l4_price_action import (
    bearish_engulfing,
    bullish_engulfing,
    hammer,
    shooting_star,
    swing_pivots,
)

OHLCV = frozenset({Capability.OHLCV})


@dataclass(frozen=True, slots=True)
class MetricSpec:
    fn: Callable[..., Any]
    requires: frozenset[Capability]
    returns: str
    defaults: dict[str, Any] = field(default_factory=dict)
    sparse: bool = False
    """True when NaN in this metric's output is information rather than a gap.

    Two legitimate cases, both declared here rather than skipped in a test:

    - no such feature at this bar (a bar that is not a pivot, a bar with no
      imbalance band)
    - no value defined yet (the warmup window of a Wilder-smoothed average,
      where the seed needs `length` observations before it exists)

    A pivot series is NaN on every bar that is not a pivot; an imbalance band is
    NaN on every bar with no imbalance. Both are statements about the series,
    not gaps in it. Everything else must never emit NaN and must say
    unavailable(reason) instead, so this flag has to be set deliberately per
    metric rather than skipped per metric in a test."""


REGISTRY: dict[str, MetricSpec] = {
    "ema": MetricSpec(ema, OHLCV, "series", {"length": 20}),
    "trend_state": MetricSpec(trend_state, OHLCV, "series", {"fast": 20, "slow": 50}),
    "session_vwap": MetricSpec(session_vwap, OHLCV, "series", {}),
    "bullish_engulfing": MetricSpec(bullish_engulfing, OHLCV, "series_bool", {}),
    "bearish_engulfing": MetricSpec(bearish_engulfing, OHLCV, "series_bool", {}),
    "hammer": MetricSpec(hammer, OHLCV, "series_bool", {}),
    "shooting_star": MetricSpec(shooting_star, OHLCV, "series_bool", {}),
    "supply_demand_zones": MetricSpec(
        supply_demand_zones, OHLCV, "zones",
        {"base_max_bars": 3, "base_max_atr": 0.6, "departure_atr": 1.5},
    ),
    "fibonacci_levels": MetricSpec(
        fibonacci_levels, OHLCV, "scalars", {"lookback": 10},
    ),
    "swing_pivots": MetricSpec(
        swing_pivots, OHLCV, "dict_of_series", {"lookback": 10}, sparse=True
    ),
    "confirmed_swing_levels": MetricSpec(
        confirmed_swing_levels, OHLCV, "dict_of_series", {"lookback": 10}, sparse=True
    ),
    "change_of_character": MetricSpec(
        change_of_character, OHLCV, "series", {"lookback": 10}
    ),
    "break_of_structure": MetricSpec(
        break_of_structure, OHLCV, "series", {"lookback": 10}
    ),
    "imbalance": MetricSpec(imbalance, OHLCV, "series", {}),
    "imbalance_bands": MetricSpec(
        imbalance_bands, OHLCV, "dict_of_series", {}, sparse=True
    ),
    # "scalars" describes the whole frame rather than each bar: a profile has
    # no per-bar value, so the no-lookahead property does not apply to it.
    "rsi": MetricSpec(rsi, OHLCV, "series", {"length": 14}, sparse=True),
    "macd": MetricSpec(
        macd, OHLCV, "dict_of_series", {"fast": 12, "slow": 26, "signal": 9}
    ),
    "true_range": MetricSpec(true_range, OHLCV, "series", {}),
    "atr": MetricSpec(atr, OHLCV, "series", {"length": 14}, sparse=True),
    "volume_profile": MetricSpec(volume_profile, OHLCV, "profile", {"buckets": 48}),
    "value_area": MetricSpec(
        value_area, OHLCV, "scalars", {"buckets": 48, "share": 0.70}
    ),
}
