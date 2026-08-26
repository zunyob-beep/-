"""같은 모양 찾기를 빠르게 — 결과는 그대로.

왜 필요한가
-----------
8년치 1분봉은 420만 개다. 길이 180짜리 구간 하나를 비교하려면 420만 개
구간을 전부 정규화해야 하고, 그게 길이마다·시점마다 반복된다. 검증 500개
시점 × 19개 길이면 13시간이 걸린다 — 못 쓴다.

어떻게 줄이는가
--------------
거리를 정확히 계산하기 전에 **확실히 먼 후보를 먼저 버린다**. 버릴 때는
반드시 '실제 거리가 이보다 작을 수 없다'는 하한을 쓴다. 하한이 임계값을
넘으면 실제 거리도 넘으므로, 버려도 결과가 변하지 않는다.

하한은 구간을 k토막(기본 8)으로 나눈 평균만으로 계산한다. 코시-슈바르츠에서

    Σ_j L_j · (토막평균 차이)²  ≤  Σ_i (원소 차이)²

이므로

    하한 = √( Σ_j L_j·Δ_j² / N )  ≤  실제 RMSE 거리

가 성립한다. 토막평균은 8개뿐이라 길이 180이든 5든 계산량이 같다.

그리고 **토막평균 자체를 누적합으로 O(1)에** 구한다. 정규화한 경로의
토막평균은 원본 값의 구간합·제곱합만 있으면 나오므로, 모든 구간에 대해
한 번에 계산할 수 있다.

수치 안정성
-----------
누적합 차이는 큰 수에서 작은 수를 빼므로 자릿수를 잃는다. 그래서
(1) 값을 중앙값으로 나눠 1 근처로 맞추고,
(2) 하한에 여유(SAFETY)를 둬서 오차 때문에 진짜 매치를 버리지 않게 한다.
여유를 둬도 버려지는 후보는 여전히 확실히 먼 것들뿐이다.
"""

from __future__ import annotations

import numpy as np

from .shape import FLAT_EPS, normalize_window, sliding_windows

#: 하한을 계산할 때 쓸 토막 수. 늘리면 더 촘촘히 걸러내지만 계산이 는다.
SEGMENTS = 8

#: 하한에 둘 여유. 누적합 오차로 진짜 매치를 버리는 일을 막는다.
SAFETY = 1e-6

#: 후보가 이보다 적으면 그냥 정확히 계산한다 (거르는 게 더 비싸다).
MIN_CANDIDATES = 20_000


def _segment_bounds(length: int, segments: int) -> np.ndarray:
    """길이를 최대한 고르게 나눈 경계. 나머지는 앞쪽 토막에 하나씩."""
    segments = max(1, min(segments, length))
    base, extra = divmod(length, segments)
    sizes = np.full(segments, base, dtype=np.int64)
    sizes[:extra] += 1
    return np.concatenate(([0], np.cumsum(sizes)))


def _paa_all(values: np.ndarray, length: int, bounds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """모든 길이-`length` 구간의 정규화 경로 토막평균과, 평평한 구간 표시.

    누적합만 쓰므로 구간 하나당 O(토막 수)다. 길이에 비례하지 않는다.
    """
    n_windows = values.size - length + 1
    if n_windows <= 0:
        return np.empty((0, bounds.size - 1)), np.empty(0, dtype=bool)

    # 1을 더해 0으로 시작하는 누적합 (구간합 = 차이 하나)
    csum = np.concatenate(([0.0], np.cumsum(values)))
    csq = np.concatenate(([0.0], np.cumsum(values * values)))

    starts = np.arange(n_windows)
    first = values[:n_windows]  # 각 구간의 첫 값 (기준)

    total = csum[starts + length] - csum[starts]
    total_sq = csq[starts + length] - csq[starts]

    with np.errstate(divide="ignore", invalid="ignore"):
        scaled = np.where(first != 0, 1.0 / first, 0.0)
        # r = v/first - 1 의 평균과 표준편차
        mean_r = total * scaled / length - 1.0
        mean_sq = total_sq * scaled * scaled / length - 2.0 * total * scaled / length + 1.0
        var_r = np.maximum(mean_sq - mean_r * mean_r, 0.0)
        sd_r = np.sqrt(var_r)

        paa = np.empty((n_windows, bounds.size - 1), dtype=np.float64)
        for j in range(bounds.size - 1):
            lo, hi = int(bounds[j]), int(bounds[j + 1])
            seg = (csum[starts + hi] - csum[starts + lo]) * scaled / (hi - lo) - 1.0
            paa[:, j] = seg - mean_r

        good = sd_r > FLAT_EPS
        paa[good] /= sd_r[good, None]
        paa[~good] = 0.0

    flat = ~good | (first == 0)
    return paa, flat


def distances_within(
    query: np.ndarray,
    values: np.ndarray,
    length: int,
    threshold: float,
    *,
    segments: int = SEGMENTS,
    chunk: int = 20_000,
) -> tuple[np.ndarray, np.ndarray]:
    """`threshold` 이내인 구간만 (위치, 거리)로 돌려준다.

    임계값을 넘는 후보는 하한으로 미리 버리므로, 전부 정규화하는 것보다
    훨씬 빠르다. 돌려주는 것은 정확한 거리이며, 전수 계산과 결과가 같다.

    `scale`은 shape 모드('모양만')만 지원한다 — 하한 유도가 z정규화를 전제한다.
    """
    n_windows = values.size - length + 1
    if n_windows <= 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float64)

    if n_windows < MIN_CANDIDATES:
        return _exact(query, values, length, threshold, chunk)

    # 값이 1 근처가 되도록 맞춘다 (누적합의 자릿수 손실을 줄인다)
    reference = float(np.median(np.abs(values))) or 1.0
    scaled_values = values / reference

    bounds = _segment_bounds(length, segments)
    paa, flat = _paa_all(scaled_values, length, bounds)

    target = normalize_window(np.asarray(query, dtype=np.float64))
    widths = np.diff(bounds).astype(np.float64)
    target_paa = np.array(
        [target[int(bounds[j]) : int(bounds[j + 1])].mean() for j in range(bounds.size - 1)]
    )

    diff = paa - target_paa
    lower = np.sqrt((diff * diff * widths).sum(axis=1) / length)

    keep = (lower <= threshold + SAFETY) & ~flat
    positions = np.flatnonzero(keep)
    if positions.size == 0:
        return positions, np.empty(0, dtype=np.float64)

    exact = _exact_at(target, values, length, positions, chunk)
    within = exact <= threshold
    return positions[within], exact[within]


def _exact(
    query: np.ndarray, values: np.ndarray, length: int, threshold: float, chunk: int
) -> tuple[np.ndarray, np.ndarray]:
    from .shape import distances_to, flat_mask

    distances = distances_to(query, values, length)
    distances = np.where(flat_mask(values, length), np.inf, distances)
    positions = np.flatnonzero(distances <= threshold)
    return positions, distances[positions]


def _exact_at(
    target: np.ndarray, values: np.ndarray, length: int, positions: np.ndarray, chunk: int
) -> np.ndarray:
    """고른 위치들에 대해서만 정확한 거리를 계산한다."""
    windows = sliding_windows(values, length)
    out = np.empty(positions.size, dtype=np.float64)
    for start in range(0, positions.size, chunk):
        block = positions[start : start + chunk]
        normalized = normalize_window(windows[block])
        delta = normalized - target
        out[start : start + block.size] = np.sqrt(np.mean(delta * delta, axis=1))
    return out
