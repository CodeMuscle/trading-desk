"""The risk limits, as supplied on 25 Nov 2026.

Deterministic code, never an agent prompt. A limit a model can reason around is
not a limit, so nothing in this package consults anything but numbers.

Every limit here is a refusal, not a warning. The engine's only output is a
size, and the size is zero whenever a rule says no or whenever a rule cannot be
evaluated at all.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RiskLimits:
    risk_per_trade: float = 0.01
    """Fraction of equity risked between entry and stop on one position."""

    portfolio_heat: float = 0.06
    """Total open risk across all positions. Six concurrent positions at 1%.

    Deliberately above the daily loss limit. Setting it equal to the daily
    limit would make a simultaneous stop-out structurally unable to breach the
    day, but allows only three concurrent positions across four markets. The
    realistic version of that risk is correlation, which the cluster cap below
    defends against directly."""

    correlated_cluster: float = 0.025
    """Maximum open risk inside one correlated cluster.

    Six percent spread across six uncorrelated positions is six bets. The same
    six percent across long EURUSD, long GBPUSD, short USDJPY and long gold is
    one bet on dollar weakness wearing four hats, and it resolves in a single
    session."""

    daily_loss: float = 0.03
    """Realised loss that halts new entries for the session. Open positions
    keep their stops: a kill switch that liquidates turns a bad day into a
    realised one at the worst possible moment."""

    monthly_soft: float = 0.10
    """Month-to-date drawdown at which risk per trade halves."""

    monthly_hard: float = 0.15
    """Month-to-date drawdown at which new entries stop until the month rolls."""

    def __post_init__(self) -> None:
        if not 0 < self.risk_per_trade <= self.portfolio_heat:
            raise ValueError(
                "risk_per_trade must be positive and no greater than portfolio_heat"
            )
        if not 0 < self.correlated_cluster <= self.portfolio_heat:
            raise ValueError(
                "correlated_cluster must be positive and no greater than portfolio_heat"
            )
        if not 0 < self.monthly_soft < self.monthly_hard:
            raise ValueError("monthly_soft must be positive and below monthly_hard")
        if self.daily_loss <= 0:
            raise ValueError("daily_loss must be positive")


DEFAULT_LIMITS = RiskLimits()
