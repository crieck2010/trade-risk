# trade-risk

Portfolio risk engine for the [trade-suite](https://github.com/crieck2010/trade-suite) —
the hedge-fund risk desk as a library. Pure Python, stdlib only, no
dependencies.

It answers two questions before any order goes out:

1. **How big?** — risk-based position sizing (`sizers.py`)
2. **Allowed?** — pre-trade limit checks with veto power (`limits.py` + `manager.py`)

## Install

```bash
pip install git+https://github.com/crieck2010/trade-risk.git
```

## Quick start

```python
from trade_risk import OrderIntent, PortfolioState, RiskManager
from trade_risk.base import LONG
from trade_risk.limits import MaxGrossExposure, MaxPositionNotional, KillSwitch
from trade_risk.sizers import VolatilityTargetSizer

risk = RiskManager(
    limits=[KillSwitch(), MaxPositionNotional(0.25), MaxGrossExposure(1.0)],
    sizer=VolatilityTargetSizer(target_vol=0.15),
    volatility_provider=lambda symbol: 0.30,  # your vol feed here
)

state = PortfolioState(cash=100_000.0, equity=100_000.0,
                       day_start_equity=100_000.0, peak_equity=100_000.0)
intent = OrderIntent(symbol="AAPL", side=LONG, price=150.0)  # unsized

decision = risk.evaluate(intent, state)
if decision.approved:
    submit(intent.with_quantity(decision.quantity))  # sized + limit-checked
else:
    log(f"vetoed by {decision.limit}: {decision.reason}")
```

## Concepts

- **`OrderIntent`** — what the strategy/agent wants to do (symbol, side,
  optional quantity, reference price). `quantity=None` means "not sized yet".
- **`PortfolioState`** — frozen mark-to-market snapshot: cash, equity,
  positions, plus `day_start_equity` and `peak_equity` for the loss/drawdown
  limits. Build it from whatever portfolio object you already have.
- **`RiskDecision`** — the verdict: `approved`, `reason`, the final
  `quantity`, and which `limit` vetoed or resized.
- **`RiskManager`** — runs the sizer, then each limit in order. First veto
  wins; a limit may *resize* instead of vetoing (later limits see the
  resized quantity). Exits are never blocked — flattening must always be
  possible.

## Position sizers (`trade_risk.sizers`)

| Sizer | Idea |
|---|---|
| `FixedFractionalSizer` | Risk `risk_pct` of equity per trade (ATR stop) |
| `VolatilityTargetSizer` | Size so the position carries `target_vol` |
| `EqualWeightSizer` | 1/`n_positions` of equity |
| `KellySizer` | Fractional Kelly from win rate × payoff |
| `FixedNotionalSizer` | Constant dollar exposure |

Sizers propose; they never veto. Direction is applied by the caller.

## Limits (`trade_risk.limits`)

| Limit | Behaviour |
|---|---|
| `MaxPositionNotional` | Per-symbol notional ≤ pct of equity (veto) |
| `MaxGrossExposure` | Σ\|notional\| ≤ pct of equity (veto) |
| `MaxNetExposure` | Directional exposure ≤ pct of equity (veto) |
| `ConcentrationLimit` | Largest position ≤ pct of gross (veto) |
| `MaxOrderNotional` | Single-order cap — **resizes** |
| `GrossExposureScale` | Soft gross cap — **shrinks** entries proportionally |
| `SymbolAllowlist` / `SymbolBlocklist` | Universe control (veto) |
| `MinPriceFilter` | Skip sub-dollar instruments (veto) |
| `MaxDailyLoss` | Halt entries after daily loss (needs `day_start_equity`) |
| `MaxDrawdown` | Halt entries after drawdown (needs `peak_equity`) |
| `DrawdownGuard` | Tiered warn → halt → **flatten** (see below) |
| `KillSwitch` | Manual halt; `trip()` / `reset()` |

**DrawdownGuard tiers** (`warn` < `halt` < `flatten`): warn passes with a
flagged reason, halt vetoes new entries, flatten vetoes entries *and* sets
`RiskManager.flatten_requested` — call `flatten_intents(state)` to get the
EXIT intents that liquidate everything.

## Analytics & monitoring

```python
from trade_risk import analytics
analytics.gross_exposure(state)   # fraction of equity
analytics.net_exposure(state)
analytics.concentration(state)    # largest position / gross
analytics.herfindahl(state)
analytics.max_drawdown(equity_series)
analytics.inverse_vol_weights({"AAPL": 0.25, "MSFT": 0.18})  # risk-parity weights

from trade_risk.monitor import DrawdownMonitor
mon = DrawdownMonitor(warn=0.05, halt=0.10, flatten=0.15)
dd, status = mon.update(equity)   # status: ok | warn | halt | flatten
```

## Agent registry

```python
from trade_risk.registry import list_sizers, list_limits, get_sizer, get_limit, describe_limits
risk = RiskManager(limits=[get_limit("max_gross_exposure", max_pct=1.0)],
                   sizer=get_sizer("kelly", fraction=0.5))
```

## Interop with the trade-suite

`trade_risk.adapters` (lazy imports — `trade-risk` itself stays dependency-free):

- **`to_backtest_sizer(risk_sizer, volatility_provider)`** → a
  `trade_backtest.PositionSizer`, so backtests size positions with risk
  logic (vol targeting, Kelly, …).
- **`RiskOverlay(strategy, risk_manager)`** — wraps any strategy-like with
  `.symbols` / `.on_bar()` (e.g. a `trade-strategies` strategy), tracks
  virtual positions mark-to-market from the bar stream, and drops vetoed
  signals. Drawdown-guard flatten injects EXIT signals.

See `examples/risk_gate_demo.py` for the full loop:
strategy → risk gate → backtest with a risk-backed sizer.

## Scaling notes

- Everything is pure functions + frozen dataclasses: trivially parallel per
  symbol, no shared mutable state except the small `DrawdownMonitor` /
  `DrawdownGuard` trackers.
- `RiskManager.evaluate` is O(#limits); keep the stack short on hot paths
  and put cheap deterministic vetoes (allowlist, kill switch) first.
- For multi-asset portfolios, size the book with
  `analytics.inverse_vol_weights` once per rebalance, then enforce with
  per-symbol intents.

## Limitations

- Virtual tracking in `RiskOverlay` assumes fills follow approved signals;
  reconcile with real fills before live use.
- Research/backtesting/paper-trading tool. Not investment advice; never
  trades live on its own.

## Changelog

See [CHANGELOG.md](CHANGELOG.md). Current version: **0.1.0**.
