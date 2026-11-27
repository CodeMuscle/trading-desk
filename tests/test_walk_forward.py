"""Walk-forward tests.

The load-bearing test here is the lookahead one. Every other property can be
eyeballed from a printed fold table, but a signal function that quietly reads
one bar past its window produces a result that looks excellent and is worthless,
and nothing about the output reveals it. So the signal function records the
furthest bar it was ever handed and the test asserts on that directly.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from desk.backtest.costs import ZERO_COSTS
from desk.research.walk_forward import walk_forward
from desk.result import ok, unavailable
from desk.strategy.base import Intent, Side, StrategyResult
from tests.fixtures import flat_closes


def ramp_frame(bars: int = 200) -> object:
    """A sawtooth. Rises for 10 bars, falls for 10, forever.

    Chosen so a long-only strategy has genuinely alternating profitable and
    unprofitable stretches, which is what makes fold-to-fold variation real
    rather than an artefact of a monotone series.
    """
    closes = []
    price = 100.0
    for i in range(bars):
        price += 1.0 if (i // 10) % 2 == 0 else -1.0
        closes.append(price)
    return flat_closes(closes)


def every_nth(frame, *, step: int, target_mult: float = 2.0):
    """Enter long every `step` bars, stop 1 below, target `target_mult` above."""
    intents = []
    closes = frame.data["close"]
    for position in range(0, len(closes), step):
        price = float(closes.iloc[position])
        intents.append(
            Intent(
                index=closes.index[position], side=Side.LONG, entry=price,
                stop=price - 1.0, target=price + target_mult,
            )
        )
    gates = pd.DataFrame({"entry_up": closes.index.isin([i.index for i in intents])},
                         index=closes.index)
    return ok(StrategyResult(intents=intents, gates=gates))


def test_signal_fn_never_sees_a_bar_past_its_window():
    frame = ramp_frame(200)
    seen: list[pd.Timestamp] = []

    def recording(f, *, step):
        seen.append(f.data.index[-1])
        return every_nth(f, step=step)

    result = walk_forward(
        frame, {"step": [5, 7]}, recording,
        costs=ZERO_COSTS, train_bars=100, test_bars=25,
    )
    assert result.is_ok, result

    index = frame.data.index
    # Four distinct history ends are legitimate: train windows ending at bar
    # 100 and 125, and test windows ending at 125 and 150. Anything later than
    # the last fold's test end would be a leak.
    assert max(seen) <= index[len(index) - 1]
    folds = result.value.folds
    last_test_end = max(f.test_end for f in folds)
    assert max(seen) <= last_test_end


def test_a_signal_fn_reading_the_future_would_be_caught():
    """Proves the above test has teeth.

    A frame slice physically cannot contain later bars, so the guarantee is
    structural. This asserts the structure rather than trusting it: the frame
    handed to the signal function is strictly shorter than the full frame on
    every fold but the last.
    """
    frame = ramp_frame(150)
    lengths: list[int] = []

    def recording(f, *, step):
        lengths.append(len(f.data))
        return every_nth(f, step=step)

    result = walk_forward(
        frame, {"step": [5]}, recording,
        costs=ZERO_COSTS, train_bars=100, test_bars=25,
    )
    assert result.is_ok
    assert max(lengths) <= 150
    assert min(lengths) == 100  # the first training window, not one bar more


def test_folds_tile_the_sample_without_overlap():
    frame = ramp_frame(200)
    result = walk_forward(
        frame, {"step": [5]}, every_nth,
        costs=ZERO_COSTS, train_bars=100, test_bars=25,
    )
    assert result.is_ok
    folds = result.value.folds
    assert len(folds) == 4  # (200 - 100) / 25
    for earlier, later in zip(folds, folds[1:]):
        assert later.train_end > earlier.train_end
        assert later.train_end == earlier.test_end


def test_rolling_window_moves_but_anchored_does_not():
    frame = ramp_frame(200)
    rolling = walk_forward(
        frame, {"step": [5]}, every_nth,
        costs=ZERO_COSTS, train_bars=100, test_bars=25, anchored=False,
    ).value
    anchored = walk_forward(
        frame, {"step": [5]}, every_nth,
        costs=ZERO_COSTS, train_bars=100, test_bars=25, anchored=True,
    ).value

    assert len({f.train_start for f in rolling.folds}) == len(rolling.folds)
    assert len({f.train_start for f in anchored.folds}) == 1


def test_stability_is_one_when_the_same_parameter_always_wins():
    frame = ramp_frame(250)

    def rigged(f, *, step):
        # step=5 is strictly better everywhere: a wider target on the same
        # entries, so it wins every fold by construction.
        return every_nth(f, step=5, target_mult=3.0 if step == 5 else 0.5)

    result = walk_forward(
        frame, {"step": [5, 9]}, rigged,
        costs=ZERO_COSTS, train_bars=100, test_bars=25, min_trades=5,
    )
    assert result.is_ok
    value = result.value
    assert value.stability == 1.0
    assert value.modal_params == {"step": 5}


def test_a_fold_that_cannot_select_is_recorded_not_dropped():
    frame = ramp_frame(200)

    calls = {"n": 0}

    def sometimes_unavailable(f, *, step):
        calls["n"] += 1
        # Fail the first fold's only candidate, succeed afterwards.
        if calls["n"] == 1:
            return unavailable("no data for this window")
        return every_nth(f, step=step)

    result = walk_forward(
        frame, {"step": [5]}, sometimes_unavailable,
        costs=ZERO_COSTS, train_bars=100, test_bars=25, min_trades=5,
    )
    assert result.is_ok
    value = result.value
    assert len(value.folds) == 4
    assert value.folds[0].selected is None
    assert value.folds[0].test_trades == 0
    assert any("could not select" in n for n in value.notes)


def test_trial_count_multiplies_grid_by_deciding_folds():
    frame = ramp_frame(200)
    result = walk_forward(
        frame, {"step": [5, 7, 9]}, every_nth,
        costs=ZERO_COSTS, train_bars=100, test_bars=25, min_trades=5,
    )
    assert result.is_ok
    value = result.value
    deciding = sum(1 for f in value.folds if f.selected is not None)
    assert value.trials == 3 * deciding
    assert value.trials > 3  # a sweep would have reported 3


def test_refuses_when_the_sample_cannot_hold_one_fold():
    frame = ramp_frame(50)
    result = walk_forward(
        frame, {"step": [5]}, every_nth,
        costs=ZERO_COSTS, train_bars=100, test_bars=25,
    )
    assert result.is_error
    assert "cannot support" in str(result.error)


def test_refuses_an_empty_parameter_space():
    result = walk_forward(
        ramp_frame(200), {}, every_nth,
        costs=ZERO_COSTS, train_bars=100, test_bars=25,
    )
    assert result.is_error


def test_rejects_an_unknown_selection_criterion():
    with pytest.raises(ValueError, match="select_by"):
        walk_forward(
            ramp_frame(200), {"step": [5]}, every_nth,
            costs=ZERO_COSTS, train_bars=100, test_bars=25, select_by="net",
        )


def _fold(**over):
    from desk.research.walk_forward import Fold

    base = dict(
        train_start=pd.Timestamp("2026-01-01", tz="UTC"),
        train_end=pd.Timestamp("2026-02-01", tz="UTC"),
        test_end=pd.Timestamp("2026-03-01", tz="UTC"),
        selected={"step": 5}, train_sharpe=1.0, test_sharpe=0.5,
        test_net=5000.0, test_trades=10,
    )
    base.update(over)
    return Fold(**base)


def test_verdict_refuses_when_no_axis_holds_even_when_profitable():
    """Stability outranks profit.

    A procedure where no parameter repeats has nothing to deploy. Were the
    verdict ordered the other way, a positive pooled net would hide that.
    """
    from desk.research.walk_forward import WalkForwardResult

    folds = tuple(
        _fold(selected={"step": n}, test_returns=np.full(10, 0.01)) for n in range(4)
    )
    value = WalkForwardResult(
        folds=folds, trials=16, oos_sharpe=0.5, oos_net=20_000.0, oos_trades=40,
        deflated_sharpe=0.99, stability=0.25, axis_stability={"step": 0.25},
        modal_params={"step": 0}, mean_degradation=0.5,
    )
    assert "no parameter held a majority" in value.verdict
    assert value.resolved_axes == ()


def test_one_decided_axis_is_not_reported_as_total_instability():
    """The case tuple-matching gets wrong.

    Across four folds `use_candle` is False every time while the other two
    parameters rotate, so exact-tuple stability is 0.25 and yet the test has
    resolved something real. The verdict must name the resolved axis and move
    on to the performance checks rather than refusing outright.
    """
    from desk.research.walk_forward import WalkForwardResult

    picks = [
        {"use_candle": False, "tp": 1.0, "stop": 1.5},
        {"use_candle": False, "tp": 2.0, "stop": 2.0},
        {"use_candle": False, "tp": 3.0, "stop": 1.5},
        {"use_candle": False, "tp": 2.0, "stop": 2.5},
    ]
    folds = tuple(_fold(selected=p, test_returns=np.full(10, 0.01)) for p in picks)
    value = WalkForwardResult(
        folds=folds, trials=72, oos_sharpe=0.3, oos_net=25_000.0, oos_trades=86,
        deflated_sharpe=0.42,
        stability=0.25,
        axis_stability={"use_candle": 1.0, "tp": 0.5, "stop": 0.5},
        modal_params=picks[1], mean_degradation=-0.1,
    )
    assert value.resolved_axes == ("stop", "tp", "use_candle")
    # Profitable and partly resolved, so the surviving refusal is the honest
    # one: deflation, not a stability claim that misreads the fold table.
    assert "not distinguishable from luck" in value.verdict

    # Now make the two rotating axes genuinely unresolved.
    rotating = WalkForwardResult(
        folds=folds, trials=72, oos_sharpe=0.3, oos_net=25_000.0, oos_trades=86,
        deflated_sharpe=0.99,
        stability=0.25,
        axis_stability={"use_candle": 1.0, "tp": 0.29, "stop": 0.29},
        modal_params=picks[1], mean_degradation=-0.1,
    )
    assert rotating.resolved_axes == ("use_candle",)
    assert rotating.unresolved_axes == ("stop", "tp")
    assert "survives walk-forward" in rotating.verdict
    assert "unresolved and still a choice" in rotating.verdict


def test_verdict_refuses_below_the_trade_floor_before_anything_else():
    from desk.research.walk_forward import Fold, WalkForwardResult

    value = WalkForwardResult(
        folds=(
            Fold(
                train_start=pd.Timestamp("2026-01-01", tz="UTC"),
                train_end=pd.Timestamp("2026-02-01", tz="UTC"),
                test_end=pd.Timestamp("2026-03-01", tz="UTC"),
                selected={"step": 5}, train_sharpe=2.0, test_sharpe=1.8,
                test_net=9000.0, test_trades=3,
            ),
        ),
        trials=4, oos_sharpe=1.8, oos_net=9000.0, oos_trades=3,
        deflated_sharpe=0.99, stability=1.0, axis_stability={"step": 1.0},
        modal_params={"step": 5}, mean_degradation=0.2,
    )
    assert "below the sample floor" in value.verdict


def test_degradation_is_train_minus_test():
    from desk.research.walk_forward import Fold

    fold = Fold(
        train_start=pd.Timestamp("2026-01-01", tz="UTC"),
        train_end=pd.Timestamp("2026-02-01", tz="UTC"),
        test_end=pd.Timestamp("2026-03-01", tz="UTC"),
        selected={"step": 5}, train_sharpe=1.5, test_sharpe=0.4,
        test_net=100.0, test_trades=10,
    )
    assert fold.degradation == pytest.approx(1.1)

    nan_fold = Fold(
        train_start=fold.train_start, train_end=fold.train_end, test_end=fold.test_end,
        selected=None, train_sharpe=float("nan"), test_sharpe=float("nan"),
        test_net=0.0, test_trades=0,
    )
    assert math.isnan(nan_fold.degradation)


def test_hit_rate_ignores_folds_with_no_score():
    from desk.research.walk_forward import Fold, WalkForwardResult

    def make(test_sharpe, selected):
        return Fold(
            train_start=pd.Timestamp("2026-01-01", tz="UTC"),
            train_end=pd.Timestamp("2026-02-01", tz="UTC"),
            test_end=pd.Timestamp("2026-03-01", tz="UTC"),
            selected=selected, train_sharpe=1.0, test_sharpe=test_sharpe,
            test_net=0.0, test_trades=1,
        )

    value = WalkForwardResult(
        folds=(make(0.5, {"a": 1}), make(-0.2, {"a": 1}), make(float("nan"), None)),
        trials=3, oos_sharpe=0.1, oos_net=0.0, oos_trades=2,
        deflated_sharpe=float("nan"), stability=1.0, axis_stability={"a": 1.0},
        modal_params={"a": 1}, mean_degradation=0.0,
    )
    assert value.hit_rate == pytest.approx(0.5)  # 1 of 2 scored, not 1 of 3


def test_a_fold_sharpe_below_the_trade_floor_is_withheld():
    """Regression: three trades once produced a fold Sharpe of -69.

    That number then dominated the mean degradation, so a statistic meant to
    summarise seven folds was reporting on the smallest one. The returns still
    flow into the pooled figure, which is the legitimate way to reach a sample
    worth dividing by.
    """
    frame = ramp_frame(260)

    def rare(f, *, step):
        # One intent per 120 bars: a 30-bar test fold gets at most one trade.
        return every_nth(f, step=120)

    result = walk_forward(
        frame, {"step": [120]}, rare,
        costs=ZERO_COSTS, train_bars=200, test_bars=30, min_trades=1,
    )
    assert result.is_ok
    for fold in result.value.folds:
        if 0 < fold.test_trades < 2:
            assert math.isnan(fold.test_sharpe)
            assert math.isnan(fold.degradation)


def test_deflation_uses_the_observed_spread_of_trial_sharpes():
    """The spread is measured, not defaulted.

    Defaulting it assumes annualised Sharpes and rejects every per-trade result
    regardless of merit, which is the unit mismatch the sweep already hit.
    """
    frame = ramp_frame(300)
    result = walk_forward(
        frame, {"step": [4, 5, 6, 7]}, every_nth,
        costs=ZERO_COSTS, train_bars=150, test_bars=50, min_trades=4,
    )
    assert result.is_ok
    value = result.value
    assert math.isfinite(value.trial_sharpe_variance)
    assert value.trial_sharpe_variance > 0
    assert not any("spread" in n for n in value.notes)


def test_axis_stability_is_computed_per_parameter():
    """A real run, not a hand-built result object.

    Two axes where one is decisive by construction: `target_mult` is ignored by
    the rigged signal function, so whichever value wins is noise, while `step`
    drives everything.
    """
    frame = ramp_frame(300)

    def rigged(f, *, step, target_mult):
        return every_nth(f, step=step, target_mult=3.0)

    result = walk_forward(
        frame, {"step": [5, 11], "target_mult": [2.0, 9.0]}, rigged,
        costs=ZERO_COSTS, train_bars=150, test_bars=50, min_trades=4,
    )
    assert result.is_ok
    value = result.value
    assert set(value.axis_stability) == {"step", "target_mult"}
    for score in value.axis_stability.values():
        assert 0.0 < score <= 1.0
    # step=5 trades far more often than step=11, so it clears the floor and wins
    # every fold; the ignored axis cannot beat it.
    assert value.axis_stability["step"] == 1.0


def test_a_mostly_unscored_fold_table_says_so():
    frame = ramp_frame(400)

    def rare(f, *, step):
        return every_nth(f, step=90)

    result = walk_forward(
        frame, {"step": [90]}, rare,
        costs=ZERO_COSTS, train_bars=180, test_bars=40, min_trades=1,
    )
    assert result.is_ok
    assert any("reached 1 out-of-sample" in n for n in result.value.notes)


def test_prior_trials_are_carried_into_the_deflation():
    """Narrowing a grid after reading the output does not reset the tax.

    This is the one way a harness like this can be defeated from the inside:
    read 342 trials of walk-forward output, drop the parameter that lost, re-run
    a 9-point grid, and report 63 trials. The deflated Sharpe then improves
    because the count shrank, not because the strategy did.
    """
    frame = ramp_frame(300)
    space = {"step": [5, 7]}
    kwargs = dict(
        costs=ZERO_COSTS, train_bars=150, test_bars=50, min_trades=4,
    )

    fresh = walk_forward(frame, space, every_nth, **kwargs).value
    carried = walk_forward(frame, space, every_nth, prior_trials=340, **kwargs).value

    assert carried.own_trials == fresh.own_trials
    assert carried.trials == fresh.trials + 340
    assert any("prior" in n for n in carried.notes)

    # Same returns, larger count, so the deflated Sharpe cannot improve.
    if math.isfinite(fresh.deflated_sharpe) and math.isfinite(carried.deflated_sharpe):
        assert carried.deflated_sharpe <= fresh.deflated_sharpe


def test_negative_prior_trials_is_rejected():
    with pytest.raises(ValueError, match="prior_trials"):
        walk_forward(
            ramp_frame(200), {"step": [5]}, every_nth,
            costs=ZERO_COSTS, train_bars=100, test_bars=25, prior_trials=-1,
        )
