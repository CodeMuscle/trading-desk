"""What a strategy template is.

One object drives three surfaces: the chart overlay that draws its markers, the
backtest that scores it, and the paper engine that trades it. They read the same
definition, so a signal cannot mean one thing on screen and another in a
backtest.

A template produces *intents*, never orders. It says "the conditions for a long
are met here, with this stop and this first target". Whether that becomes a
position, and at what size, is the risk engine's decision in M3. A template has
no access to account equity and cannot size anything, which is the same
separation that keeps an LLM away from the sizing decision later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """One tunable parameter, with the range a sweep is allowed to explore.

    The bounds exist so the M8 optimizer cannot wander into values the author
    considered nonsense, and so a UI can render a sensible control without
    being told separately.
    """

    name: str
    default: Any
    low: float | None = None
    high: float | None = None
    step: float | None = None
    help: str = ""

    def validate(self, value: Any) -> None:
        if self.low is not None and value < self.low:
            raise ValueError(f"{self.name}={value} is below its minimum {self.low}")
        if self.high is not None and value > self.high:
            raise ValueError(f"{self.name}={value} is above its maximum {self.high}")


@dataclass(frozen=True, slots=True)
class Intent:
    """A single entry intent, at the bar where the conditions completed."""

    index: pd.Timestamp
    side: Side
    entry: float
    stop: float
    target: float
    reason: dict[str, Any] = field(default_factory=dict)

    @property
    def risk(self) -> float:
        """Distance from entry to stop, in price. The R in R-multiple."""
        return abs(self.entry - self.stop)


@dataclass(frozen=True, slots=True)
class StrategyResult:
    """Everything a template produces for one frame."""

    intents: list[Intent]
    gates: pd.DataFrame
    """Per-bar truth of every condition, so 'why no signal here?' is answerable
    by reading a row rather than by re-deriving the logic."""

    def gate_at(self, index: pd.Timestamp) -> dict[str, Any]:
        return self.gates.loc[index].to_dict()

    def failing_gates_at(self, index: pd.Timestamp, side: Side) -> list[str]:
        """Which conditions blocked a signal at this bar.

        This is what the chatbot's 'why no signal here?' pill calls.
        """
        suffix = "_up" if side is Side.LONG else "_down"
        row = self.gates.loc[index]
        return [
            name
            for name, value in row.items()
            if str(name).endswith(suffix) and not bool(value)
        ]
