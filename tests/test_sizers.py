"""Tests for risk-based position sizers (hand-computed expectations)."""

import pytest

from trade_risk.sizers import (
    EqualWeightSizer,
    FixedFractionalSizer,
    FixedNotionalSizer,
    KellySizer,
    SizingContext,
    VolatilityTargetSizer,
)


def ctx(**kw):
    base = dict(equity=100_000.0, price=50.0)
    base.update(kw)
    return SizingContext(**base)


def test_fixed_fractional():
    s = FixedFractionalSizer(risk_pct=0.01, atr_multiple=2.0)
    # risk $1,000; stop distance = 2 * $2.50 = $5 -> 200 shares
    assert s.quantity(ctx(atr=2.5)) == pytest.approx(200.0)


def test_fixed_fractional_no_atr():
    assert FixedFractionalSizer().quantity(ctx()) == 0.0


def test_volatility_target():
    s = VolatilityTargetSizer(target_vol=0.15)
    # 100k * 0.15 / (0.30 * 50) = 1000 shares
    assert s.quantity(ctx(volatility=0.30)) == pytest.approx(1000.0)


def test_equal_weight():
    s = EqualWeightSizer(n_positions=10)
    assert s.quantity(ctx()) == pytest.approx(200.0)  # 10k / 50


def test_kelly():
    s = KellySizer(fraction=0.5, cap=0.25)
    # edge = 0.6 - 0.4/1.5 = 1/3; f = 0.5/3 = 1/6 -> 100k/6/50 = 333.33
    assert s.quantity(ctx(win_prob=0.6, payoff=1.5)) == pytest.approx(100000 / 6 / 50)


def test_kelly_no_edge_is_zero():
    s = KellySizer()
    assert s.quantity(ctx(win_prob=0.4, payoff=1.0)) == 0.0


def test_kelly_cap():
    s = KellySizer(fraction=1.0, cap=0.10)
    # edge = 0.9 - 0.1/2 = 0.85 -> capped at 10% -> 100k*0.1/50 = 200
    assert s.quantity(ctx(win_prob=0.9, payoff=2.0)) == pytest.approx(200.0)


def test_fixed_notional():
    assert FixedNotionalSizer(notional=25_000).quantity(ctx()) == pytest.approx(500.0)


def test_zero_price_is_zero():
    assert EqualWeightSizer().quantity(ctx(price=0.0)) == 0.0


def test_with_params_clone():
    s = EqualWeightSizer(n_positions=10).with_params(n_positions=5)
    assert s.quantity(ctx()) == pytest.approx(400.0)


def test_invalid_params():
    with pytest.raises(ValueError):
        FixedFractionalSizer(risk_pct=0.0)
    with pytest.raises(ValueError):
        KellySizer(cap=1.5)
    with pytest.raises(ValueError):
        EqualWeightSizer(n_positions=0)
