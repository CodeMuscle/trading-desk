"""A bar-by-bar backtest. Reads no bar it could not have read at the time.

The walk is deliberately boring: for each bar, resolve anything open using only
that bar, then consider opening something new. A position opened at bar i can
only be closed at bar i+1 or later, which is the Pine defect this project
corrected and which a vectorised shortcut would quietly reintroduce.

Where a bar touches both the stop and the target, the stop is assumed to have
filled first. Without tick data the order is genuinely unknown, and the
pessimistic reading is the only one that cannot flatter the result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from desk.backtest.costs import CostModel
from desk.metrics.l1_momentum import atr as atr_metric
from desk.paper.simulator import Bar, Fill, Simulator
from desk.result import MetricResult, failed, ok
from desk.risk.limits import DEFAULT_LIMITS, RiskLimits
from desk.store.frame import OHLCVFrame
from desk.strategy.base import Intent, Side


@dataclass(frozen=True, slots=True)
class Trade:
    entry_index: pd.Timestamp
    exit_index: pd.Timestamp
    side: Side
    units: float
    entry: float
    exit: float
    stop: float
    target: float
    outcome: str
    """stop, target, or end_of_data."""
    gross: float
    costs: float
    mae: float
    """Maximum adverse excursion, in R. How far it went against you before
    resolving. A strategy whose winners routinely show deep MAE has a stop
    problem that an equity curve hides."""
    mfe: float
    """Maximum favourable excursion, in R. Losers with high MFE are an exit
    problem."""

    @classmethod
    def from_fill(cls, fill: Fill) -> Trade:
        """Lift a simulator fill into the reporting type.

        Field-for-field, so a field added to one and forgotten in the other is a
        TypeError at the first trade rather than a column of zeroes in a report.
        """
        return cls(
            entry_index=fill.entry_index, exit_index=fill.exit_index, side=fill.side,
            units=fill.units, entry=fill.entry, exit=fill.exit, stop=fill.stop,
            target=fill.target, outcome=fill.outcome, gross=fill.gross,
            costs=fill.costs, mae=fill.mae, mfe=fill.mfe,
        )

    @property
    def net(self) -> float:
        return self.gross - self.costs

    @property
    def risk_currency(self) -> float:
        return abs(self.entry - self.stop) * self.units

    @property
    def r_multiple(self) -> float:
        if self.risk_currency == 0:
            return 0.0
        return self.net / self.risk_currency


@dataclass(frozen=True, slots=True)
class BacktestResult:
    trades: tuple[Trade, ...]
    equity: pd.Series
    refusals: tuple[tuple[pd.Timestamp, str], ...]
    """Intents the risk engine declined, with the reason. A strategy whose
    signals were mostly refused did not perform well; it barely traded, and
    those are different facts."""
    starting_equity: float

    @property
    def returns(self) -> np.ndarray:
        """Per-trade net return on equity, for the validation harness."""
        if not self.trades:
            return np.array([], dtype="float64")
        return np.array([t.net for t in self.trades]) / self.starting_equity

    @property
    def net_profit(self) -> float:
        return float(sum(t.net for t in self.trades))

    @property
    def total_costs(self) -> float:
        return float(sum(t.costs for t in self.trades))

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return float("nan")
        return sum(1 for t in self.trades if t.net > 0) / len(self.trades)

    @property
    def expectancy_r(self) -> float:
        if not self.trades:
            return float("nan")
        return float(np.mean([t.r_multiple for t in self.trades]))

    @property
    def max_drawdown(self) -> float:
        if self.equity.empty:
            return float("nan")
        peak = self.equity.cummax()
        return float((1.0 - self.equity / peak).max())


def run_backtest(
    frame: OHLCVFrame,
    intents: list[Intent],
    *,
    costs: CostModel,
    starting_equity: float = 100_000.0,
    limits: RiskLimits = DEFAULT_LIMITS,
    cluster: str | None = None,
) -> MetricResult:
    """Walk the frame, trading the given intents under the risk limits.

    This is a driver, not an engine. The per-bar logic lives in
    `desk.paper.simulator`, which the paper engine also drives, so a backtest
    result and a forward paper result are produced by the same arithmetic rather
    than by two implementations that agree until one of them is edited.
    """
    if starting_equity <= 0:
        return failed(ValueError("starting_equity must be positive"))

    atr_result = atr_metric(frame)
    if not atr_result.is_ok:
        return atr_result
    atr_values = atr_result.value.to_numpy(dtype="float64")

    index = frame.data.index
    open_ = frame.data["open"].to_numpy()
    high = frame.data["high"].to_numpy()
    low = frame.data["low"].to_numpy()
    close = frame.data["close"].to_numpy()
    by_bar: dict[pd.Timestamp, Intent] = {i.index: i for i in intents}

    sim = Simulator(
        equity=starting_equity, costs=costs, limits=limits, cluster=cluster
    )

    trades: list[Trade] = []
    refusals: list[tuple[pd.Timestamp, str]] = []
    equity_points: list[float] = []
    last = len(index) - 1

    for i, timestamp in enumerate(index):
        bar = Bar(
            timestamp=timestamp,
            open=float(open_[i]),
            high=float(high[i]),
            low=float(low[i]),
            close=float(close[i]),
            atr=float(atr_values[i]),
        )
        outcome = sim.on_bar(bar, by_bar.get(timestamp), is_last=i == last)
        if outcome.fill is not None:
            trades.append(Trade.from_fill(outcome.fill))
        if outcome.refusal is not None:
            refusals.append((timestamp, outcome.refusal))
        equity_points.append(sim.equity)

    return ok(
        BacktestResult(
            trades=tuple(trades),
            equity=pd.Series(equity_points, index=index),
            refusals=tuple(refusals),
            starting_equity=starting_equity,
        )
    )
