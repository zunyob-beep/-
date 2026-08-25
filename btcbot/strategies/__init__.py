"""전략 모음. import 시점에 레지스트리에 자동 등록된다."""

from __future__ import annotations

from .base import Strategy, available, get_strategy, register, strategy_class
from .ma_cross import MACross
from .rsi_reversion import RSIReversion
from .rule import RuleStrategy
from .volatility_breakout import VolatilityBreakout

__all__ = [
    "MACross",
    "RSIReversion",
    "RuleStrategy",
    "Strategy",
    "VolatilityBreakout",
    "available",
    "get_strategy",
    "register",
    "strategy_class",
]
