"""Tests for risk limits: vetoes, resizes, and EXIT pass-through."""

import pytest

from trade_risk import OrderIntent
from trade_risk.base import EXIT, LONG, SHORT
from trade_risk.limits import (
    ConcentrationLimit,
    DrawdownGuard,
    GrossExposureScale,
    KillSwitch,
    MaxDailyLoss,
    MaxDrawdown,
    MaxGrossExposure,
    MaxNetExposure,
    MaxOrderNotional,
    MaxPositionNotional,
    MinPriceFilter,
    SymbolAllowlist,
    SymbolBlocklist,
)
from trade_risk.manager import RiskManager

from conftest import intent, make_state


def test_max_position_notional_veto():
    limit = MaxPositionNotional(max_pct=0.20)
    state = make_state()  # 100k equity
    assert limit.check(intent(quantity=100, price=150), state).approved  # 15k ok
    d = limit.check(intent(quantity=200, price=150), state)
    assert not d.approved and d.limit == "max_position_notional"


def test_max_gross_exposure_accounts_existing():
    limit = MaxGrossExposure(max_pct=1.0)
    state = make_state(positions={"AAPL": (400, 150.0)})  # 60k gross
    assert limit.check(intent(quantity=200, price=150), state).approved  # +30k ok
    d = limit.check(intent(symbol="MSFT", quantity=300, price=150), state)
    assert not d.approved  # +45k -> 105k > 100k


def test_max_net_exposure_direction():
    limit = MaxNetExposure(max_pct=0.5)
    state = make_state(positions={"AAPL": (600, 100.0)})  # +60k net
    d = limit.check(intent(symbol="MSFT", side=LONG, quantity=100, price=100), state)
    assert not d.approved
    # opposite direction reduces net -> passes
    assert limit.check(intent(symbol="MSFT", side=SHORT, quantity=100, price=100), state).approved


def test_concentration():
    limit = ConcentrationLimit(max_pct=0.5)
    state = make_state(positions={"AAPL": (400, 100.0)})  # 40k
    assert limit.check(intent(symbol="MSFT", quantity=400, price=100), state).approved
    d = limit.check(intent(symbol="MSFT", quantity=500, price=100), state)
    assert not d.approved  # 50k/90k > 50%


def test_max_order_notional_resizes():
    limit = MaxOrderNotional(max_notional=10_000)
    d = limit.check(intent(quantity=100, price=150), make_state())
    assert d.approved and d.quantity == pytest.approx(10_000 / 150)
    assert d.limit == "max_order_notional"


def test_allow_and_block_lists():
    allow = SymbolAllowlist(["AAPL"])
    assert allow.check(intent(symbol="AAPL"), make_state()).approved
    assert not allow.check(intent(symbol="MSFT"), make_state()).approved
    block = SymbolBlocklist(["GME"])
    assert not block.check(intent(symbol="GME"), make_state()).approved
    assert block.check(intent(symbol="AAPL"), make_state()).approved


def test_min_price():
    limit = MinPriceFilter(min_price=1.0)
    assert not limit.check(intent(price=0.50), make_state()).approved
    assert limit.check(intent(price=1.50), make_state()).approved


def test_max_daily_loss_blocks_entries_only():
    limit = MaxDailyLoss(max_loss_pct=0.03)
    state = make_state(equity=96_000, day_start=100_000)  # -4%
    assert not limit.check(intent(), state).approved
    # exits always allowed
    assert limit.check(intent(side=EXIT, quantity=100), state).approved
    ok_state = make_state(equity=99_000, day_start=100_000)  # -1%
    assert limit.check(intent(), ok_state).approved


def test_max_drawdown():
    limit = MaxDrawdown(max_dd=0.10)
    state = make_state(equity=89_000, peak=100_000)  # -11%
    assert not limit.check(intent(), state).approved
    assert limit.check(intent(side=EXIT, quantity=10), state).approved


def test_drawdown_guard_tiers():
    guard = DrawdownGuard(warn=0.05, halt=0.10, flatten=0.15)
    state = make_state(equity=94_000, peak=100_000)  # -6%: warn
    d = guard.check(intent(), state)
    assert d.approved and "warning" in d.reason
    state = make_state(equity=89_000, peak=100_000)  # -11%: halt
    assert not guard.check(intent(), state).approved
    state = make_state(equity=84_000, peak=100_000)  # -16%: flatten
    assert not guard.check(intent(), state).approved
    assert guard.triggered_flatten


def test_kill_switch():
    ks = KillSwitch()
    assert ks.check(intent(), make_state()).approved
    ks.trip()
    assert not ks.check(intent(), make_state()).approved
    assert ks.check(intent(side=EXIT, quantity=5), make_state()).approved
    ks.reset()
    assert ks.check(intent(), make_state()).approved


def test_gross_exposure_scale_shrinks():
    limit = GrossExposureScale(soft_pct=0.9)
    state = make_state(positions={"AAPL": (800, 100.0)})  # 80k gross
    d = limit.check(intent(symbol="MSFT", quantity=500, price=100), state)
    assert d.approved and d.quantity == pytest.approx(100.0)  # 10k headroom


def test_manager_first_veto_wins_and_resizes_chain():
    mgr = RiskManager(
        limits=[SymbolBlocklist(["GME"]), MaxOrderNotional(max_notional=10_000)],
    )
    d = mgr.evaluate(intent(symbol="GME", quantity=100, price=150), make_state())
    assert not d.approved and d.limit == "symbol_blocklist"
    d = mgr.evaluate(intent(symbol="AAPL", quantity=100, price=150), make_state())
    assert d.approved and d.quantity == pytest.approx(10_000 / 150)


def test_manager_uses_sizer_when_unsized():
    from trade_risk.sizers import EqualWeightSizer

    mgr = RiskManager(
        limits=[MaxPositionNotional(0.20)], sizer=EqualWeightSizer(n_positions=10)
    )
    d = mgr.evaluate(intent(quantity=None, price=50.0), make_state())
    assert d.approved and d.quantity == pytest.approx(200.0)


def test_manager_rejects_nonpositive_size():
    mgr = RiskManager()
    d = mgr.evaluate(intent(quantity=0, price=50.0), make_state())
    assert not d.approved


def test_manager_flatten_helpers():
    guard = DrawdownGuard()
    mgr = RiskManager(limits=[guard])
    state = make_state(
        positions={"AAPL": (100, 150.0), "MSFT": (-50, 200.0)},
        equity=80_000,
        peak=100_000,
    )
    assert not mgr.evaluate(intent(), state).approved
    assert mgr.flatten_requested
    flat = mgr.flatten_intents(state)
    assert {i.symbol: i.quantity for i in flat} == {"AAPL": 100.0, "MSFT": 50.0}
    assert all(i.side == EXIT for i in flat)
