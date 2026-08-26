"""과거에서 같은 모양을 찾고, 그 직후에 무슨 일이 있었는지 센다.

핵심 절차
---------
1. 지금 시점의 직전 L개 봉을 '질의 모양'으로 삼는다.
2. 과거 전체에서 같은 길이의 구간들과 거리를 잰다.
3. 가까운 것부터 고르되, **서로 겹치지 않게** 고른다.
4. 고른 각 구간의 **직후 h봉** 수익률을 모은다.
5. 왕복 수수료를 넘겼는지로 상승/보합/하락을 나눈다.

지키는 규칙
-----------
- **미래를 안 본다.** 후보 구간은 질의 구간이 시작하기 전에 끝나야 하고,
  결과를 관측할 h봉도 질의 구간 시작 전에 끝나야 한다.
- **겹치면 안 센다.** 한 칸씩 밀린 구간들은 거의 같은 구간이다. 그걸 다
  세면 표본 30개가 사실상 1개인데 30개인 척하게 된다.
- **끊긴 구간은 버린다.** 업비트는 거래가 없는 분의 봉을 주지 않는다.
  그 자리를 이어 붙이면 실제로는 떨어진 두 시점이 연속처럼 보인다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .models import HORIZONS, Series, timeframe_length
from .shape import (
    distance_to_similarity,
    distances_to,
    flat_mask,
    is_flat,
    linearity,
    similarity_to_distance,
)

log = logging.getLogger(__name__)

#: 업비트 원화 마켓 수수료(편도). 왕복이면 두 배.
DEFAULT_FEE = 0.0005

#: 매수/매도 사이 호가 차이로 더 잃는 몫(편도).
DEFAULT_SLIPPAGE = 0.0002

#: 순열검정에서 쓸 최대 오프셋 수. 가능한 오프셋이 이보다 적으면 전수로 돈다.
#: p값의 해상도가 곧 1/이 값이므로, 285개 조합을 보정하고도 진입 기준
#: q ≤ 0.02를 넘길 수 있으려면 최소한 조합 수 / 0.02 = 수천은 되어야 한다.
DEFAULT_NULL_TRIALS = 20_000

#: 순열검정 행렬을 한 번에 몇 오프셋씩 만들지 (메모리 상한용).
_NULL_CHUNK = 4_000

#: '같은 모양'으로 인정할 최소 상관계수.
#: 이걸 안 두면 상관이 0인(=아무 관계 없는) 구간까지 표본에 들어와,
#: 무작위 데이터에서도 초과 승률 +36%짜리 '패턴'이 만들어진다.
DEFAULT_SIMILARITY = 0.85


def round_trip_cost(fee: float = DEFAULT_FEE, slippage: float = DEFAULT_SLIPPAGE) -> float:
    """한 번 사고 파는 데 드는 총비용(비율)."""
    return 2.0 * (fee + slippage)


@dataclass
class Match:
    """과거에서 찾은 같은 모양 하나."""

    end_index: int  # 모양이 끝나는 봉의 위치
    distance: float
    returns: dict[int, float] = field(default_factory=dict)  # 봉 수 -> 수익률

    @property
    def similarity(self) -> float:
        """상관계수. 1이면 완전히 같은 모양."""
        return distance_to_similarity(self.distance)


@dataclass
class Outcome:
    """한 시간 지평(h봉 뒤)에서의 집계."""

    horizon: int
    up: int
    flat: int
    down: int
    mean_return: float
    median_return: float
    best: float
    worst: float
    #: 비교 기준. '같은 위치들에서 시점만 옮겼을 때'의 평균 상승 비율.
    #: 전체 평균이 아니라 이걸 쓰는 이유는 _null_up_rates 참고.
    base_up_rate: float
    base_samples: int
    #: 순열검정 p값. 관측 승률이 우연히 나올 확률.
    p_value: float = 1.0
    #: 매치를 시간순으로 반 갈랐을 때 각 절반의 (상승 수, 전체 수).
    #: 한쪽 국면에서만 성립하는 '패턴'을 걸러내는 데 쓴다.
    first_half: tuple[int, int] = (0, 0)
    second_half: tuple[int, int] = (0, 0)

    @property
    def total(self) -> int:
        return self.up + self.flat + self.down

    @property
    def up_rate(self) -> float:
        """상승 비율. 보합도 분모에 넣는다 — 수수료도 못 넘긴 건 실패다."""
        return self.up / self.total if self.total else 0.0

    @property
    def edge(self) -> float:
        """기준 대비 얼마나 나은지. 이게 0 근처면 이 모양은 정보가 없다."""
        return self.up_rate - self.base_up_rate

    @property
    def half_rates(self) -> tuple[float, float]:
        """앞쪽 절반과 뒤쪽 절반의 승률."""
        return (
            self.first_half[0] / self.first_half[1] if self.first_half[1] else 0.0,
            self.second_half[0] / self.second_half[1] if self.second_half[1] else 0.0,
        )

    @property
    def holds_in_both_halves(self) -> bool:
        """양쪽 절반 모두에서 기준을 넘겼는가.

        매치들이 어쩌다 상승 국면 한 곳에 몰리면 전체 승률은 높아지지만
        그건 모양의 예측력이 아니다. 시간순으로 갈라서 양쪽 모두 성립할
        때만 인정한다 — 국면 효과는 대개 한쪽에서만 나타난다.
        """
        if min(self.first_half[1], self.second_half[1]) < 5:
            return False
        first, second = self.half_rates
        return first > self.base_up_rate and second > self.base_up_rate


@dataclass
class ScanResult:
    """(봉 간격 × 모양 길이) 하나에 대한 결과."""

    market: str
    timeframe: str
    length: int
    matches: list[Match]
    outcomes: dict[int, Outcome]
    candidates: int
    max_distance: float
    threshold: float
    query_flat: bool = False
    #: 질의 모양이 직선에 얼마나 가까운지 (0~1). 높으면 '모양'이 아니라
    #: '추세'를 보고 있는 것이다 — shape.linearity 참고.
    query_linearity: float = 0.0
    note: str = ""

    @property
    def sample_size(self) -> int:
        return len(self.matches)

    @property
    def min_similarity(self) -> float:
        """가장 덜 닮은 매치의 유사도. 이게 낮으면 표본을 믿을 수 없다."""
        return distance_to_similarity(self.max_distance)


def scan(
    series: Series,
    length: int,
    *,
    query_end: int | None = None,
    horizons: tuple[int, ...] = HORIZONS,
    top_k: int = 60,
    similarity: float = DEFAULT_SIMILARITY,
    max_distance: float | None = None,
    scale: str = "shape",
    fee: float = DEFAULT_FEE,
    slippage: float = DEFAULT_SLIPPAGE,
    null_trials: int = DEFAULT_NULL_TRIALS,
    seed: int = 0,
) -> ScanResult:
    """`series`의 `query_end` 시점 직전 `length`개 모양을 과거에서 찾는다.

    `query_end`는 질의 구간의 마지막 봉 위치(기본: 가장 최근 봉).
    """
    n = len(series)
    if query_end is None:
        query_end = n - 1
    if not 0 <= query_end < n:
        raise ValueError(f"query_end가 범위를 벗어났습니다: {query_end}")

    query_start = query_end - length + 1
    if query_start < 0:
        return _empty(series, length, f"봉이 부족합니다 (필요 {length}개, 가능 {query_end + 1}개)")

    closes = series.close
    query = closes[query_start : query_end + 1]
    cost = round_trip_cost(fee, slippage)
    max_h = max(horizons)

    # 후보: 구간이 끝나고 max_h봉 뒤까지 관측 가능하면서, 그 전부가
    # 질의 구간 시작 전에 끝나야 한다.
    last_allowed_end = query_start - 1 - max_h
    if last_allowed_end < length - 1:
        return _empty(series, length, "과거 데이터가 모자라 비교할 구간이 없습니다")

    n_candidates = last_allowed_end - (length - 1) + 1
    usable = closes[: last_allowed_end + 1]

    distances = distances_to(query, usable, length, scale=scale)
    distances = distances[:n_candidates]

    # 끊긴 구간(빠진 봉)과 움직임 없는 구간은 제외한다.
    invalid = _broken_mask(series, length, n_candidates) | flat_mask(usable, length)[:n_candidates]
    distances = np.where(invalid, np.inf, distances)

    order = np.argsort(distances, kind="stable")
    threshold = (
        float(max_distance)
        if max_distance is not None
        else similarity_to_distance(similarity)
    )

    matches: list[Match] = []
    taken_ends: list[int] = []
    for pos in order:
        distance = float(distances[pos])
        if not np.isfinite(distance) or distance > threshold:
            break
        end_index = int(pos) + length - 1
        # 겹치는 구간은 사실상 같은 표본이다.
        if any(abs(end_index - other) < length for other in taken_ends):
            continue
        matches.append(Match(end_index=end_index, distance=distance))
        taken_ends.append(end_index)
        if len(matches) >= top_k:
            break

    for match in matches:
        entry = closes[match.end_index]
        for h in horizons:
            match.returns[h] = float(closes[match.end_index + h] / entry - 1.0)

    rng = np.random.default_rng(seed)
    limit = last_allowed_end + 1
    ends = [m.end_index for m in matches]
    # 절반 판정을 위해 '시간순'으로 정렬한 수익률이 필요하다
    # (matches는 유사도순이라 시간 순서가 아니다).
    by_time = sorted(matches, key=lambda m: m.end_index)
    outcomes = {}
    for h in horizons:
        null = _null_up_rates(closes, ends, h, cost, limit, null_trials, rng)
        outcomes[h] = _summarize(
            h, [m.returns[h] for m in matches], cost, null,
            [m.returns[h] for m in by_time],
        )

    return ScanResult(
        market=series.market,
        timeframe=series.timeframe,
        length=length,
        matches=matches,
        outcomes=outcomes,
        candidates=int(np.count_nonzero(np.isfinite(distances))),
        max_distance=max((m.distance for m in matches), default=float("nan")),
        threshold=threshold,
        query_flat=is_flat(query),
        query_linearity=linearity(query),
    )


def _empty(series: Series, length: int, note: str) -> ScanResult:
    return ScanResult(
        market=series.market,
        timeframe=series.timeframe,
        length=length,
        matches=[],
        outcomes={},
        candidates=0,
        max_distance=float("nan"),
        threshold=float("inf"),
        note=note,
    )


def _broken_mask(series: Series, length: int, n_candidates: int) -> np.ndarray:
    """빠진 봉을 포함한 구간을 표시한다.

    구간 [i, i+length-1]이 연속이려면 그 안의 모든 간격이 봉 길이와 같아야
    한다. 누적합으로 한 번에 계산한다.
    """
    if n_candidates <= 0:
        return np.empty(0, dtype=bool)
    step = int(timeframe_length(series.timeframe).total_seconds())
    bad = (np.diff(series.ts) != step).astype(np.int64)
    if bad.size == 0:
        return np.zeros(n_candidates, dtype=bool)

    cumulative = np.concatenate(([0], np.cumsum(bad)))
    # 구간 i..i+length-1 안의 '나쁜 간격' 개수
    ends = np.arange(n_candidates) + length - 1
    counts = cumulative[np.minimum(ends, cumulative.size - 1)] - cumulative[
        np.arange(n_candidates)
    ]
    return counts > 0


def _null_up_rates(
    closes: np.ndarray,
    match_ends: list[int],
    horizon: int,
    cost: float,
    limit: int,
    trials: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """귀무가설 분포: '이 모양은 아무 정보가 없다'면 나왔을 승률들.

    왜 단순 이항검정을 쓰면 안 되는가
    --------------------------------
    이항검정은 매치들이 서로 독립인 동전 던지기라고 가정한다. 그런데
    비슷한 모양은 시간적으로 뭉쳐서 나타난다(변동성이 비슷한 구간끼리
    닮았기 때문). 그 구간이 마침 오르는 국면이었다면 매치들의 승률이
    전부 같이 높아진다 — 모양이 예측력을 가져서가 아니라 그냥 같은
    국면에서 뽑혔기 때문이다.

    실제로 순수 무작위 데이터에 이항검정을 돌렸더니 승률 61%, q=0.001짜리
    '유의한' 조합이 90개 중 28개나 나왔다. 전부 허깨비였다.

    그래서 매치 **위치는 그대로 두고 시점만 통째로 옮긴다**. 위치들의
    뭉침 구조와 수익률의 자기상관은 그대로 유지되고, '이 모양 다음에
    올랐다'는 연결만 끊긴다. 그렇게 얻은 승률 분포가 올바른 비교 기준이다.

    왜 무작위로 뽑지 않고 전수로 도는가
    ----------------------------------
    이 귀무분포는 **오프셋 하나로만 결정된다**. 가능한 오프셋이 유한하므로
    (봉 개수만큼) 표본추출할 이유가 없다 — 전부 계산하면 정확한 분포가 나온다.

    처음엔 400회만 뽑았는데, 그러면 p값의 최솟값이 1/401 = 0.0025로 막힌다.
    285개 조합을 보정하면 1등이라도 q = 0.0025 × 90 ≈ 0.22가 되어, 진입
    기준 q ≤ 0.02를 **어떤 데이터로도 넘을 수 없었다**. 실제로 비용의 10배가
    확정으로 오르는 신호를 심어놓고 돌렸더니 10개 시드 전부 놓쳤다
    (초과 승률 +71%, p는 바닥인 0.0025인데 q=0.224로 탈락).

    잡음에 안전했던 게 아니라 아예 잠겨 있었던 것이다. 전수로 돌면 해상도가
    봉 개수분의 1(1분봉 한 달이면 2e-5)이 되어 그 바닥이 사라진다.
    """
    if not match_ends or trials <= 0:
        return np.empty(0, dtype=np.float64)

    ends = np.asarray(match_ends, dtype=np.int64)
    usable = limit - horizon  # 결과를 관측할 수 있는 마지막 위치 + 1
    if usable <= 1:
        return np.empty(0, dtype=np.float64)

    available = usable - 1  # 오프셋 1 … usable-1
    if available <= trials:
        offsets = np.arange(1, usable, dtype=np.int64)  # 전수
    else:
        # 너무 많으면 중복 없이 뽑는다 (같은 오프셋을 두 번 세면 분포가 왜곡된다)
        offsets = rng.choice(np.arange(1, usable, dtype=np.int64), size=trials, replace=False)

    # (오프셋 수 × 매치 수) 행렬을 통째로 만들면 수십 MB가 되므로 나눠서 센다.
    rates = np.empty(offsets.size, dtype=np.float64)
    for start in range(0, offsets.size, _NULL_CHUNK):
        block = offsets[start : start + _NULL_CHUNK]
        shifted = (ends[None, :] + block[:, None]) % usable
        returns = closes[shifted + horizon] / closes[shifted] - 1.0
        rates[start : start + block.size] = np.count_nonzero(returns > cost, axis=1) / ends.size
    return rates


def _summarize(
    horizon: int,
    returns: list[float],
    cost: float,
    null: np.ndarray,
    chronological: list[float] | None = None,
) -> Outcome:
    values = np.asarray(returns, dtype=np.float64)
    if values.size == 0:
        return Outcome(horizon, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 1.0)

    up = int(np.count_nonzero(values > cost))
    down = int(np.count_nonzero(values < -cost))
    up_rate = up / values.size

    if null.size:
        base_rate = float(null.mean())
        # +1씩 더하는 건 관측값 자신을 분포에 포함시키는 관례다.
        # 이게 없으면 시행 수가 적을 때 p=0이 나와 확신을 과장한다.
        p_value = float((np.count_nonzero(null >= up_rate) + 1) / (null.size + 1))
    else:
        base_rate, p_value = 0.0, 1.0

    ordered = np.asarray(chronological if chronological is not None else returns)
    middle = ordered.size // 2
    first, second = ordered[:middle], ordered[middle:]

    return Outcome(
        horizon=horizon,
        up=up,
        flat=int(values.size) - up - down,
        down=down,
        mean_return=float(values.mean()),
        median_return=float(np.median(values)),
        best=float(values.max()),
        worst=float(values.min()),
        base_up_rate=base_rate,
        base_samples=int(null.size),
        p_value=p_value,
        first_half=(int(np.count_nonzero(first > cost)), int(first.size)),
        second_half=(int(np.count_nonzero(second > cost)), int(second.size)),
    )


def scan_all(
    series_by_timeframe: dict[str, Series],
    lengths: tuple[int, ...],
    **kwargs: object,
) -> list[ScanResult]:
    """봉 간격 × 모양 길이의 모든 조합을 훑는다."""
    results: list[ScanResult] = []
    for series in series_by_timeframe.values():
        for length in lengths:
            if len(series) < length + max(HORIZONS) + length + 1:
                continue  # 비교할 과거가 아예 없는 길이는 건너뛴다
            results.append(scan(series, length, **kwargs))  # type: ignore[arg-type]
    return results
