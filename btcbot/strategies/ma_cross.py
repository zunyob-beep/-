"""이동평균 교차 추세추종.

단기 이평이 장기 이평 위에 있으면 보유, 아래면 현금. 교차 시점이 아니라
"현재 상태"로 목표 비중을 정하기 때문에 봇이 재시작되어도 같은 판단을
내린다(교차 이벤트를 놓쳐 포지션이 어긋나는 문제가 없다).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..indicators import atr, ema_series, sma_series
from ..models import Action, Candle, Signal
from .base import Strategy, register


@register
class MACross(Strategy):
    """단기 이평이 장기 이평 위면 보유, 아래면 현금 (추세추종)."""

    name = "ma_cross"

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {
            "fast": 10,
            "slow": 30,
            "kind": "ema",  # "ema" | "sma"
            "atr_period": 14,
            "atr_stop_mult": 0.0,  # >0이면 진입가 아래 ATR*배수에 손절선
            "target_weight": 1.0,
        }

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        fast, slow = int(self.params["fast"]), int(self.params["slow"])
        if fast >= slow:
            raise ValueError(f"fast({fast})는 slow({slow})보다 작아야 합니다")
        if self.params["kind"] not in ("ema", "sma"):
            raise ValueError("kind는 'ema' 또는 'sma'")
        self.warmup = slow + 1

    def decide(self, candles: Sequence[Candle]) -> Signal:
        if len(candles) < self.warmup:
            return Signal(reason="warmup")

        closes = [c.close for c in candles]
        series = ema_series if self.params["kind"] == "ema" else sma_series
        fast = series(closes, int(self.params["fast"]))[-1]
        slow = series(closes, int(self.params["slow"]))[-1]
        if fast is None or slow is None:
            return Signal(reason="warmup")

        label = f"{self.params['kind']}{self.params['fast']}/{self.params['slow']}"
        if fast <= slow:
            return Signal(
                action=Action.SELL,
                target_weight=0.0,
                reason=f"데드크로스 상태 {label} ({fast:,.0f} <= {slow:,.0f})",
            )

        stop = None
        mult = float(self.params["atr_stop_mult"])
        if mult > 0:
            value = atr(candles, int(self.params["atr_period"]))
            if value is not None:
                stop = candles[-1].close - value * mult

        return Signal(
            action=Action.BUY,
            target_weight=float(self.params["target_weight"]),
            reason=f"골든크로스 상태 {label} ({fast:,.0f} > {slow:,.0f})",
            stop_price=stop,
        )
