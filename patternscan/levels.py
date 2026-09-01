"""지지선과 저항선, 그리고 그걸 찾는 데 쓰는 변곡점.

**무엇을 세는가.** 가격이 여러 번 되돌아선 자리는 다음에도 되돌아설 확률이
조금 높다 — 거기서 사고팔려던 사람들의 주문이 남아 있기 때문이라는 게
흔한 설명이다. 그게 맞든 아니든, "여러 번 되돌아섰던 자리"는 **셀 수 있는
사실**이다. 이 파일은 그 사실만 센다. 왜 그런지는 말하지 않는다.

**두 가지를 조심한다.**

1. 변곡점을 너무 촘촘히 잡으면 잡음의 톱니 하나하나가 '지지선'이 된다.
   좌우 `SWING`개보다 확실히 높거나 낮은 자리만 변곡점으로 본다.
2. 가까운 값끼리 묶지 않으면 같은 자리가 선 열 개로 나온다. 값이
   **변동성 대비** 가까우면 한 자리로 묶는다 — 비트코인 1억에서 10만원과
   엑스알피 3천원에서 10만원은 전혀 다른 거리다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import Series

#: 변곡점: 좌우 이만큼보다 높아야(낮아야) 꼭짓점으로 인정한다.
SWING = 5

#: 같은 자리로 묶을 거리. 변동폭(ATR) 대비 비율이다.
CLUSTER = 0.6

#: 이 정도는 닿아야 '선'으로 쳐 준다. 한 번 스친 자리는 선이 아니다.
MIN_TOUCHES = 2

#: 지금 값에서 이보다 멀면 안 보여준다. 단타에 5% 밖의 선은 소용없다.
FAR = 0.05

#: 얼마나 최근까지만 볼지. 8년치를 받아뒀다고 2018년의 지지선이 오늘
#: 20분짜리 거래에 쓸모 있을 리 없다. 게다가 420만 봉을 훑으면 봉 간격마다
#: 몇 초씩 잡아먹는다 — 재보니 지지·저항 2.3초, 이론 읽기 5.6초였다.
#: 1분봉이면 사흘 반, 5분봉이면 보름 남짓이다.
RECENT = 5000


@dataclass(frozen=True)
class Level:
    """가격이 여러 번 되돌아섰던 자리."""

    price: float
    touches: int
    #: 마지막으로 닿은 게 몇 봉 전인지. 오래된 선일수록 약하다고 본다.
    last_touch: int
    kind: str            # "지지" | "저항"

    @property
    def strength(self) -> float:
        """닿은 횟수와 최근성을 함께 본 점수. 비교용이지 확률이 아니다."""
        return self.touches * (1.0 / (1.0 + self.last_touch / 500.0))


def recent(series: Series, window: int = RECENT) -> Series:
    """최근 구간만 잘라 준다. 오래된 자리는 지금 거래에 쓸모가 없다."""
    return series if len(series) <= window else series.slice(len(series) - window, len(series))


def atr(series: Series, window: int = 100) -> float:
    """평균 진폭. '가깝다'를 종목·시기와 무관하게 재기 위한 자다.

    비트코인 1억에서의 10만원과 엑스알피 3천원에서의 10만원은 전혀 다른
    거리다. 퍼센트로 재도 되지만, 조용한 장과 요동치는 장의 1%도 다르다.
    """
    if len(series) < 2:
        return 0.0
    high = series.high[-window:]
    low = series.low[-window:]
    close = series.close[-window:]
    spans = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )
    return float(np.mean(spans)) if spans.size else 0.0


def swings(series: Series, reach: int = SWING) -> tuple[np.ndarray, np.ndarray]:
    """(고점 위치, 저점 위치). 좌우 `reach`개보다 확실히 높거나 낮은 자리.

    양옆을 다 보므로 마지막 `reach`개에서는 변곡점이 안 나온다 — 아직
    오른쪽이 안 그려졌기 때문이다. 그게 맞다. 지금 값이 꼭짓점인지는
    나중에야 알 수 있고, 미리 아는 척하면 그게 미래를 보는 것이다.
    """
    n = len(series)
    if n < 2 * reach + 1:
        return np.array([], dtype=int), np.array([], dtype=int)

    high, low = series.high, series.low
    middle = np.arange(reach, n - reach)
    is_high = np.ones(middle.size, dtype=bool)
    is_low = np.ones(middle.size, dtype=bool)
    for step in range(1, reach + 1):
        is_high &= (high[middle] >= high[middle - step]) & (high[middle] >= high[middle + step])
        is_low &= (low[middle] <= low[middle - step]) & (low[middle] <= low[middle + step])
    return middle[is_high], middle[is_low]


def _cluster(prices: np.ndarray, ages: np.ndarray, tolerance: float, kind: str) -> list[Level]:
    """가까운 값끼리 한 자리로 묶는다."""
    if prices.size == 0 or tolerance <= 0:
        return []
    order = np.argsort(prices)
    prices, ages = prices[order], ages[order]

    out: list[Level] = []
    start = 0
    for i in range(1, prices.size + 1):
        if i < prices.size and prices[i] - prices[start] <= tolerance:
            continue
        group = slice(start, i)
        out.append(
            Level(
                price=float(np.mean(prices[group])),
                touches=int(i - start),
                last_touch=int(np.min(ages[group])),
                kind=kind,
            )
        )
        start = i
    return out


def levels(series: Series, reach: int = SWING, most: int = 3) -> list[Level]:
    """지금 값 위아래로 가장 그럴듯한 선 몇 개씩.

    위는 저항, 아래는 지지다. 지금 값을 이미 뚫은 선은 반대쪽이 되므로
    (뚫린 저항은 지지가 된다는 흔한 이야기) 방향은 **지금 값 기준으로**
    다시 매긴다.

    최근 RECENT개만 본다 — 왜 그런지는 그 상수의 설명에 적어 두었다.
    """
    series = recent(series)
    if len(series) < 2 * reach + 2:
        return []
    span = atr(series)
    if span <= 0:
        return []

    highs, lows = swings(series, reach)
    if highs.size == 0 and lows.size == 0:
        return []

    n = len(series)
    now = float(series.close[-1])
    tolerance = span * CLUSTER

    found = _cluster(series.high[highs], n - 1 - highs, tolerance, "저항")
    found += _cluster(series.low[lows], n - 1 - lows, tolerance, "지지")

    # 방향은 지금 값이 정한다. 뚫린 저항은 더 이상 저항이 아니다.
    kept = []
    for one in found:
        if one.touches < MIN_TOUCHES:
            continue
        if abs(one.price - now) / now > FAR:
            continue
        kept.append(
            Level(one.price, one.touches, one.last_touch, "저항" if one.price > now else "지지")
        )

    above = sorted([x for x in kept if x.kind == "저항"], key=lambda x: x.price)
    below = sorted([x for x in kept if x.kind == "지지"], key=lambda x: -x.price)
    # 가까운 것부터 고르되, 그중 힘센 것을 남긴다.
    above = sorted(above[: most * 2], key=lambda x: -x.strength)[:most]
    below = sorted(below[: most * 2], key=lambda x: -x.strength)[:most]
    return sorted(above + below, key=lambda x: -x.price)


#: 피보나치 되돌림 비율. 왜 하필 이 숫자냐는 근거는 약하지만, 많은 사람이
#: 이 자리를 보고 주문을 걸기 때문에 실제로 자주 멈춘다 — 자기실현적이다.
FIBONACCI = (0.236, 0.382, 0.5, 0.618, 0.786)


def retracements(series: Series, reach: int = SWING) -> list[Level]:
    """마지막 큰 파동의 피보나치 되돌림 자리.

    되돌림은 '닿은 횟수'가 없다. 그래서 touches를 0으로 두고, 지지·저항과
    섞이지 않게 화면에서 따로 표시한다.
    """
    series = recent(series)
    if len(series) < 2 * reach + 2:
        return []
    highs, lows = swings(series, reach)
    if highs.size == 0 or lows.size == 0:
        return []

    top_at, bottom_at = int(highs[-1]), int(lows[-1])
    top, bottom = float(series.high[top_at]), float(series.low[bottom_at])
    if top <= bottom:
        return []

    now = float(series.close[-1])
    rising = bottom_at < top_at        # 저점 뒤에 고점이면 올라온 파동
    out = []
    for ratio in FIBONACCI:
        price = top - (top - bottom) * ratio if rising else bottom + (top - bottom) * ratio
        if abs(price - now) / now > FAR:
            continue
        out.append(Level(price, 0, 0, "저항" if price > now else "지지"))
    return out
