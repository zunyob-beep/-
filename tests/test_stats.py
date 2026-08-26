"""통계 판정 검증.

이 파일에서 가장 중요한 시험은 하나다:
**아무 정보가 없는 무작위 데이터에서 "들어가라"고 하면 안 된다.**

285개 조합을 뒤지면 순전한 잡음에서도 승률 60%짜리가 몇 개는 나온다.
그걸 걸러내지 못하면 이 프로그램은 사용자를 손실로 안내하는 도구가 된다.
"""

from __future__ import annotations

import numpy as np
import pytest

from patternscan.models import HORIZONS, WINDOW_LENGTHS
from patternscan.scan import (
    DEFAULT_NULL_TRIALS,
    _null_up_rates,
    round_trip_cost,
    scan,
    scan_all,
)
from patternscan.stats import (
    ENTRY_Q,
    MIN_CORROBORATION,
    MIN_SAMPLES,
    decide,
    evaluate,
    qualifies,
    wilson_interval,
)
from tests.conftest import MARKER, make_series, planted_signal


# ------------------------------------------------------------------ 순열검정
def test_null_distribution_preserves_sample_size():
    """귀무분포는 매치 개수를 그대로 유지해야 한다."""
    rng = np.random.default_rng(1)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 2000)))
    rates = _null_up_rates(closes, [100, 300, 500, 700], 5, 0.0014, 1500, 200, rng)
    assert rates.size == 200
    # 4개 매치이므로 승률은 0, .25, .5, .75, 1 중 하나
    assert set(np.unique(rates)) <= {0.0, 0.25, 0.5, 0.75, 1.0}


def test_null_distribution_is_empty_without_matches():
    rng = np.random.default_rng(1)
    closes = np.linspace(100, 110, 500)
    assert _null_up_rates(closes, [], 5, 0.001, 400, 100, rng).size == 0


def test_p_value_is_never_zero():
    """시행이 유한하므로 p=0은 확신을 과장한다."""
    rng = np.random.default_rng(2)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 3000)))
    findings = evaluate(scan_all({"minute1": make_series(closes.tolist())}, (10, 20)))
    assert all(f.p_value > 0 for f in findings)


# ------------------------------------------------------------------ 신뢰구간
def test_wilson_interval_contains_the_estimate():
    low, high = wilson_interval(30, 50)
    assert low < 0.6 < high


def test_small_samples_give_wide_intervals():
    narrow = wilson_interval(300, 500)
    wide = wilson_interval(3, 5)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0]) * 3


def test_wilson_stays_inside_zero_and_one():
    for k, n in ((0, 5), (5, 5), (1, 3)):
        low, high = wilson_interval(k, n)
        assert 0.0 <= low <= high <= 1.0


# ---------------------------------------------------- 잡음에서 신호를 만들지 않기
@pytest.mark.parametrize("seed", list(range(1, 13)))
def test_random_data_does_not_produce_a_buy_signal(seed):
    """순수 무작위 워크에서는 '들어가라'가 나오면 안 된다.

    이 시험이 깨지면 프로그램이 잡음을 신호로 파는 것이다.
    """
    rng = np.random.default_rng(seed)
    closes = 40_000_000 * np.exp(np.cumsum(rng.normal(0, 0.0008, 5000)))
    series = make_series(closes.tolist())

    results = scan_all({"minute1": series}, WINDOW_LENGTHS, horizons=HORIZONS, top_k=60)
    findings = evaluate(results)
    verdict = decide(findings, cost=round_trip_cost())

    assert not verdict.enter, (
        f"무작위 데이터에서 진입 신호가 나왔습니다: {verdict.headline}"
    )


def test_multiple_comparison_correction_is_applied():
    """보정 없이 p<0.05만 봤다면 유의한 게 여럿 나왔을 상황."""
    rng = np.random.default_rng(99)
    closes = 40_000_000 * np.exp(np.cumsum(rng.normal(0, 0.0008, 5000)))
    series = make_series(closes.tolist())

    findings = evaluate(scan_all({"minute1": series}, WINDOW_LENGTHS, top_k=60))
    testable = [f for f in findings if f.enough_samples]
    assert testable, "검정 가능한 조합이 있어야 시험이 의미 있다"

    raw_hits = sum(1 for f in testable if f.p_value < 0.05)
    corrected_hits = sum(1 for f in testable if f.significant)
    assert corrected_hits <= raw_hits
    # q값은 언제나 p값 이상이어야 한다 (보정은 느슨해지지 않는다)
    assert all(f.q_value >= f.p_value - 1e-12 for f in testable)


def test_q_values_are_between_zero_and_one():
    rng = np.random.default_rng(5)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 3000)))
    findings = evaluate(scan_all({"minute1": make_series(closes.tolist())}, (10, 20, 30)))
    assert all(0.0 <= f.q_value <= 1.0 for f in findings)


# ------------------------------------------------------------------ 표본 부족
def test_thin_samples_are_never_declared_significant():
    rng = np.random.default_rng(3)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 2000)))
    findings = evaluate(scan_all({"minute1": make_series(closes.tolist())}, (20, 40)))
    for finding in findings:
        if finding.samples < MIN_SAMPLES:
            assert not finding.significant


def test_verdict_says_why_when_nothing_qualifies():
    series = make_series([100.0 + i * 0.01 for i in range(400)])
    verdict = decide(evaluate(scan_all({"minute1": series}, (10, 20))), cost=round_trip_cost())
    assert not verdict.enter
    assert verdict.headline


def test_verdict_with_no_findings():
    verdict = decide([])
    assert not verdict.enter
    assert "없습니다" in verdict.headline


# ------------------------------------------------------------------ 신호가 있을 때
def test_the_matches_really_are_the_planted_marker():
    """찾아낸 매치가 정말 '심어둔 표식' 자리에서 나와야 한다.

    이 시험이 전에 없어서 오래 속고 있었다. 그때 쓰던 데이터는 상승 구간에서
    끝났고, 이 도구는 '지금 직전 N개'를 질의하므로 **질의 모양이 표식이 아니라
    곧게 오르는 직선**이었다. 그런 구간은 데이터 안에 수천 개 있고 전부 거리가
    0이라, 승률 97%짜리 '발견'이 나오지만 그 정체는 "오르는 중이면 다음 봉도
    오른다"였다. 심어둔 신호를 찾는지는 한 번도 검사하지 않은 셈이다.

    게다가 거리 0짜리 동점 후보가 2,633개나 되어 그중 무엇이 뽑히는지가
    1e-14 수준의 부동소수점 차이로 갈렸다 — 그래서 같은 시험이 로컬에서는
    통과하고 CI에서는 실패했다.

    그러니 승률만 보지 말고 **매치가 어디서 나왔는지**를 확인해야 한다.
    """
    closes, marker_ends = planted_signal(seed=1, with_positions=True)
    series = make_series(closes)
    result = scan(series, len(MARKER), horizons=(1, 3, 5), top_k=60)

    assert result.matches, "매치를 하나도 못 찾았습니다"
    planted = set(marker_ends)
    hits = sum(1 for m in result.matches if m.end_index in planted)
    assert hits / len(result.matches) > 0.9, (
        f"매치 {len(result.matches)}건 중 표식 자리에서 나온 것이 {hits}건뿐입니다 — "
        "심어둔 신호가 아니라 다른 걸 찾고 있습니다"
    )


def test_a_flat_query_does_not_masquerade_as_a_pattern():
    """질의 모양이 '곧게 오르는 직선'이면 과거에 똑같은 게 수천 개 있다.

    표본이 아무리 많아도 그건 모양의 예측력이 아니다. 옛 시험이 정확히
    이 상황을 '신호를 찾았다'고 읽고 있었으므로, 이제는 그런 구간이
    어떻게 처리되는지 명시해 둔다.
    """
    # 0.2%씩 곧게 오르기만 하는 데이터
    closes = [100.0 * 1.002**i for i in range(3000)]
    series = make_series(closes)
    result = scan(series, 5, horizons=(1,), top_k=60)

    # 정규화하면 전부 같은 직선이므로 거리는 0에 붙는다
    assert all(m.distance < 1e-9 for m in result.matches)
    # 그래도 '움직임 없는 구간'은 아니므로 걸러지지는 않는다 — 대신
    # 기준 승률도 같이 100%가 되어 초과가 0이 된다. 그게 정직한 답이다.
    outcome = result.outcomes[1]
    assert outcome.edge < 0.05, (
        f"곧게 오르기만 하는 데이터에서 초과 승률 {outcome.edge:+.1%}가 나왔습니다 — "
        "비교 기준이 제 역할을 못 하고 있습니다"
    )


# ------------------------------------- 285개 조합 부담 아래서도 신호를 찾는가
#
# 이 구획이 없어서 아주 오래 못 본 버그가 있었다.
#
# 순열 시행이 400회였으므로 p값의 최솟값이 1/401 = 0.0025로 막혀 있었다.
# 그런데 진입에는 q ≤ 0.02가 필요하고 q = p × 조합수 / 순위이므로, 조합이
# 90개면 1등이라도 q = 0.0025 × 90 ≈ 0.22 — **어떤 데이터로도 진입 신호가
# 나올 수 없었다**. 비용의 10배가 확정으로 오르는 신호를 심어도 놓쳤다.
#
# 잡음에 안전했던 게 아니라 아예 잠겨 있었던 것이고, 무작위 데이터 시험은
# 전부 통과했기 때문에 티가 나지 않았다. 그때 있던 '신호를 찾는가' 시험은
# 길이 1종 × 지평 3종만 봐서 다중비교 부담이 없었다 — 그래서 못 잡았다.
def test_p_value_resolution_survives_the_multiple_comparison_burden():
    """p값의 해상도가 조합 수를 감당해야 한다.

    조합 m개를 보정하면 1등의 q는 최소 p_min × m이다. 이게 진입 기준보다
    크면 진입은 수학적으로 불가능하다.
    """
    combos = len(WINDOW_LENGTHS) * len(HORIZONS)  # 실제로 뒤지는 조합 수
    p_min = 1.0 / (DEFAULT_NULL_TRIALS + 1)
    assert p_min * combos < ENTRY_Q, (
        f"순열 시행 {DEFAULT_NULL_TRIALS}회로는 p가 {p_min:.5f}까지밖에 안 내려가서, "
        f"조합 {combos}개를 보정하면 q ≥ {p_min * combos:.3f} — "
        f"진입 기준 {ENTRY_Q}를 넘길 수 없습니다."
    )


def test_null_distribution_is_exhaustive_when_it_can_be():
    """오프셋 가짓수가 유한하므로, 적으면 표본추출하지 말고 전부 세야 한다."""
    rng = np.random.default_rng(1)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 2000)))
    rates = _null_up_rates(closes, [100, 300, 500], 5, 0.0014, 1500, 10_000, rng)
    # 오프셋은 1 … usable-1 = 1494개뿐이므로 그만큼만 나와야 한다
    assert rates.size == 1500 - 5 - 1


def test_a_real_signal_is_found_even_under_the_full_search():
    """비용의 10배가 오르는 신호를 285개 조합 전부를 뒤지면서도 찾아내야 한다.

    이게 이 파일에서 잡음 시험만큼 중요하다. 잡음을 거른다고 진짜 신호까지
    놓치면 그건 '보수적'인 게 아니라 그냥 고장난 것이다.
    """
    series = make_series(planted_signal(seed=1))
    findings = evaluate(scan_all({"minute1": series}, WINDOW_LENGTHS, horizons=HORIZONS))
    verdict = decide(findings, cost=round_trip_cost())

    assert verdict.enter, (
        f"확정 상승을 심어뒀는데 못 찾았습니다: {verdict.headline} / "
        f"{'; '.join(verdict.reasons)}"
    )
    assert verdict.best is not None
    assert verdict.best.edge > 0.3
    assert verdict.best.q_value <= ENTRY_Q


def test_a_lone_surviving_combination_is_not_enough():
    """조합 하나만 기준을 넘긴 경우는 진입 근거로 삼지 않는다.

    실제 비트코인의 성질을 가진 순수 잡음 30개를 돌렸을 때 마지막까지
    남았던 거짓 신호가 정확히 이 모양이었다 — 110개 중 딱 1개 통과,
    유사도는 하한에 겨우 걸친 0.851. 진짜 신호일 때는 12~24개가 통과한다.
    """
    series = make_series(planted_signal(seed=1))
    findings = evaluate(scan_all({"minute1": series}, WINDOW_LENGTHS, horizons=HORIZONS))
    cost = round_trip_cost()

    # 진짜 신호이므로 원래는 여러 개가 통과한다
    winners = [f for f in findings if qualifies(f, cost) and f.edge > 0]
    assert len(winners) >= MIN_CORROBORATION, "이 시험의 전제가 깨졌습니다"
    assert decide(findings, cost=cost).enter

    # 하나만 남기면 진입이 막혀야 한다
    keep = winners[0]
    trimmed = [f for f in findings if f is keep or not (qualifies(f, cost) and f.edge > 0)]
    verdict = decide(trimmed, cost=cost)
    assert not verdict.enter
    assert "하나뿐" in verdict.headline


def test_the_same_search_stays_quiet_without_the_signal():
    """같은 구조에서 신호만 빼면 조용해야 한다 — 대조군."""
    series = make_series(planted_signal(seed=1, plant=False))
    findings = evaluate(scan_all({"minute1": series}, WINDOW_LENGTHS, horizons=HORIZONS))
    verdict = decide(findings, cost=round_trip_cost())
    assert not verdict.enter, f"신호가 없는데 진입 신호가 났습니다: {verdict.headline}"


def test_findings_are_sorted_with_significant_first():
    rng = np.random.default_rng(21)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 3000)))
    findings = evaluate(scan_all({"minute1": make_series(closes.tolist())}, (10, 20, 30)))
    flags = [f.significant for f in findings]
    assert flags == sorted(flags, reverse=True)
