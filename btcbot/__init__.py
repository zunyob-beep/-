"""btcbot — 업비트 비트코인 자동매매 봇.

백테스트 · 페이퍼 트레이딩 · 실거래가 같은 엔진 위에서 돈다.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .backtest import BacktestResult, grid_search, run_backtest, walk_forward
from .config import Settings
from .engine import Engine, EngineConfig
from .exchange import SimulatedBroker, UpbitBroker, UpbitClient
from .feed import BacktestFeed, LiveFeed
from .metrics import Performance, analyze
from .models import AccountState, Action, Candle, Fill, Position, Signal
from .risk import RiskConfig, RiskManager
from .strategies import Strategy, available, get_strategy

__all__ = [
    "AccountState",
    "Action",
    "BacktestFeed",
    "BacktestResult",
    "Candle",
    "Engine",
    "EngineConfig",
    "Fill",
    "LiveFeed",
    "Performance",
    "Position",
    "RiskConfig",
    "RiskManager",
    "Settings",
    "Signal",
    "SimulatedBroker",
    "Strategy",
    "UpbitBroker",
    "UpbitClient",
    "__version__",
    "analyze",
    "available",
    "get_strategy",
    "grid_search",
    "run_backtest",
    "walk_forward",
]
