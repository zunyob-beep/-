"""확률만 알려준다 — 사라 말라를 정하지 않는다.

왜 판정을 그만두고 확률만 보여주는가
-----------------------------------
실제 비트코인 105만 봉으로 재보면, 모양 비교는 적중률을 기준의 1.2~2배로
올린다. 정보가 분명히 있다. 그런데 그 우위가 왕복 수수료보다 작다 —
20분 움직임이 0.117%인데 수수료가 0.100%라, 여유가 0.017%뿐이다.

그래서 "사세요"라고 하면 거짓말이 된다. 하지만 "과거에 이런 모양 뒤에는
이랬습니다"는 참말이고, 판단은 사람이 하면 된다.

반드시 같이 보여줘야 하는 것
--------------------------
**"56%"만 보여주면 안 된다.** 평소가 55%라면 56%는 아무 의미가 없다.
그래서 이 모듈은 확률을 낼 때 항상 셋을 함께 낸다.

    1. 이 모양 뒤의 확률
    2. **평소 확률** (같은 기간 아무 때나 들어갔을 때)
    3. **불확실성** (표본이 적으면 확률은 흔들린다)

셋 중 하나라도 빠지면 사용자는 숫자를 실제보다 믿게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import HORIZONS, Series, timeframe_label, timeframe_length
from .scan import DEFAULT_FEE, DEFAULT_SIMILARITY, DEFAULT_SLIPPAGE, round_trip_cost
from .search import distances_within
from .shape import is_flat, linearity, normalize_window, similarity_to_distance
from .stats import wilson_interval

#: 이보다 표본이 적으면 확률을 말하지 않는다.
MIN_SAMPLES = 20


@dataclass
class Odds:
    """한 조합(봉 간격 × 길이 × 지평)에 대한 확률."""

    timeframe: str
    length: int
    horizon: int

    samples: int
    up: int  # 그냥 오른 개수
    beat_cost: int  # 왕복 비용까지 넘긴 개수

    base_up: float  # 평소 오를 확률
    base_beat: float  # 평소 비용 넘길 확률

    median_return: float
    best: float
    worst: float
    min_similarity: float
    query_linearity: float

    @property
    def minutes(self) -> int:
        """실제 몇 분 뒤인지. 5분봉의 '1봉 뒤'는 5분 뒤다."""
        return self.horizon * int(timeframe_length(self.timeframe).total_seconds() // 60)

    @property
    def up_rate(self) -> float:
        return self.up / self.samples if self.samples else 0.0

    @property
    def beat_rate(self) -> float:
        return self.beat_cost / self.samples if self.samples else 0.0

    @property
    def up_edge(self) -> float:
        """평소보다 얼마나 높은가. 이게 0 근처면 이 모양은 아무 말도 안 하고 있다."""
        return self.up_rate - self.base_up

    @property
    def beat_edge(self) -> float:
        return self.beat_rate - self.base_beat

    @property
    def interval(self) -> tuple[float, float]:
        """오를 확률의 95% 신뢰구간. 표본이 적으면 넓어진다."""
        return wilson_interval(self.up, self.samples)

    @property
    def tells_us_anything(self) -> bool:
        """평소와 구분되는가.

        신뢰구간이 평소 확률을 품고 있으면, 이 모양은 아무 정보도
        주지 않는 것과 구분되지 않는다.
        """
        low, high = self.interval
        return not (low <= self.base_up <= high)


@dataclass
class Example:
    """실제 사례 하나 — 확률 숫자를 눈으로 확인할 수 있게.

    승률만 보여주면 '정말 닮았나'를 확인할 방법이 없다. 실제로 겹쳐 보게
    해야 사용자가 스스로 판단할 수 있다.
    """

    end_index: int
    at: str  # 그 모양이 끝난 시각 (KST)
    similarity: float
    outcome: float  # 지평만큼 뒤의 수익률
    shape: list[float]  # 정규화한 모양 (지금 모양과 겹쳐 그리기 위해)
    after: list[float]  # 직후 경로 (진입 시점 0에서 시작)


@dataclass
class Matches:
    """찾은 과거 구간들. 확률과 사례가 모두 이걸 쓴다."""

    ends: np.ndarray
    distances: np.ndarray
    query: np.ndarray
    limit: int  # 기준 승률을 잴 수 있는 범위 (미래 참조 없음)


def find_matches(
    series: Series,
    length: int,
    *,
    query_end: int | None = None,
    max_horizon: int = max(HORIZONS),
    similarity: float = DEFAULT_SIMILARITY,
    top_k: int = 100,
) -> Matches | None:
    """`length`개 모양과 닮은 과거 구간을 찾는다 (미래 참조 없음)."""
    closes = series.close
    n = len(series)
    if query_end is None:
        query_end = n - 1

    query_start = query_end - length + 1
    last_allowed = query_start - 1 - max_horizon
    if query_start < 0 or last_allowed < length - 1:
        return None

    query = closes[query_start : query_end + 1]
    if is_flat(query):
        return None

    usable = closes[: last_allowed + 1]
    threshold = similarity_to_distance(similarity)
    positions, distances = distances_within(query, usable, length, threshold)
    if positions.size == 0:
        return None

    # 빠진 봉이 있는 구간은 버린다
    step = int(timeframe_length(series.timeframe).total_seconds())
    ends = positions + length - 1
    intact = (series.ts[ends] - series.ts[positions]) == step * (length - 1)
    ends, distances = ends[intact], distances[intact]
    if ends.size == 0:
        return None

    # 가까운 것부터, 겹치지 않게
    order = np.argsort(distances, kind="stable")
    chosen: list[int] = []
    kept: list[float] = []
    for index in order:
        end = int(ends[index])
        if any(abs(end - other) < length for other in chosen):
            continue
        chosen.append(end)
        kept.append(float(distances[index]))
        if len(chosen) >= top_k:
            break

    return Matches(
        ends=np.array(chosen, dtype=np.int64),
        distances=np.array(kept, dtype=np.float64),
        query=query,
        limit=last_allowed + 1,
    )


def examples_for(
    series: Series,
    matches: Matches,
    horizon: int,
    *,
    cost: float,
    count: int = 3,
) -> tuple[list[Example], list[Example]]:
    """올랐던 사례와 떨어졌던 사례를, **가장 닮은 것부터** 각각 `count`개.

    닮은 정도 순으로 고르는 이유: 사용자가 보고 싶은 건 '가장 비슷했던
    과거가 어떻게 됐나'이지, '가장 많이 오른 과거'가 아니다. 후자를 보여주면
    실제보다 좋아 보인다.
    """
    closes = series.close
    length = matches.query.size
    returns = closes[matches.ends + horizon] / closes[matches.ends] - 1.0

    def build(index: int) -> Example:
        end = int(matches.ends[index])
        window = closes[end - length + 1 : end + 1]
        entry = float(closes[end])
        after = closes[end : end + horizon + 1] / entry - 1.0
        return Example(
            end_index=end,
            at=series.kst_at(end).strftime("%Y-%m-%d %H:%M"),
            similarity=1.0 - (float(matches.distances[index]) ** 2) / 2.0,
            outcome=float(returns[index]),
            shape=[round(v, 4) for v in normalize_window(window).tolist()],
            after=[round(float(v), 6) for v in after],
        )

    # matches는 이미 닮은 순이므로 앞에서부터 고르면 된다
    rose = [build(i) for i in range(returns.size) if returns[i] > cost][:count]
    fell = [build(i) for i in range(returns.size) if returns[i] < 0.0][:count]
    return rose, fell


def _base_rates(closes: np.ndarray, limit: int, horizon: int, cost: float) -> tuple[float, float]:
    end = limit - horizon
    if end <= 0:
        return 0.0, 0.0
    returns = closes[horizon:limit] / closes[:end] - 1.0
    return (
        float(np.count_nonzero(returns > 0.0) / returns.size),
        float(np.count_nonzero(returns > cost) / returns.size),
    )


def odds_for(
    series: Series,
    length: int,
    *,
    horizons: tuple[int, ...] = HORIZONS,
    query_end: int | None = None,
    similarity: float = DEFAULT_SIMILARITY,
    top_k: int = 100,
    fee: float = DEFAULT_FEE,
    slippage: float = DEFAULT_SLIPPAGE,
) -> list[Odds]:
    """`length`개 모양과 닮은 과거 구간을 찾아 지평별 확률을 낸다.

    미래를 보지 않는다: 후보 구간과 그 직후 관측까지 전부 질의 구간이
    시작하기 전에 끝나야 한다.
    """
    closes = series.close
    cost = round_trip_cost(fee, slippage)
    matches = find_matches(
        series, length, query_end=query_end, max_horizon=max(horizons),
        similarity=similarity, top_k=top_k,
    )
    if matches is None:
        return []

    picked = matches.ends
    worst_distance = float(matches.distances.max())
    shape_linearity = linearity(matches.query)

    out: list[Odds] = []
    for horizon in horizons:
        returns = closes[picked + horizon] / closes[picked] - 1.0
        base_up, base_beat = _base_rates(closes, matches.limit, horizon, cost)
        out.append(
            Odds(
                timeframe=series.timeframe,
                length=length,
                horizon=horizon,
                samples=int(returns.size),
                up=int(np.count_nonzero(returns > 0.0)),
                beat_cost=int(np.count_nonzero(returns > cost)),
                base_up=base_up,
                base_beat=base_beat,
                median_return=float(np.median(returns)),
                best=float(returns.max()),
                worst=float(returns.min()),
                min_similarity=1.0 - (worst_distance * worst_distance) / 2.0,
                query_linearity=shape_linearity,
            )
        )
    return out


def odds_all(
    series_by_timeframe: dict[str, Series],
    length: int,
    **kwargs: object,
) -> list[Odds]:
    """봉 간격마다 같은 길이로 확률을 낸다."""
    out: list[Odds] = []
    for series in series_by_timeframe.values():
        out.extend(odds_for(series, length, **kwargs))  # type: ignore[arg-type]
    return out


def format_odds(rows: list[Odds], cost: float, expected: list[str] | None = None) -> str:
    """사람이 읽는 형태로. 확률 옆에 평소와 불확실성을 반드시 붙인다.

    `expected`를 주면 **결과가 없는 봉 간격도 그렇다고 말한다**. 조용히 빼면
    사용자는 1·3·5분봉을 다 본 줄 안다.
    """
    lines: list[str] = []
    by_timeframe: dict[str, list[Odds]] = {}
    for row in rows:
        by_timeframe.setdefault(row.timeframe, []).append(row)

    for timeframe in expected or []:
        if timeframe not in by_timeframe:
            lines.append(
                f"\n  ■ {timeframe_label(timeframe)} — 닮은 과거 구간을 찾지 못했습니다."
                "\n     유사도를 낮추거나(--similarity) 데이터를 더 모아야 합니다."
            )

    if not rows:
        lines.append(
            "\n  어떤 봉 간격에서도 닮은 구간을 찾지 못했습니다."
            "\n  유사도를 낮추거나(--similarity 0.7) 데이터를 더 모으세요."
        )
        return "\n".join(lines)

    for timeframe, group in by_timeframe.items():
        first = group[0]
        if first.samples < MIN_SAMPLES:
            lines.append(
                f"\n  {timeframe_label(timeframe)}: 닮은 구간이 {first.samples}개뿐이라"
                f" 확률을 말할 수 없습니다 (최소 {MIN_SAMPLES}개 필요)."
            )
            continue

        lines.append(
            f"\n  ■ {timeframe_label(timeframe)} — 직전 {first.length}개와 닮은 과거 구간"
            f" {first.samples}개 (유사도 {first.min_similarity:.2f} 이상)"
        )
        if first.query_linearity >= 0.75:
            lines.append(
                f"     ⚠ 지금 모양은 직선에 가깝습니다 (직선성 {first.query_linearity:.2f}) —"
                " 특이한 모양이 아니라 '추세 중'인 구간들을 센 것에 가깝습니다."
            )
        lines.append(
            f"     {'':>6}{'올라 있을 확률':>16}{'평소':>10}{'차이':>8}"
            f"{'수수료까지 넘길 확률':>20}{'평소':>10}"
        )
        lines.append("     " + "─" * 72)
        for row in sorted(group, key=lambda r: r.horizon):
            low, high = row.interval
            mark = "" if row.tells_us_anything else "  (평소와 구분 안 됨)"
            lines.append(
                f"     {row.minutes:>3}분 {row.up_rate:>14.0%}"
                f" {row.base_up:>9.0%} {row.up_edge:>+7.0%}"
                f" {row.beat_rate:>18.0%} {row.base_beat:>9.0%}"
            )
            lines.append(
                f"     {'':>6}({row.samples}개 중 {row.up}개)"
                f"   불확실 범위 {low:.0%}~{high:.0%}{mark}"
            )

    lines.append("")
    lines.append(f"  · '수수료까지 넘길 확률'은 왕복 {cost:.2%}를 넘긴 경우만 셉니다.")
    lines.append("  · '평소'는 같은 기간 아무 때나 들어갔을 때입니다. 이것보다 높아야 의미가 있습니다.")
    lines.append("  · 불확실 범위가 '평소'를 품고 있으면 그 확률은 우연과 구분되지 않습니다.")
    lines.append("  · 이 도구는 매수를 권하지 않습니다. 과거에 무슨 일이 있었는지만 셉니다.")
    return "\n".join(lines)


# ------------------------------------------------------------- 앞으로의 모양
#
# "그래서 앞으로 어떻게 되는데"에 답하는 그림이다. 다만 **선 하나를 그으면
# 거짓말이 된다** — 실제로 일어날 일은 하나지만, 우리가 아는 건 비슷했던
# 과거들이 제각각 흩어졌다는 사실뿐이다. 그래서 가운뎃값과 함께 **퍼진
# 정도**를 띠로 그린다. 띠가 넓으면 그건 "모른다"는 뜻이고, 그 사실이
# 화면에 보여야 한다.

@dataclass(frozen=True)
class Projection:
    """닮았던 과거들이 그 다음에 실제로 간 길."""

    timeframe: str
    length: int
    samples: int
    #: 봉마다 하나씩. 지금 값 대비 비율이므로 0에서 시작한다.
    median: list[float]
    low: list[float]      # 25%
    high: list[float]     # 75%
    worst: list[float]    # 10%
    best: list[float]     # 90%
    price_now: float

    @property
    def minutes(self) -> int:
        step = int(timeframe_length(self.timeframe).total_seconds() // 60)
        return (len(self.median) - 1) * step

    @property
    def spread(self) -> float:
        """마지막 시점에서 25%와 75%가 얼마나 벌어져 있는지."""
        return self.high[-1] - self.low[-1] if self.median else 0.0

    def prices(self, which: str) -> list[float]:
        """비율을 실제 금액으로."""
        return [self.price_now * (1.0 + v) for v in getattr(self, which)]


def project(
    series: Series,
    matches: Matches,
    ahead: int,
) -> Projection | None:
    """닮았던 과거 구간들의 **직후 경로**를 모아 가운뎃값과 띠를 낸다.

    새 이론을 들이지 않는다. 이 도구가 원래 하던 일(닮은 과거 찾기)을
    그대로 앞으로 이어 그릴 뿐이다. 그래서 이 그림이 맞을 확률은 위의
    확률 표와 정확히 같은 근거를 가진다 — 더도 덜도 아니다.
    """
    closes = series.close
    n = len(series)
    paths = []
    for end in matches.ends:
        end = int(end)
        if end + ahead >= n:
            continue
        entry = float(closes[end])
        if entry <= 0:
            continue
        paths.append(closes[end + 1 : end + 1 + ahead] / entry - 1.0)

    if len(paths) < MIN_SAMPLES:
        return None
    grid = np.vstack(paths)
    def pick(q: float) -> list[float]:
        # 0에서 시작한다 — 지금 값이 기준점이라 그래야 선이 이어진다.
        return [0.0] + [round(float(v), 6) for v in np.percentile(grid, q, axis=0)]

    return Projection(
        timeframe=series.timeframe,
        length=int(matches.query.size),
        samples=int(grid.shape[0]),
        median=pick(50), low=pick(25), high=pick(75),
        worst=pick(10), best=pick(90),
        price_now=float(closes[-1]),
    )
