"""변동성 돌파 (Larry Williams).

  목표가 = 오늘 시가 + (어제 고가 - 어제 저가) * k

봉의 종가가 목표가를 넘어서면 진입하고, KST 날짜가 바뀌면 청산한다.
`k`를 고정하는 대신 `dynamic_k=True`면 최근 봉들의 노이즈 평균을 k로 쓴다
(노이즈가 큰 횡보장에서는 목표가가 멀어져 진입이 줄어든다).

일봉으로 백테스트하면 "종가가 목표가를 넘었는지"만 보므로 장중 돌파를
놓쳐 실제보다 보수적으로 나온다. 분봉(예: 60분)으로 돌리는 편이
실제 봇 동작에 가깝다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..indicators import noise, sma
from ..models import Action, Candle, Signal
from .base import Strategy, register


@register
class VolatilityBreakout(Strategy):
    """전일 변동폭의 k배를 돌파하면 진입, 날짜가 바뀌면 청산."""

    name = "vb"

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {
            "k": 0.5,
            "dynamic_k": False,
            "noise_period": 20,
            "ma_period": 0,  # >0이면 종가가 이 이동평균 위일 때만 진입
            "target_weight": 1.0,
        }

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        # 필요한 만큼만 기다린다. 켜지도 않은 옵션 때문에 warmup이 길어지면
        # 봇을 띄우고 며칠을 아무것도 못 하고 흘려보내게 된다.
        self.warmup = 2
        if self.params["dynamic_k"]:
            self.warmup = max(self.warmup, int(self.params["noise_period"]) + 1)
        if int(self.params["ma_period"]) > 0:
            self.warmup = max(self.warmup, int(self.params["ma_period"]) + 1)

    def decide(self, candles: Sequence[Candle]) -> Signal:
        if len(candles) < self.warmup:
            return Signal(reason="warmup")

        last = candles[-1]
        today = last.kst_date

        # 오늘(KST) 첫 봉과 어제 마지막 봉을 찾는다.
        first_of_today = last
        prev_day_last: Candle | None = None
        for candle in reversed(candles[:-1]):
            if candle.kst_date == today:
                first_of_today = candle
            else:
                prev_day_last = candle
                break

        if prev_day_last is None:
            return Signal(reason="이전 거래일 데이터 없음")

        prev_range = self._prev_day_range(candles, prev_day_last.kst_date)
        if prev_range <= 0:
            return Signal(reason="전일 변동폭 0")

        k = self._k(candles)
        target = first_of_today.open + prev_range * k

        if last.close <= target:
            return Signal(
                action=Action.SELL,
                target_weight=0.0,
                reason=f"목표가 미달 (close={last.close:,.0f} <= target={target:,.0f}, k={k:.2f})",
            )

        ma_period = int(self.params["ma_period"])
        if ma_period > 0:
            ma = sma([c.close for c in candles], ma_period)
            if ma is not None and last.close < ma:
                return Signal(
                    target_weight=0.0,
                    reason=f"돌파했으나 MA{ma_period} 아래 (close={last.close:,.0f} < ma={ma:,.0f})",
                )

        return Signal(
            action=Action.BUY,
            target_weight=float(self.params["target_weight"]),
            reason=f"변동성 돌파 (close={last.close:,.0f} > target={target:,.0f}, k={k:.2f})",
        )

    def _k(self, candles: Sequence[Candle]) -> float:
        if not self.params["dynamic_k"]:
            return float(self.params["k"])
        period = int(self.params["noise_period"])
        window = candles[-period:]
        if not window:
            return float(self.params["k"])
        return sum(noise(c) for c in window) / len(window)

    @staticmethod
    def _prev_day_range(candles: Sequence[Candle], prev_date: str) -> float:
        """전 거래일 봉들을 묶어 (고가 - 저가)를 구한다.

        일봉이면 봉 하나의 range와 같고, 분봉이면 그날 전체 범위가 된다.
        """
        highs: list[float] = []
        lows: list[float] = []
        for candle in reversed(candles):
            if candle.kst_date == prev_date:
                highs.append(candle.high)
                lows.append(candle.low)
            elif highs:
                break
        if not highs:
            return 0.0
        return max(highs) - min(lows)
