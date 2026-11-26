"""What a market adapter can and cannot provide.

Written in full before the first adapter exists so the interface does not
silently become crypto-shaped. Several members here are for markets that arrive
in later milestones: NSE has no aggressor-tagged ticks, has a session grid
anchored to a fixed open, has an expiring-contract symbol master, and publishes
participant-wise open interest that has no equivalent anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import Enum


class Capability(str, Enum):
    OHLCV = "ohlcv"
    AGGRESSOR_TICKS = "aggressor_ticks"
    L2_DEPTH = "l2_depth"
    FUNDAMENTALS = "fundamentals"
    SESSION_GRID = "session_grid"
    EXPIRING_SYMBOLS = "expiring_symbols"
    PARTICIPANT_OI = "participant_oi"
    BROKER_PAPER_API = "broker_paper_api"


@dataclass(frozen=True, slots=True)
class AdapterInfo:
    venue: str
    capabilities: frozenset[Capability]
    session_open: time | None = None

    def has(self, capability: Capability) -> bool:
        return capability in self.capabilities
