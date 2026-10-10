> [!IMPORTANT]
> **Consolidated into [TradeSuite](https://github.com/crieck2010/trade-suite)**  
> This engine has been consolidated into the unified [TradeSuite monorepo](https://github.com/crieck2010/trade-suite). Active development, bug fixes, releases, and unified testing now live in `trade-suite`. This repository is preserved as an archive.

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

## ATR trailing stops (`trade_risk.trailing`)

Pure exit engine: `stop = peak − multiplier × ATR` for longs
(`trough + multiplier × ATR` for shorts), ratcheting monotonically —
a long stop never moves down, a short stop never moves up.

```python
from trade_risk.trailing import TrailingStop, atr_wilder

stops = {}
# on fill:
stops["AAPL"] = TrailingStop("long", multiplier=3.0, period=14, activation_pct=0.0)

# on each bar:
stop = stops["AAPL"].update(high, low, close)
if stop is not None and low <= stop:
    exit_position("AAPL")   # your own order path — this module never submits
```

- `update` returns **`None`** during ATR warmup (fewer than `period`
  bars) or before the optional arm-after-+X% gate (`activation_pct`):
  never a garbage stop. Default `0.0` arms immediately — protects
  capital from bar one but gets whipped in chop; arming late (e.g.
  `0.05`) avoids premature stop-outs but leaves early reversals
  unprotected (see the module docstring for the full tradeoff).
- Negative-price-safe: true ranges use abs-based math, so WTI-style
  negative prints stay non-negative and never flip signs.
- Consumed by **import** from `trade-backtest` (per-position tracker fed
  from the simulated bar stream) and `trade-paper` (fed from the live
  bar loop, exit orders submitted through the paper layer's own path).
  The logic is never copied into either repo.

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

See [CHANGELOG.md](CHANGELOG.md). Current version: **0.2.0**.

## The maths

**What you learn.** Two numbers before any order goes out: **how big**
should this position be, and **is it allowed**. Sizing turns a signal into
a quantity; limits turn a quantity into a yes/no (or a smaller quantity).

**Why it matters.** Position sizing is where returns are actually made or
lost — a great signal with reckless sizing is just a fast way to hit the
drawdown guard. The maths below is closed-form and auditable: every sizer
is one line of arithmetic, every limit is one inequality, and the
evaluation order is fixed so a veto is always explainable.

**The maths.**

- *Fixed-fractional* (`FixedFractionalSizer`): `quantity = equity × risk_pct / (ATR × atr_multiple)`
  — a stop-out at `atr_multiple` ATRs loses exactly `risk_pct` of equity
  (defaults: 1% risk, 2× ATR stop).
- *Volatility targeting* (`VolatilityTargetSizer`): `quantity = equity × target_vol / (volatility × price)`
  — the position carries `target_vol` (default 15% annualized) regardless of
  the asset's own volatility.
- *Fractional Kelly* (`KellySizer`): `f = fraction × (p − (1−p) / payoff)`,
  `quantity = equity × min(f, cap) / price` — the log-growth-optimal bet
  fraction from win probability `p` and payoff ratio `b` (avg win / avg
  loss), halved by default (`fraction=0.5`) and capped at 25% of equity;
  zero when the edge is non-positive.
- *Risk parity* (`analytics.inverse_vol_weights`): `w_i = (1/vol_i) / Σ(1/vol)`
  — each position contributes equally to portfolio volatility under the
  (strong) assumption of uncorrelated assets.
- *Concentration* (`analytics.herfindahl`): `Σ w_i²`, the sum of squared
  portfolio weights — 1.0 is a single bet, `1/n` is perfectly spread.
- *Limits* are inequalities on notional/equity: per-symbol ≤ `max_pct`
  (`MaxPositionNotional`), `Σ|notional|` ≤ cap (`MaxGrossExposure`),
  directional ≤ cap (`MaxNetExposure`), daily loss and peak-to-trough
  drawdown halts from `day_start_equity` / `peak_equity`.
- *Evaluation order* (`RiskManager.evaluate`): sizer first, then each limit
  in order — first veto wins, a limit may *resize* instead of vetoing
  (later limits see the resized quantity), and EXIT intents are never
  blocked. `DrawdownGuard` tiers warn → halt → flatten at increasing
  drawdown thresholds.
- *ATR trailing stop* (`TrailingStop`): Wilder ATR
  (`ATR_t = (ATR_{t-1} × (p−1) + TR_t) / p`, seeded by the mean of the
  first `p` true ranges); long stop `= peak(high) − multiplier × ATR`
  ratcheted upward only, short stop `= trough(low) + multiplier × ATR`
  ratcheted downward only. Undefined (returns `None`) for the first
  `period` bars and until the `activation_pct` gate opens.

**Honest limitations.**

- There is no VaR/ES engine here — tail-risk simulation lives in
  trade-montecarlo; these limits are all notional/drawdown-based.
- Sizers are only as good as their inputs: garbage volatility, ATR, or
  win-probability estimates produce precisely-sized garbage.
- `RiskOverlay`'s virtual position tracking assumes fills follow approved
  signals — reconcile against real fills before live use.
- Inverse-volatility weighting ignores correlations, so it over-allocates
  to clusters of correlated assets.
