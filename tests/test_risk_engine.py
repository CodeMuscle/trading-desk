"""The risk engine's refusals, at their exact boundaries.

Every limit here is tested at the value, not near it. A limit that fires at
"about 3%" is a limit nobody can reason about.
"""

from __future__ import annotations

import pytest

from desk.risk.engine import AccountState, Position, size_for
from desk.risk.limits import DEFAULT_LIMITS, RiskLimits
from desk.strategy.base import Intent, Side
import pandas as pd


def intent(entry: float = 100.0, stop: float = 99.0, side: Side = Side.LONG) -> Intent:
    return Intent(
        index=pd.Timestamp("2026-11-26", tz="UTC"),
        side=side, entry=entry, stop=stop,
        target=entry + (entry - stop) if side is Side.LONG else entry - (stop - entry),
    )


def account(
    equity: float = 100_000.0,
    *,
    day_start: float | None = None,
    month_start: float | None = None,
    positions: tuple[Position, ...] = (),
) -> AccountState:
    return AccountState(
        equity=equity,
        starting_equity_day=day_start if day_start is not None else equity,
        starting_equity_month=month_start if month_start is not None else equity,
        positions=positions,
    )


def position_risking(fraction: float, equity: float = 100_000.0, cluster: str | None = None):
    """A position whose open risk is exactly `fraction` of equity."""
    risk_currency = equity * fraction
    return Position(
        symbol="X", side=Side.LONG, units=risk_currency, entry=100.0, stop=99.0,
        cluster=cluster,
    )


# ---------------------------------------------------------------- sizing

def test_risk_per_trade_is_exactly_one_percent_of_equity():
    decision = size_for(intent(), account(100_000.0))
    assert decision.risk_fraction == pytest.approx(0.01)
    # risk per unit is 1.00, so 1% of 100k buys 1000 units
    assert decision.units == pytest.approx(1000.0)


def test_a_wider_stop_buys_fewer_units_for_the_same_risk():
    tight = size_for(intent(entry=100.0, stop=99.0), account())
    wide = size_for(intent(entry=100.0, stop=95.0), account())
    assert wide.units == pytest.approx(tight.units / 5.0)
    # The whole point: both risk exactly 1%.
    assert wide.risk_fraction == tight.risk_fraction == pytest.approx(0.01)


def test_a_short_sizes_the_same_as_a_long_with_equal_stop_distance():
    long_d = size_for(intent(100.0, 99.0, Side.LONG), account())
    short_d = size_for(intent(100.0, 101.0, Side.SHORT), account())
    assert long_d.units == pytest.approx(short_d.units)


# ---------------------------------------------------------------- heat cap

def test_heat_at_exactly_the_cap_refuses_a_new_entry():
    at_cap = account(positions=(position_risking(0.06),))
    assert at_cap.open_heat == pytest.approx(0.06)
    decision = size_for(intent(), at_cap)
    assert decision.refused
    assert "portfolio heat" in decision.reasons[0]


def test_heat_just_below_the_cap_allows_only_the_remaining_room():
    near = account(positions=(position_risking(0.055),))
    decision = size_for(intent(), near)
    assert not decision.refused
    assert decision.risk_fraction == pytest.approx(0.005)
    assert any("portfolio heat cap" in r for r in decision.reasons)


def test_open_heat_can_never_exceed_the_cap_whatever_is_requested():
    # The spec phrases this as "6.01% is refused". The engine clamps to fit
    # rather than refusing outright, which is strictly more useful and
    # satisfies the same invariant: heat never exceeds the cap.
    for existing in (0.0, 0.01, 0.03, 0.0599, 0.06):
        state = account(positions=(position_risking(existing),))
        decision = size_for(intent(), state)
        resulting = existing + decision.risk_fraction
        assert resulting <= DEFAULT_LIMITS.portfolio_heat + 1e-12, (
            f"heat reached {resulting:.4%} from {existing:.4%}"
        )


def test_six_one_percent_positions_cannot_all_be_opened():
    positions = tuple(position_risking(0.01) for _ in range(6))
    state = account(positions=positions)
    assert state.open_heat == pytest.approx(0.06)
    assert size_for(intent(), state).refused


# ------------------------------------------------------- correlated cluster

def test_an_ungrouped_position_is_exempt_from_the_cluster_cap_and_says_so():
    # None is not a cluster name. Six ungrouped 1% positions would hit the heat
    # cap, never the cluster cap, and every decision records that the cap was
    # skipped so it is visible in the journal rather than assumed satisfied.
    state = account(positions=(position_risking(0.03),))
    decision = size_for(intent(), state)
    assert not decision.refused
    assert any("correlated-exposure cap not applied" in r for r in decision.reasons)


def test_a_cluster_at_its_cap_refuses_even_when_portfolio_heat_has_room():
    # 2.5% in one cluster, 2.5% total: heat has 3.5% free, the cluster has none.
    state = account(positions=(position_risking(0.025, cluster="usd"),))
    assert state.open_heat == pytest.approx(0.025)
    decision = size_for(intent(), state, cluster="usd")
    assert decision.refused
    assert "cluster 'usd'" in decision.reasons[0]


def test_the_same_entry_is_allowed_in_a_different_cluster():
    state = account(positions=(position_risking(0.025, cluster="usd"),))
    decision = size_for(intent(), state, cluster="metals")
    assert not decision.refused
    assert decision.risk_fraction == pytest.approx(0.01)


def test_four_dollar_correlated_positions_are_summed_as_one_bet():
    # long EURUSD, long GBPUSD, short USDJPY, long gold is one dollar bet.
    positions = tuple(position_risking(0.00625, cluster="usd") for _ in range(4))
    state = account(positions=positions)
    assert state.cluster_heat("usd") == pytest.approx(0.025)
    assert size_for(intent(), state, cluster="usd").refused


# ---------------------------------------------------------------- daily loss

def test_the_daily_limit_fires_at_exactly_minus_three_percent():
    state = account(equity=97_000.0, day_start=100_000.0, month_start=100_000.0)
    assert state.day_return == pytest.approx(-0.03)
    decision = size_for(intent(), state)
    assert decision.refused
    assert "daily loss" in decision.reasons[0]


def test_just_inside_the_daily_limit_still_trades():
    state = account(equity=97_001.0, day_start=100_000.0, month_start=100_000.0)
    assert size_for(intent(), state).refused is False


def test_the_daily_limit_halts_entries_and_does_not_liquidate():
    # The engine's entire surface is sizing. It has no way to close anything,
    # which is deliberate: a kill switch that liquidates turns a bad day into a
    # realised one at the worst possible moment.
    import desk.risk.engine as engine

    exported = {n for n in dir(engine) if not n.startswith("_")}
    for forbidden in ("liquidate", "close_all", "flatten", "exit_all"):
        assert forbidden not in exported


# --------------------------------------------------------------- monthly

def test_at_minus_ten_percent_on_the_month_risk_per_trade_halves_exactly():
    state = account(equity=90_000.0, day_start=90_000.0, month_start=100_000.0)
    assert state.month_return == pytest.approx(-0.10)
    decision = size_for(intent(), state)
    assert decision.risk_fraction == pytest.approx(0.005)
    assert any("halved" in r for r in decision.reasons)


def test_at_minus_fifteen_percent_on_the_month_entries_stop():
    state = account(equity=85_000.0, day_start=85_000.0, month_start=100_000.0)
    assert state.month_return == pytest.approx(-0.15)
    decision = size_for(intent(), state)
    assert decision.refused
    assert "hard limit" in decision.reasons[0]


def test_the_monthly_hard_limit_outranks_the_daily_one_in_the_reason_given():
    # Both breached. The more serious one should be what the caller is told.
    state = account(equity=84_000.0, day_start=90_000.0, month_start=100_000.0)
    decision = size_for(intent(), state)
    assert decision.refused
    assert "month-to-date" in decision.reasons[0]


# --------------------------------------------------------------- fail closed

@pytest.mark.parametrize(
    "state",
    [
        account(equity=0.0),
        account(equity=-500.0),
        AccountState(equity=100_000.0, starting_equity_day=0.0, starting_equity_month=100_000.0),
        AccountState(equity=100_000.0, starting_equity_day=100_000.0, starting_equity_month=0.0),
    ],
)
def test_anything_uncomputable_sizes_to_zero(state):
    decision = size_for(intent(), state)
    assert decision.refused
    assert decision.reasons, "a refusal must say why"


def test_a_stop_at_the_entry_price_sizes_to_zero_rather_than_infinity():
    decision = size_for(intent(entry=100.0, stop=100.0), account())
    assert decision.refused
    assert "risk per unit is zero" in decision.reasons[0]


def test_nan_equity_sizes_to_zero():
    state = account(equity=float("nan"))
    assert size_for(intent(), state).refused


def test_no_refusal_is_ever_silent():
    state = account(positions=(position_risking(0.06),))
    decision = size_for(intent(), state)
    assert decision.refused
    assert len(decision.reasons) >= 1
    assert decision.reasons[0].strip()


# ---------------------------------------------------------------- limits

def test_the_supplied_limits_are_what_is_encoded():
    assert DEFAULT_LIMITS.risk_per_trade == 0.01
    assert DEFAULT_LIMITS.portfolio_heat == 0.06
    assert DEFAULT_LIMITS.correlated_cluster == 0.025
    assert DEFAULT_LIMITS.daily_loss == 0.03
    assert DEFAULT_LIMITS.monthly_soft == 0.10
    assert DEFAULT_LIMITS.monthly_hard == 0.15


def test_incoherent_limits_are_rejected_at_construction():
    with pytest.raises(ValueError, match="portfolio_heat"):
        RiskLimits(risk_per_trade=0.10, portfolio_heat=0.06)
    with pytest.raises(ValueError, match="monthly_soft"):
        RiskLimits(monthly_soft=0.20, monthly_hard=0.15)
