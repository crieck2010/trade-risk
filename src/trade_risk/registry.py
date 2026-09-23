"""Agent-facing registry of sizers and limits."""

from __future__ import annotations

from .limits import (
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
    RiskLimit,
    SymbolAllowlist,
    SymbolBlocklist,
)
from .sizers import (
    EqualWeightSizer,
    FixedFractionalSizer,
    FixedNotionalSizer,
    KellySizer,
    RiskSizer,
    VolatilityTargetSizer,
)

SIZER_REGISTRY: dict[str, type[RiskSizer]] = {
    cls.name: cls
    for cls in (
        FixedFractionalSizer,
        VolatilityTargetSizer,
        EqualWeightSizer,
        KellySizer,
        FixedNotionalSizer,
    )
}

LIMIT_REGISTRY: dict[str, type[RiskLimit]] = {
    cls.name: cls
    for cls in (
        MaxPositionNotional,
        MaxGrossExposure,
        MaxNetExposure,
        ConcentrationLimit,
        MaxOrderNotional,
        GrossExposureScale,
        SymbolAllowlist,
        SymbolBlocklist,
        MinPriceFilter,
        MaxDailyLoss,
        MaxDrawdown,
        DrawdownGuard,
        KillSwitch,
    )
}


def list_sizers() -> list[str]:
    return sorted(SIZER_REGISTRY)


def list_limits() -> list[str]:
    return sorted(LIMIT_REGISTRY)


def get_sizer(name: str, **params) -> RiskSizer:
    try:
        cls = SIZER_REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown sizer {name!r}; known: {list_sizers()}") from None
    return cls(**params)


def get_limit(name: str, **params) -> RiskLimit:
    try:
        cls = LIMIT_REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown limit {name!r}; known: {list_limits()}") from None
    return cls(**params)


def describe_sizers() -> list[dict]:
    return [SIZER_REGISTRY[name]().describe() for name in list_sizers()]


def describe_limits() -> list[dict]:
    out = []
    for name in list_limits():
        cls = LIMIT_REGISTRY[name]
        try:
            out.append(cls().describe())
        except TypeError:
            # Limits that require constructor args: describe via class metadata.
            out.append(
                {
                    "name": cls.name,
                    "description": cls.description,
                    "params": {},
                    "defaults": dict(cls.DEFAULT_PARAMS),
                }
            )
    return out
