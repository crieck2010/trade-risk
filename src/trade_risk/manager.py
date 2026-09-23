"""Risk manager: sizer + ordered limit stack -> one decision.

Limits run in order; the first veto wins.  A limit may *resize* an
intent (``RiskDecision.quantity`` set, still approved) -- later limits
see the resized quantity.
"""

from __future__ import annotations

from dataclasses import replace

from .base import EXIT, OrderIntent, PortfolioState, RiskDecision
from .limits import DrawdownGuard, RiskLimit
from .sizers import RiskSizer, SizingContext


class RiskManager:
    """Pre-trade risk gate.

    Parameters
    ----------
    limits:
        Ordered checks.  Put cheap deterministic vetoes (allowlist,
        kill switch) first and soft resizers last.
    sizer:
        Proposes a quantity when the intent has none.
    volatility_provider:
        Optional ``(symbol) -> float | None`` consulted to fill
        ``SizingContext.atr`` / ``volatility`` when the caller cannot.
    """

    def __init__(
        self,
        limits: list[RiskLimit] | None = None,
        sizer: RiskSizer | None = None,
        volatility_provider=None,
    ) -> None:
        self.limits: list[RiskLimit] = list(limits or [])
        self.sizer = sizer
        self.volatility_provider = volatility_provider

    # -- evaluation -----------------------------------------------------
    def evaluate(self, intent: OrderIntent, state: PortfolioState) -> RiskDecision:
        quantity = intent.quantity
        if quantity is None and not intent.is_exit and self.sizer is not None:
            quantity = self.sizer.quantity(self._sizing_context(intent, state))
        if quantity is not None and quantity <= 0:
            return RiskDecision(False, reason="non-positive size", limit="sizer")

        current = intent if quantity is None else intent.with_quantity(quantity)
        for limit in self.limits:
            decision = limit.check(current, state)
            if not decision.approved:
                return decision
            if decision.quantity is not None and decision.quantity != current.quantity:
                current = current.with_quantity(decision.quantity)
        return RiskDecision(True, quantity=current.quantity)

    def _sizing_context(self, intent: OrderIntent, state: PortfolioState) -> SizingContext:
        atr = vol = None
        if self.volatility_provider is not None:
            try:
                result = self.volatility_provider(intent.symbol)
            except Exception:
                result = None
            if isinstance(result, tuple):
                atr, vol = result
            else:
                vol = result
        return SizingContext(
            equity=state.equity,
            price=intent.price or 0.0,
            atr=atr,
            volatility=vol,
        )

    # -- guard helpers --------------------------------------------------
    @property
    def flatten_requested(self) -> bool:
        """True when any DrawdownGuard in the stack hit its flatten tier."""
        return any(
            isinstance(limit, DrawdownGuard) and limit.triggered_flatten
            for limit in self.limits
        )

    def poll_guards(self, state: PortfolioState) -> None:
        """Refresh stateful guards (e.g. ``DrawdownGuard.triggered_flatten``).

        Runs each guard's check with a synthetic EXIT intent so tiers
        update even when no real intent is being evaluated.
        """
        probe = OrderIntent(symbol="", side=EXIT, timestamp=state.timestamp)
        for limit in self.limits:
            if isinstance(limit, DrawdownGuard):
                limit.check(probe, state)

    def flatten_intents(self, state: PortfolioState) -> list[OrderIntent]:
        """EXIT intents closing every open position."""
        intents = []
        for symbol, pos in state.positions.items():
            if pos.quantity != 0:
                intents.append(
                    OrderIntent(
                        symbol=symbol,
                        side=EXIT,
                        quantity=abs(pos.quantity),
                        price=pos.market_price,
                        timestamp=state.timestamp,
                    )
                )
        return intents
