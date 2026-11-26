"""Walk-forward analysis: select on the past, score on the future, repeatedly.

A sweep reports the best parameters over one window. That number is not
available to a trader, because choosing it required seeing the window it is
measured on. Walk-forward removes the circularity by only ever scoring a
parameter set on bars that came after the bars used to choose it.

Three things this measures that a sweep structurally cannot:

- Out-of-sample performance of the *selection procedure* rather than of any
  parameter set. What gets deployed is the procedure, so that is the thing
  worth testing.
- Parameter stability. If the winner changes every fold, there is no parameter
  to deploy, and the sweep's single winner was a property of its window. This
  is reported as its own number because it is frequently the whole answer.
- Degradation. In-sample minus out-of-sample, per fold. A procedure that looks
  excellent in training and mediocre afterwards is being measured honestly for
  the first time.

Signals are generated on `frame[:fold_end]` and only then filtered to the fold
window. Truncating the frame is what makes the no-lookahead guarantee
structural rather than a promise: an indicator cannot read a bar that is not in
the DataFrame it was handed. It also matches what a live run would see, which a
fixed-width warm-up prefix would not.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

from desk.backtest.costs import CostModel
from desk.backtest.engine import run_backtest
from desk.research.sweep import _grid, plateau_score
from desk.result import MetricResult, failed, ok
from desk.store.frame import OHLCVFrame
from desk.strategy.base import Intent
from desk.validation.statistics import deflated_sharpe_ratio, sharpe_ratio


@dataclass(frozen=True, slots=True)
class Fold:
    """One train-then-test step."""

    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_end: pd.Timestamp
    selected: dict[str, Any] | None
    """None when no candidate in the training window cleared the sample floor.
    A fold that could not choose is recorded rather than skipped, because
    dropping it would quietly raise the reported hit rate."""
    train_sharpe: float
    test_sharpe: float
    test_net: float
    test_trades: int
    test_returns: np.ndarray = field(default_factory=lambda: np.array([], dtype="float64"))
    note: str = ""

    @property
    def degradation(self) -> float:
        """Train Sharpe minus test Sharpe. Large and positive is the signature
        of selection fitting noise in the training window."""
        if not (math.isfinite(self.train_sharpe) and math.isfinite(self.test_sharpe)):
            return float("nan")
        return self.train_sharpe - self.test_sharpe


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    folds: tuple[Fold, ...]
    trials: int
    """Grid size times folds that actually ran a selection. Every fold re-ran
    the whole grid, and the multiple-testing tax is owed on all of it."""
    oos_sharpe: float
    """Sharpe of every out-of-sample trade, pooled across folds. The headline."""
    oos_net: float
    oos_trades: int
    deflated_sharpe: float
    stability: float
    """Fraction of deciding folds that picked the modal parameter set. 1.0 means
    one parameter set won every time; 1/n means the winner never repeated."""
    modal_params: dict[str, Any] | None
    mean_degradation: float
    trial_sharpe_variance: float = float("nan")
    """Spread of Sharpes across all trials in all folds. The deflated Sharpe is
    uninterpretable without it, because it is the scale the deflation divides by."""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def hit_rate(self) -> float:
        """Fraction of folds with a positive out-of-sample Sharpe."""
        scored = [f for f in self.folds if math.isfinite(f.test_sharpe)]
        if not scored:
            return float("nan")
        return sum(1 for f in scored if f.test_sharpe > 0) / len(scored)

    @property
    def verdict(self) -> str:
        """One line, erring toward refusal.

        Order matters. Stability is checked before performance because an
        unstable procedure has no parameter set to deploy even when its pooled
        out-of-sample numbers happen to be positive.
        """
        deciding = [f for f in self.folds if f.selected is not None]
        if not deciding:
            return "no fold could select a parameter set: nothing was tested"
        if self.oos_trades < 20:
            return (
                f"{self.oos_trades} out-of-sample trades across {len(deciding)} folds: "
                "below the sample floor, so this is not evidence either way"
            )
        if self.stability < 0.5:
            return (
                f"parameter stability {self.stability:.2f}: the winner changed in more "
                f"than half of {len(deciding)} folds, so there is no parameter set to "
                "deploy regardless of the pooled result"
            )
        if self.oos_net <= 0:
            return (
                f"out-of-sample net {self.oos_net:,.0f} over {self.oos_trades} trades: "
                "the selection procedure loses money on bars it did not choose on"
            )
        if self.deflated_sharpe < 0.95:
            return (
                f"out-of-sample deflated Sharpe {self.deflated_sharpe:.2f} below 0.95 at "
                f"{self.trials} trials: profitable but not distinguishable from luck"
            )
        return (
            f"survives walk-forward: net {self.oos_net:,.0f} over {self.oos_trades} "
            f"out-of-sample trades, stability {self.stability:.2f}, deflated Sharpe "
            f"{self.deflated_sharpe:.2f}. Belongs on the shelf unenabled."
        )


def _slice(frame: OHLCVFrame, start: int | None, stop: int | None) -> OHLCVFrame:
    return replace(frame, data=frame.data.iloc[start:stop])


def _score(
    frame: OHLCVFrame,
    point: dict[str, Any],
    signal_fn: Callable[..., MetricResult],
    *,
    history_end: int,
    window_start: int,
    costs: CostModel,
    starting_equity: float,
    min_trades: int,
) -> tuple[float, float, int, np.ndarray]:
    """Backtest `point` over bars [window_start, history_end).

    Signals see `frame[:history_end]` and nothing after it. Returns
    (sharpe, net, trade count, per-trade returns); the sharpe is NaN when
    there were too few trades to compute one.

    Both the signal pass and the backtest run on `frame[:history_end]` rather
    than on the fold window, with intents filtered to the window afterwards.
    Handing the backtest a window slice instead restarts every indicator at the
    window's first bar, so the engine's ATR is unseeded for its whole warm-up
    length and refuses the trades that fall there. Each fold would then be
    scored on its back portion only, and the shorter the fold the larger the
    hole: a measurement artefact of the fold width, reported as performance.
    Trades still occur only inside the window, because the intents outside it
    are dropped before the engine sees them.
    """
    empty = np.array([], dtype="float64")
    history = _slice(frame, None, history_end)
    if history.data.empty or window_start >= history_end:
        return float("nan"), 0.0, 0, empty

    signal = signal_fn(history, **point)
    if not signal.is_ok:
        return float("nan"), 0.0, 0, empty

    first = frame.data.index[window_start]
    intents: list[Intent] = [i for i in signal.value.intents if i.index >= first]

    back = run_backtest(history, intents, costs=costs, starting_equity=starting_equity)
    if not back.is_ok:
        return float("nan"), 0.0, 0, empty
    result = back.value
    returns = result.returns
    # Below the floor the Sharpe is not a weak number, it is not a number: three
    # trades produced a fold Sharpe of -69 in testing, which then dominated the
    # mean degradation and made a summary statistic a report on its smallest
    # fold. The returns are still handed back, because pooling them across folds
    # is the legitimate route to a sample large enough to divide by.
    sharpe = sharpe_ratio(returns) if returns.size >= max(min_trades, 2) else float("nan")
    return sharpe, result.net_profit, len(result.trades), returns


def walk_forward(
    frame: OHLCVFrame,
    space: dict[str, Sequence[Any]],
    signal_fn: Callable[..., MetricResult],
    *,
    costs: CostModel,
    train_bars: int,
    test_bars: int,
    starting_equity: float = 100_000.0,
    min_trades: int = 10,
    anchored: bool = False,
    select_by: str = "plateau",
) -> MetricResult:
    """Repeatedly select on `train_bars` and score on the next `test_bars`.

    `min_trades` is the per-fold floor for a *training* candidate to be
    selectable. It is lower than a sweep's floor by necessity, since a training
    window is a fraction of the whole sample, and that is a real cost of this
    method rather than a detail: fold-level selection is noisier than
    whole-sample selection, and walk-forward is honest about the consequences
    instead of avoiding them.

    `anchored=True` trains on everything from the start of data rather than a
    rolling window. Rolling is the default because it is the harder test: it
    asks whether parameters survive regime change, where an anchored window
    dilutes any single regime into a long average.

    `select_by` is "plateau" or "sharpe". Plateau is the default for the same
    reason it is the sweep's default, and keeping both available makes the
    difference between them measurable rather than asserted.
    """
    if select_by not in {"plateau", "sharpe"}:
        raise ValueError(f"select_by must be 'plateau' or 'sharpe', got {select_by!r}")
    if train_bars < 1 or test_bars < 1:
        return failed(ValueError("train_bars and test_bars must both be at least 1"))

    points = _grid(space)
    if not points:
        return failed(ValueError("parameter space is empty"))

    total = len(frame.data)
    if total < train_bars + test_bars:
        return failed(
            ValueError(
                f"{total} bars cannot support a {train_bars}-bar train plus a "
                f"{test_bars}-bar test. Fetch more history rather than shrinking the "
                "windows; a fold too small to select on produces a number that looks "
                "like a result."
            )
        )

    folds: list[Fold] = []
    notes: list[str] = []
    trial_sharpes: list[float] = []
    selections = 0

    train_end = train_bars
    while train_end + test_bars <= total:
        test_end = train_end + test_bars
        train_start = 0 if anchored else train_end - train_bars

        sharpes: dict[tuple, float] = {}
        # Every trial's Sharpe, pooled across folds, is the spread the deflation
        # needs. Leaving it to the default assumes annualised Sharpes and rejects
        # every per-trade result regardless of merit, which is the unit mismatch
        # this project already fixed once in the sweep.

        eligible: dict[tuple, dict[str, Any]] = {}
        for point in points:
            sharpe, _net, trades, _returns = _score(
                frame, point, signal_fn,
                history_end=train_end, window_start=train_start,
                costs=costs, starting_equity=starting_equity, min_trades=min_trades,
            )
            key = tuple(sorted(point.items()))
            sharpes[key] = sharpe
            if math.isfinite(sharpe):
                trial_sharpes.append(sharpe)
            if trades >= min_trades and math.isfinite(sharpe):
                eligible[key] = point

        index = frame.data.index
        if not eligible:
            folds.append(
                Fold(
                    train_start=index[train_start], train_end=index[train_end - 1],
                    test_end=index[test_end - 1], selected=None,
                    train_sharpe=float("nan"), test_sharpe=float("nan"),
                    test_net=0.0, test_trades=0,
                    note=f"no training candidate reached {min_trades} trades",
                )
            )
            train_end += test_bars
            continue

        if select_by == "sharpe":
            chosen = max(eligible.values(), key=lambda p: sharpes[tuple(sorted(p.items()))])
        else:
            chosen = max(
                eligible.values(), key=lambda p: plateau_score(p, space, sharpes)[0]
            )
        selections += 1
        train_sharpe = sharpes[tuple(sorted(chosen.items()))]

        test_sharpe, test_net, test_trades, test_returns = _score(
            frame, chosen, signal_fn,
            history_end=test_end, window_start=train_end,
            costs=costs, starting_equity=starting_equity, min_trades=min_trades,
        )
        folds.append(
            Fold(
                train_start=index[train_start], train_end=index[train_end - 1],
                test_end=index[test_end - 1], selected=chosen,
                train_sharpe=train_sharpe, test_sharpe=test_sharpe,
                test_net=test_net, test_trades=test_trades, test_returns=test_returns,
            )
        )
        train_end += test_bars

    if not folds:
        return failed(ValueError("no fold fit in the available bars"))

    pooled = (
        np.concatenate([f.test_returns for f in folds if f.test_returns.size])
        if any(f.test_returns.size for f in folds)
        else np.array([], dtype="float64")
    )
    oos_sharpe = sharpe_ratio(pooled) if pooled.size > 1 else float("nan")
    trials = len(points) * max(selections, 1)

    spread = float(np.var(trial_sharpes, ddof=1)) if len(trial_sharpes) > 1 else float("nan")
    try:
        dsr = (
            deflated_sharpe_ratio(
                pooled, trials=trials, variance_of_sharpes=spread
            )
            if pooled.size >= 4 and math.isfinite(spread)
            else float("nan")
        )
    except ValueError as exc:
        dsr = float("nan")
        notes.append(f"deflated Sharpe not computable: {exc}")
    if not math.isfinite(spread):
        notes.append(
            "fewer than two trials produced a finite Sharpe, so the spread the "
            "deflation needs could not be measured"
        )
    if pooled.size < 4:
        notes.append(
            f"{pooled.size} pooled out-of-sample trades is too few to deflate; "
            "the headline Sharpe stands undeflated and should not be trusted"
        )

    decided = [f.selected for f in folds if f.selected is not None]
    if decided:
        counted = Counter(tuple(sorted(p.items())) for p in decided)
        modal_key, modal_count = counted.most_common(1)[0]
        modal: dict[str, Any] | None = dict(modal_key)
        stability = modal_count / len(decided)
    else:
        modal, stability = None, float("nan")
        notes.append("every fold failed to select; the procedure never ran")

    skipped = sum(1 for f in folds if f.selected is None)
    if skipped:
        notes.append(
            f"{skipped} of {len(folds)} folds could not select a parameter set and "
            "contributed no out-of-sample trades"
        )

    degradations = [f.degradation for f in folds if math.isfinite(f.degradation)]

    return ok(
        WalkForwardResult(
            folds=tuple(folds),
            trials=trials,
            oos_sharpe=oos_sharpe,
            oos_net=float(sum(f.test_net for f in folds)),
            oos_trades=int(sum(f.test_trades for f in folds)),
            deflated_sharpe=dsr,
            stability=stability,
            modal_params=modal,
            mean_degradation=float(np.mean(degradations)) if degradations else float("nan"),
            trial_sharpe_variance=spread,
            notes=tuple(notes),
        )
    )
