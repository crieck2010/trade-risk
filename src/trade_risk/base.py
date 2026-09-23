"""Core value objects for the risk engine.

These are plain frozen dataclasses with no behaviour beyond small
helpers, so any caller (agent, backtest, gateway) can construct them
from whatever portfolio representation it already has.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from math import isfinite


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    EXIT = "EXIT"


# Convenience aliases matching the trade-strategies action vocabulary.
LONG = Side.LONG
SHORT = Side.SHORT
EXIT = Side.EXIT


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class OrderIntent:
    """Something the strategy/agent wants to do.

    ``quantity`` is unsigned; the side gives direction.  ``None``
    means "not sized yet" -- :class:`RiskManager` will ask its sizer
    to propose one when a sizer is configured.
    """

    symbol: str
    side: Side
    quantity: float | None = None
    price: float | None = None  # reference (mark) price for notional math
    timestamp: datetime = field(default_factory=_utcnow)

    def with_quantity(self, quantity: float) -> "OrderIntent":
        return replace(self, quantity=quantity)

    @property
    def is_exit(self) -> bool:
        return self.side is Side.EXIT


@dataclass(frozen=True)
class PositionState:
    """Mark-to-market snapshot of one position."""

    symbol: str
    quantity: float  # signed; >0 long, <0 short
    avg_price: float = 0.0
    market_price: float = 0.0

    @property
    def notional(self) -> float:
        return abs(self.quantity) * self.market_price

    @property
    def signed_notional(self) -> float:
        return self.quantity * self.market_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.market_price - self.avg_price) * self.quantity


@dataclass(frozen=True)
class PortfolioState:
    """Everything a limit needs to judge an intent."""

    timestamp: datetime = field(default_factory=_utcnow)
    cash: float = 0.0
    equity: float = 0.0
    positions: dict[str, PositionState] = field(default_factory=dict)
    day_start_equity: float | None = None  # for MaxDailyLoss
    peak_equity: float | None = None  # for drawdown limits

    def position(self, symbol: str) -> PositionState | None:
        return self.positions.get(symbol)

    def with_mark(self, symbol: str, price: float) -> "PortfolioState":
        """Return a copy with one position re-marked to ``price``."""
        pos = self.positions.get(symbol)
        if pos is None:
            return self
        positions = dict(self.positions)
        positions[symbol] = replace(pos, market_price=price)
        equity = self.cash + sum(p.signed_notional for p in positions.values())
        return replace(self, positions=positions, equity=equity)


@dataclass(frozen=True)
class RiskDecision:
    """The verdict of :class:`RiskManager.evaluate`.

    ``approved=False`` is a veto: do not submit the order.
    ``quantity`` is the final unsigned size to submit when approved
    (the sizer's proposal after every limit had its say).  It may be
    smaller than requested when a limit resized the intent.
    """

    approved: bool
    reason: str = ""
    quantity: float | None = None
    limit: str = ""  # name of the limit that vetoed/resized, "" if none

    def __bool__(self) -> bool:
        return self.approved


def valid_number(value: float | None, name: str) -> float:
    if value is None or not isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return float(value)


def require_positive(value: float, name: str) -> float:
    value = valid_number(value, name)
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value!r}")
    return value


def require_fraction(value: float, name: str) -> float:
    value = valid_number(value, name)
    if not 0 < value <= 1:
        raise ValueError(f"{name} must be in (0, 1], got {value!r}")
    return value
