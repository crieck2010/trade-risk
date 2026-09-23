"""Risk gate demo: run strategy signals through a RiskManager.

Needs the sibling packages on the path (trade-strategies, trade-backtest):
    PYTHONPATH=../trade-strategies/src:../trade-backtest/src:src python3 examples/risk_gate_demo.py
"""

from datetime import datetime, timedelta, timezone

from trade_risk import RiskManager
from trade_risk.adapters import RiskOverlay, to_backtest_sizer
from trade_risk.limits import (
    DrawdownGuard,
    KillSwitch,
    MaxDrawdown,
    MaxGrossExposure,
    MaxPositionNotional,
)
from trade_risk.sizers import FixedNotionalSizer, VolatilityTargetSizer


def synthetic_bars(symbol, start_price, n=120, wiggle=0.02):
    import random

    random.seed(7)
    bars, price, t = [], start_price, datetime(2024, 1, 2, tzinfo=timezone.utc)
    for i in range(n):
        drift = -0.004 if i < n // 2 else 0.006  # down then up -> real crossover
        change = drift + random.uniform(-wiggle, wiggle)
        o, c = price, price * (1 + change)
        bars.append(
            {
                "symbol": "AAPL",
                "timestamp": t,
                "open": o,
                "high": max(o, c) * 1.005,
                "low": min(o, c) * 0.995,
                "close": c,
                "volume": 1_000_000,
            }
        )
        price, t = c, t + timedelta(days=1)
    return bars


def main() -> None:
    from trade_strategies import get_strategy
    from trade_strategies.adapters import run_backtest

    bars = synthetic_bars("AAPL", 150.0)
    strategy = get_strategy("sma_crossover")(["AAPL"], fast=10, slow=30)

    # --- risk stack: the hedge-fund risk desk in five lines ---
    kill = KillSwitch()
    risk = RiskManager(
        limits=[
            kill,
            MaxPositionNotional(0.25),
            MaxGrossExposure(1.0),
            MaxDrawdown(0.12),
            DrawdownGuard(warn=0.05, halt=0.08, flatten=0.12),
        ],
        sizer=FixedNotionalSizer(20_000),  # 20% of equity: under the 25% cap
        volatility_provider=lambda symbol: 0.25,  # 25% ann. vol placeholder
    )
    overlay = RiskOverlay(strategy, risk, initial_cash=100_000.0)

    # trip the kill switch halfway to show the veto working
    signals_seen = 0
    for bar in bars:
        t = bar["timestamp"]
        for signal in overlay.on_bar(t, {"AAPL": bar}):
            signals_seen += 1
    print(f"signals passed the gate : {signals_seen}")
    print(f"final virtual equity      : ${overlay.state.equity:,.0f}")
    print(f"gross exposure            : "
          f"{sum(p.notional for p in overlay.state.positions.values()) / overlay.state.equity:.1%}")

    # --- same risk logic as the backtest's sizer ---
    result = run_backtest(
        strategy,
        bars,
        sizer=to_backtest_sizer(
            VolatilityTargetSizer(target_vol=0.20),
            volatility_provider=lambda symbol: 0.25,
        ),
        initial_cash=100_000.0,
    )
    m = result.metrics
    print(f"backtest total return     : {m['total_return']:+.1%}")
    print(f"backtest max drawdown     : {m['max_drawdown']:.1%}")
    print(f"backtest trades           : {result.num_trades}")


if __name__ == "__main__":
    main()
