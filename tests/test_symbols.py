"""'구간별로 자르기' 방식 검증.

연속 비교와 같은 성질을 지켜야 한다: 가격대와 변동폭이 달라도 같은 모양이면
같은 기호가 나와야 하고, 방향이 다르면 다른 기호가 나와야 한다.
"""

from __future__ import annotations

import numpy as np
import pytest

from patternscan.symbols import (
    DEFAULT_BUCKETS,
    breakpoints,
    mismatch_fraction,
    symbolize,
    symbolize_all,
)


# ------------------------------------------------------------------ 경계
def test_breakpoints_match_the_normal_quantiles():
    """5등분 경계는 표준정규분포의 20·40·60·80 분위수여야 한다."""
    assert breakpoints(5) == pytest.approx([-0.8416, -0.2533, 0.2533, 0.8416], abs=1e-3)
    assert breakpoints(2) == pytest.approx([0.0], abs=1e-9)


def test_breakpoints_are_symmetric_and_sorted():
    for buckets in (3, 4, 5, 7, 9):
        points = breakpoints(buckets)
        assert points.size == buckets - 1
        assert np.all(np.diff(points) > 0)
        assert points == pytest.approx(-points[::-1], abs=1e-9)


def test_breakpoints_split_a_normal_sample_evenly():
    """같은 확률로 나뉘어야 특정 기호만 잔뜩 나오지 않는다."""
    sample = np.random.default_rng(0).normal(size=200_000)
    counts = np.bincount(np.searchsorted(breakpoints(5), sample), minlength=5)
    assert np.all(np.abs(counts / sample.size - 0.2) < 0.01)


def test_too_few_buckets_is_rejected():
    with pytest.raises(ValueError, match="2개 이상"):
        breakpoints(1)


# ------------------------------------------------------------------ 기호화
def test_price_level_does_not_change_the_symbols():
    """4천달러 구간과 4천만원 구간의 같은 모양은 같은 기호여야 한다."""
    low = np.array([100.0, 100.5, 101.2, 102.4, 104.0])
    assert np.array_equal(symbolize(low), symbolize(low * 900_000))


def test_amplitude_does_not_change_the_symbols():
    """창 안의 표준편차로 나누므로 변동폭이 달라도 같아야 한다."""
    base = np.array([100.0, 100.5, 101.2, 102.4, 104.0])
    amplified = 100.0 * (1.0 + (base / 100.0 - 1.0) * 10.0)
    assert np.array_equal(symbolize(base), symbolize(amplified))


def test_a_window_of_length_n_gives_n_symbols():
    """경로를 그대로 자르므로 봉 수와 기호 수가 같다."""
    assert symbolize(np.arange(100.0, 110.0)).size == 10


def test_rising_and_falling_get_opposite_symbols():
    """오를지 내릴지가 질문이므로, 방향이 반대면 기호도 반대여야 한다.

    처음에는 봉마다의 변화율을 기호화했는데 그러면 평균을 빼는 과정에서
    전체 방향이 지워져, '가속 상승'과 '감속 하락'이 똑같은 기호가 나왔다.
    """
    rising = np.array([100.0, 100.5, 101.2, 102.4, 104.0])
    falling = rising[::-1].copy()
    up, down = symbolize(rising), symbolize(falling)
    assert np.array_equal(up, down[::-1]), "방향을 뒤집으면 기호도 뒤집혀야 합니다"
    assert not np.array_equal(up, down)
    assert up[0] < up[-1] and down[0] > down[-1]


def test_zigzag_alternates_between_two_symbols():
    """오르내림만 반복하는 구간은 두 기호가 번갈아 나와야 한다."""
    zigzag = np.array([100.0, 101.0, 100.0, 101.0, 100.0])
    symbols = symbolize(zigzag)
    assert len(set(symbols.tolist())) == 2
    assert symbols[0] == symbols[2] == symbols[4]
    assert symbols[1] == symbols[3]
    assert symbols[1] > symbols[0]


def test_a_steady_trend_uses_the_whole_range():
    """곧게 오르는 구간은 낮은 기호에서 높은 기호까지 훑어야 한다."""
    symbols = symbolize(np.array([100.0 * 1.002**i for i in range(40)]))
    assert symbols[0] == 0
    assert symbols[-1] == DEFAULT_BUCKETS - 1
    assert np.all(np.diff(symbols) >= 0)


def test_symbols_stay_inside_the_bucket_range():
    rng = np.random.default_rng(4)
    values = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 200)))
    for buckets in (3, 5, 7):
        symbols = symbolize(values, buckets)
        assert symbols.min() >= 0
        assert symbols.max() <= buckets - 1


def test_flat_window_gives_one_symbol():
    """안 움직인 창은 나눌 게 없다 — 가운데 구간으로 몰린다."""
    symbols = symbolize(np.array([100.0] * 10))
    assert len(set(symbols.tolist())) == 1


def test_rows_are_symbolized_independently():
    matrix = np.array([[100.0, 101.0, 103.0], [200.0, 202.0, 206.0]])
    out = symbolize(matrix)
    assert out.shape == (2, 3)
    assert np.array_equal(out[0], out[1])  # 같은 비율 -> 같은 기호


def test_window_shorter_than_two_is_rejected():
    with pytest.raises(ValueError, match="2 이상"):
        symbolize(np.array([100.0]))


# ------------------------------------------------------------------ 비교
def test_identical_window_has_zero_mismatch():
    values = np.array([100.0, 101, 103, 102, 105, 104, 107, 106])
    assert mismatch_fraction(values[:4], values, 4)[0] == pytest.approx(0.0)


def test_mismatch_is_a_fraction():
    rng = np.random.default_rng(6)
    values = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 5000)))
    fractions = mismatch_fraction(values[:20], values, 20)
    assert np.all((fractions >= 0.0) & (fractions <= 1.0))


def test_symbolize_all_covers_every_window():
    values = np.arange(100.0, 120.0)
    out = symbolize_all(values, 5)
    assert out.shape == (len(values) - 5 + 1, 5)


def test_chunking_does_not_change_mismatch():
    rng = np.random.default_rng(7)
    values = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 4000)))
    whole = mismatch_fraction(values[:25], values, 25, chunk=10_000)
    chunked = mismatch_fraction(values[:25], values, 25, chunk=53)
    assert whole == pytest.approx(chunked)


def test_more_buckets_means_more_mismatches():
    """구간을 잘게 자를수록 우연히 같은 기호가 나올 확률이 준다."""
    rng = np.random.default_rng(8)
    values = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 20_000)))
    coarse = mismatch_fraction(values[-40:], values[:-50], 40, 3).mean()
    fine = mismatch_fraction(values[-40:], values[:-50], 40, 9).mean()
    assert fine > coarse
