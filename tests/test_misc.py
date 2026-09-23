"""Tests for analytics, monitor, registry, and adapters."""

import pytest

from trade_risk import analytics
from trade_risk.monitor import DrawdownMonitor
from trade_risk.sizers import FixedNotionalSizer
from trade_risk.registry import (
    describe_limits,
    describe_sizers,
    get_limit,
    get_sizer,
    list_limits,
    list_sizers,
)

from conftest import make_state


def test_exposures():
    state = make_state(positions={"AAPL": (100, 150.0), "MSFT": (-50, 200.0)})
    # gross = 15k + 10k = 25k; net = 15k - 10k = 5k
    assert analytics.gross_exposure(state) == pytest.approx(0.25)
    assert analytics.net_exposure(state) == pytest.approx(0.05)


def test_concentration_and_herfindahl():
    state = make_state(positions={"AAPL": (100, 150.0), "MSFT": (-50, 200.0)})
    assert analytics.concentration(state) == pytest.approx(0.6)
    assert analytics.herfindahl(state) == pytest.approx(0.6**2 + 0.4**2)
    assert analytics.concentration(make_state()) == 0.0


def test_drawdown_series():
    eq = [100.0, 110.0, 105.0, 90.0, 95.0]
    dd = analytics.drawdown_series(eq)
    assert dd[1] == 0.0
    assert dd[3] == pytest.approx((110 - 90) / 110)
    assert analytics.max_drawdown(eq) == pytest.approx((110 - 90) / 110)
    assert analytics.max_drawdown([]) == 0.0


def test_inverse_vol_weights():
    w = analytics.inverse_vol_weights({"A": 0.2, "B": 0.4})
    assert w["A"] == pytest.approx(2 / 3) and w["B"] == pytest.approx(1 / 3)
    assert analytics.inverse_vol_weights({"A": 0.0}) == {}


def test_position_weights():
    state = make_state(positions={"AAPL": (100, 150.0)})
    assert analytics.position_weights(state)["AAPL"] == pytest.approx(0.15)


def test_drawdown_monitor_tiers():
    mon = DrawdownMonitor(warn=0.05, halt=0.10, flatten=0.15)
    assert mon.update(100.0) == (0.0, "ok")
    assert mon.update(94.0) == (pytest.approx(0.06), "warn")
    assert mon.update(89.0) == (pytest.approx(0.11), "halt")
    assert mon.update(84.0)[1] == "flatten"
    mon.reset(84.0)
    assert mon.status() == "ok"


def test_registry_roundtrip():
    assert "volatility_target" in list_sizers()
    assert "max_gross_exposure" in list_limits()
    assert get_sizer("equal_weight", n_positions=5).n_positions == 5
    assert get_limit("kill_switch").name == "kill_switch"
    with pytest.raises(KeyError):
        get_sizer("nope")
    with pytest.raises(KeyError):
        get_limit("nope")
    assert any(d["name"] == "kelly" for d in describe_sizers())
    assert any(d["name"] == "symbol_allowlist" for d in describe_limits())


def test_to_backtest_sizer():
    tb = pytest.importorskip("trade_backtest")
    from trade_risk.adapters import to_backtest_sizer
    from trade_risk.sizers import EqualWeightSizer, FixedNotionalSizer

    sizer = to_backtest_sizer(EqualWeightSizer(n_positions=10))
    assert isinstance(sizer, tb.PositionSizer)
    portfolio = tb.Portfolio(100_000.0, tb.FixedQuantitySizer(1))
    from datetime import datetime, timezone

    sig = tb.Signal(symbol="AAPL", action=tb.SignalAction.LONG, strength=1.0,
                    timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc))
    assert sizer.size(sig, 50.0, portfolio) == pytest.approx(200.0)
    short = tb.Signal(symbol="AAPL", action=tb.SignalAction.SHORT, strength=0.5,
                      timestamp=sig.timestamp)
    assert sizer.size(short, 50.0, portfolio) == pytest.approx(-100.0)
    flat = tb.Signal(symbol="AAPL", action=tb.SignalAction.EXIT, strength=1.0,
                     timestamp=sig.timestamp)
    assert sizer.size(flat, 50.0, portfolio) == 0.0


def test_risk_overlay_vetoes_and_tracks():
    ts = pytest.importorskip("trade_strategies")
    from types import SimpleNamespace

    from trade_risk.adapters import RiskOverlay
    from trade_risk.manager import RiskManager
    from trade_risk.limits import SymbolBlocklist

    class LoudStrategy:
        symbols = ("AAPL", "GME")

        def on_bar(self, timestamp, bars):
            return [
                SimpleNamespace(symbol="AAPL", action="LONG", strength=1.0,
                                timestamp=timestamp),
                SimpleNamespace(symbol="GME", action="LONG", strength=1.0,
                                timestamp=timestamp),
            ]

    overlay = RiskOverlay(
        LoudStrategy(),
        RiskManager(
            limits=[SymbolBlocklist(["GME"])],
            sizer=FixedNotionalSizer(10_000),
        ),
        initial_cash=100_000.0,
    )
    from datetime import datetime, timezone

    t = datetime(2024, 1, 2, tzinfo=timezone.utc)
    bars = {"AAPL": {"close": 150.0}, "GME": {"close": 20.0}}
    out = overlay.on_bar(t, bars)
    assert [s.symbol for s in out] == ["AAPL"]  # GME vetoed
    assert overlay.state.positions["AAPL"].quantity != 0.0
    assert "GME" not in overlay.state.positions
    # mark-to-market moves equity
    overlay.on_bar(t, {"AAPL": {"close": 160.0}, "GME": {"close": 20.0}})
    assert overlay.state.equity > 100_000.0


def test_risk_overlay_flatten_injects_exits():
    from types import SimpleNamespace
    from datetime import datetime, timezone

    from trade_risk.adapters import RiskOverlay
    from trade_risk.manager import RiskManager
    from trade_risk.limits import DrawdownGuard
    from trade_risk.sizers import FixedNotionalSizer

    class QuietStrategy:
        symbols = ("AAPL",)

        def on_bar(self, timestamp, bars):
            return []

    overlay = RiskOverlay(
        QuietStrategy(),
        RiskManager(
            limits=[DrawdownGuard(warn=0.01, halt=0.02, flatten=0.03)],
            sizer=FixedNotionalSizer(50_000),
        ),
        initial_cash=100_000.0,
    )
    t = datetime(2024, 1, 2, tzinfo=timezone.utc)
    # open a virtual long at 100
    overlay._apply_fill("AAPL", "LONG", 500.0, 100.0)
    # crash to 90 -> 10% drawdown on equity (50k -> 45k of 100k... plus cash 50k = 95k)
    out = overlay.on_bar(t, {"AAPL": {"close": 90.0}})
    exits = [s for s in out if str(getattr(s.action, "name", s.action)) == "EXIT"]
    assert exits and exits[0].symbol == "AAPL"
    assert overlay.state.positions == {}
