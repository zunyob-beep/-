"""통계 판정.

이 프로그램은 (봉 간격 3종) × (모양 길이 19종) × (시간 지평 5종) = 285개
조합을 한 번에 본다. 아무 정보가 없는 무작위 데이터에서도 285번 중
p<0.05인 조합이 평균 14개 나온다. 그래서 "승률 62%!" 하나만 보고
들어가면 거의 확실히 잡음을 쫓는 것이다.

여기서 하는 일은 세 가지다.

1. **올바른 기준과 비교한다.** p값은 이항검정이 아니라 순열검정으로
   구한다 (이유는 scan._null_up_rates). 이항검정을 썼더니 순수 무작위
   데이터에서 '유의한' 조합이 90개 중 28개 나왔다.
2. **표본이 몇 개인지 본다.** 8개 중 6개 올랐다(75%)는 동전을 여덟 번
   던진 것과 구분되지 않는다.
3. **여러 번 본 걸 보정한다.** 285개를 뒤졌다는 사실을 반영한다
   (Benjamini-Hochberg).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import timeframe_label
from .scan import Outcome, ScanResult
from .shape import distance_to_similarity

#: 이 이하 표본으로는 아무 말도 하지 않는다.
MIN_SAMPLES = 20

#: 허용할 거짓 발견 비율 (Benjamini-Hochberg)
DEFAULT_FDR = 0.10


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """승률의 95% 신뢰구간 (Wilson).

    표본이 적을 때 단순 근사(k/n ± z·SE)는 구간이 [0,1]을 벗어나거나
    지나치게 좁아진다. Wilson은 그 두 문제가 없다.
    """
    if n <= 0:
        return (0.0, 1.0)
    phat = k / n
    denominator = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


@dataclass
class Finding:
    """조합 하나에 대한 판정."""

    timeframe: str
    length: int
    horizon: int
    samples: int
    up: int
    down: int
    flat: int
    up_rate: float
    base_up_rate: float
    edge: float
    mean_return: float
    median_return: float
    worst: float
    p_value: float
    ci_low: float
    ci_high: float
    max_distance: float
    first_half_rate: float = 0.0
    second_half_rate: float = 0.0
    holds_in_both_halves: bool = False
    significant: bool = False
    q_value: float = 1.0

    @property
    def label(self) -> str:
        return f"{timeframe_label(self.timeframe)} 직전 {self.length}개 → {self.horizon}봉 뒤"

    @property
    def enough_samples(self) -> bool:
        return self.samples >= MIN_SAMPLES

    @property
    def min_similarity(self) -> float:
        """표본에 들어온 것 중 가장 덜 닮은 모양의 상관계수.

        거리(0.63 같은 숫자)는 읽어도 감이 안 온다. 상관계수로 바꿔서
        "제일 안 닮은 것도 0.87은 됩니다"처럼 읽히게 한다.
        """
        return distance_to_similarity(self.max_distance)


def evaluate(results: list[ScanResult], fdr: float = DEFAULT_FDR) -> list[Finding]:
    """모든 조합을 판정하고 다중비교를 보정해 돌려준다.

    반환은 edge(기준 대비 초과 승률) 내림차순이다.
    """
    findings: list[Finding] = []
    for result in results:
        for horizon, outcome in sorted(result.outcomes.items()):
            findings.append(_finding(result, horizon, outcome))

    _apply_fdr(findings, fdr)
    findings.sort(key=lambda f: (f.significant, f.edge), reverse=True)
    return findings


def _finding(result: ScanResult, horizon: int, outcome: Outcome) -> Finding:
    n = outcome.total
    # p값은 scan에서 순열검정으로 이미 구했다. 이항검정을 쓰면 매치들이
    # 서로 독립이라고 가정하게 되는데, 그 가정이 이 데이터에서는 크게 틀린다
    # (scan._null_up_rates 참고).
    p_value = outcome.p_value
    low, high = wilson_interval(outcome.up, n)
    return Finding(
        timeframe=result.timeframe,
        length=result.length,
        horizon=horizon,
        samples=n,
        up=outcome.up,
        down=outcome.down,
        flat=outcome.flat,
        up_rate=outcome.up_rate,
        base_up_rate=outcome.base_up_rate,
        edge=outcome.edge,
        mean_return=outcome.mean_return,
        median_return=outcome.median_return,
        worst=outcome.worst,
        p_value=p_value,
        ci_low=low,
        ci_high=high,
        max_distance=result.max_distance,
        first_half_rate=outcome.half_rates[0],
        second_half_rate=outcome.half_rates[1],
        holds_in_both_halves=outcome.holds_in_both_halves,
    )


def _apply_fdr(findings: list[Finding], fdr: float) -> None:
    """Benjamini-Hochberg 보정.

    285개를 뒤졌다는 사실을 반영한다. 보정 없이 p<0.05만 보면 아무 정보가
    없는 데이터에서도 '유의한' 조합이 십수 개 나온다.
    """
    testable = [f for f in findings if f.enough_samples]
    m = len(testable)
    if m == 0:
        return

    testable.sort(key=lambda f: f.p_value)
    threshold_rank = 0
    for rank, finding in enumerate(testable, start=1):
        if finding.p_value <= fdr * rank / m:
            threshold_rank = rank

    for rank, finding in enumerate(testable, start=1):
        finding.q_value = min(1.0, finding.p_value * m / rank)
        finding.significant = rank <= threshold_rank


@dataclass
class Verdict:
    """전체 결론."""

    enter: bool
    headline: str
    reasons: list[str]
    best: Finding | None
    tested: int
    significant: int


#: 진입을 인정하는 q값 상한. FDR 기준(0.10)보다 훨씬 엄격하게 잡는다.
#: FDR 0.10은 '발견의 10%는 거짓'을 허용한다는 뜻인데, 데이터가 순전한
#: 잡음이면 발견 전부가 거짓이다. 그 상황에서 진입 신호를 내지 않으려면
#: 여기서 한 번 더 조여야 한다.
ENTRY_Q = 0.02


def qualifies(finding: Finding, cost: float) -> bool:
    """진입 근거로 인정할 조건.

    통계적 유의성 하나만으로는 부족하다. 무작위 데이터로 시험해보면
    유의하면서 초과 승률 +30%인 조합이 얼마든지 나온다. 그래서 셋을
    모두 요구한다.
    """
    return (
        finding.enough_samples
        and finding.significant
        and finding.q_value <= ENTRY_Q
        # 평균 수익이 왕복 비용을 넘어야 실제로 돈이 된다.
        # 승률이 높아도 이기는 폭이 작으면 수수료로 다 나간다.
        and finding.mean_return > cost
        # 매치를 시간순으로 갈랐을 때 양쪽 모두에서 성립해야 한다.
        # 한쪽 국면에만 몰린 '패턴'을 거른다.
        and finding.holds_in_both_halves
    )


def decide(
    findings: list[Finding], fdr: float = DEFAULT_FDR, cost: float = 0.0014
) -> Verdict:
    """단타에 들어갈지 말지.

    기본 입장은 '들어가지 않는다'이다. 근거가 기준을 넘겨야만 뒤집는다.
    """
    tested = sum(1 for f in findings if f.enough_samples)
    winners = [f for f in findings if qualifies(f, cost) and f.edge > 0]
    reasons: list[str] = []

    if not findings:
        return Verdict(False, "판정할 수 없습니다 — 비교할 과거 데이터가 없습니다.", [], None, 0, 0)

    if tested == 0:
        return Verdict(
            False,
            f"판정 보류 — 표본 {MIN_SAMPLES}개를 넘긴 조합이 하나도 없습니다.",
            ["같은 모양이 과거에 거의 없었다는 뜻입니다. 데이터를 더 모으거나 모양 길이를 줄여보세요."],
            None,
            0,
            0,
        )

    if not winners:
        best = max((f for f in findings if f.enough_samples), key=lambda f: f.edge)
        reasons.append(
            f"가장 나은 조합: {best.label}, 초과 승률 {best.edge:+.1%} "
            f"(표본 {best.samples}건, q={best.q_value:.3f})"
        )
        if best.q_value > ENTRY_Q:
            reasons.append(
                f"{tested}개 조합을 뒤졌다는 점을 보정하면 우연과 충분히 구분되지 않습니다."
            )
        if best.mean_return <= cost:
            reasons.append(
                f"평균 수익 {best.mean_return:+.3%}가 왕복 비용 {cost:.3%}를 넘지 못합니다 — "
                "승률이 높아도 수수료로 나갑니다."
            )
        if not best.holds_in_both_halves:
            reasons.append(
                f"매치를 시간순으로 나누면 앞쪽 {best.first_half_rate:.0%} / "
                f"뒤쪽 {best.second_half_rate:.0%}로, 한쪽 구간에서만 나타납니다."
            )
        reasons.append("지금은 들어갈 근거가 없습니다.")
        return Verdict(False, "들어가지 마세요 — 근거가 기준에 못 미칩니다.", reasons, best, tested, 0)

    best = winners[0]
    reasons.append(
        f"{best.label}: 과거 같은 모양 {best.samples}건 중 "
        f"{best.up}건 상승 ({best.up_rate:.1%}), 기준 {best.base_up_rate:.1%} 대비 {best.edge:+.1%}"
    )
    reasons.append(
        f"승률 95% 신뢰구간 {best.ci_low:.1%}~{best.ci_high:.1%}, "
        f"다중비교 보정 후 q={best.q_value:.3f}"
    )
    reasons.append(f"평균 수익률 {best.mean_return:+.3%}, 최악의 경우 {best.worst:+.2%}")
    reasons.append(
        f"시간순 앞쪽 절반 {best.first_half_rate:.0%} / 뒤쪽 절반 {best.second_half_rate:.0%}"
        " — 양쪽 모두에서 성립합니다."
    )
    if len(winners) > 1:
        reasons.append(f"같은 방향으로 유의한 조합이 {len(winners)}개입니다.")

    return Verdict(
        True,
        f"들어갈 만합니다 — {best.label} 기준 승률 {best.up_rate:.1%}",
        reasons,
        best,
        tested,
        len(winners),
    )
