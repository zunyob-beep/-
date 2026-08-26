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
    seed: int = 1,
    occurrences: int = 90,
    drift: float = 0.0025,
    plant: bool = True,
    with_positions: bool = False,
) -> list[float] | tuple[list[float], list[int]]:
    """표식 직후 반드시 오르는 종가 열. 마지막이 표식으로 끝난다.

    세 가지를 일부러 지킨다. 앞선 판에서 셋 다 안 지켜서 시험이 엉뚱한 걸
    검사하고 있었다.

    1. **표식 사이 간격을 무작위로** 둔다. 일정한 주기로 두면 순열검정이
       모든 매치를 같은 폭으로 밀 때 위상이 통째로 맞아떨어져, 귀무분포가
       관측 승률을 그대로 재현해 버린다(그 경우 "모양이 아니라 주기가
       원인"이라는 판정이 오히려 옳다).
    2. **마지막을 표식으로 끝낸다.** 이 도구는 '지금 직전 N개'를 질의하므로,
       데이터가 잡음 구간에서 끝나면 표식은 질의 대상조차 되지 않는다.
       옛 시험은 상승 구간에서 끝나는 데이터를 썼고, 그래서 질의 모양이
       표식이 아니라 '곧게 오르는 직선'이었다 — 심어둔 신호를 찾는지는
       한 번도 검사하지 않은 채 "오르는 중이면 다음 봉도 오른다"만 확인하고
       있었다.
    3. **표식 직후 상승을 짧게(6봉)** 둔다. 길게 두면 그 안의 모든 구간이
       서로 완전히 같은 직선이 되어 거리 0짜리 후보가 수천 개 생기고,
       그중 무엇이 뽑히는지가 1e-14 수준의 부동소수점 차이로 갈린다.
       실제로 옛 시험은 그 이유로 로컬과 CI에서 결과가 달랐다.

    `plant=False`면 표식 뒤에도 잡음이 이어진다 — 같은 구조에서 신호만
    없앤 대조군이다.

    `with_positions=True`면 표식이 끝나는 위치도 같이 준다. 매치가 정말
    표식 자리에서 나왔는지 확인하는 데 쓴다.
    """
    rng = np.random.default_rng(seed)
    closes: list[float] = []
    marker_ends: list[int] = []
    level = 40_000_000.0

    def noise(count: int) -> None:
        nonlocal level
        for _ in range(count):
            level *= float(np.exp(rng.normal(0, 0.0009)))
            closes.append(level)

    def place_marker() -> None:
        nonlocal level
        base = level
        for value in MARKER:
            closes.append(base * value / 100.0)
        marker_ends.append(len(closes) - 1)
        level = closes[-1]

    for _ in range(occurrences):
        noise(int(rng.integers(40, 160)))
        place_marker()
        if plant:
            for _ in range(6):
                level *= 1.0 + drift
                closes.append(level)
        else:
            noise(6)
        noise(int(rng.integers(10, 40)))

    place_marker()
    return (closes, marker_ends) if with_positions else closes


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
