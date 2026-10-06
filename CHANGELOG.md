# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-10-06

### Added
- `trade_risk.trailing`: pure ATR trailing-stop engine (Feature C).
  `atr_wilder(highs, lows, closes, period=14)` implements Wilder's ATR
  (seed = mean of first `period` true ranges, then Wilder recursion);
  `true_range` uses abs-based math so WTI-style negative prints stay
  non-negative and never flip signs. `TrailingStop(side, multiplier=3.0,
  period=14, activation_pct=0.0)` tracks peak/trough, emits a monotone
  ratcheting stop (`long: peak − k·ATR` only rises; `short: trough + k·ATR`
  only falls), returns `None` during ATR warmup (fewer than `period`
  bars) and before the optional arm-after-+X% gate. Stdlib only; no
  broker, exchange, or UI imports; consumed by trade-backtest /
  trade-paper via import (documented in the module docstring), never
  copied. 12 tests with hand-computed arithmetic.

## [0.1.0] - 2026-09-23

### Added
- Core value objects: `OrderIntent`, `PortfolioState`, `PositionState`,
  `RiskDecision` (frozen dataclasses, stdlib only).
- `RiskManager`: ordered limit stack + optional sizer; first veto wins,
  limits may resize; exits never blocked; `flatten_requested` /
  `flatten_intents` / `poll_guards` for drawdown liquidation.
- Sizers: `FixedFractionalSizer` (ATR stop), `VolatilityTargetSizer`,
  `EqualWeightSizer`, `KellySizer` (fractional, capped), `FixedNotionalSizer`.
- Limits: `MaxPositionNotional`, `MaxGrossExposure`, `MaxNetExposure`,
  `ConcentrationLimit`, `MaxOrderNotional` (resize), `GrossExposureScale`
  (soft shrink), `SymbolAllowlist`, `SymbolBlocklist`, `MinPriceFilter`,
  `MaxDailyLoss`, `MaxDrawdown`, `DrawdownGuard` (warn/halt/flatten tiers),
  `KillSwitch`.
- `analytics`: gross/net exposure, concentration, Herfindahl,
  drawdown series, max drawdown, inverse-vol weights, position weights.
- `DrawdownMonitor`: stateful peak/drawdown tracker with tiered status.
- Agent-facing `registry`: `SIZER_REGISTRY`, `LIMIT_REGISTRY`, getters,
  `describe_*` metadata.
- `adapters` (lazy): `to_backtest_sizer` bridge to `trade-backtest`,
  `RiskOverlay` signal gate for `trade-strategies` strategies with virtual
  mark-to-market tracking and flatten injection.
- 37 tests; examples `sizing_demo.py`, `risk_gate_demo.py`.
