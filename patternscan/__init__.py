"""patternscan — 과거에 같은 모양이 있었는지 찾아 단타 진입 여부를 판정한다.

주문 기능은 없다. 시세를 읽고 통계를 내는 것까지가 전부다.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .data import fetch, load_cached
from .models import HORIZONS, TIMEFRAMES, WINDOW_LENGTHS, Candle, Series
from .scan import Match, Outcome, ScanResult, round_trip_cost, scan, scan_all
from .stats import Finding, Verdict, decide, evaluate
from .upbit import UpbitClient

__all__ = [
    "HORIZONS",
    "TIMEFRAMES",
    "WINDOW_LENGTHS",
    "Candle",
    "Finding",
    "Match",
    "Outcome",
    "ScanResult",
    "Series",
    "UpbitClient",
    "Verdict",
    "__version__",
    "decide",
    "evaluate",
    "fetch",
    "load_cached",
    "round_trip_cost",
    "scan",
    "scan_all",
]
