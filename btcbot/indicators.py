"""기술적 지표. 외부 의존성 없이 순수 파이썬으로 계산한다.

모든 함수는 "가장 최근 값" 하나만 돌려주는 스칼라 버전과,
백테스트 플롯/디버깅용 시리즈 버전을 함께 제공한다.
시리즈 버전은 값이 확정되지 않은 앞부분을 None으로 채워
입력과 길이를 맞춘다(인덱스가 어긋나 미래를 훔쳐보는 사고를 막기 위해).
"""

from __future__ import annotations

from collections.abc import Sequence

from .models import Candle

Number = float | int


def sma(values: Sequence[Number], period: int) -> float | None:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def sma_series(values: Sequence[Number], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema_series(values: Sequence[Number], period: int) -> list[float | None]:
    """첫 EMA 시드는 앞 `period`개의 단순평균."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def ema(values: Sequence[Number], period: int) -> float | None:
    return ema_series(values, period)[-1] if values else None


def rsi_series(values: Sequence[Number], period: int = 14) -> list[float | None]:
    """Wilder 방식 RSI."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out

    gains = losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_from(avg_gain, avg_loss)

    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi(values: Sequence[Number], period: int = 14) -> float | None:
    return rsi_series(values, period)[-1] if values else None


def true_range(prev_close: float, candle: Candle) -> float:
    return max(
        candle.high - candle.low,
        abs(candle.high - prev_close),
        abs(candle.low - prev_close),
    )


def atr_series(candles: Sequence[Candle], period: int = 14) -> list[float | None]:
    """Wilder ATR. candles[0]은 이전 종가가 없어 항상 None."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(candles)
    if len(candles) <= period:
        return out

    trs = [true_range(candles[i - 1].close, candles[i]) for i in range(1, len(candles))]
    prev = sum(trs[:period]) / period
    out[period] = prev
    for i in range(period, len(trs)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i + 1] = prev
    return out


def atr(candles: Sequence[Candle], period: int = 14) -> float | None:
    return atr_series(candles, period)[-1] if candles else None


def stddev(values: Sequence[Number], period: int) -> float | None:
    """표본이 아닌 모집단 표준편차(볼린저밴드 관례)."""
    if len(values) < period or period <= 0:
        return None
    window = values[-period:]
    mean = sum(window) / period
    var = sum((v - mean) ** 2 for v in window) / period
    return var**0.5


def bollinger(
    values: Sequence[Number], period: int = 20, mult: float = 2.0
) -> tuple[float, float, float] | None:
    """(하단, 중심, 상단)."""
    mid = sma(values, period)
    sd = stddev(values, period)
    if mid is None or sd is None:
        return None
    return mid - mult * sd, mid, mid + mult * sd


def noise(candle: Candle) -> float:
    """변동성 돌파에서 쓰는 노이즈 비율. 0에 가까울수록 추세가 강한 봉."""
    rng = candle.range
    if rng <= 0:
        return 1.0
    return 1.0 - abs(candle.close - candle.open) / rng


def highest(candles: Sequence[Candle], period: int) -> float | None:
    if len(candles) < period or period <= 0:
        return None
    return max(c.high for c in candles[-period:])


def lowest(candles: Sequence[Candle], period: int) -> float | None:
    if len(candles) < period or period <= 0:
        return None
    return min(c.low for c in candles[-period:])
