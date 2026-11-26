"""What a trade actually costs, applied always and with no way to turn it off.

There is no gross-returns view anywhere in this package. A gross equity curve
on a strategy that trades often is not an optimistic estimate of the same
strategy; it is a different and false one. The honest number is the only number
available.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CostModel:
    commission_bps: float = 10.0
    """Basis points of notional, charged per side. 10 bps is a realistic
    crypto taker fee; an exchange-specific figure belongs here when the venue
    is known."""

    slippage_atr_fraction: float = 0.05
    """Slippage as a fraction of ATR, applied against you on entry and exit.

    Denominated in ATR rather than ticks because slippage scales with
    volatility: the same order fills worse in a fast market, and a fixed tick
    assumption flatters exactly the conditions where a strategy gets hurt."""

    def commission(self, notional: float) -> float:
        return abs(notional) * self.commission_bps / 10_000.0

    def slippage(self, atr: float) -> float:
        """Price slippage per unit, always adverse."""
        return abs(atr) * self.slippage_atr_fraction


ZERO_COSTS = CostModel(commission_bps=0.0, slippage_atr_fraction=0.0)
"""Only for tests that need to isolate strategy logic from cost arithmetic.
Never for reporting a result."""
