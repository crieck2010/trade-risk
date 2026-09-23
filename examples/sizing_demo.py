"""Compare risk sizers and limits side by side (stdlib only)."""

from trade_risk import OrderIntent, PortfolioState, PositionState, RiskManager
from trade_risk.base import LONG
from trade_risk.limits import MaxGrossExposure, MaxPositionNotional, SymbolBlocklist
from trade_risk.sizers import (
    EqualWeightSizer,
    FixedFractionalSizer,
    KellySizer,
    SizingContext,
    VolatilityTargetSizer,
)
from trade_risk.registry import describe_limits, describe_sizers

EQUITY, PRICE = 100_000.0, 50.0


def main() -> None:
    print("--- sizer comparison ($100k equity, $50 stock) ---")
    cases = [
        ("fixed_fractional", FixedFractionalSizer(risk_pct=0.01, atr_multiple=2.0),
         SizingContext(equity=EQUITY, price=PRICE, atr=2.5)),
        ("volatility_target", VolatilityTargetSizer(target_vol=0.15),
         SizingContext(equity=EQUITY, price=PRICE, volatility=0.30)),
        ("equal_weight(10)", EqualWeightSizer(n_positions=10),
         SizingContext(equity=EQUITY, price=PRICE)),
        ("kelly", KellySizer(fraction=0.5, cap=0.25),
         SizingContext(equity=EQUITY, price=PRICE, win_prob=0.6, payoff=1.5)),
    ]
    for label, sizer, ctx in cases:
        print(f"{label:20s} -> {sizer.quantity(ctx):8.1f} shares")

    print("\n--- limit stack on a $30k AAPL buy ---")
    state = PortfolioState(cash=100_000.0, equity=100_000.0, positions={})
    risk = RiskManager(
        limits=[
            SymbolBlocklist(["GME"]),
            MaxPositionNotional(0.20),
            MaxGrossExposure(1.0),
        ],
        sizer=EqualWeightSizer(n_positions=10),
    )
    intent = OrderIntent(symbol="AAPL", side=LONG, quantity=600, price=50.0)
    d = risk.evaluate(intent, state)
    print(f"approved={d.approved} qty={d.quantity} limit={d.limit!r} reason={d.reason!r}")

    print("\n--- registry ---")
    print(f"sizers: {len(describe_sizers())}, limits: {len(describe_limits())}")


if __name__ == "__main__":
    main()
