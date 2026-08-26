"""'같은 모양'의 정의를 검증한다.

여기가 틀리면 나머지 통계는 전부 엉뚱한 것을 세게 된다.
"""

from __future__ import annotations

import numpy as np
import pytest

from patternscan.shape import (
    distances_to,
    flat_mask,
    is_flat,
    linearity,
    normalize_window,
    sliding_windows,
)


def test_price_level_does_not_matter():
    """4천만원 구간과 9천만원 구간의 같은 모양은 같은 모양이어야 한다."""
    low = np.array([100.0, 101, 102, 101, 103])
    high = low * 900_000  # 가격대만 다르고 비율은 동일
    assert normalize_window(low) == pytest.approx(normalize_window(high))


def test_amplitude_is_ignored_in_shape_mode():
    """오르내린 '순서'가 같으면 폭이 달라도 같은 모양(shape 모드)."""
    small = np.array([100.0, 100.5, 101.0, 100.5, 101.5])
    big = np.array([100.0, 105.0, 110.0, 105.0, 115.0])
    assert normalize_window(small, "shape") == pytest.approx(
        normalize_window(big, "shape"), abs=0.02
    )


def test_amplitude_matters_in_amplitude_mode():
    """폭까지 같아야 한다고 하면 둘은 달라야 한다."""
    small = np.array([100.0, 100.5, 101.0, 100.5, 101.5])
    big = np.array([100.0, 105.0, 110.0, 105.0, 115.0])
    a = normalize_window(small, "amplitude")
    b = normalize_window(big, "amplitude")
    assert np.abs(a - b).max() > 0.05


def test_normalized_shape_has_zero_mean():
    values = np.array([100.0, 103, 99, 105, 101])
    assert normalize_window(values).mean() == pytest.approx(0.0, abs=1e-12)


def test_shape_mode_has_unit_scale():
    values = np.array([100.0, 103, 99, 105, 101])
    assert normalize_window(values, "shape").std() == pytest.approx(1.0)


def test_inverted_shape_is_not_the_same():
    """올랐다 내린 모양과 내렸다 오른 모양은 달라야 한다."""
    up_then_down = np.array([100.0, 102, 104, 102, 100])
    down_then_up = np.array([100.0, 98, 96, 98, 100])
    a = normalize_window(up_then_down)
    b = normalize_window(down_then_up)
    assert np.abs(a - b).max() > 1.0


def test_rows_are_normalized_independently():
    matrix = np.array([[100.0, 101, 102], [200.0, 202, 204]])
    out = normalize_window(matrix)
    assert out.shape == (2, 3)
    assert out[0] == pytest.approx(out[1])  # 같은 비율 -> 같은 모양


def test_rejects_unknown_scale():
    with pytest.raises(ValueError, match="scale"):
        normalize_window(np.array([1.0, 2.0]), "무언가")


# ------------------------------------------------------------------ 평평한 구간
def test_flat_window_is_detected():
    assert is_flat(np.array([100.0] * 10))
    assert not is_flat(np.array([100.0, 100.1, 100.0]))


def test_flat_mask_marks_only_flat_windows():
    values = np.array([100.0] * 5 + [101.0, 102.0, 103.0])
    mask = flat_mask(values, 3)
    assert mask[0] and mask[1] and mask[2]  # 전부 100인 구간
    assert not mask[-1]  # 움직이는 구간


# ------------------------------------------------------------------ 거리
def test_identical_window_has_zero_distance():
    values = np.array([100.0, 101, 103, 102, 105, 104])
    distances = distances_to(values[:3], values, 3)
    assert distances[0] == pytest.approx(0.0, abs=1e-12)


def test_distance_grows_with_difference():
    base = np.array([100.0, 101.0, 102.0, 101.0, 100.0])
    close = np.array([100.0, 101.0, 102.1, 101.0, 100.0])
    far = np.array([100.0, 99.0, 98.0, 99.0, 100.0])
    d_close = distances_to(base, close, 5)[0]
    d_far = distances_to(base, far, 5)[0]
    assert d_close < d_far


def test_distances_cover_every_window():
    values = np.arange(100, 110, dtype=np.float64)
    assert distances_to(values[:4], values, 4).size == len(values) - 4 + 1


def test_chunking_does_not_change_results():
    """긴 데이터를 나눠 계산해도 결과가 같아야 한다."""
    rng = np.random.default_rng(7)
    values = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 3000)))
    query = values[:30]
    whole = distances_to(query, values, 30, chunk=10_000)
    chunked = distances_to(query, values, 30, chunk=97)
    assert whole == pytest.approx(chunked)


def test_sliding_windows_shape():
    values = np.arange(10, dtype=np.float64)
    assert sliding_windows(values, 4).shape == (7, 4)
    assert sliding_windows(values, 20).shape == (0, 20)


def test_sliding_windows_rejects_bad_length():
    with pytest.raises(ValueError):
        sliding_windows(np.arange(5, dtype=np.float64), 0)


# ------------------------------------------------------------------ 직선성
def test_a_straight_line_is_fully_linear():
    """곧게 오르기만 하는 구간은 '모양'이랄 게 없다."""
    rising = np.array([100.0 * 1.002**i for i in range(60)])
    falling = np.array([100.0 * 0.998**i for i in range(60)])
    assert linearity(rising) > 0.99
    assert linearity(falling) > 0.99


def test_a_zigzag_is_not_linear():
    """오르내림이 뚜렷하면 직선성이 낮아야 한다."""
    zigzag = np.array([100.0 + 5 * (-1) ** i for i in range(60)])
    assert linearity(zigzag) < 0.2


def test_a_hump_is_not_linear():
    """올랐다 내리는 봉우리는 직선과 전혀 다르다."""
    x = np.linspace(-1, 1, 60)
    hump = 100.0 * (1.0 + 0.05 * (1 - x**2))
    assert linearity(hump) < 0.2


def test_linearity_ignores_price_level_and_amplitude():
    """정규화 후에 재므로 가격대나 변동폭에는 영향받지 않아야 한다."""
    base = np.array([100.0 + i + (i % 7) for i in range(60)])
    assert linearity(base) == pytest.approx(linearity(base * 900_000), abs=1e-9)


def test_flat_window_counts_as_linear():
    """전혀 안 움직인 구간은 0으로 나눌 수 없다 — 직선으로 본다."""
    assert linearity(np.array([100.0] * 30)) == 1.0


def test_linearity_is_between_zero_and_one():
    rng = np.random.default_rng(3)
    for _ in range(20):
        values = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 50)))
        assert 0.0 <= linearity(values) <= 1.0
