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


def test_the_journal_records_how_stale_each_fill_was(store):
    frame = trending_frame(60)
    store.create("acct", venue="fixture", starting_equity=50_000.0)
    engine = PaperEngine(store, "acct", costs=CostModel())

    # A wall clock two hours past the last bar: the poller was late.
    late = frame.data.index[-1].to_pydatetime() + timedelta(hours=2)
    engine.advance(frame, intents_every(frame, 5), now=late)

    journal = store.journal("acct")
    assert not journal.empty
    assert journal["lag_seconds"].notna().all()
    # Every fill is older than the wall clock, so every lag is positive.
    assert (journal["lag_seconds"] > 0).all()


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
