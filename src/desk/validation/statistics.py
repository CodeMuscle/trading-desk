"""The statistics that decide whether a backtest result means anything.

Every function here exists to answer one question: could this result have
arisen by chance given how many things were tried? That is why trial count is
a required argument rather than an optional one. A Sharpe ratio reported
without the number of trials behind it is not a weak claim, it is an
uninterpretable one.

References:
  Bailey & Lopez de Prado, "The Deflated Sharpe Ratio" (2014)
  Bailey, Borwein, Lopez de Prado & Zhu, "The Probability of Backtest
  Overfitting" (2015)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations

import numpy as np

# Euler-Mascheroni, used in the expected-maximum-Sharpe term.
_GAMMA = 0.5772156649015329


def sharpe_ratio(returns: np.ndarray, periods_per_year: int = 1) -> float:
    """Annualised Sharpe of a return series. Zero excess rate assumed."""
    returns = np.asarray(returns, dtype="float64")
    if returns.size < 2:
        return float("nan")
    sd = returns.std(ddof=1)
    if sd == 0:
        return float("nan")
    return float(returns.mean() / sd * math.sqrt(periods_per_year))


def _expected_max_sharpe(trials: int, variance_of_sharpes: float) -> float:
    """Expected maximum Sharpe from `trials` independent strategies with no edge.

    This is the number a sweep has to beat before its winner means anything.
    Try enough coin flips and one of them looks like skill.
    """
    if trials < 2:
        return 0.0
    from scipy.stats import norm  # local import: only this module needs scipy

    sd = math.sqrt(variance_of_sharpes)
    a = norm.ppf(1.0 - 1.0 / trials)
    b = norm.ppf(1.0 - 1.0 / (trials * math.e))
    return sd * ((1.0 - _GAMMA) * a + _GAMMA * b)


def deflated_sharpe_ratio(
    returns: np.ndarray,
    *,
    trials: int,
    variance_of_sharpes: float | None = None,
) -> float:
    """Probability the observed Sharpe reflects real skill, given `trials`.

    Returns a probability in [0, 1]. Above 0.95 is the conventional threshold.

    `trials` is required and must be honest. It is the cumulative count across
    every run of the sweep that produced this candidate, not the size of one
    run. Re-running a sweep weekly for a year is one long multiple-testing
    exercise, not fifty-two independent ones.
    """
    if trials < 1:
        raise ValueError("trials must be at least 1; pass the real number tried")

    returns = np.asarray(returns, dtype="float64")
    n = returns.size
    if n < 4:
        return float("nan")

    observed = sharpe_ratio(returns)
    if not math.isfinite(observed):
        return float("nan")

    from scipy.stats import norm

    # Sample skew and kurtosis of the returns, which shape the Sharpe estimator's
    # own variance. Fat left tails make a given Sharpe less trustworthy.
    sd = returns.std(ddof=1)
    centred = returns - returns.mean()
    skew = float((centred**3).mean() / sd**3)
    kurtosis = float((centred**4).mean() / sd**4)

    if variance_of_sharpes is None:
        if trials > 1:
            raise ValueError(
                "variance_of_sharpes is required when trials > 1. It is the "
                "variance of the Sharpe ratios actually observed across the "
                "sweep's trials, and must be measured on the same scale as the "
                "Sharpe being deflated. Defaulting it to 1.0 silently assumes "
                "annualised Sharpes, which made every per-period result fail "
                "regardless of merit. Use deflated_sharpe_from_trials() to have "
                "it computed consistently."
            )
        # A single attempt selected nothing, so there is no spread to correct
        # for and the benchmark term vanishes.
        variance_of_sharpes = 0.0

    benchmark = _expected_max_sharpe(trials, variance_of_sharpes)

    denominator = 1.0 - skew * observed + (kurtosis - 1.0) / 4.0 * observed**2
    if denominator <= 0:
        return float("nan")
    statistic = (observed - benchmark) * math.sqrt(n - 1) / math.sqrt(denominator)
    return float(norm.cdf(statistic))


@dataclass(frozen=True, slots=True)
class PBOResult:
    probability: float
    """Share of splits where the in-sample best performed below median
    out-of-sample. Above 0.5 means the selection procedure is worse than
    random: the sweep found nothing."""

    n_splits: int
    logits: tuple[float, ...]


def deflated_sharpe_from_trials(
    trial_returns: np.ndarray, *, chosen: int | None = None, trials: int | None = None
) -> float:
    """Deflated Sharpe for one column of a trial matrix, scaled consistently.

    This is the interface to prefer. It measures the spread of Sharpes across
    the sweep's own trials rather than assuming one, which is the detail that
    decides whether the statistic can tell a real edge from a lucky one.

    `trials` defaults to the number of columns, and should be overridden with
    the cumulative count when a sweep has been re-run: fifty-two weekly runs of
    the same sweep are one multiple-testing exercise, not fifty-two.
    """
    matrix = np.asarray(trial_returns, dtype="float64")
    if matrix.ndim != 2:
        raise ValueError("trial_returns must be 2-D: (observations, trials)")

    sharpes = np.array([sharpe_ratio(matrix[:, c]) for c in range(matrix.shape[1])])
    finite = sharpes[np.isfinite(sharpes)]
    if finite.size < 2:
        return float("nan")

    if chosen is None:
        chosen = int(np.nanargmax(sharpes))
    return deflated_sharpe_ratio(
        matrix[:, chosen],
        trials=trials if trials is not None else matrix.shape[1],
        variance_of_sharpes=float(finite.var(ddof=1)),
    )


def probability_of_backtest_overfitting(
    trial_returns: np.ndarray, *, n_splits: int = 16
) -> PBOResult:
    """Combinatorially symmetric cross-validation over a matrix of trials.

    `trial_returns` is (observations, trials): one column per parameter
    combination tried, one row per period.

    The procedure splits the periods into S chunks, takes every way of
    assigning half the chunks to training, picks the best trial in-sample, and
    asks where that trial ranked out-of-sample. If the in-sample winner is
    routinely below median out-of-sample, selection is overfitting.
    """
    matrix = np.asarray(trial_returns, dtype="float64")
    if matrix.ndim != 2:
        raise ValueError("trial_returns must be 2-D: (observations, trials)")
    observations, trials = matrix.shape
    if trials < 2:
        raise ValueError("PBO needs at least 2 trials to choose between")
    if n_splits % 2 != 0:
        raise ValueError("n_splits must be even so it can halve")
    if observations < n_splits:
        raise ValueError(
            f"need at least {n_splits} observations for {n_splits} splits, got {observations}"
        )

    chunks = np.array_split(np.arange(observations), n_splits)
    logits: list[float] = []

    for train_ids in combinations(range(n_splits), n_splits // 2):
        train_rows = np.concatenate([chunks[i] for i in train_ids])
        test_rows = np.concatenate(
            [chunks[i] for i in range(n_splits) if i not in train_ids]
        )

        in_sample = np.array([sharpe_ratio(matrix[train_rows, c]) for c in range(trials)])
        out_sample = np.array([sharpe_ratio(matrix[test_rows, c]) for c in range(trials)])
        if not np.isfinite(in_sample).any() or not np.isfinite(out_sample).any():
            continue

        best = int(np.nanargmax(in_sample))
        finite = np.isfinite(out_sample)
        if finite.sum() < 2 or not finite[best]:
            continue

        # Relative rank of the chosen trial out of sample, in (0, 1).
        rank = (out_sample[finite] < out_sample[best]).sum() / finite.sum()
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(math.log(rank / (1 - rank)))

    if not logits:
        return PBOResult(probability=float("nan"), n_splits=n_splits, logits=())

    probability = float(np.mean([1.0 if x <= 0 else 0.0 for x in logits]))
    return PBOResult(probability=probability, n_splits=n_splits, logits=tuple(logits))


def minimum_backtest_length(sharpe_target: float, *, trials: int = 1) -> float:
    """Years of data needed before a Sharpe of `sharpe_target` is credible.

    From Bailey et al: with more trials you need more data, because the
    expected maximum Sharpe of worthless strategies rises with the number
    tried. Returns years, assuming annualised inputs.
    """
    if sharpe_target <= 0:
        return float("inf")
    benchmark = _expected_max_sharpe(max(trials, 2), 1.0)
    if benchmark >= sharpe_target:
        return float("inf")
    return float(2.0 / (sharpe_target - benchmark) ** 2)


def monte_carlo_drawdown(
    returns: np.ndarray, *, simulations: int = 2000, seed: int = 20261126
) -> dict[str, float]:
    """Distribution of max drawdown under reshuffling of the same trades.

    The realised drawdown is one draw from this distribution, and usually not
    the worst one. A strategy whose realised drawdown sits at the 10th
    percentile of its own reshuffles got lucky in the ordering.
    """
    returns = np.asarray(returns, dtype="float64")
    if returns.size < 2:
        return {"realised": float("nan"), "median": float("nan"), "p95": float("nan")}

    rng = np.random.default_rng(seed)

    def max_drawdown(series: np.ndarray) -> float:
        equity = np.cumprod(1.0 + series)
        peak = np.maximum.accumulate(equity)
        return float((1.0 - equity / peak).max())

    realised = max_drawdown(returns)
    draws = np.array(
        [max_drawdown(rng.permutation(returns)) for _ in range(simulations)]
    )
    return {
        "realised": realised,
        "median": float(np.median(draws)),
        "p95": float(np.percentile(draws, 95)),
        "realised_percentile": float((draws < realised).mean()),
    }
