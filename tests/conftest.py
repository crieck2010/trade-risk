"""Shared fixtures for trade-risk tests."""

from __future__ import annotations

import pytest

from trade_risk import OrderIntent, PortfolioState, PositionState
from trade_risk.base import LONG, SHORT, EXIT


def make_state(
    equity: float = 100_000.0,
    positions: dict | None = None,
    day_start: float | None = None,
    peak: float | None = None,
) -> PortfolioState:
    pos = {}
    for symbol, (qty, price) in (positions or {}).items():
        pos[symbol] = PositionState(
            symbol=symbol, quantity=qty, avg_price=price, market_price=price
        )
    invested = sum(abs(q) * p for q, p in (positions or {}).values())
    cash = equity - invested
    return PortfolioState(
        cash=cash,
        equity=equity,
        positions=pos,
        day_start_equity=day_start if day_start is not None else equity,
        peak_equity=peak if peak is not None else equity,
    )


def intent(symbol="AAPL", side=LONG, quantity=100.0, price=150.0) -> OrderIntent:
    return OrderIntent(symbol=symbol, side=side, quantity=quantity, price=price)
