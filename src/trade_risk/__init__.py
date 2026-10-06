"""trade-risk: portfolio risk engine.

Dependency-free risk management for the trade-suite: pre-trade limit
checks (the risk-manager veto), risk-based position sizing, drawdown
guards, and portfolio analytics.  Everything is pure Python (stdlib
only) so it can be embedded in agents, backtests, or live gateways.

The core workflow::

    from trade_risk import OrderIntent, PortfolioState, RiskManager
    from trade_risk.limits import MaxGrossExposure, MaxDrawdown, KillSwitch
    from trade_risk.sizers import VolatilityTargetSizer

    manager = RiskManager(
        limits=[MaxGrossExposure(1.0), MaxDrawdown(0.15), KillSwitch()],
        sizer=VolatilityTargetSizer(target_vol=0.15),
    )
    decision = manager.evaluate(intent, state)
    if decision.approved:
        submit(intent.with_quantity(decision.quantity))

Bridges to the sibling engines live in :mod:`trade_risk.adapters` and
are imported lazily so ``trade-risk`` never depends on them.
"""

from .base import (
    LONG,
    SHORT,
    EXIT,
    OrderIntent,
    PortfolioState,
    PositionState,
    RiskDecision,
)
from .manager import RiskManager
from .trailing import TrailingStop, atr_wilder, true_range

__version__ = "0.2.0"
__all__ = [
    "LONG",
    "SHORT",
    "EXIT",
    "OrderIntent",
    "PortfolioState",
    "PositionState",
    "RiskDecision",
    "RiskManager",
    "TrailingStop",
    "atr_wilder",
    "true_range",
    "__version__",
]
