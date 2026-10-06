"""Tests for the ATR trailing-stop engine (trade_risk.trailing)."""

import math

import pytest

from trade_risk import LONG, SHORT
from trade_risk.base import Side
from trade_risk.trailing import TrailingStop, atr_wilder, true_range


# Hand-computed fixture, period = 3.
#
#   bar0: H=10.0 L= 8.0 C= 9.0 -> TR0 = |10-8| = 2.0   (no prev close)
#   bar1: H=11.0 L= 9.0 C=10.5 -> TR1 = max(|11-9|=2.0, |11-9|=2.0, |9-9|=0.0) = 2.0
#   bar2: H=10.0 L= 8.5 C= 9.0 -> TR2 = max(|10-8.5|=1.5, |10-10.5|=0.5, |8.5-10.5|=2.0) = 2.0
#   bar3: H=12.0 L= 9.0 C=11.0 -> TR3 = max(|12-9|=3.0, |12-9|=3.0, |9-9|=0.0) = 3.0
#   bar4: H=11.0 L= 9.5 C=10.0 -> TR4 = max(|11-9.5|=1.5, |11-11|=0.0, |9.5-11|=1.5) = 1.5
#
# ATR (period 3): seed at bar2 = (2+2+2)/3 = 2.0
#                 bar3: (2.0*2 + 3.0)/3 = 7/3 = 2.3333...
#                 bar4: (2.3333*2 + 1.5)/3 = 6.1666.../3 = 2.0555...
HIGHS = [10.0, 11.0, 10.0, 12.0, 11.0]
LOWS = [8.0, 9.0, 8.5, 9.0, 9.5]
CLOSES = [9.0, 10.5, 9.0, 11.0, 10.0]


def test_true_range_first_bar_and_negative_safe():
    # no previous close: falls back to |high - low|
    assert true_range(10.0, 8.0, None) == 2.0
    # WTI-style negative prints: abs math keeps TR non-negative, no sign flip
    tr = true_range(-37.63, -41.00, -30.00)
    assert tr == pytest.approx(max(abs(-37.63 - -41.00), abs(-37.63 - -30.00), abs(-41.00 - -30.00)))
    assert tr >= 0.0 and math.isfinite(tr)
    # inverted data (low > high) cannot produce a negative TR either
    assert true_range(8.0, 10.0, 9.0) >= 0.0


def test_atr_wilder_hand_computed():
    # warmup: fewer than `period` bars -> None, never a partial value
    assert atr_wilder(HIGHS[:2], LOWS[:2], CLOSES[:2], period=3) is None
    # seed at exactly `period` bars
    assert atr_wilder(HIGHS[:3], LOWS[:3], CLOSES[:3], period=3) == pytest.approx(2.0)
    # Wilder recursion
    assert atr_wilder(HIGHS[:4], LOWS[:4], CLOSES[:4], period=3) == pytest.approx(7 / 3)
    assert atr_wilder(HIGHS, LOWS, CLOSES, period=3) == pytest.approx(
        (2 * (7 / 3) + 1.5) / 3
    )


def test_atr_wilder_validation():
    with pytest.raises(ValueError):
        atr_wilder(HIGHS[:3], LOWS[:3], CLOSES[:2], period=3)  # ragged
    with pytest.raises(ValueError):
        atr_wilder(HIGHS[:3], LOWS[:3], CLOSES[:3], period=0)
    with pytest.raises(ValueError):
        atr_wilder(HIGHS[:3], LOWS[:3], CLOSES[:3], period="3")
    with pytest.raises(ValueError):
        atr_wilder([10.0, math.nan, 10.0], LOWS[:3], CLOSES[:3], period=3)


def test_long_stop_hand_computed_and_ratchets_up():
    ts = TrailingStop("long", multiplier=3.0, period=3)
    # bar0, bar1: warmup -> None
    assert ts.update(10.0, 8.0, 9.0) is None
    assert ts.update(11.0, 9.0, 10.5) is None
    # bar2: ATR=2.0, peak(high)=11.0 -> stop = 11 - 3*2 = 5.0
    assert ts.update(10.0, 8.5, 9.0) == pytest.approx(5.0)
    # bar3: ATR=7/3, peak(high)=12.0 -> raw = 12 - 7 = 5.0 -> ratchet holds 5.0
    assert ts.update(12.0, 9.0, 11.0) == pytest.approx(5.0)
    # bar4: ATR=(2*7/3+1.5)/3, peak stays 12 -> raw = 12-3*ATR > 5.0, ratchets UP
    stop = ts.update(11.0, 9.5, 10.0)
    assert stop == pytest.approx(12.0 - 3.0 * ((2 * (7 / 3) + 1.5) / 3))
    assert stop > 5.0
    assert ts.stop == pytest.approx(stop)
    assert ts.bars_seen == 5


def test_long_ratchet_monotone_over_whipsaw():
    ts = TrailingStop("long", multiplier=2.0, period=3)
    bars = [
        (10, 9, 9.5), (12, 10, 11.5), (14, 12, 13.5),   # warmup, run up
        (11, 9, 9.5),   # sharp pullback: ATR spikes, stop must NOT loosen
        (10, 8, 8.5),   # deeper pullback
        (15, 13, 14.5), # new high: stop may tighten upward
        (13, 11, 11.5), # whipsaw down
        (16, 14, 15.5), # new high again
    ]
    stops = [ts.update(h, l, c) for h, l, c in bars]
    emitted = [s for s in stops if s is not None]
    assert emitted, "expected some stops after warmup"
    for prev, cur in zip(emitted, emitted[1:]):
        assert cur >= prev, f"long stop loosened: {prev} -> {cur}"


def test_short_ratchet_monotone_over_whipsaw():
    ts = TrailingStop("short", multiplier=2.0, period=3)
    bars = [
        (10, 9, 9.5), (9, 7, 7.5), (8, 6, 6.5),      # warmup, run down
        (10, 9, 9.5),   # sharp rally: ATR spikes, stop must NOT loosen (up)
        (11, 10, 10.5), # deeper rally
        (7, 5, 5.5),    # new low: stop may tighten downward
        (9, 7, 7.5),    # whipsaw up
        (6, 4, 4.5),    # new low again
    ]
    stops = [ts.update(h, l, c) for h, l, c in bars]
    emitted = [s for s in stops if s is not None]
    assert emitted, "expected some stops after warmup"
    for prev, cur in zip(emitted, emitted[1:]):
        assert cur <= prev, f"short stop loosened: {prev} -> {cur}"


def test_short_side_end_to_end():
    # period=2 hand check: TR0=|10-9|=1, TR1=max(|9-8|=1,|9-9.5|=0.5,|8-9.5|=1.5)=1.5
    # seed ATR at bar1 = (1+1.5)/2 = 1.25; trough(low)=min(9,8)=8 -> stop = 8 + 2*1.25 = 10.5
    ts = TrailingStop("short", multiplier=2.0, period=2)
    assert ts.update(10.0, 9.0, 9.5) is None       # warmup
    assert ts.update(9.0, 8.0, 8.5) == pytest.approx(10.5)
    assert ts.armed and ts.atr == pytest.approx(1.25)


def test_activation_gate():
    # entry close = 100.0; gate at +5% needs close >= 105 before a stop appears
    ts = TrailingStop("long", multiplier=3.0, period=3, activation_pct=0.05)
    assert ts.update(101, 99, 100.0) is None   # warmup anyway
    assert ts.update(102, 100, 101.0) is None  # warmup anyway
    assert ts.update(103, 101, 102.0) is None  # ATR ready (3 bars) but gate closed: +2% < 5%
    assert not ts.armed
    s = ts.update(106, 104, 105.5)             # +5.5% -> gate opens, stop appears
    assert ts.armed
    assert s is not None and math.isfinite(s)


def test_activation_gate_short_and_zero_entry():
    ts = TrailingStop("short", multiplier=2.0, period=2, activation_pct=0.10)
    ts.update(100, 98, 99.0)   # warmup
    # short is up 10% when price FALLS 10% from entry: needs close <= 89.1
    assert ts.update(96, 94, 95.0) is None  # only -4%: gate closed
    assert ts.update(90, 88, 88.0) is not None  # -11.1%: gate open
    # zero entry price: gate can never open, but no crash; update still feeds bars
    z = TrailingStop("long", multiplier=2.0, period=2, activation_pct=0.05)
    z.update(0.5, -0.5, 0.0)
    assert z.update(1.5, 0.5, 1.0) is None
    assert not z.armed


def test_warmup_never_emits_garbage():
    for side in ("long", "short"):
        ts = TrailingStop(side, period=14)
        for i in range(13):  # one bar short of warmup
            assert ts.update(100 + i, 99 + i, 99.5 + i) is None
        assert ts.stop is None
        assert ts.update(113, 112, 112.5) is not None  # 14th bar: defined


def test_negative_prices_end_to_end():
    # WTI-style: every input negative. TRs stay non-negative; no sign flips.
    ts = TrailingStop("long", multiplier=3.0, period=3)
    bars = [(-30.0, -41.0, -37.6), (-25.0, -38.0, -30.0), (-20.0, -33.0, -25.0),
            (-10.0, -22.0, -15.0)]
    out = [ts.update(h, l, c) for h, l, c in bars]
    assert out[0] is None and out[1] is None  # warmup
    for s in out[2:]:
        assert s is not None and math.isfinite(s)
    assert ts.atr is not None and ts.atr >= 0.0
    # monotone: a long stop may only rise
    assert out[3] >= out[2]


def test_constructor_validation():
    with pytest.raises(ValueError):
        TrailingStop("sideways")
    with pytest.raises(ValueError):
        TrailingStop("long", multiplier=0.0)
    with pytest.raises(ValueError):
        TrailingStop("long", multiplier=-2.0)
    with pytest.raises(ValueError):
        TrailingStop("long", period=0)
    with pytest.raises(ValueError):
        TrailingStop("long", activation_pct=-0.01)
    with pytest.raises(ValueError):
        TrailingStop("exit")
    # accepts the Side enum and the trade-suite aliases
    assert TrailingStop(LONG).side is Side.LONG
    assert TrailingStop(SHORT).side is Side.SHORT
    assert TrailingStop("LONG", multiplier=3, period=14).multiplier == 3.0


def test_reset_and_describe():
    ts = TrailingStop("long", multiplier=3.0, period=3, activation_pct=0.02)
    for h, l, c in [(10, 9, 9.5), (11, 10, 10.5), (12, 11, 11.5)]:
        ts.update(h, l, c)
    assert ts.bars_seen == 3
    d = ts.describe()
    assert d["params"] == {"side": "LONG", "multiplier": 3.0, "period": 3,
                          "activation_pct": 0.02}
    ts.reset()
    assert ts.bars_seen == 0 and ts.stop is None and not ts.armed
    assert ts.update(10, 9, 9.5) is None  # warmup again from scratch
