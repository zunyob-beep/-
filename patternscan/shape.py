"""'모양'을 숫자로 만들고 비교한다.

같은 모양이란 무엇인가 — 이 프로그램에서 제일 중요한 정의다.

가격을 그대로 비교하면 4,000만원 구간과 9,000만원 구간은 아무리 모양이
같아도 절대 안 겹친다. 그래서 두 단계로 정규화한다:

1. **첫 봉 대비 수익률 경로**로 바꾼다 (가격대 무관)
2. **표준편차로 나눈다** (변동성 무관 — 오르내린 '모양'만 남는다)

2번은 취향이 갈린다. 변동성까지 같아야 같은 모양이라고 볼 수도 있다.
그래서 `scale="shape"`(기본, 변동성 무시)와 `scale="amplitude"`(변동폭도
같아야 함)를 모두 제공한다.

거리는 정규화한 경로 사이의 RMSE다. 0이면 완전히 같은 모양이고,
값이 클수록 다르다.
"""

from __future__ import annotations

import numpy as np

#: 표준편차가 이보다 작으면 '움직임이 없는 구간'으로 본다(0으로 나누기 방지).
FLAT_EPS = 1e-12


def normalize_window(window: np.ndarray, scale: str = "shape") -> np.ndarray:
    """봉 하나짜리 창(1차원) 또는 여러 창(2차원)을 정규화한다.

    2차원이면 행마다 독립적으로 정규화한다.
    """
    values = np.asarray(window, dtype=np.float64)
    single = values.ndim == 1
    if single:
        values = values[None, :]

    # 1) 첫 값 대비 상대 변화 (가격대 제거)
    base = values[:, :1]
    with np.errstate(divide="ignore", invalid="ignore"):
        path = np.where(base != 0, values / base - 1.0, 0.0)

    # 2) 평균을 빼서 '수준'을 없애고, 원하면 표준편차로 나눠 '크기'도 없앤다
    path = path - path.mean(axis=1, keepdims=True)
    if scale == "shape":
        sd = path.std(axis=1, keepdims=True)
        path = np.divide(path, sd, out=np.zeros_like(path), where=sd > FLAT_EPS)
    elif scale != "amplitude":
        raise ValueError(f"scale은 'shape' 또는 'amplitude'여야 합니다: {scale!r}")

    return path[0] if single else path


def is_flat(window: np.ndarray) -> bool:
    """움직임이 거의 없는 구간인지.

    호가가 한 번도 안 움직인 창은 정규화하면 전부 0이 되고, 그런 창끼리는
    거리가 0이라 '완벽히 같은 모양'으로 잡힌다. 통계에 넣으면 의미 없는
    표본이 잔뜩 들어오므로 걸러낸다.
    """
    values = np.asarray(window, dtype=np.float64)
    if values.size == 0:
        return True
    base = values.flat[0]
    if base == 0:
        return True
    return float(np.std(values / base - 1.0)) <= FLAT_EPS


def sliding_windows(values: np.ndarray, length: int) -> np.ndarray:
    """길이 `length`짜리 모든 연속 구간. (n-length+1, length) 모양의 뷰."""
    if length <= 0:
        raise ValueError("창 길이는 1 이상이어야 합니다")
    if values.size < length:
        return np.empty((0, length), dtype=np.float64)
    return np.lib.stride_tricks.sliding_window_view(values, length)


def distances_to(
    query: np.ndarray,
    values: np.ndarray,
    length: int,
    scale: str = "shape",
    chunk: int = 20_000,
) -> np.ndarray:
    """`values`의 모든 길이-`length` 구간과 `query` 사이의 거리.

    1분봉 한 달(4만 봉)에 길이 180이면 4만×180 행렬이다. 통째로 정규화하면
    메모리를 수십 MB씩 쓰므로 나눠서 처리한다.
    """
    windows = sliding_windows(values, length)
    if windows.shape[0] == 0:
        return np.empty(0, dtype=np.float64)

    target = normalize_window(np.asarray(query, dtype=np.float64), scale)
    out = np.empty(windows.shape[0], dtype=np.float64)

    for start in range(0, windows.shape[0], chunk):
        block = normalize_window(windows[start : start + chunk], scale)
        diff = block - target
        out[start : start + block.shape[0]] = np.sqrt(np.mean(diff * diff, axis=1))
    return out


def flat_mask(values: np.ndarray, length: int) -> np.ndarray:
    """각 구간이 '움직임 없는 구간'인지 표시하는 불리언 배열."""
    windows = sliding_windows(values, length)
    if windows.shape[0] == 0:
        return np.empty(0, dtype=bool)
    base = windows[:, :1]
    with np.errstate(divide="ignore", invalid="ignore"):
        path = np.where(base != 0, windows / base - 1.0, 0.0)
    return (path.std(axis=1) <= FLAT_EPS) | (base[:, 0] == 0)


def similarity_to_distance(similarity: float) -> float:
    """'유사도'(상관계수)를 거리 임계값으로 바꾼다.

    shape 모드에서는 두 경로가 평균 0, 표준편차 1로 정규화되므로

        거리² = 2 × (1 − 상관계수)

    가 정확히 성립한다. 그래서 거리는 이렇게 읽으면 된다:

        거리 0.00 → 상관 1.00  (완전히 같은 모양)
        거리 0.45 → 상관 0.90
        거리 0.63 → 상관 0.80
        거리 1.00 → 상관 0.50
        거리 1.41 → 상관 0.00  (아무 관계 없음)

    이 변환이 없으면 사용자는 "거리 1.78"이 얼마나 나쁜지 알 수 없다.
    실제로 임계값 없이 돌렸을 때 거리 1.78짜리(상관이 오히려 음수인)
    구간들을 '같은 모양'이라며 통계에 넣고 있었다.
    """
    similarity = max(-1.0, min(1.0, float(similarity)))
    return float(np.sqrt(max(0.0, 2.0 * (1.0 - similarity))))


def distance_to_similarity(distance: float) -> float:
    """거리를 상관계수로. `similarity_to_distance`의 역."""
    if not np.isfinite(distance):
        return float("nan")
    return float(1.0 - (distance * distance) / 2.0)
