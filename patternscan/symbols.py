"""모양을 기호로 바꿔서 비교한다 — '구간별로 자르기'.

무엇이 다른가
-------------
shape.py는 모양을 연속된 숫자로 두고 상관계수로 비교한다. 여기서는 봉마다의
움직임을 **몇 개 구간 중 하나로 자른 뒤** 기호가 같은지를 센다.

    연속:  [-0.31, +1.42, +0.05, -0.88, +1.21]   → 상관계수 0.87
    기호:  [  1,     4,     2,     1,     4  ]   → 5칸 중 5칸 일치

왜 이게 나을 수 있나
-------------------
상관계수 0.85를 요구하면 길이가 길수록 만족하는 과거 구간이 거의 없다.
1분봉 43,200개에서 길이 180으로 재보면 다섯 번에 한 번만 표본 20개를 채웠다.
기호로 자르면 비슷한 것끼리 더 잘 묶여 **표본이 늘고 통계적 힘이 세진다**.

왜 나쁠 수 있나
--------------
정보를 버린다. 그리고 경계에 걸친 움직임이 반대 기호로 튄다 —
+0.09%와 +0.11%가 다른 기호가 될 수 있다. 구간을 잘게 자를수록 이 문제는
줄지만, 대신 다시 안 묶여서 표본이 준다.

어느 쪽이 나은지는 정해져 있지 않다. `validate`로 재서 고르면 된다.

경계를 어떻게 잡나
-----------------
**창 안에서의 표준편차로 나눈 뒤** 정규분포 분위수로 자른다. 고정된 퍼센트
(예: ±0.1%)로 자르지 않는 이유는, 비트코인의 변동성이 시기마다 크게 다르기
때문이다. 2022년 하락장의 0.1%와 2023년 조용한 구간의 0.1%는 전혀 다른
사건인데, 고정 경계로 자르면 한쪽은 전부 '급등락', 다른 쪽은 전부 '보합'이
되어 비교가 안 된다.
"""

from __future__ import annotations

import numpy as np

from .shape import normalize_window, sliding_windows

#: 기본 구간 수. 5개면 급등/상승/보합/하락/급락 정도의 해상도다.
DEFAULT_BUCKETS = 5


def breakpoints(buckets: int) -> np.ndarray:
    """표준정규분포를 같은 확률로 `buckets`등분하는 경계.

    5등분이면 [-0.84, -0.25, +0.25, +0.84] — 각 구간에 20%씩 들어간다.
    같은 확률로 나눠야 특정 기호만 잔뜩 나오는 일이 없다.
    """
    if buckets < 2:
        raise ValueError("구간은 2개 이상이어야 합니다")
    # 정규분포의 분위수 (scipy 없이 역오차함수 근사)
    probabilities = np.arange(1, buckets) / buckets
    return _normal_quantile(probabilities)


def _normal_quantile(p: np.ndarray) -> np.ndarray:
    """표준정규분포의 분위수. Acklam 근사 — 소수점 아래 9자리까지 맞다."""
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    p = np.asarray(p, dtype=np.float64)
    out = np.empty_like(p)
    low, high = p < 0.02425, p > 1 - 0.02425
    middle = ~(low | high)

    q = np.sqrt(-2 * np.log(np.where(low, p, 0.5)))
    out = np.where(
        low,
        (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1),
        out,
    )
    q = np.sqrt(-2 * np.log(np.where(high, 1 - p, 0.5)))
    out = np.where(
        high,
        -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1),
        out,
    )
    q = np.where(middle, p, 0.5) - 0.5
    r = q * q
    out = np.where(
        middle,
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1),
        out,
    )
    return out


def symbolize(window: np.ndarray, buckets: int = DEFAULT_BUCKETS) -> np.ndarray:
    """봉 하나짜리 창(1차원) 또는 여러 창(2차원)을 기호 열로 바꾼다.

    **가격 경로 자체**를 정규화한 뒤 구간에 넣는다 (shape.normalize_window와
    같은 정규화). 길이 N인 창은 기호 N개가 된다.

    왜 '변화율'이 아니라 '경로'인가
    ------------------------------
    처음에는 봉마다의 변화율을 기호화했는데, 그러면 변화율의 평균을 빼는
    과정에서 **전체 방향이 지워진다**. 실제로 시험해 보니

        가속 상승 [100, 100.5, 101.2, 102.4, 104] → [0 1 3 4]
        감속 하락 [104, 102.4, 101.2, 100.5, 100] → [0 1 3 4]   ← 똑같다

    오를지 내릴지가 질문인데 그 정보를 버리고 있었다. 경로를 정규화하면
    상승은 기호가 커지는 열, 하락은 작아지는 열이 되어 구분된다.
    """
    values = np.asarray(window, dtype=np.float64)
    if values.shape[-1] < 2:
        raise ValueError("창 길이는 2 이상이어야 합니다")
    path = normalize_window(values)
    return np.searchsorted(breakpoints(buckets), path).astype(np.int8)


def symbolize_all(values: np.ndarray, length: int, buckets: int = DEFAULT_BUCKETS) -> np.ndarray:
    """모든 길이-`length` 구간의 기호 열. (구간 수, length) 모양."""
    windows = sliding_windows(values, length)
    if windows.shape[0] == 0:
        return np.empty((0, length), dtype=np.int8)
    return symbolize(windows, buckets)


def mismatch_fraction(
    query: np.ndarray, values: np.ndarray, length: int,
    buckets: int = DEFAULT_BUCKETS, chunk: int = 20_000,
) -> np.ndarray:
    """각 구간이 질의와 **몇 칸이나 다른지**의 비율 (0=완전히 같음, 1=전부 다름).

    이 값을 그대로 '거리'로 쓰면 연속 방식과 같은 배관을 탈 수 있다.
    유사도 0.85는 '기호의 15%까지만 달라도 된다'는 뜻이 된다.
    """
    windows = sliding_windows(values, length)
    if windows.shape[0] == 0:
        return np.empty(0, dtype=np.float64)

    target = symbolize(np.asarray(query, dtype=np.float64), buckets)
    out = np.empty(windows.shape[0], dtype=np.float64)
    for start in range(0, windows.shape[0], chunk):
        block = symbolize(windows[start : start + chunk], buckets)
        out[start : start + block.shape[0]] = np.count_nonzero(block != target, axis=1) / target.size
    return out
