"""Supply and demand zones, and Fibonacci levels anchored to real swings.

The zone definition here is the Wyckoff and Seiden reading, not the ICT one, at
the user's explicit preference. A zone is a *base* followed by a *departure*: a
short stretch of small-range bars where price went nowhere, and then a decisive
move away from it. The reasoning is that a narrow base means the imbalance was
resolved in few prints, and a strong departure means the resting orders that
caused it were large enough to move price rather than merely absorb it.

Three properties are measured rather than asserted, because each of them is the
difference between a level and a story:

- **Departure strength**, in ATR. A base followed by a drift is not a zone; any
  stretch of chart contains bases, and the departure is what separates one that
  mattered.
- **Freshness.** A zone price has already traded back through has had its
  resting orders filled. It is reported as tested rather than dropped, since
  how a level failed is information the next decision needs.
- **Age**, in bars. Not used to rank, only reported, because whether an old zone
  is weaker is a claim this engine has not tested on its own data.

Fibonacci is anchored to confirmed swings, never to the visible window. Drawing
from the highest and lowest bar on screen makes every level a function of the
scroll position, which is how a retracement level ends up meaning nothing: the
same chart at two zoom levels gives two different sets of numbers. Where no
swing has been confirmed yet, this returns Unavailable rather than falling back
to window extremes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from desk.metrics.l1_momentum import atr as atr_metric
from desk.result import MetricResult, failed, ok, unavailable
from desk.store.frame import OHLCVFrame

RETRACEMENTS = (0.236, 0.382, 0.5, 0.618, 0.786)
"""0.5 is not a Fibonacci ratio. It is kept because it is in universal use and
removing it would make this disagree with every other chart for no gain; the
docstring is the honest place to say so rather than a comment nobody reads."""

EXTENSIONS = (1.272, 1.618)


def _stale(frame: OHLCVFrame) -> bool:
    try:
        return frame.is_stale(now=datetime.now(timezone.utc))
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class Zone:
    kind: str
    """demand or supply."""
    top: float
    bottom: float
    start_index: pd.Timestamp
    """First bar of the base."""
    end_index: pd.Timestamp
    """Last bar of the base. The departure begins on the next bar."""
    departure_atr: float
    """How far price travelled away from the base, in ATR at the departure bar."""
    base_bars: int
    tested: bool
    """Whether price has traded back into the zone since it formed."""
    age_bars: int

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0


def supply_demand_zones(
    frame: OHLCVFrame,
    base_max_bars: int = 3,
    base_max_atr: float = 0.6,
    departure_atr: float = 1.5,
    departure_within: int = 10,
    scan_bars: int = 300,
    max_zones: int = 12,
) -> MetricResult:
    """Bases followed by a decisive departure.

    `base_max_atr` bounds how wide the base may be, in ATR: a base is a pause,
    and a stretch spanning two ATR is a trend in miniature rather than a shelf
    of resting orders.

    `departure_atr` is the move required away from the base for it to count.
    Both are in ATR rather than in price or ticks so that the same parameters
    mean the same thing on an instrument at 60,000 and one at 60.

    `departure_within` bounds how long the departure may take, and it is the
    parameter that makes this usable in a backtest. Measuring the departure over
    every bar after the base means a zone's score depends on the whole future:
    the same base scores differently today than it did last week, and a strategy
    reading it at bar i would be reading bars it could not have seen. With a
    bound, a zone is settled `departure_within` bars after its base ends and
    never changes again, which is the same fixed-lag confirmation a swing pivot
    has.

    `tested` is the exception and is deliberately an as-of-now property: it
    scans every bar since the zone formed, because whether price has since
    returned is exactly the question a reader is asking. It is not a field a
    backtest may read at bar i for a zone formed before i.
    """
    if base_max_bars < 1:
        return failed(ValueError(f"base_max_bars must be at least 1, got {base_max_bars}"))
    if base_max_atr <= 0 or departure_atr <= 0:
        return failed(ValueError("base_max_atr and departure_atr must be positive"))
    if departure_within < 1:
        return failed(
            ValueError(f"departure_within must be at least 1, got {departure_within}")
        )

    atr_result = atr_metric(frame)
    if not atr_result.is_ok:
        return atr_result
    atr = atr_result.value.to_numpy(dtype="float64")

    d = frame.data
    if len(d) < base_max_bars + 2:
        return unavailable(
            f"{len(d)} bars cannot contain a base of up to {base_max_bars} plus a "
            "departure bar"
        )

    high = d["high"].to_numpy(dtype="float64")
    low = d["low"].to_numpy(dtype="float64")
    close = d["close"].to_numpy(dtype="float64")
    index = d.index

    zones: list[Zone] = []
    last = len(d) - 1
    # Bounded scan. A zone is a near-term argument about resting orders, and
    # over a 1500-bar window the ones that survive a strength ranking are
    # whichever ancient bases preceded the biggest moves on record. Those are
    # facts about history, not levels anyone is trading against, and they crowd
    # out the recent ones on both the chart and the ranking.
    oldest = max(0, len(d) - scan_bars)

    for end in range(len(d) - 2, oldest - 1, -1):
        if len(zones) >= max_zones:
            break
        scale = atr[end]
        if not np.isfinite(scale) or scale <= 0:
            continue

        for size in range(1, base_max_bars + 1):
            start = end - size + 1
            if start < 0:
                break
            top = float(high[start : end + 1].max())
            bottom = float(low[start : end + 1].min())
            if (top - bottom) > base_max_atr * scale:
                continue  # too wide to be a pause

            # The departure is measured from the base's own edge, not from its
            # midpoint: what matters is how far price got clear of the shelf.
            # Bounded, so the zone is settled at a fixed lag rather than
            # re-scored by every bar that ever arrives after it.
            after = close[end + 1 : end + 1 + departure_within]
            if after.size == 0:
                continue
            up = (float(after.max()) - top) / scale
            down = (bottom - float(after.min())) / scale

            if up >= departure_atr and up >= down:
                kind, strength = "demand", up
            elif down >= departure_atr:
                kind, strength = "supply", down
            else:
                continue

            # Tested means price RETURNED to the band, which requires that it
            # first left. Scanning from the bar after the base marks every zone
            # tested by its own departure bar: price moves continuously, so the
            # bar that leaves a shelf still touches it on the way out. Find the
            # first bar fully clear of the band, and only then look for re-entry.
            tested = False
            left_at: int | None = None
            for j in range(end + 1, len(d)):
                if low[j] > top or high[j] < bottom:
                    left_at = j
                    break
            if left_at is not None:
                tail_high = high[left_at:]
                tail_low = low[left_at:]
                tested = bool(np.any((tail_low <= top) & (tail_high >= bottom)))

            zones.append(
                Zone(
                    kind=kind, top=top, bottom=bottom,
                    start_index=index[start], end_index=index[end],
                    departure_atr=float(strength), base_bars=size,
                    tested=tested, age_bars=last - end,
                )
            )
            break  # the narrowest base that qualifies wins; wider ones overlap it

    if not zones:
        return unavailable(
            f"no base of up to {base_max_bars} bars was followed by a "
            f"{departure_atr} ATR departure in the last {scan_bars} bars"
        )

    # Freshest first, which is how they are read.
    zones.sort(key=lambda z: z.end_index, reverse=True)
    return ok(
        zones,
        stale=_stale(frame),
        base_max_bars=base_max_bars,
        base_max_atr=base_max_atr,
        departure_atr=departure_atr,
        departure_within=departure_within,
        scan_bars=scan_bars,
        fresh=sum(1 for z in zones if not z.tested),
    )


def fibonacci_levels(frame: OHLCVFrame, lookback: int = 10) -> MetricResult:
    """Retracements and extensions between the last confirmed swing pair.

    Anchored to pivots confirmed `lookback` bars after the fact, never to the
    highest and lowest bar in view. Window extremes make every level a function
    of the scroll position, so the same chart at two zooms yields two different
    sets of numbers and none of them is a level anyone was watching.
    """
    from desk.metrics.l4_price_action import swing_pivots

    pivots = swing_pivots(frame, lookback=lookback)
    if not pivots.is_ok:
        return pivots

    highs = pivots.value["highs"].dropna()
    lows = pivots.value["lows"].dropna()
    if highs.empty or lows.empty:
        return unavailable(
            f"no confirmed swing high and low at lookback {lookback}; a Fibonacci "
            "level without an anchor would be a level drawn from the window edges, "
            "which changes every time the chart is scrolled"
        )

    high_at, high_price = highs.index[-1], float(highs.iloc[-1])
    low_at, low_price = lows.index[-1], float(lows.iloc[-1])
    if high_price == low_price:
        return unavailable("the last confirmed swing high and low are the same price")

    # Direction follows which pivot came last: a retracement measures back from
    # the most recent leg, so an up leg and a down leg invert the ladder.
    upward = low_at < high_at
    span = abs(high_price - low_price)
    anchor = high_price if upward else low_price
    sign = -1.0 if upward else 1.0

    levels = {
        f"{ratio:.3f}": anchor + sign * span * ratio
        for ratio in RETRACEMENTS
    }
    levels.update(
        {f"{ratio:.3f}": anchor + sign * span * ratio for ratio in EXTENSIONS}
    )
    levels["0.000"] = anchor
    levels["1.000"] = low_price if upward else high_price

    return ok(
        levels,
        stale=_stale(frame),
        direction="up" if upward else "down",
        swing_high=high_price,
        swing_low=low_price,
        swing_high_at=high_at.isoformat(),
        swing_low_at=low_at.isoformat(),
        lookback=lookback,
    )
