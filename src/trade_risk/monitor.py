"""Stateful monitors: drawdown tracking across an equity stream."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DrawdownMonitor:
    """Tracks peak equity and current drawdown tick by tick.

    ``update(equity)`` returns ``(drawdown, status)`` where status is
    one of ``"ok"``, ``"warn"``, ``"halt"``, ``"flatten"`` according to
    the configured tiers.
    """

    warn: float = 0.05
    halt: float = 0.10
    flatten: float = 0.15
    peak: float = 0.0
    drawdown: float = 0.0

    def __post_init__(self) -> None:
        if not 0 < self.warn < self.halt < self.flatten <= 1:
            raise ValueError("need 0 < warn < halt < flatten <= 1")

    def update(self, equity: float) -> tuple[float, str]:
        if equity > self.peak:
            self.peak = equity
        self.drawdown = (
            0.0 if self.peak <= 0 else max(0.0, (self.peak - equity) / self.peak)
        )
        return self.drawdown, self.status()

    def status(self) -> str:
        dd = self.drawdown
        if dd >= self.flatten:
            return "flatten"
        if dd >= self.halt:
            return "halt"
        if dd >= self.warn:
            return "warn"
        return "ok"

    def reset(self, equity: float = 0.0) -> None:
        self.peak = equity
        self.drawdown = 0.0
