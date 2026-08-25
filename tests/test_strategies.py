from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from btcbot.models import Action
from btcbot.strategies import available, get_strategy
from tests.conftest import make_candle, series

KST_START = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)  # 09:00 KST
#: KST 자정에 정확히 맞춘 시각. 분봉으로 날짜 경계를 시험할 때 필요하다.
KST_MIDNIGHT = datetime(2023, 12, 31, 15, 0, tzinfo=timezone.utc)  # 2024-01-01 00:00 KST


def hourly(prices: list[float], start: datetime | None = None):
    return series(prices, start=start or KST_START, step=timedelta(hours=1))


def test_registry_lists_all_strategies():
    assert set(available()) == {"vb", "ma_cross", "rsi"}


def test_unknown_strategy_raises():
    with pytest.raises(KeyError):
        get_strategy("does_not_exist")


def test_unknown_param_raises():
    with pytest.raises(ValueError):
        get_strategy("vb", nonsense=1)


def test_strategy_returns_hold_during_warmup():
    strategy = get_strategy("ma_cross", fast=5, slow=20)
    signal = strategy.decide(hourly([100.0] * 5))
    assert signal.target_weight is None


# ------------------------------------------------------------------ 변동성 돌파
def build_two_days(prev_high: float, prev_low: float, today_open: float, today_close: float):
    """어제 봉 하나 + 오늘 봉 하나(같은 KST 날짜 경계를 넘도록)."""
    day1 = KST_START  # 2024-01-01 09:00 KST
    day2 = KST_START + timedelta(days=1)
    return [
        make_candle(day1, open_=prev_low, high=prev_high, low=prev_low, close=prev_high),
        make_candle(
            day2,
            open_=today_open,
            high=max(today_open, today_close),
            low=min(today_open, today_close),
            close=today_close,
        ),
    ]


def test_vb_buys_when_close_exceeds_target():
    # 전일 폭 20, k=0.5 -> 목표 = 100 + 10 = 110
    candles = build_two_days(prev_high=120, prev_low=100, today_open=100, today_close=115)
    signal = get_strategy("vb", k=0.5).decide(candles)
    assert signal.action is Action.BUY
    assert signal.target_weight == 1.0


def test_vb_stays_out_below_target():
    candles = build_two_days(prev_high=120, prev_low=100, today_open=100, today_close=105)
    signal = get_strategy("vb", k=0.5).decide(candles)
    assert signal.target_weight == 0.0


def test_vb_larger_k_requires_bigger_move():
    candles = build_two_days(prev_high=120, prev_low=100, today_open=100, today_close=115)
    assert get_strategy("vb", k=0.5).decide(candles).target_weight == 1.0
    assert get_strategy("vb", k=1.0).decide(candles).target_weight == 0.0


def test_vb_exits_when_day_changes():
    """돌파해서 샀더라도 다음 날 첫 봉에서는 목표가가 새로 계산돼 청산된다."""
    strategy = get_strategy("vb", k=0.5)
    candles = build_two_days(prev_high=120, prev_low=100, today_open=100, today_close=115)
    assert strategy.decide(candles).target_weight == 1.0

    # 다음 날 첫 봉: 시가 그대로라 아직 목표가 미달
    next_day = [
        *candles,
        make_candle(KST_START + timedelta(days=2), open_=115, high=116, low=114, close=115),
    ]
    assert strategy.decide(next_day).target_weight == 0.0


def test_vb_ma_filter_blocks_entry_in_downtrend():
    """돌파했더라도 이동평균 아래면 진입하지 않는다.

    급락 중 잠깐의 반등에 올라타는 것이 변동성 돌파의 대표적인 약점이라,
    이 필터가 실제로 작동하는지가 중요하다.
    """
    # 1일차: 300 -> 204로 24시간 하락 (당일 변동폭 96)
    day1 = [
        make_candle(
            KST_MIDNIGHT + timedelta(hours=i),
            open_=300 - i * 4,
            high=300 - i * 4,
            low=300 - (i + 1) * 4,
            close=300 - (i + 1) * 4,
        )
        for i in range(24)
    ]
    # 2일차: 시가 204에서 반등해 목표가(204 + 96*0.1 = 213.6)를 넘지만
    # 20봉 이동평균(약 235)에는 한참 못 미친다.
    day2 = [
        make_candle(KST_MIDNIGHT + timedelta(days=1), open_=204, high=206, low=203, close=205),
        make_candle(
            KST_MIDNIGHT + timedelta(days=1, hours=1), open_=205, high=216, low=205, close=215
        ),
    ]
    candles = day1 + day2

    plain = get_strategy("vb", k=0.1).decide(candles)
    filtered = get_strategy("vb", k=0.1, ma_period=20).decide(candles)
    assert plain.target_weight == 1.0
    assert filtered.target_weight == 0.0
    assert "MA20" in filtered.reason


def test_vb_dynamic_k_uses_noise():
    candles = hourly([100.0 + i for i in range(60)])
    signal = get_strategy("vb", dynamic_k=True, noise_period=20).decide(candles)
    assert "k=" in signal.reason


def test_vb_handles_zero_range_day():
    flat = [
        make_candle(KST_START, open_=100, high=100, low=100, close=100),
        make_candle(KST_START + timedelta(days=1), open_=100, high=100, low=100, close=100),
    ]
    signal = get_strategy("vb").decide(flat)
    assert signal.target_weight is None  # 판단 보류


# --------------------------------------------------------------- 이동평균 교차
def test_ma_cross_long_in_uptrend(uptrend):
    signal = get_strategy("ma_cross", fast=5, slow=20).decide(uptrend)
    assert signal.action is Action.BUY
    assert signal.target_weight == 1.0


def test_ma_cross_flat_in_downtrend(downtrend):
    signal = get_strategy("ma_cross", fast=5, slow=20).decide(downtrend)
    assert signal.action is Action.SELL
    assert signal.target_weight == 0.0


def test_ma_cross_rejects_fast_ge_slow():
    with pytest.raises(ValueError):
        get_strategy("ma_cross", fast=20, slow=10)


def test_ma_cross_atr_stop_below_price(uptrend):
    signal = get_strategy("ma_cross", fast=5, slow=20, atr_stop_mult=2.0).decide(uptrend)
    assert signal.stop_price is not None
    assert signal.stop_price < uptrend[-1].close


def test_ma_cross_is_stateless_across_restarts(uptrend):
    """같은 입력이면 몇 번을 호출해도 같은 결정이어야 한다."""
    strategy = get_strategy("ma_cross", fast=5, slow=20)
    first = strategy.decide(uptrend)
    second = get_strategy("ma_cross", fast=5, slow=20).decide(uptrend)
    assert first == second


# ------------------------------------------------------------------ RSI 회귀
def test_rsi_buys_when_oversold_above_trend_ma():
    # 길게 상승시켜 200일선을 올려둔 뒤 마지막에 급락시킨다
    prices = [100.0 + i * 2 for i in range(260)] + [620.0 - i * 12 for i in range(12)]
    signal = get_strategy("rsi", period=14, oversold=35, trend_ma=200).decide(series(prices))
    assert signal.action is Action.BUY
    assert signal.target_weight > 0


def test_rsi_blocks_entry_below_trend_ma(downtrend):
    prices = [200.0 - i * 0.5 for i in range(260)]
    signal = get_strategy("rsi", period=14, oversold=40, trend_ma=200).decide(series(prices))
    assert signal.target_weight == 0.0
    assert "하락추세" in signal.reason


def test_rsi_neutral_zone_holds_position():
    # 등폭 등락이 반복되면 RSI는 50 부근에 머문다 -> 중립 구간
    prices = [100.0 + (i % 2) for i in range(60)]
    strategy = get_strategy("rsi", period=14, oversold=30, exit_rsi=70, trend_ma=0)
    signal = strategy.decide(series(prices))
    assert 30 < float(signal.reason.split()[1]) < 70
    assert signal.target_weight is None  # 유지: 사지도 팔지도 않는다


def test_rsi_exits_when_overbought(uptrend):
    signal = get_strategy("rsi", period=14, exit_rsi=55, trend_ma=0).decide(uptrend)
    assert signal.action is Action.SELL
    assert signal.target_weight == 0.0


def test_rsi_scale_in_grows_with_depth():
    shallow = [100.0 + i for i in range(60)] + [160.0 - i * 3 for i in range(8)]
    deep = [100.0 + i for i in range(60)] + [160.0 - i * 8 for i in range(8)]
    strategy = get_strategy("rsi", period=14, oversold=45, trend_ma=0, scale_in=True)
    weight_shallow = strategy.decide(series(shallow)).target_weight
    weight_deep = strategy.decide(series(deep)).target_weight
    assert weight_deep >= weight_shallow
