"""The paper driver: feeds live bars to the shared simulator and persists between.

Its one job is to be the same arithmetic as the backtest with a different bar
source, so that forward paper returns are comparable to backtest returns. That
comparability is the point: a backtest Sharpe is deflated by every parameter set
ever tried, while forward returns carry no selection at all, so their trial count
is one. The equivalence is asserted by a test that replays a frame through this
driver and demands the trades match `run_backtest` exactly.

Two differences from the backtest driver, both deliberate.

`is_last` is never set. The backtest closes an open position at the final bar so
its result has no dangling position; forward trading has no last bar, and a
position still open is simply still open.

Processing lag is recorded rather than modelled. A paper fill is simulated, so
there is no realised price to compare a modelled one against, and inventing a
latency parameter would be inventing a number. What is genuinely measurable is
the wall-clock gap between a bar closing and this driver processing it: if the
poller lagged four minutes, the journal shows the fill was taken at a price that
was already stale, and that is a fact about the run rather than an assumption
about the venue.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from desk.backtest.costs import CostModel
from desk.metrics.l1_momentum import atr as atr_metric
from desk.paper.account import AccountRecord, AccountStore
from desk.paper.simulator import Bar, Fill, Simulator
from desk.result import MetricResult, failed, ok
from desk.risk.limits import DEFAULT_LIMITS, RiskLimits
from desk.store.frame import OHLCVFrame
from desk.strategy.base import Intent


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    bars_processed: int
    bars_skipped: int
    """Bars at or before `last_bar`, already applied on an earlier call. A live
    poller re-fetches overlapping windows by design, so this is the normal case
    and not a warning."""
    fills: tuple[Fill, ...]
    refusals: tuple[tuple[pd.Timestamp, str], ...]
    equity: float
    position_open: bool


class PaperEngine:
    """Drives one account forward over new bars.

    Holds no simulation state of its own. The account row is the state, and it is
    read at the start of `advance` and written at the end, so a crash mid-advance
    loses the advance rather than corrupting the account.
    """

    def __init__(
        self,
        store: AccountStore,
        account: str,
        *,
        costs: CostModel,
        limits: RiskLimits = DEFAULT_LIMITS,
        cluster: str | None = None,
    ) -> None:
        self.store = store
        self.account = account
        self.costs = costs
        self.limits = limits
        self.cluster = cluster

    def advance(
        self,
        frame: OHLCVFrame,
        intents: list[Intent],
        *,
        now: datetime | None = None,
    ) -> MetricResult:
        """Apply every bar in `frame` newer than the account's last processed bar.

        Bars at or before `last_bar` are skipped rather than replayed. Replaying
        one charges its commission twice and resolves its stop twice, and a live
        poller overlaps windows on every call, so idempotency here is a
        correctness requirement rather than an optimisation.
        """
        record = self.store.load(self.account)
        if record is None:
            return failed(ValueError(f"account {self.account!r} does not exist"))

        atr_result = atr_metric(frame)
        if not atr_result.is_ok:
            return atr_result
        atr_values = atr_result.value.to_numpy(dtype="float64")

        sim = Simulator(
            equity=record.equity,
            costs=self.costs,
            limits=self.limits,
            cluster=self.cluster,
            starting_equity=record.starting_equity,
            day_start=record.day_start,
            month_start=record.month_start,
            current_day=record.current_day,
            current_month=record.current_month,
            position=record.position,
        )

        index = frame.data.index
        open_ = frame.data["open"].to_numpy()
        high = frame.data["high"].to_numpy()
        low = frame.data["low"].to_numpy()
        close = frame.data["close"].to_numpy()
        by_bar = {i.index: i for i in intents}

        wall_clock = now or datetime.now(timezone.utc)
        fills: list[Fill] = []
        refusals: list[tuple[pd.Timestamp, str]] = []
        skipped = 0
        processed = 0
        last_bar = record.last_bar

        for i, timestamp in enumerate(index):
            if last_bar is not None and timestamp <= last_bar:
                skipped += 1
                continue

            bar = Bar(
                timestamp=timestamp,
                open=float(open_[i]),
                high=float(high[i]),
                low=float(low[i]),
                close=float(close[i]),
                atr=float(atr_values[i]),
            )
            # Never is_last: forward trading has no final bar, and closing a
            # position because the fetched window ended would be an exit the
            # market never offered.
            outcome = sim.on_bar(bar, by_bar.get(timestamp), is_last=False)
            processed += 1
            last_bar = timestamp

            if outcome.fill is not None:
                fills.append(outcome.fill)
                lag = (wall_clock - timestamp.to_pydatetime()).total_seconds()
                self.store.record_fill(
                    self.account, outcome.fill,
                    symbol=frame.symbol, timeframe=frame.timeframe,
                    equity_after=sim.equity,
                    lag_seconds=lag if np.isfinite(lag) else None,
                )
            if outcome.refusal is not None:
                refusals.append((timestamp, outcome.refusal))
                self.store.record_refusal(
                    self.account, symbol=frame.symbol,
                    bar_index=timestamp, reason=outcome.refusal,
                )

        self.store.save(
            replace(
                record,
                equity=sim.equity,
                day_start=sim.day_start,
                month_start=sim.month_start,
                current_day=sim.current_day,
                current_month=sim.current_month,
                last_bar=last_bar,
                position=sim.position,
            )
        )

        return ok(
            AdvanceResult(
                bars_processed=processed,
                bars_skipped=skipped,
                fills=tuple(fills),
                refusals=tuple(refusals),
                equity=sim.equity,
                position_open=sim.position is not None,
            )
        )

    def state(self) -> AccountRecord | None:
        return self.store.load(self.account)
