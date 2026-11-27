"""The one bar-stepping simulator. Both the backtest and the paper engine use it.

There is deliberately no second implementation. A paper engine written separately
from the backtest drifts from it, and the moment it does, paper results stop
being comparable to backtest results. That comparability is the entire reason the
paper engine is worth building: a backtest's Sharpe is deflated by every
parameter set ever tried, while forward paper returns have no selection applied
to them at all, so their trial count is one. Two engines that disagree by a tick
of slippage turn that argument into a guess.

So the loop body lives here, holding its own mutable state, and the two callers
differ only in where bars come from. The backtest feeds it a DataFrame it already
has; the paper engine feeds it one bar at a time as the market prints them, and
persists the state in between.

The resolve-then-consider order is load-bearing and is the Pine defect this
project corrected: a position opened at bar i cannot be closed before bar i+1,
because at the moment of entry the bar's remaining path is not yet known.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date

import pandas as pd

from desk.backtest.costs import CostModel
from desk.risk.engine import AccountState, size_for
from desk.risk.limits import DEFAULT_LIMITS, RiskLimits
from desk.strategy.base import Intent, Side


@dataclass(frozen=True, slots=True)
class OpenPosition:
    """A live position. Frozen, so advancing it returns a new one.

    `mae` and `mfe` accumulate across every bar the position is alive, which is
    why they live here rather than being derived at exit: the excursion path is
    gone once the trade closes.
    """

    entry_index: pd.Timestamp
    side: Side
    units: float
    entry: float
    stop: float
    target: float
    entry_commission: float
    mae: float = 0.0
    mfe: float = 0.0

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)


@dataclass(frozen=True, slots=True)
class Fill:
    """A closed trade, emitted by the simulator rather than assembled by callers."""

    entry_index: pd.Timestamp
    exit_index: pd.Timestamp
    side: Side
    units: float
    entry: float
    exit: float
    stop: float
    target: float
    outcome: str
    gross: float
    costs: float
    mae: float
    mfe: float


@dataclass(frozen=True, slots=True)
class Bar:
    timestamp: pd.Timestamp
    high: float
    low: float
    close: float
    atr: float
    """ATR at this bar, for slippage. NaN means the indicator is not seeded yet,
    and a trade that cannot be costed is not taken."""


@dataclass(frozen=True, slots=True)
class BarOutcome:
    """What one bar produced. Empty on most bars."""

    fill: Fill | None = None
    refusal: str | None = None
    opened: bool = False


@dataclass
class Simulator:
    """Mutable simulation state, advanced one bar at a time.

    Deliberately not frozen. This is the one mutable object in the engine
    because it models a thing that genuinely changes: an account between two
    bars. The values it holds and emits are all frozen.
    """

    equity: float
    costs: CostModel
    limits: RiskLimits = DEFAULT_LIMITS
    cluster: str | None = None
    starting_equity: float = field(default=0.0)
    day_start: float = field(default=0.0)
    month_start: float = field(default=0.0)
    current_day: date | None = None
    current_month: tuple[int, int] | None = None
    position: OpenPosition | None = None

    def __post_init__(self) -> None:
        if self.equity <= 0:
            raise ValueError("equity must be positive")
        if not self.starting_equity:
            self.starting_equity = self.equity
        if not self.day_start:
            self.day_start = self.equity
        if not self.month_start:
            self.month_start = self.equity

    def on_bar(self, bar: Bar, intent: Intent | None, *, is_last: bool) -> BarOutcome:
        """Resolve any open position on this bar, then consider a new intent.

        `is_last` closes an open position at the bar's close. The backtest sets
        it on the final bar of the frame so the result has no dangling position;
        the paper engine never sets it, because in forward trading there is no
        last bar and a position left open is simply still open.
        """
        self._roll_periods(bar.timestamp)

        fill: Fill | None = None
        if self.position is not None:
            fill = self._resolve(bar, is_last=is_last)

        refusal: str | None = None
        opened = False
        if self.position is None and intent is not None:
            refusal, opened = self._consider(bar, intent)

        return BarOutcome(fill=fill, refusal=refusal, opened=opened)

    def _roll_periods(self, timestamp: pd.Timestamp) -> None:
        """Reset the daily and monthly loss-limit baselines on rollover."""
        if timestamp.date() != self.current_day:
            self.current_day = timestamp.date()
            self.day_start = self.equity
        if (timestamp.year, timestamp.month) != self.current_month:
            self.current_month = (timestamp.year, timestamp.month)
            self.month_start = self.equity

    def _resolve(self, bar: Bar, *, is_last: bool) -> Fill | None:
        position = self.position
        assert position is not None
        risk = position.risk_per_unit

        if position.side is Side.LONG:
            mae = max(position.mae, (position.entry - bar.low) / risk)
            mfe = max(position.mfe, (bar.high - position.entry) / risk)
            hit_stop = bar.low <= position.stop
            hit_target = bar.high >= position.target
        else:
            mae = max(position.mae, (bar.high - position.entry) / risk)
            mfe = max(position.mfe, (position.entry - bar.low) / risk)
            hit_stop = bar.high >= position.stop
            hit_target = bar.low <= position.target

        position = replace(position, mae=mae, mfe=mfe)
        self.position = position

        if hit_stop:
            # Pessimistic when a bar touches both: without ticks the order is
            # genuinely unknown, and this is the only reading that cannot
            # flatter the result.
            exit_price, outcome = position.stop, "stop"
        elif hit_target:
            exit_price, outcome = position.target, "target"
        elif is_last:
            exit_price, outcome = bar.close, "end_of_data"
        else:
            return None

        return self._close(position, bar, exit_price, outcome)

    def _close(
        self, position: OpenPosition, bar: Bar, exit_price: float, outcome: str
    ) -> Fill:
        if not math.isfinite(bar.atr):
            # Unreachable by construction: entry refuses any bar with an unseeded
            # ATR, and ATR stays finite once seeded. Raising rather than
            # defaulting to zero slippage keeps a future change to either fact
            # from silently reintroducing free exits.
            raise AssertionError(
                f"ATR is {bar.atr} at exit bar {bar.timestamp}; cannot cost the exit"
            )
        slip = self.costs.slippage(bar.atr)
        fill_price = (
            exit_price - slip if position.side is Side.LONG else exit_price + slip
        )

        if position.side is Side.LONG:
            gross = (fill_price - position.entry) * position.units
        else:
            gross = (position.entry - fill_price) * position.units
        total = position.entry_commission + self.costs.commission(
            fill_price * position.units
        )

        self.equity += gross - total
        self.position = None
        return Fill(
            entry_index=position.entry_index,
            exit_index=bar.timestamp,
            side=position.side,
            units=position.units,
            entry=position.entry,
            exit=fill_price,
            stop=position.stop,
            target=position.target,
            outcome=outcome,
            gross=gross,
            costs=total,
            mae=position.mae,
            mfe=position.mfe,
        )

    def _consider(self, bar: Bar, intent: Intent) -> tuple[str | None, bool]:
        account = AccountState(
            equity=self.equity,
            starting_equity_day=self.day_start,
            starting_equity_month=self.month_start,
            positions=(),
        )
        decision = size_for(intent, account, cluster=self.cluster, limits=self.limits)
        if decision.refused:
            return (decision.reasons[0] if decision.reasons else "refused"), False

        if not math.isfinite(bar.atr):
            # ATR is not seeded, so slippage cannot be priced. Taking the trade
            # multiplies NaN into the fill and from there into equity, which
            # never recovers: every later intent is then refused for a
            # non-numeric account and the run reports one trade and silence.
            return (
                "ATR not seeded at this bar, so slippage cannot be priced; a "
                "trade that cannot be costed is not taken"
            ), False

        slip = self.costs.slippage(bar.atr)
        # Entry fills worse than the signal price, in the direction that hurts.
        fill_price = (
            intent.entry + slip if intent.side is Side.LONG else intent.entry - slip
        )
        self.position = OpenPosition(
            entry_index=bar.timestamp,
            side=intent.side,
            units=decision.units,
            entry=fill_price,
            stop=intent.stop,
            target=intent.target,
            entry_commission=self.costs.commission(fill_price * decision.units),
        )
        return None, True
