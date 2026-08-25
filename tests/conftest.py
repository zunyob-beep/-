from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from btcbot.models import Candle


def make_candle(
    ts: datetime,
    open_: float,
    high: float | None = None,
    low: float | None = None,
    close: float | None = None,
    volume: float = 1.0,
    market: str = "KRW-BTC",
) -> Candle:
    close = open_ if close is None else close
    high = max(open_, close) if high is None else high
    low = min(open_, close) if low is None else low
    return Candle(market=market, ts=ts, open=open_, high=high, low=low, close=close, volume=volume)


def series(prices, start: datetime | None = None, step: timedelta | None = None) -> list[Candle]:
    """종가 리스트를 일봉 시퀀스로. 시가는 직전 종가로 이어붙인다."""
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    step = step or timedelta(days=1)
    candles = []
    prev = prices[0]
    for i, price in enumerate(prices):
        candles.append(
            make_candle(
                start + step * i,
                open_=prev,
                high=max(prev, price) * 1.002,
                low=min(prev, price) * 0.998,
                close=price,
            )
        )
        prev = price
    return candles


@pytest.fixture
def uptrend() -> list[Candle]:
    return series([100.0 * (1.01**i) for i in range(120)])


@pytest.fixture
def downtrend() -> list[Candle]:
    return series([100.0 * (0.99**i) for i in range(120)])


@pytest.fixture
def choppy() -> list[Candle]:
    return series([100.0 + 5 * math.sin(i / 3) for i in range(200)])


@pytest.fixture
def sample_candles(uptrend) -> list[Candle]:
    return uptrend
