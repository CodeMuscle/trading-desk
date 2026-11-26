"""The sweep, tested on the thing it exists to refuse."""

from __future__ import annotations

import math

import numpy as np
import pytest

from desk.backtest.costs import ZERO_COSTS, CostModel
from desk.research.sweep import sweep
from desk.result import ok
from desk.strategy.base import Intent, Side, StrategyResult
from desk.strategy.confluence import run as confluence_run
from tests.fixtures import frame_from_rows
import pandas as pd


def noisy_frame(bars: int = 600, seed: int = 11):
    rng = np.random.default_rng(seed)
    rows = []
    price = 100.0
    for _ in range(bars):
        o = price
        c = o * (1.0 + rng.normal(0.0, 0.01))
        rows.append(
            {"open": o, "high": max(o, c) * 1.004, "low": min(o, c) * 0.996,
             "close": c, "volume": 100.0}
        )
        price = c
    return frame_from_rows(rows)


def test_an_uncapped_sweep_is_impossible():
    # max_trials has no default, so a caller cannot forget to bound it.
    with pytest.raises(TypeError):
        sweep(noisy_frame(), {"tp1_r_multiple": [1.0]}, confluence_run, costs=ZERO_COSTS)


def test_a_grid_larger_than_its_cap_is_refused_not_truncated():
    space = {"tp1_r_multiple": [1.0, 1.5, 2.0], "atr_stop_mult": [1.0, 1.5, 2.0]}
    result = sweep(noisy_frame(), space, confluence_run, costs=ZERO_COSTS, max_trials=4)
    assert result.is_error
    assert "understate the trial count" in str(result.error)


def test_the_trial_count_includes_prior_runs():
    space = {"tp1_r_multiple": [1.0, 2.0]}
    result = sweep(
        noisy_frame(), space, confluence_run, costs=ZERO_COSTS,
        max_trials=10, prior_trials=98,
    )
    assert result.is_ok
    assert result.value.trials == 100, "a weekly re-run must deflate against the total"


def test_an_empty_space_is_an_error():
    assert sweep(noisy_frame(), {}, confluence_run, costs=ZERO_COSTS, max_trials=5).is_error


def test_candidates_below_the_sample_floor_are_excluded_from_selection():
    space = {"tp1_r_multiple": [1.0, 2.0, 3.0]}
    result = sweep(
        noisy_frame(400), space, confluence_run, costs=CostModel(),
        max_trials=10, min_trades=1000,
    )
    assert result.is_ok
    value = result.value
    assert value.best_by_sharpe is None
    assert value.best_by_plateau is None
    assert any("sample floor" in n for n in value.notes)
    assert "no candidate produced a usable result" in value.verdict


def test_the_verdict_refuses_when_pbo_is_above_a_half():
    # Constructed directly: the sweep's own plumbing is tested elsewhere, this
    # checks the sentence a human reads.
    from desk.research.sweep import Candidate, SweepResult

    candidate = Candidate(params={}, trades=50, net=1.0, sharpe=0.5, max_drawdown=0.1,
                          plateau_score=0.5)
    verdict = SweepResult(
        candidates=(candidate,), trials=500, pbo=0.72, best_by_sharpe=candidate,
        best_by_plateau=candidate, deflated_sharpe=0.99, matrix_shape=(100, 5),
    ).verdict
    assert "found nothing" in verdict


def test_the_verdict_refuses_when_deflated_sharpe_is_low():
    from desk.research.sweep import Candidate, SweepResult

    candidate = Candidate(params={}, trades=50, net=1.0, sharpe=0.5, max_drawdown=0.1,
                          plateau_score=0.5)
    verdict = SweepResult(
        candidates=(candidate,), trials=5000, pbo=0.10, best_by_sharpe=candidate,
        best_by_plateau=candidate, deflated_sharpe=0.40, matrix_shape=(100, 5),
    ).verdict
    assert "not distinguishable from luck" in verdict
    assert "5000 trials" in verdict


def test_a_survivor_is_shelved_rather_than_enabled():
    from desk.research.sweep import Candidate, SweepResult

    candidate = Candidate(params={}, trades=80, net=1.0, sharpe=0.9, max_drawdown=0.1,
                          plateau_score=0.9)
    verdict = SweepResult(
        candidates=(candidate,), trials=50, pbo=0.05, best_by_sharpe=candidate,
        best_by_plateau=candidate, deflated_sharpe=0.99, matrix_shape=(400, 5),
    ).verdict
    assert "shelf" in verdict and "unenabled" in verdict


def test_plateau_scoring_prefers_a_broad_region_to_an_isolated_spike():
    # Tested directly on the scoring function. An end-to-end fixture would need
    # a price path that makes specific parameter values win, which is a harder
    # thing to construct honestly than the rule being tested.
    from desk.research.sweep import plateau_score

    space = {"knob": [1, 2, 3, 4, 5]}
    sharpes = {
        (("knob", 1),): -0.40,
        (("knob", 2),): -0.30,
        (("knob", 3),): 1.80,   # lone spike, poor either side
        (("knob", 4),): 0.55,   # modest, and 5 is similar
        (("knob", 5),): 0.50,
    }
    spike, spike_width = plateau_score({"knob": 3}, space, sharpes)
    broad, broad_width = plateau_score({"knob": 4}, space, sharpes)

    assert sharpes[(("knob", 3),)] > sharpes[(("knob", 4),)], "the spike wins on raw Sharpe"
    assert broad > spike, "but the broad region must win on plateau score"
    assert spike < 1.80, "the spike is penalised by the spread it sits in"
    assert broad_width >= spike_width


def test_plateau_score_falls_back_to_the_point_itself_when_alone():
    from desk.research.sweep import plateau_score

    space = {"knob": [1]}
    score, width = plateau_score({"knob": 1}, space, {(("knob", 1),): 0.7})
    assert score == pytest.approx(0.7)
    assert width == 0


def test_an_axis_with_no_values_yields_no_trials():
    result = sweep(
        noisy_frame(200), {"tp1_r_multiple": []}, confluence_run,
        costs=ZERO_COSTS, max_trials=5,
    )
    assert result.is_error


def test_the_matrix_has_one_column_per_trial_and_one_row_per_bar():
    space = {"tp1_r_multiple": [1.0, 2.0, 3.0]}
    frame = noisy_frame(500)
    result = sweep(frame, space, confluence_run, costs=ZERO_COSTS, max_trials=10)
    rows, cols = result.value.matrix_shape
    assert rows == len(frame.data)
    assert cols == 3
