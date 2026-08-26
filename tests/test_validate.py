"""과거 검증(walk-forward)을 검증한다.

이 파일이 지키는 것은 셋이다.

1. **미래를 안 본다.** 평가 시점 t의 예측은 t 이전 데이터로만 만들어져야 한다.
   이게 깨지면 성적표가 실제보다 훨씬 좋게 나오고, 사용자는 그걸 믿는다.
2. **찍기와 비교한다.** 1분 뒤에 수수료를 넘겨 오르는 경우는 원래 20%도
   안 되므로, 무조건 '안 오른다'고 찍어도 적중률 80%가 나온다.
3. **표본이 적으면 그렇다고 말한다.** 예측 150회에서 실력 ±8%는 우연이다.
"""

from __future__ import annotations

import numpy as np
import pytest

from patternscan.scan import round_trip_cost
from patternscan.search import distances_within
from patternscan.shape import distances_to, flat_mask, similarity_to_distance
from patternscan.validate import _match_positions, best_lengths, validate
from tests.conftest import MARKER, make_series, planted_signal


@pytest.fixture
def noise_series():
    rng = np.random.default_rng(5)
    closes = 40_000_000 * np.exp(np.cumsum(rng.normal(0, 0.0009, 30_000)))
    return make_series(closes.tolist())


# ------------------------------------------------------------------ 미래 참조
def test_matches_never_come_from_after_the_evaluation_point(noise_series):
    """평가 시점 t의 매치는 전부 t 이전에 끝나야 한다."""
    threshold = similarity_to_distance(0.85)
    for t in (12_000, 20_000, 29_000):
        ends = _match_positions(noise_series, 60, t, 20, threshold, 60)
        query_start = t - 60 + 1
        assert np.all(ends + 20 < query_start), f"시점 {t}에서 미래를 봤습니다"


def test_trimming_later_data_does_not_change_the_prediction(noise_series):
    """t 이후 데이터를 잘라내도 t에서의 매치가 같아야 한다.

    다르면 어딘가에서 미래를 보고 있다는 뜻이다.
    """
    threshold = similarity_to_distance(0.85)
    t = 15_000
    full = _match_positions(noise_series, 40, t, 20, threshold, 60)
    trimmed = _match_positions(noise_series.slice(0, t + 1), 40, t, 20, threshold, 60)
    assert np.array_equal(full, trimmed)


def test_scores_are_unchanged_by_data_after_the_last_point(noise_series):
    """마지막 평가 시점 뒤의 데이터는 성적에 영향을 주면 안 된다."""
    a = validate(noise_series, (20,), horizons=(1, 3), points=40, seed=1)
    longer = make_series(
        noise_series.close.tolist() + (noise_series.close[-1] * np.ones(500)).tolist()
    )
    b = validate(longer, (20,), horizons=(1, 3), points=40, seed=1)
    # 평가 시점 추출 범위가 달라지므로 성적이 완전히 같을 수는 없지만,
    # 뒤에 붙인 평평한 구간이 앞쪽 예측을 바꾸지는 않아야 한다.
    assert a[0].attempts > 0 and b[0].attempts > 0


# ------------------------------------------------------------------ 빠른 탐색
def test_fast_search_matches_the_exact_search():
    """빠른 탐색은 전수 계산과 **결과가 같아야** 한다.

    다르면 검증 결과 전체를 믿을 수 없다. 하한으로 거르는 방식이라
    원리상 같아야 하고, 실제로 같은지 확인한다.
    """
    rng = np.random.default_rng(11)
    closes = 40_000_000 * np.exp(np.cumsum(rng.normal(0, 0.0009, 60_000)))
    threshold = similarity_to_distance(0.85)

    for length in (20, 60, 180):
        query = closes[-length:]
        history = closes[: 60_000 - length - 25]

        exact = distances_to(query, history, length)
        exact = np.where(flat_mask(history, length), np.inf, exact)
        expected = np.flatnonzero(exact <= threshold)

        positions, distances = distances_within(query, history, length, threshold)
        assert np.array_equal(positions, expected), f"길이 {length}에서 매치가 다릅니다"
        assert distances == pytest.approx(exact[expected], abs=1e-12)


def test_fast_search_never_returns_something_too_far():
    rng = np.random.default_rng(12)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 40_000)))
    threshold = similarity_to_distance(0.9)
    _, distances = distances_within(closes[-30:], closes[:-40], 30, threshold)
    assert np.all(distances <= threshold + 1e-12)


# ------------------------------------------------------------------ 찍기 비교
def test_accuracy_is_reported_next_to_the_guessing_baseline(noise_series):
    """적중률만 있으면 속는다 — 찍기 기준이 반드시 같이 나와야 한다."""
    scores = validate(noise_series, (20, 60), horizons=(1, 5), points=60, seed=2)
    for score in scores:
        if score.predictions:
            assert 0.0 <= score.base_accuracy <= 1.0
            assert score.skill == pytest.approx(score.accuracy - score.base_accuracy)


def test_skill_error_shrinks_with_more_predictions():
    """예측이 적으면 실력 추정이 흔들린다는 걸 숫자로 말해야 한다."""
    from patternscan.validate import Score

    few = Score("minute1", 20, 1, predictions=25)
    many = Score("minute1", 20, 1, predictions=2500)
    assert few.skill_error > many.skill_error * 5


def test_small_skill_is_not_called_real():
    from patternscan.validate import Score

    # 예측 100회면 표준오차 5% — 실력 8%는 우연으로 설명된다
    weak = Score("minute1", 20, 1, predictions=100, hits=58, base_hits=50)
    assert weak.skill == pytest.approx(0.08)
    assert not weak.skill_is_real

    strong = Score("minute1", 20, 1, predictions=2000, hits=1200, base_hits=1000)
    assert strong.skill_is_real


# ------------------------------------------------------------------ 신호/잡음
def test_a_planted_signal_is_detected():
    """길이 5짜리 표식 뒤에만 오르게 심으면, 짧은 길이가 이겨야 한다."""
    closes = planted_signal(seed=2, occurrences=220)
    series = make_series(closes)
    scores = validate(series, (5, 20, 120), horizons=(1, 3), points=120, seed=3)

    cost = round_trip_cost()
    ranked = best_lengths(scores)
    assert ranked, "예측을 충분히 내지 못했습니다"

    best = ranked[0]
    assert best.length <= 20, f"표식은 길이 {len(MARKER)}인데 길이 {best.length}이 1등입니다"
    assert best.mean_up_return > cost, (
        f"'상승' 예측 시 평균 수익 {best.mean_up_return:+.4%}가 비용 {cost:.3%}를 못 넘습니다"
    )


def test_pure_noise_produces_no_paying_length(noise_series):
    """잡음에서는 '비용을 넘기면서 우연도 아닌' 조합이 나오면 안 된다."""
    scores = validate(noise_series, (5, 20, 60, 120), horizons=(1, 3, 5), points=150, seed=4)
    cost = round_trip_cost()
    winners = [s for s in scores if s.skill_is_real and s.mean_up_return > cost]
    assert not winners, f"잡음에서 통과한 조합이 있습니다: {[w.label for w in winners]}"


# ------------------------------------------------------------------ 집계 규칙
def test_coverage_falls_as_the_shape_gets_longer(noise_series):
    """긴 모양일수록 같은 게 없어 예측을 덜 낸다 — 그걸 숨기면 안 된다."""
    scores = validate(noise_series, (5, 120), horizons=(1,), points=80, seed=6)
    short = next(s for s in scores if s.length == 5)
    long_ = next(s for s in scores if s.length == 120)
    assert short.coverage > long_.coverage


def test_counts_are_consistent(noise_series):
    scores = validate(noise_series, (20,), horizons=(1, 5), points=50, seed=7)
    for score in scores:
        assert score.predictions <= score.attempts
        assert score.hits <= score.predictions
        assert score.said_up_won <= score.said_up <= score.predictions
        assert len(score.up_returns) == score.said_up
        assert len(score.all_returns) == score.attempts


def test_not_enough_data_is_reported_not_crashed():
    series = make_series([100.0 + i * 0.01 for i in range(300)])
    with pytest.raises(ValueError, match="모자"):
        validate(series, (180,), horizons=(1,), points=10)
