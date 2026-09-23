"""Pre-trade risk limits: the risk-manager veto.

Each limit inspects an :class:`OrderIntent` against a
:class:`PortfolioState` and returns a :class:`RiskDecision`.  Limits
never block EXIT intents (flattening must always be possible) except
by resizing them to the actual position size.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from . import analytics
from .base import (
    EXIT,
    OrderIntent,
    PortfolioState,
    RiskDecision,
    require_fraction,
    require_positive,
    valid_number,
)


class RiskLimit(ABC):
    """One check in the risk stack."""

    name: str = "base"
    description: str = ""
    DEFAULT_PARAMS: dict = {}

    @abstractmethod
    def check(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        """Return ``RiskDecision(True)`` to pass, ``(False, reason)`` to veto."""
        raise NotImplementedError

    def _params(self) -> dict:
        return dict(getattr(self, "_init_params", {}))

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "params": self._params(),
            "defaults": dict(self.DEFAULT_PARAMS),
        }

    @staticmethod
    def _pass() -> RiskDecision:
        return RiskDecision(True)

    def _veto(self, reason: str) -> RiskDecision:
        return RiskDecision(False, reason=reason, limit=self.name)


def _intent_notional(intent: OrderIntent) -> float:
    if intent.quantity is None or intent.price is None:
        return 0.0
    return abs(intent.quantity) * intent.price


class MaxPositionNotional(RiskLimit):
    """Single-symbol notional (existing + intent) <= ``max_pct`` of equity."""

    name = "max_position_notional"
    description = "Cap per-symbol notional as a fraction of equity."
    DEFAULT_PARAMS = {"max_pct": 0.20}

    def __init__(self, max_pct: float = 0.20) -> None:
        self.max_pct = require_fraction(max_pct, "max_pct")
        self._init_params = {"max_pct": self.max_pct}

    def check(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        if intent.is_exit or state.equity <= 0:
            return self._pass()
        existing = state.position(intent.symbol)
        current = existing.notional if existing else 0.0
        if current + _intent_notional(intent) > state.equity * self.max_pct:
            return self._veto(
                f"{intent.symbol} notional would exceed {self.max_pct:.0%} of equity"
            )
        return self._pass()


class MaxGrossExposure(RiskLimit):
    """Sum of |notionals| (incl. intent) <= ``max_pct`` of equity."""

    name = "max_gross_exposure"
    description = "Cap total gross exposure as a fraction of equity."
    DEFAULT_PARAMS = {"max_pct": 1.0}

    def __init__(self, max_pct: float = 1.0) -> None:
        self.max_pct = require_positive(max_pct, "max_pct")
        self._init_params = {"max_pct": self.max_pct}

    def check(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        if intent.is_exit or state.equity <= 0:
            return self._pass()
        gross = sum(p.notional for p in state.positions.values())
        if gross + _intent_notional(intent) > state.equity * self.max_pct:
            return self._veto(f"gross exposure would exceed {self.max_pct:.0%} of equity")
        return self._pass()


class MaxNetExposure(RiskLimit):
    """|net signed notional| (incl. intent) <= ``max_pct`` of equity."""

    name = "max_net_exposure"
    description = "Cap directional (net) exposure as a fraction of equity."
    DEFAULT_PARAMS = {"max_pct": 0.80}

    def __init__(self, max_pct: float = 0.80) -> None:
        self.max_pct = require_positive(max_pct, "max_pct")
        self._init_params = {"max_pct": self.max_pct}

    def check(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        if intent.is_exit or state.equity <= 0:
            return self._pass()
        net = sum(p.signed_notional for p in state.positions.values())
        if intent.quantity is not None and intent.price is not None:
            signed = intent.quantity * intent.price
            if intent.side is not None and intent.side.name == "SHORT":
                signed = -signed
            net += signed
        if abs(net) > state.equity * self.max_pct:
            return self._veto(f"net exposure would exceed {self.max_pct:.0%} of equity")
        return self._pass()


class ConcentrationLimit(RiskLimit):
    """Largest position <= ``max_pct`` of gross notional after the intent."""

    name = "concentration"
    description = "Cap the largest position as a fraction of gross notional."
    DEFAULT_PARAMS = {"max_pct": 0.35}

    def __init__(self, max_pct: float = 0.35) -> None:
        self.max_pct = require_fraction(max_pct, "max_pct")
        self._init_params = {"max_pct": self.max_pct}

    def check(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        if intent.is_exit:
            return self._pass()
        notionals = {s: p.notional for s, p in state.positions.items()}
        notionals[intent.symbol] = notionals.get(intent.symbol, 0.0) + _intent_notional(intent)
        gross = sum(notionals.values())
        if gross > 0 and max(notionals.values()) / gross > self.max_pct:
            return self._veto(
                f"{intent.symbol} would exceed {self.max_pct:.0%} of gross notional"
            )
        return self._pass()


class MaxOrderNotional(RiskLimit):
    """Resize (never veto) single orders to ``max_notional``."""

    name = "max_order_notional"
    description = "Cap single-order notional by resizing the intent."
    DEFAULT_PARAMS = {"max_notional": 50_000.0}

    def __init__(self, max_notional: float = 50_000.0) -> None:
        self.max_notional = require_positive(max_notional, "max_notional")
        self._init_params = {"max_notional": self.max_notional}

    def check(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        notional = _intent_notional(intent)
        if notional <= self.max_notional or not intent.price:
            return self._pass()
        resized = self.max_notional / intent.price
        return RiskDecision(
            True,
            reason=f"order resized to ${self.max_notional:,.0f} cap",
            quantity=resized,
            limit=self.name,
        )


class SymbolAllowlist(RiskLimit):
    """Veto any symbol not in ``symbols``."""

    name = "symbol_allowlist"
    description = "Only trade explicitly approved symbols."
    DEFAULT_PARAMS = {"symbols": []}

    def __init__(self, symbols: list[str]) -> None:
        self.symbols = set(symbols)
        self._init_params = {"symbols": sorted(self.symbols)}

    def check(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        if intent.symbol in self.symbols:
            return self._pass()
        return self._veto(f"{intent.symbol} is not on the allowlist")


class SymbolBlocklist(RiskLimit):
    """Veto any symbol in ``symbols``."""

    name = "symbol_blocklist"
    description = "Never trade blocked symbols."
    DEFAULT_PARAMS = {"symbols": []}

    def __init__(self, symbols: list[str]) -> None:
        self.symbols = set(symbols)
        self._init_params = {"symbols": sorted(self.symbols)}

    def check(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        if intent.symbol in self.symbols:
            return self._veto(f"{intent.symbol} is blocklisted")
        return self._pass()


class MinPriceFilter(RiskLimit):
    """Veto entries in instruments priced below ``min_price``."""

    name = "min_price"
    description = "Skip penny/sub-dollar instruments on entry."
    DEFAULT_PARAMS = {"min_price": 1.0}

    def __init__(self, min_price: float = 1.0) -> None:
        self.min_price = require_positive(min_price, "min_price")
        self._init_params = {"min_price": self.min_price}

    def check(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        if intent.is_exit or intent.price is None:
            return self._pass()
        if intent.price < self.min_price:
            return self._veto(f"{intent.symbol} @ {intent.price} below min price {self.min_price}")
        return self._pass()


class MaxDailyLoss(RiskLimit):
    """Block new entries once today's loss exceeds ``max_loss_pct``.

    Needs ``state.day_start_equity``; passes silently when it is absent.
    """

    name = "max_daily_loss"
    description = "Halt new entries after a daily loss limit is hit."
    DEFAULT_PARAMS = {"max_loss_pct": 0.03}

    def __init__(self, max_loss_pct: float = 0.03) -> None:
        self.max_loss_pct = require_fraction(max_loss_pct, "max_loss_pct")
        self._init_params = {"max_loss_pct": self.max_loss_pct}

    def check(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        if intent.is_exit or not state.day_start_equity or state.day_start_equity <= 0:
            return self._pass()
        day_pnl = (state.equity - state.day_start_equity) / state.day_start_equity
        if day_pnl <= -self.max_loss_pct:
            return self._veto(f"daily loss {day_pnl:.2%} breached {self.max_loss_pct:.0%} limit")
        return self._pass()


class MaxDrawdown(RiskLimit):
    """Block new entries once drawdown from ``peak_equity`` exceeds ``max_dd``."""

    name = "max_drawdown"
    description = "Halt new entries after a max-drawdown breach."
    DEFAULT_PARAMS = {"max_dd": 0.15}

    def __init__(self, max_dd: float = 0.15) -> None:
        self.max_dd = require_fraction(max_dd, "max_dd")
        self._init_params = {"max_dd": self.max_dd}

    def check(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        if intent.is_exit or not state.peak_equity or state.peak_equity <= 0:
            return self._pass()
        dd = (state.peak_equity - state.equity) / state.peak_equity
        if dd >= self.max_dd:
            return self._veto(f"drawdown {dd:.2%} breached {self.max_dd:.0%} limit")
        return self._pass()


class DrawdownGuard(RiskLimit):
    """Tiered drawdown response.

    - below ``warn``: pass
    - ``warn`` <= dd < ``halt``: pass, but flag (reason notes the warning)
    - ``halt`` <= dd < ``flatten``: veto new entries
    - dd >= ``flatten``: veto entries; the manager surfaces
      :attr:`RiskManager.flatten_requested` so the caller can liquidate.

    Needs ``state.peak_equity``; passes silently when it is absent.
    """

    name = "drawdown_guard"
    description = "Tiered warn / halt / flatten response to drawdown."
    DEFAULT_PARAMS = {"warn": 0.05, "halt": 0.10, "flatten": 0.15}

    def __init__(self, warn: float = 0.05, halt: float = 0.10, flatten: float = 0.15) -> None:
        for label, value in (("warn", warn), ("halt", halt), ("flatten", flatten)):
            valid_number(value, label)
        if not 0 < warn < halt < flatten <= 1:
            raise ValueError("need 0 < warn < halt < flatten <= 1")
        self.warn, self.halt, self.flatten = warn, halt, flatten
        self._init_params = {"warn": warn, "halt": halt, "flatten": flatten}
        self.triggered_flatten = False

    def drawdown(self, state: PortfolioState) -> float:
        if not state.peak_equity or state.peak_equity <= 0:
            return 0.0
        return max(0.0, (state.peak_equity - state.equity) / state.peak_equity)

    def check(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        dd = self.drawdown(state)
        self.triggered_flatten = dd >= self.flatten
        if intent.is_exit:
            return self._pass()
        if dd >= self.flatten:
            return self._veto(f"drawdown {dd:.2%} >= flatten tier {self.flatten:.0%}")
        if dd >= self.halt:
            return self._veto(f"drawdown {dd:.2%} >= halt tier {self.halt:.0%}")
        if dd >= self.warn:
            return RiskDecision(True, reason=f"drawdown warning: {dd:.2%}", limit=self.name)
        return self._pass()


class KillSwitch(RiskLimit):
    """Manual halt: vetoes every entry until :meth:`reset`."""

    name = "kill_switch"
    description = "Manual trading halt; exits always allowed."
    DEFAULT_PARAMS = {}

    def __init__(self) -> None:
        self.tripped = False
        self._init_params = {}

    def trip(self) -> None:
        self.tripped = True

    def reset(self) -> None:
        self.tripped = False

    def check(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        if intent.is_exit or not self.tripped:
            return self._pass()
        return self._veto("kill switch tripped")


class GrossExposureScale(RiskLimit):
    """Soft cap: scale entries down (never veto) when gross > ``soft_pct``.

    The intent is shrunk proportionally so post-trade gross lands on
    ``soft_pct`` of equity.  Place after hard vetoes in the stack.
    """

    name = "gross_exposure_scale"
    description = "Proportionally shrink entries that would breach soft gross cap."
    DEFAULT_PARAMS = {"soft_pct": 0.9}

    def __init__(self, soft_pct: float = 0.9) -> None:
        self.soft_pct = require_positive(soft_pct, "soft_pct")
        self._init_params = {"soft_pct": self.soft_pct}

    def check(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        if intent.is_exit or state.equity <= 0 or not intent.price or not intent.quantity:
            return self._pass()
        gross = sum(p.notional for p in state.positions.values())
        headroom = state.equity * self.soft_pct - gross
        if headroom <= 0:
            return self._veto(f"no gross headroom under {self.soft_pct:.0%} soft cap")
        allowed = headroom / intent.price
        if allowed < intent.quantity:
            return RiskDecision(
                True,
                reason=f"scaled to {self.soft_pct:.0%} gross soft cap",
                quantity=allowed,
                limit=self.name,
            )
        return self._pass()
