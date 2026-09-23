"""Bridges to the sibling engines (lazy imports -- no hard dependency).

- :func:`to_backtest_sizer` wraps a risk sizer as a
  ``trade_backtest.PositionSizer`` so backtests size positions with
  risk logic (vol targeting, Kelly, ...).
- :class:`RiskOverlay` wraps any strategy-like object exposing
  ``.symbols`` and ``.on_bar(timestamp, bars)`` (e.g. a
  trade-strategies strategy) and vetoes its signals through a
  :class:`RiskManager`, tracking virtual positions mark-to-market
  from the bar stream.  Exits are never blocked; a drawdown-guard
  flatten injects EXIT signals for every open virtual position.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime

from .base import EXIT, LONG, SHORT, OrderIntent, PortfolioState, PositionState
from .manager import RiskManager
from .sizers import RiskSizer, SizingContext

__all__ = ["to_backtest_sizer", "RiskOverlay"]


def to_backtest_sizer(
    sizer: RiskSizer,
    volatility_provider: Callable[[str], float | tuple[float | None, float | None] | None]
    | None = None,
):
    """Adapt a risk sizer to ``trade_backtest.PositionSizer``.

    ``volatility_provider(symbol)`` may return a volatility fraction,
    an ``(atr, volatility)`` tuple, or ``None``.
    """
    try:
        from trade_backtest.portfolio import PositionSizer as _Base
        from trade_backtest.models import SignalAction
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "to_backtest_sizer needs the trade-backtest package installed"
        ) from exc

    class _Adapter(_Base):
        def __init__(self) -> None:
            self.sizer = sizer
            self.volatility_provider = volatility_provider

        def size(self, signal, price: float, portfolio) -> float:  # noqa: ANN001, ANN202
            atr = vol = None
            if self.volatility_provider is not None:
                try:
                    result = self.volatility_provider(signal.symbol)
                except Exception:
                    result = None
                if isinstance(result, tuple):
                    atr, vol = result
                else:
                    vol = result
            ctx = SizingContext(
                equity=portfolio.equity, price=price, atr=atr, volatility=vol
            )
            qty = self.sizer.quantity(ctx) * signal.strength
            if signal.action is SignalAction.LONG:
                return qty
            if signal.action is SignalAction.SHORT:
                return -qty
            return 0.0

    _Adapter.__name__ = f"RiskBacked_{type(sizer).__name__}"
    return _Adapter()


class RiskOverlay:
    """Risk-managed wrapper around a signal-producing strategy.

    Duck-typed: ``strategy`` only needs ``.symbols`` and
    ``.on_bar(timestamp, bars)`` returning signal-likes with
    ``.symbol``, ``.action`` (``LONG``/``SHORT``/``EXIT`` or an enum
    exposing ``.name``), ``.strength`` and ``.timestamp``.

    Virtual bookkeeping assumes fills follow the signals that pass
    the gate (no slippage/fees modelled here -- the backtest layer
    owns execution realism).  Reconcile with real fills before live
    use.
    """

    def __init__(
        self,
        strategy,
        risk_manager: RiskManager,
        initial_cash: float = 100_000.0,
    ) -> None:
        self.strategy = strategy
        self.risk = risk_manager
        self.state = PortfolioState(
            cash=float(initial_cash),
            equity=float(initial_cash),
            positions={},
            day_start_equity=float(initial_cash),
            peak_equity=float(initial_cash),
        )

    @property
    def symbols(self):  # noqa: ANN201
        return self.strategy.symbols

    # -- internals ------------------------------------------------------
    @staticmethod
    def _action_name(signal) -> str:  # noqa: ANN001, ANN202
        action = signal.action
        return action.name if hasattr(action, "name") else str(action)

    @staticmethod
    def _bar_close(bar) -> float:  # noqa: ANN001, ANN202
        if isinstance(bar, Mapping):
            return float(bar["close"])
        return float(bar.close)

    def _mark_to_market(self, timestamp: datetime, bars: Mapping) -> None:
        positions = dict(self.state.positions)
        for symbol, bar in bars.items():
            pos = positions.get(symbol)
            if pos is not None:
                positions[symbol] = replace(pos, market_price=self._bar_close(bar))
        equity = self.state.cash + sum(p.signed_notional for p in positions.values())
        peak = self.state.peak_equity
        peak = equity if peak is None else max(peak, equity)
        self.state = replace(
            self.state,
            timestamp=timestamp,
            positions=positions,
            equity=equity,
            peak_equity=peak,
        )

    def _apply_fill(self, symbol: str, side_name: str, quantity: float, price: float) -> None:
        positions = dict(self.state.positions)
        pos = positions.get(symbol)
        current = pos.quantity if pos else 0.0
        if side_name == "EXIT":
            new_qty, cash_delta = 0.0, current * price
        elif side_name == "LONG":
            new_qty, cash_delta = current + quantity, -quantity * price
        else:  # SHORT
            new_qty, cash_delta = current - quantity, quantity * price
        if abs(new_qty) < 1e-12:
            positions.pop(symbol, None)
        else:
            positions[symbol] = PositionState(
                symbol=symbol,
                quantity=new_qty,
                avg_price=price if pos is None else pos.avg_price,
                market_price=price,
            )
        cash = self.state.cash + cash_delta
        equity = cash + sum(p.signed_notional for p in positions.values())
        peak = self.state.peak_equity
        peak = equity if peak is None else max(peak, equity)
        self.state = replace(
            self.state, positions=positions, cash=cash, equity=equity, peak_equity=peak
        )

    # -- public API ------------------------------------------------------
    def on_bar(self, timestamp: datetime, bars: Mapping) -> list:
        """Run the strategy, gate its signals, return the approved ones."""
        self._mark_to_market(timestamp, bars)
        raw = list(self.strategy.on_bar(timestamp, bars) or [])
        approved = []
        for signal in raw:
            side_name = self._action_name(signal)
            side = {"LONG": LONG, "SHORT": SHORT}.get(side_name, EXIT)
            price = self._bar_close(bars[signal.symbol])
            intent = OrderIntent(
                symbol=signal.symbol,
                side=side,
                quantity=None,
                price=price,
                timestamp=timestamp,
            )
            decision = self.risk.evaluate(intent, self.state)
            if not decision.approved:
                continue  # vetoed: risk-manager says no
            qty = decision.quantity or 0.0
            if side_name != "EXIT" and qty <= 0:
                continue
            self._apply_fill(signal.symbol, side_name, qty, price)
            approved.append(signal)

        self.risk.poll_guards(self.state)
        if self.risk.flatten_requested:
            for intent in self.risk.flatten_intents(self.state):
                price = self._bar_close(bars[intent.symbol])
                self._apply_fill(intent.symbol, "EXIT", intent.quantity or 0.0, price)
                approved.append(_exit_signal(intent.symbol, timestamp))
        return approved


def _exit_signal(symbol: str, timestamp: datetime):
    """Minimal signal-like for injected flatten exits."""
    from types import SimpleNamespace

    return SimpleNamespace(
        symbol=symbol, action=EXIT, strength=1.0, timestamp=timestamp
    )
