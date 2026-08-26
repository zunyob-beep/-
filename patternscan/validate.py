"""이 방법이 과거에 실제로 맞았는지 확인한다.

지금까지 이 도구는 "지금 이 순간 들어갈까"만 답했다. 그런데 정작 알고 싶은
것은 그 답을 믿어도 되는지다. 그래서 **과거의 여러 시점으로 돌아가서**,
그 시점까지의 데이터만 보고 예측을 낸 뒤 실제로 무슨 일이 있었는지 센다.

절차
----
1. 평가할 시점 t를 여러 개 고른다.
2. 각 t와 각 길이 N에 대해, **t 이전 데이터만으로** 직전 N개 모양과 같은
   모양을 찾는다.
3. 그 매치들의 직후 결과로 방향을 예측한다 (기준보다 잘 올랐으면 '상승').
4. t에서 실제로 h봉 뒤에 무슨 일이 있었는지와 맞춰본다.
5. 길이별로 적중률을 모은다.

반드시 같이 봐야 하는 것
-----------------------
**적중률만 보면 속는다.** 1분 뒤에 수수료를 넘겨 오르는 경우는 원래 20%도
안 된다. 그러니 무조건 "안 오른다"고 찍기만 해도 적중률 80%가 나온다.
그래서 항상 **다수 쪽으로만 찍었을 때의 적중률(base_hits)**과 나란히 놓고,
그보다 얼마나 나은지를 본다. 그게 진짜 실력이다.

그리고 방향이 맞았는지보다 실제로 돈이 됐는지가 더 중요하므로,
'상승'이라고 예측했을 때의 평균 수익률도 같이 센다 — 이게 왕복 비용을
넘지 못하면 방향을 아무리 잘 맞혀도 소용이 없다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .models import HORIZONS, Series, timeframe_label, timeframe_length
from .scan import DEFAULT_FEE, DEFAULT_SIMILARITY, DEFAULT_SLIPPAGE, round_trip_cost
from .search import distances_within
from .shape import is_flat, similarity_to_distance

log = logging.getLogger(__name__)

#: 예측을 내려면 과거 매치가 최소 이만큼은 있어야 한다.
MIN_SAMPLES = 20


@dataclass
class Score:
    """길이 하나 · 지평 하나에 대한 성적표."""

    timeframe: str
    length: int
    horizon: int

    #: 예측을 낼 수 있었던 시점 수 (매치가 모자라면 예측을 안 낸다)
    predictions: int = 0
    #: 평가한 전체 시점 수 — predictions / attempts 가 '얼마나 자주 답할 수 있나'
    attempts: int = 0

    #: 수수료를 넘겼는지를 맞힌 횟수 (이 도구가 실제로 답해야 하는 문제)
    hits: int = 0
    #: 항상 다수 쪽으로만 찍었을 때 맞았을 횟수 — 이걸 넘어야 실력이다
    base_hits: int = 0

    #: 단순 부호(오르내림)를 맞힌 횟수와, 그때의 찍기 기준
    sign_hits: int = 0
    sign_base_hits: int = 0

    #: '상승'이라고 예측한 횟수와, 그때 실제로 비용을 넘긴 횟수
    said_up: int = 0
    said_up_won: int = 0
    #: '상승'이라고 예측했을 때의 실제 수익률들
    up_returns: list[float] = field(default_factory=list)
    #: 전체 시점의 수익률 (비교 기준)
    all_returns: list[float] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """얼마나 자주 답할 수 있었나."""
        return self.predictions / self.attempts if self.attempts else 0.0

    @property
    def accuracy(self) -> float:
        return self.hits / self.predictions if self.predictions else 0.0

    @property
    def base_accuracy(self) -> float:
        """항상 다수 쪽으로 찍었을 때의 적중률. 이걸 넘어야 의미가 있다."""
        return self.base_hits / self.predictions if self.predictions else 0.0

    @property
    def skill(self) -> float:
        """기준 대비 초과 적중률. 0 이하면 찍는 것만 못하다."""
        return self.accuracy - self.base_accuracy

    @property
    def sign_accuracy(self) -> float:
        """단순 오르내림 적중률."""
        return self.sign_hits / self.predictions if self.predictions else 0.0

    @property
    def sign_skill(self) -> float:
        base = self.sign_base_hits / self.predictions if self.predictions else 0.0
        return self.sign_accuracy - base

    @property
    def skill_error(self) -> float:
        """실력의 표준오차.

        예측 150회면 표준오차가 4%다. 즉 **아무 실력이 없어도 ±8%는 그냥
        나온다**. 실제로 순수 잡음으로 돌려보면 실력 +11%짜리 조합이 나온다.
        이 값을 같이 보지 않으면 그걸 발견으로 착각한다.
        """
        if self.predictions < 2:
            return 1.0
        return float(np.sqrt(0.25 / self.predictions))

    @property
    def skill_is_real(self) -> bool:
        """실력이 우연으로 설명되지 않는가 (2.5 표준오차).

        길이 19종 × 지평 5종을 한 번에 보므로 2 표준오차로는 부족하다.
        """
        return self.skill > 2.5 * self.skill_error

    @property
    def up_win_rate(self) -> float:
        """'상승'이라 했을 때 실제로 비용을 넘긴 비율."""
        return self.said_up_won / self.said_up if self.said_up else 0.0

    @property
    def mean_up_return(self) -> float:
        """'상승'이라 했을 때의 평균 수익률. 왕복 비용을 넘어야 돈이 된다."""
        return float(np.mean(self.up_returns)) if self.up_returns else 0.0

    @property
    def mean_all_return(self) -> float:
        return float(np.mean(self.all_returns)) if self.all_returns else 0.0

    @property
    def edge_return(self) -> float:
        """아무 때나 들어간 것 대비 얼마나 나은가."""
        return self.mean_up_return - self.mean_all_return

    @property
    def label(self) -> str:
        return f"{timeframe_label(self.timeframe)} 직전 {self.length}개 → {self.horizon}봉 뒤"


def _base_rates(closes: np.ndarray, limit: int, horizon: int, cost: float) -> tuple[float, float]:
    """구간 전체에서 h봉 뒤가 (비용 초과, 그냥 상승)이었던 비율.

    둘을 따로 구하는 이유: 예측을 '비용을 넘길까'로 냈으면 채점도 비용으로,
    '오를까'로 냈으면 채점도 부호로 해야 한다. 섞으면 기준이 어긋나
    실력이 실제보다 좋거나 나쁘게 나온다.
    """
    end = limit - horizon
    if end <= 0:
        return 0.0, 0.0
    returns = closes[horizon:limit] / closes[:end] - 1.0
    return (
        float(np.count_nonzero(returns > cost) / returns.size),
        float(np.count_nonzero(returns > 0.0) / returns.size),
    )


def _match_positions(
    series: Series,
    length: int,
    query_end: int,
    max_horizon: int,
    threshold: float,
    top_k: int,
) -> np.ndarray:
    """`query_end` 이전 데이터에서만 같은 모양을 찾는다 (미래 참조 없음).

    반환은 모양이 끝나는 위치들. 서로 겹치지 않는다.
    """
    closes = series.close
    query_start = query_end - length + 1
    last_allowed_end = query_start - 1 - max_horizon
    if query_start < 0 or last_allowed_end < length - 1:
        return np.empty(0, dtype=np.int64)

    query = closes[query_start : query_end + 1]
    if is_flat(query):
        return np.empty(0, dtype=np.int64)

    usable = closes[: last_allowed_end + 1]
    positions, distances = distances_within(query, usable, length, threshold)
    if positions.size == 0:
        return positions

    # 끊긴 구간(빠진 봉)을 포함한 것은 버린다
    step = int(timeframe_length(series.timeframe).total_seconds())
    ends = positions + length - 1
    spans = series.ts[ends] - series.ts[positions]
    intact = spans == step * (length - 1)
    ends, distances = ends[intact], distances[intact]
    if ends.size == 0:
        return ends

    # 가까운 것부터, 겹치지 않게
    order = np.argsort(distances, kind="stable")
    chosen: list[int] = []
    for index in order:
        end = int(ends[index])
        if any(abs(end - other) < length for other in chosen):
            continue
        chosen.append(end)
        if len(chosen) >= top_k:
            break
    return np.array(sorted(chosen), dtype=np.int64)


def validate(
    series: Series,
    lengths: tuple[int, ...],
    *,
    horizons: tuple[int, ...] = HORIZONS,
    points: int = 500,
    similarity: float = DEFAULT_SIMILARITY,
    top_k: int = 60,
    fee: float = DEFAULT_FEE,
    slippage: float = DEFAULT_SLIPPAGE,
    min_samples: int = MIN_SAMPLES,
    seed: int = 0,
    progress: object = None,
) -> list[Score]:
    """여러 시점으로 돌아가 길이별 적중률을 잰다.

    평가 시점은 데이터 뒤쪽 절반에서 고른다 — 앞쪽은 비교할 과거가 모자라
    어떤 길이든 예측을 못 내기 때문이다.
    """
    closes = series.close
    n = len(series)
    cost = round_trip_cost(fee, slippage)
    threshold = similarity_to_distance(similarity)
    max_h = max(horizons)
    max_len = max(lengths)

    lowest = 2 * max_len + max_h + 10
    highest = n - max_h - 1
    if highest <= lowest:
        raise ValueError(
            f"데이터가 모자랍니다: 봉 {n:,}개로는 길이 {max_len}을 검증할 수 없습니다"
        )

    rng = np.random.default_rng(seed)
    evaluation_points = np.unique(rng.integers(lowest, highest, size=points * 2))[:points]

    scores = {
        (length, horizon): Score(series.timeframe, length, horizon)
        for length in lengths
        for horizon in horizons
    }

    for done, t in enumerate(evaluation_points, start=1):
        t = int(t)
        actual = {h: float(closes[t + h] / closes[t] - 1.0) for h in horizons}

        for length in lengths:
            ends = _match_positions(series, length, t, max_h, threshold, top_k)
            limit = t - length + 1 - max_h  # 기준을 잴 수 있는 범위 (미래 참조 없음)

            for horizon in horizons:
                score = scores[(length, horizon)]
                score.attempts += 1
                score.all_returns.append(actual[horizon])

                if ends.size < min_samples:
                    continue

                outcomes = closes[ends + horizon] / closes[ends] - 1.0
                cost_rate = float(np.count_nonzero(outcomes > cost) / outcomes.size)
                sign_rate = float(np.count_nonzero(outcomes > 0.0) / outcomes.size)
                cost_base, sign_base = _base_rates(closes, limit, horizon, cost)

                score.predictions += 1
                moved = actual[horizon]

                # ① 비용 기준 — 이 도구가 실제로 답해야 하는 문제
                predict_up = cost_rate > cost_base
                beat_cost = moved > cost
                if predict_up == beat_cost:
                    score.hits += 1
                if (cost_base > 0.5) == beat_cost:
                    score.base_hits += 1

                # ② 부호 기준 — '방향을 맞히는가'만 따로
                predict_rise = sign_rate > sign_base
                rose = moved > 0.0
                if predict_rise == rose:
                    score.sign_hits += 1
                if (sign_base > 0.5) == rose:
                    score.sign_base_hits += 1

                if predict_up:
                    score.said_up += 1
                    score.up_returns.append(moved)
                    if beat_cost:
                        score.said_up_won += 1

        if progress is not None:
            progress(done, evaluation_points.size)  # type: ignore[operator]

    return sorted(scores.values(), key=lambda s: (s.length, s.horizon))


def best_lengths(scores: list[Score], min_predictions: int = 30) -> list[Score]:
    """실력(기준 대비 초과 적중률) 순으로. 예측을 거의 못 낸 조합은 뺀다."""
    usable = [s for s in scores if s.predictions >= min_predictions]
    return sorted(usable, key=lambda s: s.skill, reverse=True)
