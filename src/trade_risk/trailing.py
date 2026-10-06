"""ATR trailing stops: per-position exit devices.

A trailing stop is the simplest honest exit: let a winner run while the
market confirms the trend, then exit when the price falls more than a
volatility-scaled distance below the best price seen.  The distance is
Wilder's Average True Range (ATR), so the stop widens in volatile
markets and tightens in quiet ones.

The stop *ratchets*: a long stop only ever moves up (toward the price),
a short stop only ever moves down.  It never loosens, no matter what --
that is the whole point of the device.

Everything here is stdlib-only and broker-free, like the rest of
``trade-risk``: it consumes bar data (highs/lows/closes) and emits stop
prices.  Submitting orders is the caller's job.

Warmup
------
ATR is undefined until ``period`` bars have been seen: :func:`atr_wilder`
returns ``None`` and :meth:`TrailingStop.update` returns ``None`` rather
than a garbage stop.  The first bar has no previous close, so its true
range is ``high - low``; the first ATR is the mean of the first
``period`` true ranges, after which Wilder's recursion applies.

Negative prices
---------------
True ranges are computed with abs-based math and are therefore
non-negative by construction, even for WTI-style negative prints:
``TR = max(abs(high - low), abs(high - prev_close), abs(low - prev_close))``.
Negative inputs can neither flip a sign nor crash the engine; ATR and
stops derived from them are always finite and sane.

Activation
----------
``activation_pct`` (default ``0.0``) arms the stop only after the
position is up ``X%`` from the entry close, where the favourable move is
measured against ``abs(entry)`` so negative entry prices work.

The tradeoff is real and you should pick deliberately:

* Arm immediately (``0.0``): capital is protected from the first bar,
  but in chop the stop sits close and gets whipped -- expect premature
  stop-outs on noise.
* Arm late (e.g. ``0.05``): the position breathes through chop, but an
  early reversal back toward entry is unprotected -- you give back the
  whole move before the stop exists.

There is no right answer; there is only which loss you prefer.

Integration
-----------
This module ships the *engine*.  It is consumed by import; the logic is
never copied into the consuming repos:

* **trade-backtest** (simulation): keep one ``TrailingStop`` per open
  simulated position.  On each bar, feed the bar's high/low/close to
  ``update``; when it returns a stop and the close (or low/high for
  longs/shorts) touches it, emit the exit in the backtest's own order
  machinery.  The warmup rule means stops simply don't exist before
  ``period`` bars -- the backtest must not invent one.
* **trade-paper** (live): same pattern in the bar/event loop -- one
  tracker per live position, fed from the paper broker's bar stream.
  When a stop is touched, the *paper* layer submits the exit order
  through its normal path (with its own approval/kill-switch logic);
  this module never submits anything.

Both directions work because the tracker is pure state-in/state-out:
no broker, exchange, or UI imports anywhere in this file.
"""

from __future__ import annotations

from .base import LONG, SHORT, Side, require_positive, valid_number

__all__ = ["atr_wilder", "true_range", "TrailingStop"]


def _as_side(side: str | Side) -> Side:
    if isinstance(side, Side):
        return side
    try:
        return Side(str(side).upper())
    except ValueError:
        raise ValueError(
            f"side must be 'long' or 'short' (or Side.LONG/Side.SHORT), got {side!r}"
        ) from None


def true_range(high: float, low: float, prev_close: float | None) -> float:
    """One bar's true range, negative-price-safe.

    ``max(abs(high - low), abs(high - prev_close), abs(low - prev_close))``.
    ``prev_close=None`` (first bar) falls back to ``abs(high - low)``.
    Always non-negative, even for WTI-style negative prints.
    """
    high = valid_number(high, "high")
    low = valid_number(low, "low")
    components = [abs(high - low)]
    if prev_close is not None:
        prev_close = valid_number(prev_close, "prev_close")
        components.append(abs(high - prev_close))
        components.append(abs(low - prev_close))
    return max(components)


def atr_wilder(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> float | None:
    """Wilder's Average True Range over the bar series.

    The first ATR is the mean of the first ``period`` true ranges
    (bar 0's true range is ``high - low`` since it has no previous
    close); afterwards ``ATR_t = (ATR_{t-1} * (period - 1) + TR_t) /
    period``.

    Returns ``None`` during warmup (fewer than ``period`` bars) --
    never a partially-computed value.
    """
    if not isinstance(period, int) or isinstance(period, bool) or period < 1:
        raise ValueError(f"period must be a positive int, got {period!r}")
    n = len(highs)
    if not (n == len(lows) == len(closes)):
        raise ValueError(
            f"highs/lows/closes must have equal length, got {n}/{len(lows)}/{len(closes)}"
        )
    if n < period:
        return None  # warmup: ATR is undefined, not zero

    prev_close: float | None = None
    atr: float | None = None
    tr_sum = 0.0
    for i in range(n):
        tr = true_range(highs[i], lows[i], prev_close)
        if i < period:
            tr_sum += tr
            if i == period - 1:
                atr = tr_sum / period  # seed: mean of first `period` TRs
        else:
            assert atr is not None
            atr = (atr * (period - 1) + tr) / period  # Wilder recursion
        prev_close = valid_number(closes[i], f"closes[{i}]")
    assert atr is not None
    return atr


class TrailingStop:
    """Stateful ATR trailing stop for one position.

    Long:  ``stop = peak - multiplier * ATR``, ratchets up only.
    Short: ``stop = trough + multiplier * ATR``, ratchets down only.

    ``update(high, low, close)`` feeds one bar and returns the current
    stop, or ``None`` during ATR warmup (fewer than ``period`` bars) or
    before the ``activation_pct`` gate arms.  Once armed, the emitted
    stop is monotone by construction: ``max`` against the previous stop
    for longs, ``min`` for shorts -- an ATR spike can never loosen it.
    """

    name = "atr_trailing_stop"
    description = "Monotone ATR trailing stop; None during warmup/pre-activation."
    DEFAULT_PARAMS = {"multiplier": 3.0, "period": 14, "activation_pct": 0.0}

    def __init__(
        self,
        side: str | Side,
        multiplier: float = 3.0,
        period: int = 14,
        activation_pct: float = 0.0,
    ) -> None:
        self.side = _as_side(side)
        if self.side is Side.EXIT:
            raise ValueError("side must be 'long' or 'short', not EXIT")
        self.multiplier = require_positive(multiplier, "multiplier")
        if not isinstance(period, int) or isinstance(period, bool) or period < 1:
            raise ValueError(f"period must be a positive int, got {period!r}")
        self.period = period
        activation_pct = valid_number(activation_pct, "activation_pct")
        if activation_pct < 0:
            raise ValueError(f"activation_pct must be >= 0, got {activation_pct!r}")
        self.activation_pct = activation_pct

        # bar history (recomputed ATR each update: O(n), auditable)
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._closes: list[float] = []
        self._entry: float | None = None  # close of the first bar seen
        self._peak: float | None = None  # long: highest high since arming
        self._trough: float | None = None  # short: lowest low since arming
        self._stop: float | None = None  # last emitted stop (the ratchet)

    @property
    def bars_seen(self) -> int:
        return len(self._closes)

    @property
    def atr(self) -> float | None:
        """Current Wilder ATR, or ``None`` during warmup."""
        return atr_wilder(self._highs, self._lows, self._closes, self.period)

    @property
    def stop(self) -> float | None:
        """Last emitted stop (the ratcheted value), ``None`` if never armed."""
        return self._stop

    @property
    def armed(self) -> bool:
        """Whether the activation gate has opened."""
        if self._entry is None:
            return False
        if self.activation_pct == 0.0:
            return True
        denom = abs(self._entry)
        if denom == 0.0:
            return False  # zero entry: gate can never open
        if self.side is Side.LONG:
            move = (self._closes[-1] - self._entry) / denom
        else:
            move = (self._entry - self._closes[-1]) / denom
        return move >= self.activation_pct

    def update(self, high: float, low: float, close: float) -> float | None:
        """Feed one bar; return the current stop or ``None``.

        ``None`` means "no stop yet" -- ATR warmup (fewer than
        ``period`` bars) or the activation gate not yet opened.  It
        never means a zero or garbage stop; callers must treat it as
        "position runs unprotected this bar".
        """
        high = valid_number(high, "high")
        low = valid_number(low, "low")
        close = valid_number(close, "close")
        self._highs.append(high)
        self._lows.append(low)
        self._closes.append(close)
        if self._entry is None:
            self._entry = close

        # Peak/trough track every bar from entry -- the ratchet only
        # governs the *emitted* stop, not the extremes it is built from.
        if self.side is Side.LONG:
            self._peak = high if self._peak is None else max(self._peak, high)
        else:
            self._trough = low if self._trough is None else min(self._trough, low)

        atr = self.atr
        if atr is None or not self.armed:
            return None

        if self.side is Side.LONG:
            assert self._peak is not None
            raw = self._peak - self.multiplier * atr
            self._stop = raw if self._stop is None else max(self._stop, raw)
        else:
            assert self._trough is not None
            raw = self._trough + self.multiplier * atr
            self._stop = raw if self._stop is None else min(self._stop, raw)
        return self._stop

    def reset(self) -> None:
        """Clear all state (e.g. after the position closes)."""
        self._highs.clear()
        self._lows.clear()
        self._closes.clear()
        self._entry = None
        self._peak = None
        self._trough = None
        self._stop = None

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "params": {
                "side": self.side.value,
                "multiplier": self.multiplier,
                "period": self.period,
                "activation_pct": self.activation_pct,
            },
            "defaults": dict(self.DEFAULT_PARAMS),
        }
