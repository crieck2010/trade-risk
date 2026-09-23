"""Risk-based position sizers: intent -> unsigned quantity.

A sizer answers "how big?" from a :class:`SizingContext`.  Sizers never
veto -- that is the limits' job -- they only propose a size.  Direction
(LONG/SHORT/EXIT) is applied by the caller.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .base import require_fraction, require_positive, valid_number


@dataclass(frozen=True)
class SizingContext:
    """Everything a sizer may consult.  Fields default to ``None``
    when the caller cannot supply them; each sizer documents what it
    requires."""

    equity: float = 0.0
    price: float = 0.0  # reference price of the instrument
    atr: float | None = None  # average true range, price units
    volatility: float | None = None  # per-period stdev of returns, fraction
    win_prob: float | None = None  # estimated win rate, for Kelly
    payoff: float | None = None  # avg win / avg loss, for Kelly


class RiskSizer(ABC):
    """Proposes an unsigned quantity for a new position."""

    name: str = "base"
    description: str = ""
    DEFAULT_PARAMS: dict = {}

    @abstractmethod
    def quantity(self, ctx: SizingContext) -> float:
        raise NotImplementedError

    def with_params(self, **params) -> "RiskSizer":
        clone = self.__class__(**{**self._params(), **params})
        return clone

    def _params(self) -> dict:
        return dict(getattr(self, "_init_params", {}))

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "params": self._params(),
            "defaults": dict(self.DEFAULT_PARAMS),
        }


class FixedFractionalSizer(RiskSizer):
    """Risk ``risk_pct`` of equity per trade.

    Quantity = ``equity * risk_pct / stop_distance`` where the stop
    distance is ``atr_multiple * atr`` (needs ``ctx.atr``).
    """

    name = "fixed_fractional"
    description = "Size so a stop-out loses risk_pct of equity (ATR stop)."
    DEFAULT_PARAMS = {"risk_pct": 0.01, "atr_multiple": 2.0}

    def __init__(self, risk_pct: float = 0.01, atr_multiple: float = 2.0) -> None:
        self.risk_pct = require_fraction(risk_pct, "risk_pct")
        self.atr_multiple = require_positive(atr_multiple, "atr_multiple")
        self._init_params = {"risk_pct": self.risk_pct, "atr_multiple": self.atr_multiple}

    def quantity(self, ctx: SizingContext) -> float:
        if ctx.atr is None or ctx.atr <= 0 or ctx.equity <= 0:
            return 0.0
        stop_distance = ctx.atr * self.atr_multiple
        return ctx.equity * self.risk_pct / stop_distance


class VolatilityTargetSizer(RiskSizer):
    """Size so the position contributes ``target_vol`` (annualised).

    Quantity = ``equity * target_vol / (volatility * price)``.
    Needs ``ctx.volatility`` as a per-period fraction in the same
    period as ``target_vol`` (e.g. both annualised).
    """

    name = "volatility_target"
    description = "Scale size inversely to asset volatility for a vol target."
    DEFAULT_PARAMS = {"target_vol": 0.15}

    def __init__(self, target_vol: float = 0.15) -> None:
        self.target_vol = require_positive(target_vol, "target_vol")
        self._init_params = {"target_vol": self.target_vol}

    def quantity(self, ctx: SizingContext) -> float:
        if not ctx.volatility or ctx.volatility <= 0 or ctx.price <= 0 or ctx.equity <= 0:
            return 0.0
        return ctx.equity * self.target_vol / (ctx.volatility * ctx.price)


class EqualWeightSizer(RiskSizer):
    """Split equity evenly across ``n_positions`` slots."""

    name = "equal_weight"
    description = "1/n of equity per position."
    DEFAULT_PARAMS = {"n_positions": 10}

    def __init__(self, n_positions: int = 10) -> None:
        if n_positions < 1:
            raise ValueError(f"n_positions must be >= 1, got {n_positions!r}")
        self.n_positions = int(n_positions)
        self._init_params = {"n_positions": self.n_positions}

    def quantity(self, ctx: SizingContext) -> float:
        if ctx.price <= 0 or ctx.equity <= 0:
            return 0.0
        return ctx.equity / self.n_positions / ctx.price


class KellySizer(RiskSizer):
    """Fractional Kelly: ``f = fraction * (p - (1-p)/payoff)``.

    Quantity = ``equity * min(f, cap) / price``.  Needs ``ctx.win_prob``
    and ``ctx.payoff`` (avg win / avg loss); falls back to 0 when the
    edge is non-positive.
    """

    name = "kelly"
    description = "Fractional Kelly criterion from win rate and payoff ratio."
    DEFAULT_PARAMS = {"fraction": 0.5, "cap": 0.25}

    def __init__(self, fraction: float = 0.5, cap: float = 0.25) -> None:
        self.fraction = require_positive(fraction, "fraction")
        self.cap = require_fraction(cap, "cap")
        self._init_params = {"fraction": self.fraction, "cap": self.cap}

    def quantity(self, ctx: SizingContext) -> float:
        p = ctx.win_prob
        b = ctx.payoff
        if p is None or b is None or b <= 0 or ctx.price <= 0 or ctx.equity <= 0:
            return 0.0
        p = valid_number(p, "win_prob")
        edge = p - (1.0 - p) / b
        if edge <= 0:
            return 0.0
        f = min(self.fraction * edge, self.cap)
        return ctx.equity * f / ctx.price


class FixedNotionalSizer(RiskSizer):
    """Target a fixed notional ``notional`` per position."""

    name = "fixed_notional"
    description = "Constant dollar exposure per position."
    DEFAULT_PARAMS = {"notional": 10_000.0}

    def __init__(self, notional: float = 10_000.0) -> None:
        self.notional = require_positive(notional, "notional")
        self._init_params = {"notional": self.notional}

    def quantity(self, ctx: SizingContext) -> float:
        if ctx.price <= 0:
            return 0.0
        return self.notional / ctx.price
