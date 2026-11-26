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

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from desk.backtest.costs import CostModel
from desk.metrics.l1_momentum import atr as atr_metric
from desk.result import MetricResult, failed, ok
from desk.risk.engine import AccountState, Position, size_for
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
    """Walk the frame, trading the given intents under the risk limits."""
    if starting_equity <= 0:
        return failed(ValueError("starting_equity must be positive"))

    atr_result = atr_metric(frame)
    if not atr_result.is_ok:
        return atr_result
    atr_values = atr_result.value

    index = frame.data.index
    high = frame.data["high"].to_numpy()
    low = frame.data["low"].to_numpy()
    close = frame.data["close"].to_numpy()

    by_bar: dict[pd.Timestamp, Intent] = {i.index: i for i in intents}

    equity = starting_equity
    day_start = starting_equity
    month_start = starting_equity
    current_day = index[0].date() if len(index) else None
    current_month = (index[0].year, index[0].month) if len(index) else None

    trades: list[Trade] = []
    refusals: list[tuple[pd.Timestamp, str]] = []
    equity_points: list[float] = []

    open_trade: dict | None = None

    for i, timestamp in enumerate(index):
        # Period rollovers, which reset the daily and monthly limit baselines.
        if timestamp.date() != current_day:
            current_day = timestamp.date()
            day_start = equity
        if (timestamp.year, timestamp.month) != current_month:
            current_month = (timestamp.year, timestamp.month)
            month_start = equity

        if open_trade is not None:
            side = open_trade["side"]
            risk_per_unit = abs(open_trade["entry"] - open_trade["stop"])

            # Track excursion on every bar the position is alive.
            if side is Side.LONG:
                open_trade["mae"] = max(open_trade["mae"], (open_trade["entry"] - low[i]) / risk_per_unit)
                open_trade["mfe"] = max(open_trade["mfe"], (high[i] - open_trade["entry"]) / risk_per_unit)
                hit_stop = low[i] <= open_trade["stop"]
                hit_target = high[i] >= open_trade["target"]
            else:
                open_trade["mae"] = max(open_trade["mae"], (high[i] - open_trade["entry"]) / risk_per_unit)
                open_trade["mfe"] = max(open_trade["mfe"], (open_trade["entry"] - low[i]) / risk_per_unit)
                hit_stop = high[i] >= open_trade["stop"]
                hit_target = low[i] <= open_trade["target"]

            exit_price: float | None = None
            outcome = ""
            if hit_stop:
                # Pessimistic when both are touched: the order is unknown
                # without ticks, and only this reading cannot flatter.
                exit_price, outcome = open_trade["stop"], "stop"
            elif hit_target:
                exit_price, outcome = open_trade["target"], "target"
            elif i == len(index) - 1:
                exit_price, outcome = float(close[i]), "end_of_data"

            if exit_price is not None:
                equity, trade = _close(
                    open_trade, timestamp, exit_price, outcome, equity, costs, atr_values, i
                )
                trades.append(trade)
                open_trade = None

        intent = by_bar.get(timestamp)
        if open_trade is None and intent is not None:
            account = AccountState(
                equity=equity,
                starting_equity_day=day_start,
                starting_equity_month=month_start,
                positions=(),
            )
            decision = size_for(intent, account, cluster=cluster, limits=limits)
            if decision.refused:
                refusals.append((timestamp, decision.reasons[0] if decision.reasons else "refused"))
            else:
                bar_atr = float(atr_values.iloc[i])
                slip = costs.slippage(bar_atr)
                # Entry fills worse than the signal price, in the direction
                # that hurts.
                fill = intent.entry + slip if intent.side is Side.LONG else intent.entry - slip
                open_trade = {
                    "entry_index": timestamp,
                    "side": intent.side,
                    "units": decision.units,
                    "entry": fill,
                    "stop": intent.stop,
                    "target": intent.target,
                    "mae": 0.0,
                    "mfe": 0.0,
                    "entry_commission": costs.commission(fill * decision.units),
                }

        equity_points.append(equity)

    return ok(
        BacktestResult(
            trades=tuple(trades),
            equity=pd.Series(equity_points, index=index),
            refusals=tuple(refusals),
            starting_equity=starting_equity,
        )
    )


def _close(
    open_trade: dict,
    timestamp: pd.Timestamp,
    exit_price: float,
    outcome: str,
    equity: float,
    costs: CostModel,
    atr_values: pd.Series,
    i: int,
) -> tuple[float, Trade]:
    units = open_trade["units"]
    slip = costs.slippage(float(atr_values.iloc[i]))
    # Exit also fills against you.
    fill = exit_price - slip if open_trade["side"] is Side.LONG else exit_price + slip

    if open_trade["side"] is Side.LONG:
        gross = (fill - open_trade["entry"]) * units
    else:
        gross = (open_trade["entry"] - fill) * units

    total_costs = open_trade["entry_commission"] + costs.commission(fill * units)
    trade = Trade(
        entry_index=open_trade["entry_index"],
        exit_index=timestamp,
        side=open_trade["side"],
        units=units,
        entry=open_trade["entry"],
        exit=fill,
        stop=open_trade["stop"],
        target=open_trade["target"],
        outcome=outcome,
        gross=gross,
        costs=total_costs,
        mae=open_trade["mae"],
        mfe=open_trade["mfe"],
    )
    return equity + trade.net, trade
