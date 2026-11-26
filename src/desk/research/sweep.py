"""Parameter sweeps that pay their own multiple-testing tax.

Generating many variants and keeping the best is, mechanically, the procedure
that manufactures false discoveries. The controls are not restraint, they are
plumbing:

- Every sweep records its trial count, and that count is handed to the
  statistics rather than being available to omit.
- Candidates are scored on the mean of their neighbourhood in parameter space,
  penalised by its spread. An isolated spike surrounded by poor results is
  rejected however good the spike is.
- Nothing promotes itself. A sweep produces a shelf, and enabling is a human
  action taken elsewhere.

Per-bar returns, not per-trade, are what feed the trial matrix. Trials produce
different numbers of trades, and a matrix needs aligned rows; equity sampled on
the frame's own index gives every trial the same length by construction.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from desk.backtest.costs import CostModel
from desk.backtest.engine import BacktestResult, run_backtest
from desk.result import MetricResult, failed, ok
from desk.store.frame import OHLCVFrame
from desk.strategy.base import Intent
from desk.validation.statistics import (
    deflated_sharpe_from_trials,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)


@dataclass(frozen=True, slots=True)
class Candidate:
    params: dict[str, Any]
    trades: int
    net: float
    sharpe: float
    max_drawdown: float
    plateau_score: float = float("nan")
    """Mean Sharpe of this point and its neighbours, minus their spread. The
    selection criterion, because a peak with a cliff either side is noise."""
    plateau_width: int = 0
    """How many immediate neighbours in parameter space were also profitable."""


@dataclass(frozen=True, slots=True)
class SweepResult:
    candidates: tuple[Candidate, ...]
    trials: int
    """Cumulative across every run of this sweep. Re-running weekly for a year
    is one multiple-testing exercise, not fifty-two."""
    pbo: float
    best_by_sharpe: Candidate | None
    best_by_plateau: Candidate | None
    deflated_sharpe: float
    matrix_shape: tuple[int, int]
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def verdict(self) -> str:
        """One line a human can act on, erring toward refusal."""
        if self.best_by_plateau is None:
            return "no candidate produced a usable result"
        if not math.isfinite(self.pbo):
            return "PBO not computable; treat as unvalidated"
        if self.pbo > 0.5:
            return (
                f"PBO {self.pbo:.2f} above 0.5: the selection is worse than random, "
                "so this sweep found nothing"
            )
        if self.deflated_sharpe < 0.95:
            return (
                f"deflated Sharpe {self.deflated_sharpe:.2f} below 0.95 at "
                f"{self.trials} trials: not distinguishable from luck"
            )
        return (
            f"PBO {self.pbo:.2f}, deflated Sharpe {self.deflated_sharpe:.2f} at "
            f"{self.trials} trials: survives, and belongs on the shelf unenabled"
        )


def _grid(space: dict[str, Sequence[Any]]) -> list[dict[str, Any]]:
    """Every combination in the space.

    Guards the empty case explicitly. itertools.product() with no iterables
    yields one empty tuple, so an empty space would otherwise become a single
    trial with default parameters: a sweep quietly reporting a trial count of
    one for work it never did.
    """
    if not space:
        return []
    names = sorted(space)
    for name in names:
        if len(space[name]) == 0:
            return []
    return [dict(zip(names, values)) for values in itertools.product(*(space[n] for n in names))]


def _neighbours(point: dict[str, Any], space: dict[str, Sequence[Any]]) -> list[dict[str, Any]]:
    """Points one step away along any single axis.

    Used for plateau scoring. A candidate whose neighbours all fail is a spike,
    and a spike is what overfitting looks like from above.
    """
    out: list[dict[str, Any]] = []
    for name, values in space.items():
        ordered = list(values)
        position = ordered.index(point[name])
        for offset in (-1, 1):
            neighbour_at = position + offset
            if 0 <= neighbour_at < len(ordered):
                moved = dict(point)
                moved[name] = ordered[neighbour_at]
                out.append(moved)
    return out


def plateau_score(
    point: dict[str, Any],
    space: dict[str, Sequence[Any]],
    sharpes: dict[tuple, float],
) -> tuple[float, int]:
    """Score a point by its neighbourhood, not by itself.

    Returns (score, width). The score is the mean Sharpe of the point and its
    immediate neighbours minus their standard deviation, so a high value
    surrounded by poor ones is penalised for the spread it sits in. The width
    counts how many neighbours were also positive.

    This is the selection criterion rather than a diagnostic printed after the
    fact. A parameter value that only works at exactly that value is the shape
    overfitting takes, and choosing by peak is choosing it on purpose.
    """
    own = sharpes.get(tuple(sorted(point.items())), float("nan"))
    values = [own]
    width = 0
    for neighbour in _neighbours(point, space):
        found = sharpes.get(tuple(sorted(neighbour.items())))
        if found is not None and math.isfinite(found):
            values.append(found)
            if found > 0:
                width += 1
    finite = [v for v in values if math.isfinite(v)]
    if len(finite) < 2:
        return own, width
    return float(np.mean(finite) - np.std(finite)), width


def sweep(
    frame: OHLCVFrame,
    space: dict[str, Sequence[Any]],
    signal_fn: Callable[..., MetricResult],
    *,
    costs: CostModel,
    starting_equity: float = 100_000.0,
    max_trials: int,
    prior_trials: int = 0,
    min_trades: int = 20,
) -> MetricResult:
    """Run every combination in `space` and score the set honestly.

    `max_trials` is required. An uncapped sweep is one whose trial count you
    will later be tempted to forget.

    `prior_trials` carries the cumulative count from earlier runs of this same
    sweep, so a weekly re-run deflates against the total rather than resetting.

    `min_trades` is the sample floor below which a candidate is kept in the
    results but marked unusable, because a result from three trades is not a
    worse result, it is not a result.
    """
    points = _grid(space)
    if not points:
        return failed(ValueError("parameter space is empty"))
    if len(points) > max_trials:
        return failed(
            ValueError(
                f"grid has {len(points)} combinations but max_trials is {max_trials}. "
                "Raise the cap deliberately or shrink the grid; silently truncating "
                "would understate the trial count the statistics depend on."
            )
        )

    columns: list[np.ndarray] = []
    candidates: list[Candidate] = []
    by_params: dict[tuple, Candidate] = {}
    usable: list[int] = []

    for point in points:
        signal = signal_fn(frame, **point)
        if not signal.is_ok:
            continue
        intents: list[Intent] = signal.value.intents
        back = run_backtest(
            frame, intents, costs=costs, starting_equity=starting_equity
        )
        if not back.is_ok:
            continue
        result: BacktestResult = back.value

        bar_returns = result.equity.pct_change().fillna(0.0).to_numpy()
        columns.append(bar_returns)

        candidate = Candidate(
            params=point,
            trades=len(result.trades),
            net=result.net_profit,
            sharpe=sharpe_ratio(result.returns) if len(result.returns) > 1 else float("nan"),
            max_drawdown=result.max_drawdown,
        )
        candidates.append(candidate)
        by_params[tuple(sorted(point.items()))] = candidate
        if len(result.trades) >= min_trades:
            usable.append(len(candidates) - 1)

    if not candidates:
        return failed(ValueError("no combination produced a runnable backtest"))

    # Plateau scoring: a point is only as good as the region around it.
    sharpe_by_point = {key: c.sharpe for key, c in by_params.items()}
    scored: list[Candidate] = []
    for candidate in candidates:
        score, width = plateau_score(candidate.params, space, sharpe_by_point)
        scored.append(
            Candidate(
                params=candidate.params, trades=candidate.trades, net=candidate.net,
                sharpe=candidate.sharpe, max_drawdown=candidate.max_drawdown,
                plateau_score=score, plateau_width=width,
            )
        )

    trials = len(points) + prior_trials
    matrix = np.column_stack(columns)

    notes: list[str] = []
    try:
        pbo = probability_of_backtest_overfitting(matrix, n_splits=10).probability
    except ValueError as exc:
        pbo = float("nan")
        notes.append(f"PBO not computable: {exc}")

    try:
        dsr = deflated_sharpe_from_trials(matrix, trials=trials)
    except ValueError as exc:
        dsr = float("nan")
        notes.append(f"deflated Sharpe not computable: {exc}")

    pool = [scored[i] for i in usable] or []
    if not pool:
        notes.append(
            f"no candidate reached {min_trades} trades; every result here is below "
            "the sample floor and none of them is evidence of anything"
        )

    best_sharpe = max(pool, key=lambda c: c.sharpe, default=None) if pool else None
    best_plateau = max(pool, key=lambda c: c.plateau_score, default=None) if pool else None

    return ok(
        SweepResult(
            candidates=tuple(scored),
            trials=trials,
            pbo=pbo,
            best_by_sharpe=best_sharpe,
            best_by_plateau=best_plateau,
            deflated_sharpe=dsr,
            matrix_shape=matrix.shape,
            notes=tuple(notes),
        )
    )
