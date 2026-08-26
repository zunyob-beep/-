"""탐색 규칙을 검증한다.

이 파일이 지키는 것은 세 가지다. 셋 중 하나라도 깨지면 화면에 뜨는
승률은 실제보다 좋아 보이게 되고, 사용자는 그걸 믿고 돈을 넣는다.

1. 미래를 안 본다
2. 겹치는 구간을 두 번 세지 않는다
3. 빠진 봉을 이어붙여 없던 모양을 만들지 않는다
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest

from patternscan.scan import DEFAULT_FEE, DEFAULT_SLIPPAGE, round_trip_cost, scan, scan_all
from tests.conftest import make_series, repeating

PATTERN = [100.0, 101.0, 102.5, 101.5, 103.0]


# ------------------------------------------------------------------ 미래 참조
def test_matches_never_come_from_the_future(random_walk):
    """모든 매치는 질의 구간이 시작하기 전에 끝나야 한다."""
    length = 20
    query_end = len(random_walk) - 1
    result = scan(random_walk, length, query_end=query_end, top_k=40)

    query_start = query_end - length + 1
    for match in result.matches:
        assert match.end_index < query_start, "질의 구간과 겹치거나 미래에서 왔다"


def test_outcome_window_also_stays_in_the_past(random_walk):
    """매치의 '직후 h봉'까지도 질의 구간 전에 끝나야 한다.

    안 그러면 결과를 관측하는 데 질의 시점 이후 데이터를 쓰게 된다.
    """
    length, max_h = 20, 20
    query_end = len(random_walk) - 1
    query_start = query_end - length + 1
    result = scan(random_walk, length, query_end=query_end, top_k=40)

    for match in result.matches:
        assert match.end_index + max_h < query_start


def test_scanning_mid_history_ignores_later_data():
    """과거 시점을 질의하면 그 이후 데이터는 결과에 영향을 주면 안 된다."""
    closes = repeating(PATTERN, 12)
    full = make_series(closes)
    query_end = 300

    a = scan(full, 5, query_end=query_end, top_k=20)
    # 뒤쪽 데이터를 잘라내도 같은 결과가 나와야 한다
    trimmed = make_series(closes[: query_end + 1])
    b = scan(trimmed, 5, query_end=query_end, top_k=20)

    assert [m.end_index for m in a.matches] == [m.end_index for m in b.matches]
    assert [m.returns for m in a.matches] == [m.returns for m in b.matches]


# ------------------------------------------------------------------ 중복 계산
def test_matches_do_not_overlap():
    """한 칸씩 밀린 구간은 사실상 같은 표본이다."""
    closes = repeating(PATTERN, 15)
    series = make_series(closes)
    result = scan(series, 5, top_k=50)

    ends = sorted(m.end_index for m in result.matches)
    for a, b in pairwise(ends):
        assert b - a >= 5, f"구간 {a}와 {b}가 겹칩니다"


def test_overlap_rule_reduces_sample_count(random_walk):
    """겹침을 허용했다면 훨씬 많이 잡혔을 것 — 그게 함정이다."""
    result = scan(random_walk, 30, top_k=100)
    ends = sorted(m.end_index for m in result.matches)
    assert all(b - a >= 30 for a, b in pairwise(ends))


# ------------------------------------------------------------------ 빠진 봉
def test_windows_with_missing_candles_are_excluded():
    """거래가 없어 빠진 봉을 이어붙이면 없던 모양이 생긴다."""
    closes = repeating(PATTERN, 15)
    # 앞쪽 어딘가의 봉을 통째로 뺀다
    series = make_series(closes, skip={52, 53})
    result = scan(series, 5, top_k=50)

    step = int(series.ts[1] - series.ts[0])
    for match in result.matches:
        start = match.end_index - 5 + 1
        spans = np.diff(series.ts[start : match.end_index + 1])
        assert np.all(spans == step), "빠진 봉이 있는 구간이 매치로 잡혔다"


# ------------------------------------------------------------------ 평평한 구간
def test_flat_windows_are_not_counted(flat_series):
    """전혀 안 움직인 구간끼리는 거리가 0이라 전부 '완벽한 매치'가 된다."""
    result = scan(flat_series, 10, top_k=50)
    assert result.matches == []
    assert result.query_flat is True


# ------------------------------------------------------------------ 집계
def test_up_requires_beating_the_round_trip_cost():
    """수수료도 못 넘긴 상승은 상승이 아니다."""
    cost = round_trip_cost()
    assert cost == pytest.approx(2 * (DEFAULT_FEE + DEFAULT_SLIPPAGE))

    # 마지막 매치 직후 정확히 '비용만큼' 오르는 데이터
    closes = repeating(PATTERN, 15)
    series = make_series(closes)
    result = scan(series, 5, top_k=30, horizons=(1,))
    outcome = result.outcomes[1]

    for match in result.matches:
        r = match.returns[1]
        if r > cost:
            assert outcome.up >= 1
        elif r < -cost:
            assert outcome.down >= 1


def test_counts_add_up():
    series = make_series(repeating(PATTERN, 15))
    result = scan(series, 5, top_k=30)
    for outcome in result.outcomes.values():
        assert outcome.up + outcome.flat + outcome.down == outcome.total
        assert outcome.total == len(result.matches)


def test_up_rate_counts_flat_as_failure():
    """수수료를 못 넘긴 건 분모에 남아야 한다. 안 그러면 승률이 부풀려진다."""
    series = make_series(repeating(PATTERN, 15))
    result = scan(series, 5, top_k=30)
    outcome = next(iter(result.outcomes.values()))
    assert outcome.up_rate == pytest.approx(outcome.up / outcome.total)


def test_base_rate_is_measured_on_the_same_data(random_walk):
    """비교 기준이 없으면 승률 55%가 좋은 건지 알 수 없다."""
    result = scan(random_walk, 20, top_k=40)
    for outcome in result.outcomes.values():
        assert 0.0 <= outcome.base_up_rate <= 1.0
        assert outcome.base_samples > 0


def test_returns_are_measured_from_the_pattern_end(random_walk):
    result = scan(random_walk, 20, top_k=10, horizons=(1, 5))
    closes = random_walk.close
    for match in result.matches:
        entry = closes[match.end_index]
        for h in (1, 5):
            expected = closes[match.end_index + h] / entry - 1.0
            assert match.returns[h] == pytest.approx(expected)


# ------------------------------------------------------------------ 경계 조건
def test_not_enough_history_is_reported_not_crashed():
    series = make_series([100.0 + i for i in range(30)])
    result = scan(series, 25)
    assert result.matches == []
    assert result.note


def test_query_end_out_of_range():
    series = make_series([100.0 + i for i in range(50)])
    with pytest.raises(ValueError, match="범위"):
        scan(series, 5, query_end=999)


def test_max_distance_filters_matches(random_walk):
    loose = scan(random_walk, 20, top_k=60)
    tight = scan(random_walk, 20, top_k=60, max_distance=0.05)
    assert len(tight.matches) <= len(loose.matches)
    assert all(m.distance <= 0.05 for m in tight.matches)


def test_matches_are_sorted_by_similarity(random_walk):
    result = scan(random_walk, 20, top_k=30)
    distances = [m.distance for m in result.matches]
    assert distances == sorted(distances)


def test_scan_all_covers_every_combination(random_walk):
    series = {"minute1": random_walk}
    results = scan_all(series, (5, 10, 20))
    assert {r.length for r in results} == {5, 10, 20}
    assert all(r.timeframe == "minute1" for r in results)


def test_scan_all_skips_lengths_without_history():
    short = make_series([100.0 + i * 0.1 for i in range(60)])
    results = scan_all({"minute1": short}, (5, 180))
    assert all(r.length != 180 for r in results)


# ------------------------------------------------------------------ 유사도 하한
def test_matches_must_actually_be_similar(random_walk):
    """유사도 하한이 없으면 '같은 모양'이 아닌 것까지 표본에 들어온다.

    z정규화 경로에서 거리 1.41은 상관계수 0, 즉 아무 관계 없는 모양이다.
    하한을 두기 전에는 거리 1.78짜리(상관이 음수인) 구간들을 세면서
    무작위 데이터에서 초과 승률 +36%짜리 '패턴'을 만들어냈다.
    """
    result = scan(random_walk, 120, top_k=60, similarity=0.85)
    assert all(m.similarity >= 0.85 - 1e-9 for m in result.matches)
    if result.matches:
        assert result.min_similarity >= 0.85 - 1e-9


def test_stricter_similarity_gives_fewer_matches(random_walk):
    loose = scan(random_walk, 60, top_k=200, similarity=0.5)
    tight = scan(random_walk, 60, top_k=200, similarity=0.95)
    assert len(tight.matches) <= len(loose.matches)


def test_long_windows_honestly_report_few_matches(random_walk):
    """모양이 길수록 똑같은 게 잘 없다 — 그걸 숨기면 안 된다."""
    short = scan(random_walk, 5, top_k=60)
    long_ = scan(random_walk, 180, top_k=60)
    assert len(long_.matches) < len(short.matches)


def test_similarity_and_distance_round_trip():
    from patternscan.shape import distance_to_similarity, similarity_to_distance

    for rho in (1.0, 0.9, 0.75, 0.5, 0.0):
        assert distance_to_similarity(similarity_to_distance(rho)) == pytest.approx(rho)


def test_uncorrelated_shapes_are_near_distance_root_two():
    """상관 0인 두 모양의 거리는 √2 ≈ 1.41이어야 한다."""
    from patternscan.shape import similarity_to_distance

    assert similarity_to_distance(0.0) == pytest.approx(np.sqrt(2))
    assert similarity_to_distance(1.0) == pytest.approx(0.0)
