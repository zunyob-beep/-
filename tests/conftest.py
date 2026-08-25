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


#: 심어둘 표식 모양. 눈에 띄되 흔하지 않은 모양이면 된다.
MARKER = [100.0, 99.2, 98.3, 99.6, 101.1]


def planted_signal(
    seed: int = 1, occurrences: int = 90, drift: float = 0.0025, plant: bool = True
) -> list[float]:
    """표식 직후 반드시 오르는 종가 열. 마지막이 표식으로 끝난다.

    두 가지를 일부러 지킨다.

    1. **표식 사이 간격을 무작위로** 둔다. 일정한 주기로 두면 순열검정이
       모든 매치를 같은 폭으로 밀 때 위상이 통째로 맞아떨어져, 귀무분포가
       관측 승률을 그대로 재현해 버린다(그 경우 "모양이 아니라 주기가
       원인"이라는 판정이 오히려 옳다).
    2. **마지막을 표식으로 끝낸다.** 이 도구는 '지금 직전 N개'를 질의하므로,
       데이터가 잡음 구간에서 끝나면 표식은 질의 대상조차 되지 않는다.

    `plant=False`면 표식 뒤에도 잡음이 이어진다 — 같은 구조에서 신호만
    없앤 대조군이다.
    """
    rng = np.random.default_rng(seed)
    closes: list[float] = []
    level = 40_000_000.0

    def noise(count: int) -> None:
        nonlocal level
        for _ in range(count):
            level *= float(np.exp(rng.normal(0, 0.0009)))
            closes.append(level)

    for _ in range(occurrences):
        noise(int(rng.integers(40, 160)))
        base = level
        for value in MARKER:
            closes.append(base * value / 100.0)
        level = closes[-1]
        if plant:
            for _ in range(6):
                level *= 1.0 + drift
                closes.append(level)
        else:
            noise(6)
        noise(int(rng.integers(10, 40)))

    base = level
    for value in MARKER:
        closes.append(base * value / 100.0)
    return closes


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
