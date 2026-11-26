"""The three-state return type every metric in the engine uses.

A metric returns a value, or says it cannot be computed and why, or reports an
error. It never returns NaN to mean "not supported here", because a number that
looks fine and is wrong is the failure mode this whole engine is built to avoid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class State(Enum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class MetricResult:
    state: State
    _value: Any = None
    reason: str | None = None
    error: BaseException | None = None
    stale: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ok(self) -> bool:
        return self.state is State.OK

    @property
    def is_unavailable(self) -> bool:
        return self.state is State.UNAVAILABLE

    @property
    def is_error(self) -> bool:
        return self.state is State.ERROR

    @property
    def value(self) -> Any:
        if self.state is not State.OK:
            raise ValueError(
                f"a {self.state.value} result has no value "
                f"(reason={self.reason!r}, error={self.error!r})"
            )
        return self._value


def _reject_nan(value: Any) -> None:
    if isinstance(value, float) and math.isnan(value):
        raise ValueError(
            "NaN is not a valid metric value. Use unavailable(reason) to say "
            "a metric cannot be computed here."
        )


def ok(value: Any, *, stale: bool = False, **meta: Any) -> MetricResult:
    _reject_nan(value)
    return MetricResult(state=State.OK, _value=value, stale=stale, meta=dict(meta))


def unavailable(reason: str, **meta: Any) -> MetricResult:
    if not reason or not reason.strip():
        raise ValueError("unavailable() requires a non-empty reason")
    return MetricResult(state=State.UNAVAILABLE, reason=reason, meta=dict(meta))


def failed(error: BaseException, **meta: Any) -> MetricResult:
    return MetricResult(state=State.ERROR, error=error, meta=dict(meta))
