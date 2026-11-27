"""Paper engine tests.

Two tests here carry the weight. The equivalence test is the one that makes the
paper engine worth building: if paper and backtest disagree, forward returns stop
being comparable to backtest returns and the trial-count argument becomes a
guess. The restart test is the one that makes the risk limits real: a daily loss
limit that a process restart clears is not a limit.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from desk.backtest.costs import ZERO_COSTS, CostModel
from desk.backtest.engine import run_backtest
from desk.paper.account import MAX_ACCOUNTS, AccountStore
from desk.paper.engine import PaperEngine
from desk.risk.limits import RiskLimits
from desk.strategy.base import Intent, Side
from tests.fixtures import frame_from_rows


@pytest.fixture
def store(tmp_path):
    with AccountStore(tmp_path / "desk.sqlite") as s:
        yield s


def trending_frame(bars: int = 120, *, start: str = "2026-01-01"):
    """A gentle uptrend with real intrabar range, so stops and targets both fire."""
    rows = []
    price = 100.0
    for i in range(bars):
        price += 0.6 if i % 3 else -0.4
        rows.append(
            {
                "open": price,
                "high": price + 1.4,
                "low": price - 1.1,
                "close": price + 0.2,
                "volume": 100.0 + i,
            }
        )
    return frame_from_rows(rows, start=start)


def intents_every(frame, step: int, *, stop_off: float = 2.0, target_off: float = 4.0):
    closes = frame.data["close"]
    out = []
    for position in range(20, len(closes), step):
        price = float(closes.iloc[position])
        out.append(
            Intent(
                index=closes.index[position], side=Side.LONG, entry=price,
                stop=price - stop_off, target=price + target_off,
            )
        )
    return out


def test_paper_reproduces_the_backtest_bar_for_bar(store):
    """The load-bearing test.

    Same frame, same intents, same costs: the paper driver must produce the same
    trades as the backtest driver, because they are the same simulator. Any
    divergence here means forward paper returns cannot be compared against
    backtest returns, and the whole reason for running a paper engine at all is
    that comparison.
    """
    frame = trending_frame(150)
    intents = intents_every(frame, 7)
    costs = CostModel()

    expected = run_backtest(
        frame, intents, costs=costs, starting_equity=50_000.0
    ).value

    store.create("paper", venue="fixture", starting_equity=50_000.0)
    engine = PaperEngine(store, "paper", costs=costs)
    advanced = engine.advance(frame, intents)
    assert advanced.is_ok, advanced
    got = advanced.value

    # The backtest force-closes any open position on the final bar; the paper
    # engine does not, because forward trading has no final bar. Compare only
    # the trades that resolved on their own terms.
    closed = [t for t in expected.trades if t.outcome != "end_of_data"]
    assert len(got.fills) == len(closed)
    for fill, trade in zip(got.fills, closed):
        assert fill.entry_index == trade.entry_index
        assert fill.exit_index == trade.exit_index
        assert fill.side is trade.side
        assert fill.units == pytest.approx(trade.units, rel=0, abs=1e-12)
        assert fill.entry == pytest.approx(trade.entry, rel=0, abs=1e-12)
        assert fill.exit == pytest.approx(trade.exit, rel=0, abs=1e-12)
        assert fill.outcome == trade.outcome
        assert fill.gross == pytest.approx(trade.gross, rel=0, abs=1e-12)
        assert fill.costs == pytest.approx(trade.costs, rel=0, abs=1e-12)
        assert fill.mae == pytest.approx(trade.mae, rel=0, abs=1e-12)
        assert fill.mfe == pytest.approx(trade.mfe, rel=0, abs=1e-12)


def test_advancing_one_bar_at_a_time_matches_advancing_all_at_once(store):
    """Chunking must not change the result.

    A live engine is fed whatever the poller returns, which is an arbitrary
    number of bars per call. If the chunk size changes the outcome, every
    comparison between two paper runs is confounded by polling cadence.
    """
    frame = trending_frame(120)
    intents = intents_every(frame, 5)
    costs = CostModel()

    store.create("bulk", venue="fixture", starting_equity=50_000.0)
    bulk = PaperEngine(store, "bulk", costs=costs).advance(frame, intents).value

    store.create("drip", venue="fixture", starting_equity=50_000.0)
    drip = PaperEngine(store, "drip", costs=costs)
    for cut in range(1, len(frame.data) + 1):
        sliced = frame.data.iloc[:cut]
        import dataclasses

        drip.advance(dataclasses.replace(frame, data=sliced), intents)

    assert drip.state().equity == pytest.approx(bulk.equity, rel=0, abs=1e-9)
    assert len(store.journal("drip")) == len(store.journal("bulk"))


def test_a_restart_does_not_reset_the_daily_loss_baseline(store):
    """A limit a restart clears is not a limit.

    The daily loss limit measures against the equity at the day's first bar. If
    a reload re-seeds that baseline from current equity, then restarting the
    process after a bad morning hands back the full daily allowance, and the kill
    switch becomes advisory. Same rule that keeps risk out of agent prompts.
    """
    frame = trending_frame(60)
    store.create("acct", venue="fixture", starting_equity=10_000.0)
    engine = PaperEngine(store, "acct", costs=CostModel())
    engine.advance(frame, intents_every(frame, 4))

    before = engine.state()
    assert before.current_day is not None

    # A fresh store object against the same file: a process restart.
    reopened = AccountStore(store.path)
    try:
        after = reopened.load("acct")
        assert after.day_start == pytest.approx(before.day_start)
        assert after.month_start == pytest.approx(before.month_start)
        assert after.current_day == before.current_day
        assert after.current_month == before.current_month
        assert after.equity == pytest.approx(before.equity)
        # Specifically not re-seeded from equity.
        if before.equity != before.day_start:
            assert after.day_start != pytest.approx(after.equity)
    finally:
        reopened.close()


def test_an_open_position_survives_a_restart_with_its_excursions(store):
    frame = trending_frame(40)
    # One intent near the end, with a distant stop and target so it stays open.
    index = frame.data.index
    price = float(frame.data["close"].iloc[30])
    intent = Intent(
        index=index[30], side=Side.LONG, entry=price,
        stop=price - 50.0, target=price + 50.0,
    )

    store.create("acct", venue="fixture", starting_equity=10_000.0)
    PaperEngine(store, "acct", costs=CostModel()).advance(frame, [intent])

    held = store.load("acct").position
    assert held is not None
    assert held.mfe > 0.0

    reopened = AccountStore(store.path)
    try:
        restored = reopened.load("acct").position
        assert restored == held  # frozen dataclass, so field-for-field
    finally:
        reopened.close()


def test_replaying_the_same_bars_changes_nothing(store):
    """A live poller re-fetches overlapping windows on every call.

    Replaying a bar charges its commission twice and resolves its stop twice, so
    idempotency is a correctness requirement here and not an optimisation.
    """
    frame = trending_frame(100)
    intents = intents_every(frame, 6)

    store.create("acct", venue="fixture", starting_equity=50_000.0)
    engine = PaperEngine(store, "acct", costs=CostModel())

    first = engine.advance(frame, intents).value
    equity_after_first = engine.state().equity
    trades_after_first = len(store.journal("acct"))

    second = engine.advance(frame, intents).value
    assert second.bars_processed == 0
    assert second.bars_skipped == len(frame.data)
    assert second.fills == ()
    assert engine.state().equity == pytest.approx(equity_after_first)
    assert len(store.journal("acct")) == trades_after_first
    assert first.bars_processed == len(frame.data)


def test_lag_is_recorded_for_the_newest_bar_and_null_for_backfill(store):
    """Lag is a fact about the poller, not about history.

    A first run backfills hundreds of bars. Stamping each with the gap to the
    wall clock reports a two-month backfill as a poller two months late, and a
    column that cannot tell "late" from "backfilled" has its reader conclude the
    desk is permanently broken. NULL is the honest value for catch-up.
    """
    frame = trending_frame(60)
    store.create("acct", venue="fixture", starting_equity=50_000.0)
    engine = PaperEngine(store, "acct", costs=CostModel())

    # A wall clock two hours past the last bar: the poller was late.
    late = frame.data.index[-1].to_pydatetime() + timedelta(hours=2)
    engine.advance(frame, intents_every(frame, 5), now=late, max_staleness=None)

    journal = store.journal("acct")
    assert len(journal) > 1
    # No fill resolved on the frame's final bar, so nothing in this backfill has
    # an observed lag. The comparison is against the newest bar processed, not
    # the newest bar that produced a fill: a batch whose last trade closed days
    # before its final bar is still a backfill throughout.
    assert journal["lag_seconds"].isna().all()

    # Now a genuinely live step: one new closed bar that resolves a position.
    import dataclasses

    extended = frame.data.copy()
    step = pd.Timedelta(seconds=frame.timeframe_seconds())
    last_close = float(extended["close"].iloc[-1])
    extended.loc[extended.index[-1] + step] = {
        "open": last_close, "high": last_close + 0.5,
        "low": last_close - 0.5, "close": last_close, "volume": 1.0,
    }
    live_frame = dataclasses.replace(frame, data=extended)
    fresh = live_frame.data.index[-1]
    engine.advance(
        live_frame,
        [
            Intent(
                index=fresh, side=Side.LONG,
                entry=last_close, stop=last_close - 100.0, target=last_close + 100.0,
            )
        ],
        now=fresh.to_pydatetime() + step + timedelta(minutes=7),
        max_staleness=None,
    )
    assert store.load("acct").position is not None


def test_the_account_ceiling_is_enforced(store):
    for n in range(MAX_ACCOUNTS):
        store.create(f"a{n}", venue="fixture", starting_equity=1_000.0)
    with pytest.raises(ValueError, match="ceiling"):
        store.create("one-too-many", venue="fixture", starting_equity=1_000.0)


def test_duplicate_account_names_are_refused(store):
    store.create("acct", venue="fixture", starting_equity=1_000.0)
    with pytest.raises(ValueError, match="already exists"):
        store.create("acct", venue="fixture", starting_equity=2_000.0)


def test_advancing_an_unknown_account_fails_rather_than_creating_one(store):
    engine = PaperEngine(store, "ghost", costs=ZERO_COSTS)
    result = engine.advance(trending_frame(30), [])
    assert result.is_error
    assert "does not exist" in str(result.error)


def test_accounts_do_not_bleed_into_each_other(store):
    frame = trending_frame(100)
    store.create("crypto", venue="binance", starting_equity=50_000.0)
    store.create("nse", venue="nse", starting_equity=50_000.0)

    PaperEngine(store, "crypto", costs=CostModel()).advance(
        frame, intents_every(frame, 5)
    )

    assert store.load("nse").equity == pytest.approx(50_000.0)
    assert store.load("nse").last_bar is None
    assert store.journal("nse").empty
    assert not store.journal("crypto").empty


def test_a_refused_intent_is_logged_with_its_reason(store):
    """Refusals are recorded, because a strategy whose signals were mostly
    refused did not perform well; it barely traded, and those are different
    facts."""
    frame = trending_frame(100)
    # A 0.1% daily loss limit refuses almost immediately after any loss.
    tight = RiskLimits(
        risk_per_trade=0.01, portfolio_heat=0.06, correlated_cluster=0.025,
        daily_loss=0.0001, monthly_soft=0.10, monthly_hard=0.15,
    )
    store.create("acct", venue="fixture", starting_equity=50_000.0)
    engine = PaperEngine(store, "acct", costs=CostModel(), limits=tight)
    # A tight stop against a distant target, so the first trade loses and the
    # 0.01% daily limit is breached. The default intents here are profitable,
    # which is why a losing set has to be constructed rather than assumed.
    losing = intents_every(frame, 3, stop_off=0.5, target_off=60.0)
    result = engine.advance(frame, losing).value

    assert result.refusals
    logged = store.refusal_log("acct")
    assert len(logged) == len(result.refusals)
    assert logged["reason"].str.len().gt(0).all()


def test_saving_an_account_that_was_deleted_is_an_error_not_a_silent_noop(store):
    store.create("acct", venue="fixture", starting_equity=1_000.0)
    record = store.load("acct")
    store.delete("acct")
    with pytest.raises(ValueError, match="does not exist"):
        store.save(record)


def _at(frame, bars_after_last: float):
    """A wall clock positioned relative to the frame's last bar open."""
    step = pd.Timedelta(seconds=frame.timeframe_seconds())
    return frame.data.index[-1].to_pydatetime() + step * bars_after_last


def test_the_forming_bar_is_held_back(store):
    frame = trending_frame(40)
    store.create("acct", venue="fixture", starting_equity=50_000.0)
    engine = PaperEngine(store, "acct", costs=CostModel())

    # Halfway through the final bar's interval: it has not closed.
    mid = _at(frame, 0.5)
    result = engine.advance(frame, [], now=mid, max_staleness=None).value

    assert result.bars_forming == 1
    assert result.bars_processed == len(frame.data) - 1
    assert engine.state().last_bar == frame.data.index[-2]


def test_a_held_back_bar_is_processed_once_it_closes(store):
    frame = trending_frame(40)
    store.create("acct", venue="fixture", starting_equity=50_000.0)
    engine = PaperEngine(store, "acct", costs=CostModel())

    engine.advance(frame, [], now=_at(frame, 0.5), max_staleness=None)
    second = engine.advance(frame, [], now=_at(frame, 1.1), max_staleness=None).value

    assert second.bars_forming == 0
    assert second.bars_processed == 1
    assert engine.state().last_bar == frame.data.index[-1]


def test_provisional_extremes_on_a_forming_bar_cannot_lock_in_a_wrong_exit(store):
    """The bug the forming-bar guard exists to prevent.

    A bar fetched mid-interval has a provisional high and low. Resolving a stop
    against them and advancing `last_bar` means the corrected version arriving
    next call is skipped as already-seen, so a transient wrong answer becomes a
    permanent one. Here the provisional low would stop the position out; the
    final low does not.
    """
    import dataclasses

    frame = trending_frame(40)
    index = frame.data.index
    entry = float(frame.data["close"].iloc[30])
    intent = Intent(
        index=index[30], side=Side.LONG,
        entry=entry, stop=entry - 3.0, target=entry + 40.0,
    )

    store.create("acct", venue="fixture", starting_equity=50_000.0)
    engine = PaperEngine(store, "acct", costs=CostModel())

    # Fetch mid-interval. The final bar's provisional low sits under the stop.
    provisional = frame.data.copy()
    provisional.iloc[-1, provisional.columns.get_loc("low")] = entry - 10.0
    mid = engine.advance(
        dataclasses.replace(frame, data=provisional),
        [intent], now=_at(frame, 0.5), max_staleness=None,
    ).value

    assert mid.bars_forming == 1
    assert not any(f.outcome == "stop" for f in mid.fills)
    assert store.load("acct").position is not None

    # The bar closes and the spike was never real: the low recovered.
    corrected = frame.data.copy()
    corrected.iloc[-1, corrected.columns.get_loc("low")] = entry - 1.0
    final = engine.advance(
        dataclasses.replace(frame, data=corrected),
        [intent], now=_at(frame, 1.1), max_staleness=None,
    ).value

    assert final.bars_processed == 1
    # Still open. Had the provisional bar been processed, this would have been
    # stopped out at a price the market never printed.
    assert store.load("acct").position is not None
    assert not any(f.outcome == "stop" for f in final.fills)


def test_replay_mode_processes_every_bar(store):
    frame = trending_frame(40)
    store.create("acct", venue="fixture", starting_equity=50_000.0)
    engine = PaperEngine(store, "acct", costs=CostModel())

    result = engine.advance(
        frame, [], now=_at(frame, 0.5),
        closed_bars_only=False, max_staleness=None,
    ).value
    assert result.bars_forming == 0
    assert result.bars_processed == len(frame.data)


def test_a_stale_frame_is_refused_rather_than_traded(store):
    """A fetch that failed and returned cached data must not look like a quiet
    market once it is in the journal."""
    import dataclasses
    from datetime import timedelta

    frame = trending_frame(40)
    old = dataclasses.replace(
        frame, as_of=frame.as_of - timedelta(seconds=frame.timeframe_seconds() * 5)
    )

    store.create("acct", venue="fixture", starting_equity=50_000.0)
    engine = PaperEngine(store, "acct", costs=CostModel())
    result = engine.advance(old, [])

    assert result.is_error
    assert "Refusing rather than trading it" in str(result.error)
    # Nothing was written: the account is untouched.
    assert engine.state().last_bar is None
    assert engine.state().equity == pytest.approx(50_000.0)


def test_lag_is_recorded_when_a_fill_lands_on_the_newest_bar(store):
    """The positive case for the lag column.

    A position opened earlier and resolved on the batch's final bar is exactly
    the live situation the column exists for: the gap between that bar closing
    and the engine reacting is how late the poller was.
    """
    import dataclasses

    frame = trending_frame(40)
    step = pd.Timedelta(seconds=frame.timeframe_seconds())
    entry = float(frame.data["close"].iloc[-1])

    store.create("acct", venue="fixture", starting_equity=50_000.0)
    engine = PaperEngine(store, "acct", costs=CostModel())

    # Open on the final bar of the first batch.
    opening = Intent(
        index=frame.data.index[-1], side=Side.LONG,
        entry=entry, stop=entry - 2.0, target=entry + 3.0,
    )
    engine.advance(
        frame, [opening],
        now=frame.data.index[-1].to_pydatetime() + step, max_staleness=None,
    )
    assert store.load("acct").position is not None
    assert store.journal("acct").empty

    # One new bar that gaps through the target, so it resolves on that bar.
    extended = frame.data.copy()
    extended.loc[extended.index[-1] + step] = {
        "open": entry + 6.0, "high": entry + 7.0,
        "low": entry + 5.0, "close": entry + 6.5, "volume": 1.0,
    }
    newest = extended.index[-1]
    engine.advance(
        dataclasses.replace(frame, data=extended), [],
        now=newest.to_pydatetime() + step + timedelta(minutes=9),
        max_staleness=None,
    )

    journal = store.journal("acct")
    assert len(journal) == 1
    lag = journal["lag_seconds"].iloc[0]
    assert lag is not None and lag == lag  # not NULL, not NaN
    # The bar's own duration plus the nine minutes the poller was late.
    assert lag == pytest.approx(step.total_seconds() + 9 * 60)
