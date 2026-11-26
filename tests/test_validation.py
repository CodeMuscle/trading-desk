"""The harness, tested against results whose truth is known in advance.

A validation harness that passes everything is worse than no harness, because
it converts "untested" into "approved". The decisive tests here feed it pure
noise dressed up as a winner and require it to say so.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from desk.validation.statistics import (
    deflated_sharpe_from_trials,
    deflated_sharpe_ratio,
    minimum_backtest_length,
    monte_carlo_drawdown,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)

SEED = 20261126


# ------------------------------------------------------------ sharpe

def test_sharpe_of_a_constant_series_is_undefined_not_infinite():
    assert math.isnan(sharpe_ratio(np.full(50, 0.01)))


def test_sharpe_scales_with_the_annualisation_factor():
    rng = np.random.default_rng(SEED)
    r = rng.normal(0.001, 0.01, 500)
    assert sharpe_ratio(r, 252) == pytest.approx(sharpe_ratio(r, 1) * math.sqrt(252))


def test_a_series_too_short_to_judge_returns_nan():
    assert math.isnan(sharpe_ratio(np.array([0.01])))


# --------------------------------------------------- deflated sharpe

def test_the_trial_count_is_required_and_must_be_real():
    rng = np.random.default_rng(SEED)
    r = rng.normal(0.001, 0.01, 300)
    with pytest.raises(ValueError, match="trials"):
        deflated_sharpe_ratio(r, trials=0)


def test_more_trials_always_lowers_confidence_in_the_same_result():
    # The identical return series, claimed as the winner of ever-larger
    # sweeps. Nothing about the series changed; only how many things were
    # tried to find it.
    rng = np.random.default_rng(SEED)
    returns = rng.normal(0.0012, 0.01, 500)

    confidences = [
        deflated_sharpe_ratio(returns, trials=n, variance_of_sharpes=0.05)
        for n in (1, 10, 100, 1000, 10_000)
    ]
    assert all(
        later <= earlier + 1e-12
        for earlier, later in zip(confidences, confidences[1:])
    ), f"confidence must not rise with trial count: {confidences}"
    assert confidences[0] > confidences[-1]


def test_a_genuinely_strong_single_trial_result_survives():
    rng = np.random.default_rng(SEED)
    strong = rng.normal(0.004, 0.01, 500)  # Sharpe around 0.4 per period
    assert deflated_sharpe_ratio(strong, trials=1) > 0.95


def test_the_same_result_does_not_survive_being_the_best_of_ten_thousand():
    rng = np.random.default_rng(SEED)
    strong = rng.normal(0.004, 0.01, 500)
    assert deflated_sharpe_ratio(strong, trials=10_000, variance_of_sharpes=0.05) < 0.95


def test_a_coin_flip_series_is_not_credible_at_any_trial_count():
    rng = np.random.default_rng(SEED)
    noise = rng.normal(0.0, 0.01, 500)
    assert deflated_sharpe_ratio(noise, trials=1) < 0.95


def test_the_sharpe_spread_must_be_supplied_when_more_than_one_thing_was_tried():
    # Defaulting it silently assumed annualised Sharpes, which made every
    # per-period result fail regardless of merit: the planted-edge control
    # scored identically to pure noise. Forcing the caller to state the scale
    # is what stops that returning.
    rng = np.random.default_rng(SEED)
    returns = rng.normal(0.002, 0.01, 300)
    with pytest.raises(ValueError, match="variance_of_sharpes"):
        deflated_sharpe_ratio(returns, trials=50)
    # A single honest attempt needs no spread, because nothing was selected.
    assert math.isfinite(deflated_sharpe_ratio(returns, trials=1))


def test_the_harness_separates_a_planted_edge_from_pure_noise():
    # THE CONTROL. Without this the harness could flag everything and look
    # rigorous while being useless. One matrix has no edge anywhere; the other
    # has exactly one real, stationary edge. The statistic must tell them apart.
    rng = np.random.default_rng(SEED)
    noise = rng.normal(0.0, 0.01, size=(400, 50))
    planted = rng.normal(0.0, 0.01, size=(400, 50))
    planted[:, 11] += 0.006

    noise_dsr = deflated_sharpe_from_trials(noise)
    real_dsr = deflated_sharpe_from_trials(planted)

    assert noise_dsr < 0.95, f"noise scored {noise_dsr:.3f}; the harness is not filtering"
    assert real_dsr > 0.95, f"a real edge scored {real_dsr:.3f}; the harness is not discriminating"
    assert real_dsr > noise_dsr


def test_the_trial_matrix_interface_picks_the_best_column_by_default():
    rng = np.random.default_rng(SEED)
    matrix = rng.normal(0.0, 0.01, size=(300, 20))
    matrix[:, 5] += 0.008
    assert deflated_sharpe_from_trials(matrix) == pytest.approx(
        deflated_sharpe_from_trials(matrix, chosen=5)
    )


# ------------------------------------------------------------- PBO

def test_pbo_rejects_a_matrix_with_nothing_to_choose_between():
    with pytest.raises(ValueError, match="at least 2 trials"):
        probability_of_backtest_overfitting(np.zeros((100, 1)))


def test_pbo_near_one_when_every_trial_is_pure_noise():
    # THE HARNESS SELF-TEST. 50 strategies with no edge whatsoever. Picking
    # the in-sample best is picking the luckiest, and the luckiest does not
    # stay lucky. If this does not come out high, the harness is decoration.
    rng = np.random.default_rng(SEED)
    noise = rng.normal(0.0, 0.01, size=(400, 50))
    result = probability_of_backtest_overfitting(noise, n_splits=10)
    assert result.probability > 0.5, (
        f"PBO was {result.probability:.2f} on pure noise. A selection procedure "
        "that cannot be shown to overfit noise cannot be trusted on real data."
    )


def test_pbo_low_when_one_trial_has_a_real_and_persistent_edge():
    rng = np.random.default_rng(SEED)
    matrix = rng.normal(0.0, 0.01, size=(400, 20))
    matrix[:, 7] += 0.006  # a genuine, stationary edge in one column
    result = probability_of_backtest_overfitting(matrix, n_splits=10)
    assert result.probability < 0.5


def test_pbo_demands_enough_observations_to_split():
    with pytest.raises(ValueError, match="observations"):
        probability_of_backtest_overfitting(np.zeros((8, 5)), n_splits=16)


def test_pbo_rejects_an_odd_split_count():
    with pytest.raises(ValueError, match="even"):
        probability_of_backtest_overfitting(np.zeros((100, 5)), n_splits=7)


# ------------------------------------------- minimum backtest length

def test_a_higher_sharpe_target_needs_less_data():
    assert minimum_backtest_length(2.0, trials=10) < minimum_backtest_length(1.0, trials=10)


def test_more_trials_demand_more_data_for_the_same_claim():
    # Target chosen high enough that both are finite, or the comparison is
    # inf > inf and says nothing.
    assert minimum_backtest_length(5.0, trials=1000) > minimum_backtest_length(5.0, trials=10)


def test_a_modest_sharpe_found_by_a_small_sweep_is_already_unprovable():
    # The number that should stop a sweep before it starts. Ten worthless
    # strategies produce a best Sharpe of about 1.57 on average, so a Sharpe
    # of 1.5 claimed as the winner of ten trials cannot be evidence of
    # anything, at any sample size.
    assert minimum_backtest_length(1.5, trials=10) == float("inf")
    # One honest attempt, and the same 1.5 needs a couple of years.
    assert 1.0 < minimum_backtest_length(1.5, trials=1) < 5.0


def test_a_sharpe_the_sweep_size_already_explains_needs_infinite_data():
    # If the expected best-of-N from worthless strategies already reaches your
    # target, no amount of data makes that target evidence of anything.
    assert minimum_backtest_length(0.05, trials=100_000) == float("inf")


def test_a_non_positive_target_is_unreachable():
    assert minimum_backtest_length(0.0) == float("inf")
    assert minimum_backtest_length(-1.0) == float("inf")


# --------------------------------------------------------- monte carlo

def test_reshuffling_preserves_total_return_but_not_the_drawdown_path():
    rng = np.random.default_rng(SEED)
    returns = rng.normal(0.001, 0.02, 300)
    result = monte_carlo_drawdown(returns, simulations=400)
    assert result["p95"] >= result["median"]
    assert 0.0 <= result["realised_percentile"] <= 1.0


def test_clustered_losses_show_up_as_a_high_percentile():
    # Twenty consecutive losses is close to the worst ordering these trades
    # admit, so the realised drawdown should sit at the top of its own
    # reshuffle distribution.
    returns = np.concatenate([np.full(20, -0.02), np.full(80, 0.01)])
    result = monte_carlo_drawdown(returns, simulations=400)
    assert result["realised_percentile"] > 0.9
    assert result["realised"] > result["median"]


def test_a_lucky_ordering_shows_up_as_a_low_percentile():
    # The same twenty losses and eighty gains, spread evenly instead of
    # clustered. Identical total return, a fraction of the drawdown, and the
    # reshuffles are mostly worse than what was realised. A strategy sitting
    # here got lucky in the ordering rather than being less risky.
    returns = np.full(100, 0.01)
    returns[::5] = -0.02
    result = monte_carlo_drawdown(returns, simulations=400)
    assert result["realised_percentile"] < 0.1
    assert result["realised"] < result["median"]


def test_monte_carlo_is_deterministic_for_a_given_seed():
    rng = np.random.default_rng(SEED)
    returns = rng.normal(0.001, 0.02, 200)
    a = monte_carlo_drawdown(returns, simulations=200, seed=7)
    b = monte_carlo_drawdown(returns, simulations=200, seed=7)
    assert a == b
