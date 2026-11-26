"""The backtest, tested on frames whose outcome is known by construction."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from desk.backtest.costs import ZERO_COSTS, CostModel
from desk.backtest.engine import run_backtest
from desk.risk.limits import RiskLimits
from desk.strategy.base import Intent, Side
from tests.fixtures import flat_closes, frame_from_rows


def flat_bars(n: int, price: float = 100.0, span: float = 1.0):
    return [
        {"open": price, "high": price + span, "low": price - span,
         "close": price, "volume": 10.0}
        for _ in range(n)
    ]


def intent_at(frame, bar: int, side: Side = Side.LONG, entry=100.0, stop=99.0, target=101.0):
    return Intent(index=frame.data.index[bar], side=side, entry=entry, stop=stop, target=target)


def test_a_position_cannot_close_on_its_own_entry_bar():
    # The entry bar's range spans both stop and target. If the engine resolved
    # on the entry bar it would record a trade here; it must not.
    rows = flat_bars(40)
    rows[20] = {"open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0, "volume": 10.0}
    frame = frame_from_rows(rows)
    result = run_backtest(frame, [intent_at(frame, 20)], costs=ZERO_COSTS)
    assert result.is_ok
    for trade in result.value.trades:
        assert trade.exit_index > trade.entry_index


def test_when_a_bar_touches_both_levels_the_stop_is_assumed_first():
    rows = flat_bars(40)
    rows[25] = {"open": 100.0, "high": 106.0, "low": 94.0, "close": 100.0, "volume": 10.0}
    frame = frame_from_rows(rows)
    result = run_backtest(frame, [intent_at(frame, 20)], costs=ZERO_COSTS)
    trades = result.value.trades
    assert len(trades) == 1
    assert trades[0].outcome == "stop", "the flattering reading must not be taken"
    assert trades[0].net < 0


def test_a_clean_target_hit_is_recorded_as_such():
    rows = flat_bars(40, span=0.2)
    rows[25] = {"open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0, "volume": 10.0}
    frame = frame_from_rows(rows)
    result = run_backtest(frame, [intent_at(frame, 20)], costs=ZERO_COSTS)
    trades = result.value.trades
    assert len(trades) == 1
    assert trades[0].outcome == "target"
    assert trades[0].net > 0


def test_an_unresolved_position_is_closed_at_the_last_bar_and_labelled():
    frame = frame_from_rows(flat_bars(40, span=0.2))
    result = run_backtest(frame, [intent_at(frame, 20)], costs=ZERO_COSTS)
    trades = result.value.trades
    assert len(trades) == 1
    assert trades[0].outcome == "end_of_data"
    assert trades[0].exit_index == frame.data.index[-1]


def test_costs_are_always_applied_and_always_reduce_the_result():
    rows = flat_bars(40, span=0.2)
    rows[25] = {"open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0, "volume": 10.0}
    frame = frame_from_rows(rows)

    free = run_backtest(frame, [intent_at(frame, 20)], costs=ZERO_COSTS).value
    real = run_backtest(frame, [intent_at(frame, 20)], costs=CostModel()).value

    assert real.total_costs > 0
    assert real.net_profit < free.net_profit


def test_slippage_fills_the_entry_against_you():
    rows = flat_bars(40, span=0.2)
    frame = frame_from_rows(rows)
    long_fill = run_backtest(
        frame, [intent_at(frame, 20, Side.LONG)], costs=CostModel()
    ).value.trades[0].entry
    short_fill = run_backtest(
        frame, [intent_at(frame, 20, Side.SHORT, entry=100.0, stop=101.0, target=99.0)],
        costs=CostModel(),
    ).value.trades[0].entry
    assert long_fill > 100.0, "a long should fill above the signal price"
    assert short_fill < 100.0, "a short should fill below it"


def test_the_risk_engine_sizes_every_entry():
    frame = frame_from_rows(flat_bars(40, span=0.2))
    result = run_backtest(
        frame, [intent_at(frame, 20)], costs=ZERO_COSTS, starting_equity=100_000.0
    ).value
    trade = result.trades[0]
    # 1% of 100k over a 1.00 stop distance
    assert trade.units == pytest.approx(1000.0)
    assert trade.risk_currency == pytest.approx(1000.0, rel=0.01)


def test_a_refused_intent_is_recorded_with_its_reason_not_silently_dropped():
    frame = frame_from_rows(flat_bars(40, span=0.2))
    # A stop at the entry price is unsizeable.
    bad = intent_at(frame, 20, stop=100.0)
    result = run_backtest(frame, [bad], costs=ZERO_COSTS).value
    assert result.trades == ()
    assert len(result.refusals) == 1
    assert "risk per unit is zero" in result.refusals[0][1]


def test_no_trade_opens_while_another_is_running():
    rows = flat_bars(60, span=0.2)
    frame = frame_from_rows(rows)
    intents = [intent_at(frame, b) for b in (20, 21, 22)]
    result = run_backtest(frame, intents, costs=ZERO_COSTS).value
    assert len(result.trades) <= 1


def test_mae_and_mfe_are_measured_in_r_and_only_while_open():
    rows = flat_bars(40, span=0.1)
    rows[22] = {"open": 100.0, "high": 100.1, "low": 99.5, "close": 100.0, "volume": 10.0}
    rows[25] = {"open": 100.0, "high": 101.5, "low": 99.9, "close": 101.0, "volume": 10.0}
    frame = frame_from_rows(rows)
    trade = run_backtest(frame, [intent_at(frame, 20)], costs=ZERO_COSTS).value.trades[0]
    # stop is 1.00 away, so a 0.50 adverse move is 0.5R
    assert trade.mae == pytest.approx(0.5, abs=0.05)
    assert trade.mfe >= 1.0


def test_the_equity_curve_has_one_point_per_bar():
    frame = frame_from_rows(flat_bars(40, span=0.2))
    result = run_backtest(frame, [intent_at(frame, 20)], costs=ZERO_COSTS).value
    assert len(result.equity) == len(frame.data)
    assert result.equity.index.equals(frame.data.index)


def test_returns_are_expressed_against_starting_equity_for_the_harness():
    rows = flat_bars(40, span=0.2)
    rows[25] = {"open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0, "volume": 10.0}
    frame = frame_from_rows(rows)
    result = run_backtest(
        frame, [intent_at(frame, 20)], costs=ZERO_COSTS, starting_equity=100_000.0
    ).value
    assert len(result.returns) == len(result.trades)
    assert result.returns[0] == pytest.approx(result.trades[0].net / 100_000.0)


def test_the_daily_limit_stops_further_entries_within_the_same_session():
    # A tight daily limit plus a losing trade should refuse the next intent.
    rows = flat_bars(200, span=0.1)
    rows[30] = {"open": 100.0, "high": 100.1, "low": 90.0, "close": 100.0, "volume": 10.0}
    frame = frame_from_rows(rows, timeframe="1h")
    limits = RiskLimits(daily_loss=0.005)
    intents = [intent_at(frame, 25), intent_at(frame, 35)]
    result = run_backtest(frame, intents, costs=ZERO_COSTS, limits=limits).value
    assert len(result.trades) == 1
    assert any("daily loss" in reason for _, reason in result.refusals)


def test_a_negative_starting_equity_is_an_error():
    frame = frame_from_rows(flat_bars(40))
    assert run_backtest(frame, [], costs=ZERO_COSTS, starting_equity=-1.0).is_error


def test_an_intent_before_atr_is_seeded_is_refused_not_silently_poisoned():
    """Regression: a NaN ATR used to end the run without saying so.

    ATR needs its full length of real bars before it produces a number. An
    intent landing earlier multiplied that NaN into the entry fill, then into
    equity, and equity never recovers from NaN: the risk engine refused every
    later intent for a non-numeric account, so a 20-intent run reported one
    trade and nineteen refusals whose stated reason pointed at the account
    rather than at the unseeded indicator.

    This was reachable only through short frames, which is why the confluence
    strategy never hit it: its 200-EMA gate cannot fire inside the first 14
    bars. Walk-forward reaches it on every fold.
    """
    closes = [100.0 + i for i in range(40)]
    frame = flat_closes(closes)
    first = frame.data.index[0]
    intents = [
        Intent(index=first, side=Side.LONG, entry=100.0, stop=99.0, target=102.0),
        Intent(
            index=frame.data.index[30], side=Side.LONG,
            entry=130.0, stop=129.0, target=132.0,
        ),
    ]

    result = run_backtest(frame, intents, costs=CostModel(), starting_equity=100_000.0)
    assert result.is_ok
    value = result.value

    assert len(value.refusals) == 1
    assert "ATR not seeded" in value.refusals[0][1]

    # The second intent, well past the warm-up, still trades. The bug's real
    # damage was here: one unpriceable bar used to disable the rest of the run.
    assert len(value.trades) == 1
    assert value.trades[0].entry_index == frame.data.index[30]
    assert math.isfinite(value.equity.iloc[-1])
    assert not value.equity.isna().any()
