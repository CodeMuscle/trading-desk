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
from datetime import datetime, timedelta, timezone

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
    bars_seeded: int
    """Bars skipped on a new account to set the forward baseline, trading none.

    Non-zero only on an account's first advance. A journal that mixes replayed
    history with forward results is no longer forward: the whole reason to paper
    trade is that forward returns carry no selection, so their trial count is
    one, and a backfill of the same strategy the sweeps already chose carries all
    405 of those trials. Opt in with `backfill=True` when replay is what you
    want."""
    bars_forming: int
    """Bars whose close time has not passed yet, held back for a later call.

    Normally 1 in live mode and 0 on historical replay. A bar that has not closed
    is not yet a bar: its high and low can still move, so resolving a stop
    against it decides the trade on a version of history that will be overwritten
    within the hour."""
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
        closed_bars_only: bool = True,
        max_staleness: float | None = 2.0,
        backfill: bool = False,
    ) -> MetricResult:
        """Apply every closed bar in `frame` newer than the account's last.

        Bars at or before `last_bar` are skipped rather than replayed. Replaying
        one charges its commission twice and resolves its stop twice, and a live
        poller overlaps windows on every call, so idempotency here is a
        correctness requirement rather than an optimisation.

        `closed_bars_only` holds back the bar still forming, and it interacts with
        that idempotency in the one way that actually bites. A bar fetched
        mid-interval has a provisional high and low. Process it and `last_bar`
        advances past it, so the corrected version arriving on the next call is
        skipped as already-seen, and a stop resolved against the provisional
        extremes is locked in against a version of history that no longer exists.
        Holding the forming bar back is therefore not a refinement; without it the
        idempotency guard turns a transient wrong answer into a permanent one.

        Pass False only for historical replay, where every bar in the frame is
        final by construction.

        `max_staleness` refuses a frame whose `as_of` is older than that multiple
        of the timeframe, because a poller that silently failed and returned a
        cached frame should not look like a quiet market. None disables the check
        for replay.

        `backfill` controls what the first advance on a new account does with the
        history in the frame. The default trades none of it and sets the baseline
        at the newest closed bar, because the paper journal's value is that it
        contains forward results only. A first run that replays four hundred bars
        writes a backtest into the journal and labels it paper: a live run against
        BTC/USDT 4h did exactly that and produced seventeen trades at a 70.6% win
        rate, a number that looks like validation and is a replay of the strategy
        the sweeps had already selected across 405 trials. Pass True when replay
        is genuinely what is wanted.
        """
        record = self.store.load(self.account)
        if record is None:
            return failed(ValueError(f"account {self.account!r} does not exist"))

        wall_clock = now or datetime.now(timezone.utc)
        if max_staleness is not None and frame.is_stale(
            wall_clock, tolerance_multiple=max_staleness
        ):
            age = (wall_clock - frame.as_of).total_seconds()
            return failed(
                ValueError(
                    f"frame for {frame.symbol} {frame.timeframe} is {age:.0f}s old, "
                    f"beyond {max_staleness}x the timeframe. Refusing rather than "
                    "trading it: a fetch that failed and returned cached data is "
                    "indistinguishable from a quiet market once it is in the journal."
                )
            )

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

        fills: list[Fill] = []
        pending: list[tuple[pd.Timestamp, Fill, float]] = []
        refusals: list[tuple[pd.Timestamp, str]] = []
        skipped = 0
        processed = 0
        forming = 0
        seeded = 0
        last_bar = record.last_bar
        bar_seconds = frame.timeframe_seconds()

        def closed(timestamp: pd.Timestamp) -> bool:
            if not closed_bars_only:
                return True
            return wall_clock >= timestamp.to_pydatetime() + timedelta(
                seconds=bar_seconds
            )

        if last_bar is None and not backfill:
            # New account: set the baseline at the newest closed bar and trade
            # none of the history, so everything the journal ever holds is
            # forward. Counted and reported rather than done silently.
            closed_bars = [ts for ts in index if closed(ts)]
            seeded = len(closed_bars)
            forming = len(index) - seeded
            last_bar = closed_bars[-1] if closed_bars else None
            self.store.save(replace(record, last_bar=last_bar))
            return ok(
                AdvanceResult(
                    bars_processed=0, bars_skipped=0, bars_seeded=seeded,
                    bars_forming=forming, fills=(), refusals=(),
                    equity=record.equity, position_open=record.position is not None,
                )
            )

        for i, timestamp in enumerate(index):
            if last_bar is not None and timestamp <= last_bar:
                skipped += 1
                continue
            if not closed(timestamp):
                forming += 1
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
                # Buffered rather than written here, because whether a fill's lag
                # is meaningful is only known once the batch ends. See below.
                pending.append((timestamp, outcome.fill, sim.equity))
            if outcome.refusal is not None:
                refusals.append((timestamp, outcome.refusal))
                self.store.record_refusal(
                    self.account, symbol=frame.symbol,
                    bar_index=timestamp, reason=outcome.refusal,
                )

        # Processing lag is only a fact about the poller for the newest bar in the
        # batch, which is the one a live run is reacting to. Every earlier bar in
        # the same call is historical catch-up, and stamping it with the gap to
        # the wall clock reports a backfill of two months as a poller two months
        # late. NULL is the honest value there: the lag was not observed, and a
        # journal column that cannot distinguish "late" from "backfilled" would
        # have a reader conclude the desk is permanently broken.
        # Compared against the newest bar *processed*, not the newest bar that
        # happened to produce a fill. A backfill whose last trade closed six days
        # before the batch's final bar is still a backfill, and an earlier draft
        # of this compared against the latest fill and so reported that trade as
        # six days late.
        for timestamp, fill, equity_after in pending:
            lag: float | None = None
            if last_bar is not None and timestamp == last_bar:
                measured = (wall_clock - timestamp.to_pydatetime()).total_seconds()
                lag = measured if np.isfinite(measured) else None
            self.store.record_fill(
                self.account, fill,
                symbol=frame.symbol, timeframe=frame.timeframe,
                equity_after=equity_after, lag_seconds=lag,
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
                bars_seeded=seeded,
                bars_forming=forming,
                fills=tuple(fills),
                refusals=tuple(refusals),
                equity=sim.equity,
                position_open=sim.position is not None,
            )
        )

    def state(self) -> AccountRecord | None:
        return self.store.load(self.account)
