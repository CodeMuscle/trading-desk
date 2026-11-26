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


REGISTRY: dict[str, MetricSpec] = {
    "ema": MetricSpec(ema, OHLCV, "series", {"length": 20}),
    "trend_state": MetricSpec(trend_state, OHLCV, "series", {"fast": 20, "slow": 50}),
    "session_vwap": MetricSpec(session_vwap, OHLCV, "series", {}),
    "bullish_engulfing": MetricSpec(bullish_engulfing, OHLCV, "series_bool", {}),
    "bearish_engulfing": MetricSpec(bearish_engulfing, OHLCV, "series_bool", {}),
    "hammer": MetricSpec(hammer, OHLCV, "series_bool", {}),
    "shooting_star": MetricSpec(shooting_star, OHLCV, "series_bool", {}),
    "swing_pivots": MetricSpec(swing_pivots, OHLCV, "dict_of_series", {"lookback": 10}),
}
