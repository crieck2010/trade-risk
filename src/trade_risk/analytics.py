"""Pure portfolio analytics: exposures, concentration, drawdown.

All functions take plain numbers / mappings so agents and reporting
code can use them without constructing engine objects.
"""

from __future__ import annotations

from collections.abc import Mapping

from .base import PortfolioState


def gross_exposure(state: PortfolioState) -> float:
    """Sum of absolute notionals, as a fraction of equity."""
    if state.equity <= 0:
        return 0.0
    return sum(p.notional for p in state.positions.values()) / state.equity


def net_exposure(state: PortfolioState) -> float:
    """Net signed notional as a fraction of equity."""
    if state.equity <= 0:
        return 0.0
    return sum(p.signed_notional for p in state.positions.values()) / state.equity


def concentration(state: PortfolioState) -> float:
    """Largest single position as a fraction of gross notional (0..1)."""
    notionals = [p.notional for p in state.positions.values()]
    gross = sum(notionals)
    if gross <= 0:
        return 0.0
    return max(notionals) / gross


def herfindahl(state: PortfolioState) -> float:
    """Herfindahl index of position weights (1/n .. 1); higher = concentrated."""
    notionals = [p.notional for p in state.positions.values()]
    gross = sum(notionals)
    if gross <= 0:
        return 0.0
    return sum((n / gross) ** 2 for n in notionals)


def drawdown_series(equities: list[float]) -> list[float]:
    """Drawdown (positive fraction) at each point of an equity series."""
    out: list[float] = []
    peak = float("-inf")
    for eq in equities:
        peak = max(peak, eq)
        out.append(0.0 if peak <= 0 else (peak - eq) / peak)
    return out


def max_drawdown(equities: list[float]) -> float:
    """Largest peak-to-trough drawdown as a positive fraction."""
    series = drawdown_series(equities)
    return max(series) if series else 0.0


def inverse_vol_weights(vols: Mapping[str, float]) -> dict[str, float]:
    """Risk-parity-style weights: ``w_i = (1/vol_i) / sum(1/vol)``.

    ``vols`` maps symbol -> per-period volatility (any consistent
    units: ATR/price, return stdev, ...).  Non-positive vols are
    dropped; an empty result means "no allocatable symbols".
    """
    inv = {s: 1.0 / v for s, v in vols.items() if v and v > 0}
    total = sum(inv.values())
    if total <= 0:
        return {}
    return {s: w / total for s, w in inv.items()}


def position_weights(state: PortfolioState) -> dict[str, float]:
    """Signed notional weights keyed by symbol (fractions of equity)."""
    if state.equity <= 0:
        return {}
    return {s: p.signed_notional / state.equity for s, p in state.positions.items()}
