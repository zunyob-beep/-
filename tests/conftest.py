from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from patternscan.models import Candle, Series, timeframe_length

START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def make_series(
    closes,
    market: str = "KRW-BTC",
    timeframe: str = "minute1",
    start: datetime | None = None,
    skip: set[int] | None = None,
) -> Series:
    """종가 목록으로 Series를 만든다.

    `skip`에 든 인덱스는 봉을 통째로 빼서 '거래가 없어 빠진 봉'을 흉내낸다.
    """
    start = start or START
    step = timeframe_length(timeframe)
    skip = skip or set()
    candles = []
    prev = closes[0]
    for i, close in enumerate(closes):
        if i in skip:
            prev = close
            continue
        candles.append(
            Candle(
                ts=start + step * i,
                open=prev,
                high=max(prev, close) * 1.001,
                low=min(prev, close) * 0.999,
                close=float(close),
                volume=1.0,
            )
        )
        prev = close
    return Series.from_candles(market, timeframe, candles)


def repeating(pattern, repeats: int, filler: float = 100.0, gap: int = 40):
    """같은 모양이 여러 번 나오는 종가 열을 만든다.

    모양 사이에는 무의미한 구간(filler)을 넣어 서로 겹치지 않게 한다.
    """
    out: list[float] = []
    for _ in range(repeats):
        out.extend(pattern)
        out.extend(filler + 0.01 * i for i in range(gap))
    return out


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(12345)


@pytest.fixture
def random_walk(rng) -> Series:
    steps = rng.normal(0, 0.0008, 6000)
    closes = 40_000_000 * np.exp(np.cumsum(steps))
    return make_series(closes.tolist())


@pytest.fixture
def flat_series() -> Series:
    return make_series([100.0] * 500)
