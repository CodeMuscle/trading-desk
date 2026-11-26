import pandas as pd
import pytest

from desk.strategy.base import Side
from desk.strategy.confluence import DEFAULTS, PARAMS, run
from tests.fixtures import frame_from_rows


def trending_frame(bars: int = 400, up: bool = True):
    """A frame with enough history for a 200 EMA and a clear direction."""
    rows = []
    price = 100.0
    for i in range(bars):
        drift = (0.5 if up else -0.5) * (1.0 if i > bars * 0.4 else 0.1)
        o = price
        c = o + drift + ((i * 31) % 7 - 3) * 0.08
        rows.append(
            {
                "open": o,
                "high": max(o, c) + 0.4 + (i % 5) * 0.05,
                "low": min(o, c) - 0.4 - (i % 3) * 0.05,
                "close": c,
                "volume": 100.0 + (i % 11) * 5,
            }
        )
        price = c
    return frame_from_rows(rows)


def test_every_parameter_has_a_default_and_they_match():
    assert set(DEFAULTS) == {p.name for p in PARAMS}
    for spec in PARAMS:
        assert DEFAULTS[spec.name] == spec.default


def test_an_unknown_parameter_is_an_error_not_silently_ignored():
    result = run(trending_frame(), ema_fastt=20)
    assert result.is_error
    assert "unknown parameters" in str(result.error)


def test_a_parameter_outside_its_declared_range_is_an_error():
    result = run(trending_frame(), atr_stop_mult=99.0)
    assert result.is_error
    assert "above its maximum" in str(result.error)


def test_a_short_frame_reports_which_input_was_unavailable():
    short = frame_from_rows(
        [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}] * 30
    )
    result = run(short)
    assert result.is_unavailable
    assert "bars" in result.reason


def test_the_gate_matrix_has_a_column_for_every_pine_condition():
    result = run(trending_frame())
    assert result.is_ok
    gates = result.value.gates
    for name in (
        "trend_up", "mom_up", "bias_up", "breakout_up", "candle_up",
        "trend_down", "mom_down", "bias_down", "breakout_down", "candle_down",
        "raw_buy", "raw_sell",
    ):
        assert name in gates.columns


def test_raw_buy_is_the_and_of_all_five_long_conditions_not_a_score():
    gates = run(trending_frame()).value.gates
    expected = (
        gates["trend_up"] & gates["mom_up"] & gates["bias_up"]
        & gates["breakout_up"] & gates["candle_up"]
    )
    pd.testing.assert_series_equal(gates["raw_buy"], expected, check_names=False)


def test_four_of_five_conditions_does_not_produce_a_signal():
    gates = run(trending_frame()).value.gates
    four_true = (
        gates[["trend_up", "mom_up", "bias_up", "breakout_up", "candle_up"]].sum(axis=1) == 4
    )
    assert not gates.loc[four_true, "raw_buy"].any()


def test_every_intent_has_a_stop_on_the_losing_side_of_entry():
    for intent in run(trending_frame()).value.intents:
        if intent.side is Side.LONG:
            assert intent.stop < intent.entry < intent.target
        else:
            assert intent.target < intent.entry < intent.stop


def test_risk_is_the_distance_from_entry_to_stop():
    for intent in run(trending_frame()).value.intents:
        assert intent.risk == pytest.approx(abs(intent.entry - intent.stop))
        assert intent.risk > 0


def test_the_target_sits_at_the_requested_r_multiple():
    result = run(trending_frame(), tp1_r_multiple=2.0)
    for intent in result.value.intents:
        reward = abs(intent.target - intent.entry)
        assert reward == pytest.approx(intent.risk * 2.0)


def test_cooldown_is_respected_between_consecutive_intents():
    frame = trending_frame()
    result = run(frame, cooldown_bars=20)
    positions = [frame.data.index.get_loc(i.index) for i in result.value.intents]
    for earlier, later in zip(positions, positions[1:]):
        assert later - earlier >= 20


def test_no_second_entry_opens_while_one_is_still_running():
    # Every intent must be separated by at least one bar, because the walk
    # cannot open a position while another is unresolved.
    frame = trending_frame()
    result = run(frame)
    positions = [frame.data.index.get_loc(i.index) for i in result.value.intents]
    assert len(positions) == len(set(positions))
    for earlier, later in zip(positions, positions[1:]):
        assert later > earlier


def test_disabling_a_filter_can_only_widen_the_gate():
    frame = trending_frame()
    strict = len(run(frame).value.intents)
    loose = len(run(frame, use_candle=False).value.intents)
    assert loose >= strict


def test_failing_gates_explains_why_a_bar_produced_no_signal():
    frame = trending_frame()
    result = run(frame).value
    quiet = result.gates.index[~result.gates["raw_buy"]][50]
    failing = result.failing_gates_at(quiet, Side.LONG)
    assert failing, "a bar with no buy signal must have at least one failing gate"
    assert all(name.endswith("_up") for name in failing)


def test_no_intent_lands_during_the_atr_warmup():
    frame = trending_frame()
    from desk.metrics.l1_momentum import atr

    warm = atr(frame, 14).value
    for intent in run(frame).value.intents:
        assert not pd.isna(warm.loc[intent.index])
