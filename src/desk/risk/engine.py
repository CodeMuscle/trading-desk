"""Position sizing and the refusals that precede it.

The engine answers one question: how much of this instrument may be bought or
sold right now. Every answer is a number of units and a reason, and the number
is zero whenever any limit says no.

Fail-closed is the governing rule. If equity is unknown, if the stop distance
is zero, if a correlation cannot be computed, if anything at all cannot be
evaluated, the size is zero. There is no last-known-good and no default
percentage, because both of those are a system continuing to trade while blind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from desk.risk.limits import DEFAULT_LIMITS, RiskLimits
from desk.strategy.base import Intent, Side


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    side: Side
    units: float
    entry: float
    stop: float
    cluster: str | None = None
    """Which correlated group this belongs to, or None when ungrouped.

    Positions sharing a named cluster are summed against the cluster cap.
    None means no correlation group is known, and such positions are exempt
    from that cap.

    None is deliberately not a cluster name. An earlier version defaulted this
    to the string "uncorrelated", which summed every supposedly-uncorrelated
    position into a single group and so treated them as correlated with each
    other: precisely backwards, and it refused legitimate entries.

    In M3 the cluster is supplied by the caller. From the NSE milestone it
    comes from the L0 pairwise correlation matrix rather than being assumed by
    asset class."""

    @property
    def open_risk(self) -> float:
        """Currency at risk if this position stops out."""
        return abs(self.entry - self.stop) * self.units


@dataclass(frozen=True, slots=True)
class AccountState:
    equity: float
    starting_equity_day: float
    starting_equity_month: float
    positions: tuple[Position, ...] = ()
    as_of: date | None = None

    @property
    def open_heat(self) -> float:
        """Total open risk as a fraction of equity."""
        if self.equity <= 0:
            return float("inf")
        return sum(p.open_risk for p in self.positions) / self.equity

    def cluster_heat(self, cluster: str | None) -> float:
        """Open risk inside one named cluster. Ungrouped positions count toward
        no cluster, so an unnamed cluster has no heat by definition."""
        if cluster is None:
            return 0.0
        if self.equity <= 0:
            return float("inf")
        return (
            sum(p.open_risk for p in self.positions if p.cluster == cluster)
            / self.equity
        )

    @property
    def day_return(self) -> float:
        if self.starting_equity_day <= 0:
            return float("-inf")
        return (self.equity - self.starting_equity_day) / self.starting_equity_day

    @property
    def month_return(self) -> float:
        if self.starting_equity_month <= 0:
            return float("-inf")
        return (self.equity - self.starting_equity_month) / self.starting_equity_month


@dataclass(frozen=True, slots=True)
class SizeDecision:
    units: float
    risk_fraction: float
    reasons: tuple[str, ...] = field(default_factory=tuple)
    """Why the size is what it is. Populated on refusal and on reduction, so a
    zero is never silent."""

    @property
    def refused(self) -> bool:
        return self.units == 0.0


def _refuse(reason: str) -> SizeDecision:
    return SizeDecision(units=0.0, risk_fraction=0.0, reasons=(reason,))


def size_for(
    intent: Intent,
    account: AccountState,
    *,
    cluster: str | None = None,
    limits: RiskLimits = DEFAULT_LIMITS,
) -> SizeDecision:
    """How many units of this intent may be opened.

    Checked in order of severity: hard stops first, then scaling, then the
    capacity caps. Each check that fails returns immediately, so the reason a
    caller sees is the most serious one rather than the last one evaluated.
    """
    notes: list[str] = []

    # --- things that make the question unanswerable ---
    if account.equity <= 0:
        return _refuse("equity is zero or negative")
    if not (account.equity == account.equity):  # NaN
        return _refuse("equity is not a number")
    if intent.risk <= 0:
        return _refuse("stop is at or beyond entry, so risk per unit is zero")
    if account.starting_equity_day <= 0 or account.starting_equity_month <= 0:
        return _refuse("period starting equity is unknown, cannot evaluate limits")

    # --- hard halts ---
    if account.month_return <= -limits.monthly_hard:
        return _refuse(
            f"month-to-date {account.month_return:.2%} at or beyond the hard "
            f"limit of -{limits.monthly_hard:.0%}; no new entries until the month rolls"
        )
    if account.day_return <= -limits.daily_loss:
        return _refuse(
            f"daily loss {account.day_return:.2%} at or beyond the limit of "
            f"-{limits.daily_loss:.0%}; new entries halted for the session"
        )

    # --- drawdown scaling ---
    risk_fraction = limits.risk_per_trade
    if account.month_return <= -limits.monthly_soft:
        risk_fraction /= 2.0
        notes.append(
            f"month-to-date {account.month_return:.2%} past -{limits.monthly_soft:.0%}; "
            "risk per trade halved"
        )

    # --- capacity caps ---
    heat_room = limits.portfolio_heat - account.open_heat
    if heat_room <= 0:
        return _refuse(
            f"portfolio heat {account.open_heat:.2%} already at the "
            f"{limits.portfolio_heat:.0%} cap"
        )
    if cluster is None:
        # Recorded on every decision rather than passed over, so an entry that
        # skipped the correlated-exposure cap is visible in the journal instead
        # of looking like one that satisfied it.
        notes.append("no cluster supplied; correlated-exposure cap not applied")
        cluster_room = float("inf")
    else:
        cluster_room = limits.correlated_cluster - account.cluster_heat(cluster)
        if cluster_room <= 0:
            return _refuse(
                f"cluster {cluster!r} heat {account.cluster_heat(cluster):.2%} already at "
                f"the {limits.correlated_cluster:.1%} cap"
            )

    allowed = min(risk_fraction, heat_room, cluster_room)
    if allowed < risk_fraction:
        notes.append(
            f"reduced from {risk_fraction:.2%} to {allowed:.2%} by the "
            + ("portfolio heat cap" if heat_room <= cluster_room else f"cluster {cluster!r} cap")
        )

    units = (account.equity * allowed) / intent.risk
    if units <= 0:
        return _refuse("computed size rounded to zero")

    return SizeDecision(units=units, risk_fraction=allowed, reasons=tuple(notes))
