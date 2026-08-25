from __future__ import annotations

import pytest

from btcbot.indicators import (
    atr_series,
    bollinger,
    ema_series,
    noise,
    rsi,
    rsi_series,
    sma,
    sma_series,
    stddev,
)
from tests.conftest import series


def test_sma_needs_full_window():
    assert sma([1, 2], 3) is None
    assert sma([1, 2, 3], 3) == 2.0
    assert sma([1, 2, 3, 4], 3) == 3.0


def test_sma_series_aligns_with_input():
    values = [1, 2, 3, 4, 5]
    out = sma_series(values, 3)
    assert len(out) == len(values)
    assert out[:2] == [None, None]
    assert out[2:] == [2.0, 3.0, 4.0]


def test_ema_series_seeds_with_sma():
    values = [1, 2, 3, 4, 5]
    out = ema_series(values, 3)
    assert out[2] == pytest.approx(2.0)  # 첫 값은 단순평균
    assert out[3] == pytest.approx(2.0 + (4 - 2.0) * 0.5)


def test_rsi_all_gains_is_100():
    assert rsi([float(i) for i in range(1, 30)], 14) == pytest.approx(100.0)


def test_rsi_all_losses_is_zero():
    assert rsi([float(i) for i in range(30, 1, -1)], 14) == pytest.approx(0.0)


def test_rsi_flat_series_is_neutral():
    assert rsi([100.0] * 30, 14) == pytest.approx(50.0)


def test_rsi_series_length_and_warmup():
    values = [float(i) for i in range(30)]
    out = rsi_series(values, 14)
    assert len(out) == len(values)
    assert out[13] is None
    assert out[14] is not None


def test_rsi_short_input_is_all_none():
    assert rsi_series([1.0, 2.0], 14) == [None, None]


def test_atr_series_warmup_and_positive():
    candles = series([100 + i for i in range(40)])
    out = atr_series(candles, 14)
    assert len(out) == len(candles)
    assert out[13] is None
    assert out[14] is not None and out[14] > 0


def test_stddev_and_bollinger():
    values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    assert stddev(values, 8) == pytest.approx(2.0)
    lower, mid, upper = bollinger(values, 8, mult=2.0)
    assert mid == pytest.approx(5.0)
    assert lower == pytest.approx(1.0)
    assert upper == pytest.approx(9.0)


def test_bollinger_returns_none_when_short():
    assert bollinger([1.0, 2.0], 20) is None


def test_noise_is_zero_for_pure_trend_candle():
    from datetime import datetime, timezone

    from tests.conftest import make_candle

    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    trending = make_candle(ts, 100, high=110, low=100, close=110)
    assert noise(trending) == pytest.approx(0.0)

    doji = make_candle(ts, 105, high=110, low=100, close=105)
    assert noise(doji) == pytest.approx(1.0)


def test_period_must_be_positive():
    with pytest.raises(ValueError):
        sma([1, 2, 3], 0)
    with pytest.raises(ValueError):
        rsi_series([1.0, 2.0], 0)
