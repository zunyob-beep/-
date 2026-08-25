"""RSI 평균회귀.

RSI가 `oversold` 아래로 내려가면 분할 진입하고, `exit_rsi` 위로 올라오면
청산한다. 추세장에서는 계속 물리므로 `trend_ma`(기본 200)를 두어
장기 이평 위에 있을 때만 매수한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..indicators import rsi_series, sma
from ..models import Action, Candle, Signal
from .base import Strategy, register


@register
class RSIReversion(Strategy):
    """RSI 과매도에서 분할 매수, 회복하면 청산 (평균회귀)."""

    name = "rsi"

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {
            "period": 14,
            "oversold": 30.0,
            "exit_rsi": 55.0,
            "trend_ma": 200,  # 0이면 추세 필터 끔
            "target_weight": 1.0,
            "scale_in": True,  # RSI가 낮을수록 비중을 키움
        }

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self.warmup = max(int(self.params["period"]) + 2, int(self.params["trend_ma"]) + 1)

    def decide(self, candles: Sequence[Candle]) -> Signal:
        if len(candles) < self.warmup:
            return Signal(reason="warmup")

        closes = [c.close for c in candles]
        value = rsi_series(closes, int(self.params["period"]))[-1]
        if value is None:
            return Signal(reason="warmup")

        if value >= float(self.params["exit_rsi"]):
            return Signal(
                action=Action.SELL,
                target_weight=0.0,
                reason=f"RSI {value:.1f} >= 청산선 {self.params['exit_rsi']}",
            )

        oversold = float(self.params["oversold"])
        if value > oversold:
            # 중립 구간: 이미 들고 있으면 유지, 없으면 진입하지 않는다.
            return Signal(action=Action.HOLD, target_weight=None, reason=f"RSI {value:.1f} 중립")

        trend_ma = int(self.params["trend_ma"])
        if trend_ma > 0:
            ma = sma(closes, trend_ma)
            if ma is not None and closes[-1] < ma:
                return Signal(
                    target_weight=0.0,
                    reason=f"RSI {value:.1f} 과매도지만 MA{trend_ma} 아래 — 하락추세 회피",
                )

        weight = float(self.params["target_weight"])
        if self.params["scale_in"] and oversold > 0:
            # RSI 30 -> 50%, RSI 0 -> 100%
            depth = (oversold - value) / oversold
            weight *= min(1.0, 0.5 + 0.5 * depth)

        return Signal(
            action=Action.BUY,
            target_weight=round(weight, 4),
            reason=f"RSI {value:.1f} <= 과매도선 {oversold}",
        )
