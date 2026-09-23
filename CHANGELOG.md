# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
