"""Confluence gate, ported from the user's Pine strategy.

Source: reference/pine/Forex_Confluence_AutoTrade_Pro.pine

Five conditions must all agree, which is a gate and not a score:

    rawBuy = trendUp and momUp and biasUp and breakoutUp and candleUp

plus a cooldown of N bars between signals, and entries only while flat.

Two deliberate departures from the Pine source, both recorded in
docs/superpowers/specs/2026-11-25-metrics-spine-design.md:

1. **TP1 cannot be marked on the entry bar.** The Pine sets `tp1Done := true`
   when `high >= tp1Level`, which on the entry bar reads that same bar's high.
   Pine evaluates on bar close, so that is an optimistic intrabar fill
   assumption and it inflates results. Here TP1 requires a bar strictly after
   entry.

2. **Sizing is not the template's business.** The Pine risks 100% of equity per
   trade, which decouples exposure from stop distance: a wide ATR stop and a
   tight one receive identical capital. This template emits a stop and lets the
   risk engine size it in M3.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from desk.metrics.l1_momentum import atr, macd, rsi
from desk.metrics.l1_trend import ema
from desk.metrics.l4_price_action import bearish_engulfing, bullish_engulfing, hammer, shooting_star
from desk.metrics.l4_structure import confirmed_swing_levels
from desk.result import MetricResult, failed, ok
from desk.store.frame import OHLCVFrame
from desk.strategy.base import Intent, ParamSpec, Side, StrategyResult

NAME = "confluence_gate"

PARAMS: tuple[ParamSpec, ...] = (
    ParamSpec("ema_fast", 20, 2, 100, 1, "Fast EMA"),
    ParamSpec("ema_slow", 50, 5, 300, 1, "Slow EMA"),
    ParamSpec("ema_bias", 200, 20, 500, 1, "Major trend EMA, bias filter"),
    ParamSpec("rsi_length", 14, 2, 50, 1, "RSI length"),
    ParamSpec("rsi_bull", 50.0, 30.0, 70.0, 1.0, "RSI threshold for longs"),
    ParamSpec("rsi_bear", 50.0, 30.0, 70.0, 1.0, "RSI threshold for shorts"),
    ParamSpec("swing_lookback", 10, 2, 50, 1, "Swing high/low lookback"),
    ParamSpec("cooldown_bars", 5, 0, 50, 1, "Minimum bars between signals"),
    ParamSpec("atr_length", 14, 2, 50, 1, "ATR length"),
    ParamSpec("atr_stop_mult", 1.5, 0.2, 10.0, 0.1, "Initial stop = ATR x"),
    ParamSpec("tp1_r_multiple", 1.0, 0.2, 10.0, 0.1, "Book partial at R multiple"),
    ParamSpec("use_trend_filter", True, help="Require price beyond the bias EMA"),
    ParamSpec("use_breakout", True, help="Require a close beyond the last swing"),
    ParamSpec("use_candle", True, help="Require candlestick confirmation"),
)

DEFAULTS: dict[str, object] = {p.name: p.default for p in PARAMS}


def _stale(frame: OHLCVFrame) -> bool:
    try:
        return frame.is_stale(now=datetime.now(timezone.utc))
    except ValueError:
        return False


def run(frame: OHLCVFrame, **overrides: object) -> MetricResult:
    """Evaluate the gate across a frame and return entry intents.

    The gate itself is vectorised, because every condition is a per-bar truth.
    Cooldown and the flat-only rule are sequential, because they depend on what
    already happened, so those run in a single forward pass that reads no bar
    beyond the one it is standing on.
    """
    params = dict(DEFAULTS)
    unknown = set(overrides) - set(params)
    if unknown:
        return failed(ValueError(f"unknown parameters: {sorted(unknown)}"))
    params.update(overrides)
    for spec in PARAMS:
        try:
            spec.validate(params[spec.name])
        except ValueError as exc:
            return failed(exc)

    pieces = {
        "ema_fast": ema(frame, int(params["ema_fast"])),
        "ema_slow": ema(frame, int(params["ema_slow"])),
        "ema_bias": ema(frame, int(params["ema_bias"])),
        "rsi": rsi(frame, int(params["rsi_length"])),
        "macd": macd(frame),
        "atr": atr(frame, int(params["atr_length"])),
        "swings": confirmed_swing_levels(frame, int(params["swing_lookback"])),
        "bull_engulf": bullish_engulfing(frame),
        "bear_engulf": bearish_engulfing(frame),
        "hammer": hammer(frame),
        "star": shooting_star(frame),
    }
    for name, result in pieces.items():
        if not result.is_ok:
            # A missing input is not a missing signal. Say which one and why,
            # rather than returning an empty intent list that reads as "no
            # setups here".
            if result.is_unavailable:
                return result
            return failed(ValueError(f"{name} failed: {result.error}"))

    close = frame.data["close"]
    fast, slow, bias = (
        pieces["ema_fast"].value,
        pieces["ema_slow"].value,
        pieces["ema_bias"].value,
    )
    rsi_values = pieces["rsi"].value
    hist = pieces["macd"].value["hist"]
    atr_values = pieces["atr"].value
    swing_high = pieces["swings"].value["high"]
    swing_low = pieces["swings"].value["low"]

    use_trend = bool(params["use_trend_filter"])
    use_breakout = bool(params["use_breakout"])
    use_candle = bool(params["use_candle"])

    gates = pd.DataFrame(index=frame.data.index)
    gates["trend_up"] = fast > slow
    gates["trend_down"] = fast < slow
    gates["mom_up"] = (rsi_values > float(params["rsi_bull"])) & (hist > 0)
    gates["mom_down"] = (rsi_values < float(params["rsi_bear"])) & (hist < 0)
    gates["bias_up"] = (close > bias) if use_trend else True
    gates["bias_down"] = (close < bias) if use_trend else True
    gates["breakout_up"] = (
        (swing_high.notna() & (close > swing_high)) if use_breakout else True
    )
    gates["breakout_down"] = (
        (swing_low.notna() & (close < swing_low)) if use_breakout else True
    )
    gates["candle_up"] = (
        (pieces["bull_engulf"].value | pieces["hammer"].value) if use_candle else True
    )
    gates["candle_down"] = (
        (pieces["bear_engulf"].value | pieces["star"].value) if use_candle else True
    )

    up_columns = ["trend_up", "mom_up", "bias_up", "breakout_up", "candle_up"]
    down_columns = ["trend_down", "mom_down", "bias_down", "breakout_down", "candle_down"]
    gates["raw_buy"] = gates[up_columns].all(axis=1)
    gates["raw_sell"] = gates[down_columns].all(axis=1)

    intents = _walk(
        frame=frame,
        gates=gates,
        atr_values=atr_values,
        cooldown=int(params["cooldown_bars"]),
        stop_mult=float(params["atr_stop_mult"]),
        tp1_r=float(params["tp1_r_multiple"]),
    )
    return ok(
        StrategyResult(intents=intents, gates=gates),
        stale=_stale(frame),
        params=params,
    )


def _walk(
    *,
    frame: OHLCVFrame,
    gates: pd.DataFrame,
    atr_values: pd.Series,
    cooldown: int,
    stop_mult: float,
    tp1_r: float,
) -> list[Intent]:
    """One forward pass applying cooldown and the flat-only rule.

    Reads nothing beyond the bar it is on. A position opened at bar i is
    resolved by later bars only, and TP1 requires a bar strictly after entry,
    which is the Pine defect this port corrects.
    """
    index = frame.data.index
    close = frame.data["close"].to_numpy()
    high = frame.data["high"].to_numpy()
    low = frame.data["low"].to_numpy()
    raw_buy = gates["raw_buy"].to_numpy()
    raw_sell = gates["raw_sell"].to_numpy()
    atr_array = atr_values.to_numpy()

    intents: list[Intent] = []
    last_signal_bar: int | None = None
    open_side: Side | None = None
    open_stop = 0.0
    open_target = 0.0

    for i in range(len(index)):
        if open_side is not None:
            # Resolve on bars strictly after entry. Both levels touched in the
            # same bar is ambiguous without tick data, so the stop is assumed
            # first: the pessimistic reading, not the flattering one.
            if open_side is Side.LONG:
                if low[i] <= open_stop or high[i] >= open_target:
                    open_side = None
            else:
                if high[i] >= open_stop or low[i] <= open_target:
                    open_side = None
            continue

        if atr_array[i] != atr_array[i]:  # NaN during the ATR warmup
            continue
        if last_signal_bar is not None and (i - last_signal_bar) < cooldown:
            continue

        side: Side | None = None
        if raw_buy[i]:
            side = Side.LONG
        elif raw_sell[i]:
            side = Side.SHORT
        if side is None:
            continue

        entry = float(close[i])
        risk = float(atr_array[i]) * stop_mult
        if risk <= 0:
            continue
        if side is Side.LONG:
            stop, target = entry - risk, entry + risk * tp1_r
        else:
            stop, target = entry + risk, entry - risk * tp1_r

        intents.append(
            Intent(
                index=index[i],
                side=side,
                entry=entry,
                stop=stop,
                target=target,
                reason={c: bool(gates.iloc[i][c]) for c in gates.columns},
            )
        )
        last_signal_bar = i
        open_side, open_stop, open_target = side, stop, target

    return intents
