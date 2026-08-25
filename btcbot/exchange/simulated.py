"""모의 브로커. 백테스트와 페이퍼 트레이딩이 공유한다.

두 모드의 차이는 "가격이 어디서 오느냐"뿐이다:
백테스트는 과거 봉에서, 페이퍼는 실시간 시세에서 온다. 어느 쪽이든
엔진이 `mark(ts, price)`로 현재 시점을 알려주고, 체결은 그 가격에
수수료와 슬리피지를 얹어 계산한다.
"""

from __future__ import annotations

import math
from datetime import datetime

from ..models import AccountState, Fill, Position, Side
from .base import DEFAULT_FEE_RATE, MIN_ORDER_KRW, Broker, OrderRejected


def _tolerance(scale: float) -> float:
    """`scale` 규모의 금액을 다룰 때 무시해도 되는 부동소수 오차.

    전액 매수는 `수량 = 예산 / (가격 × (1+수수료))` 로 구한 뒤 다시 곱해
    금액을 되돌리므로 마지막 자리에 오차가 남는다. 고정값 1e-6을 쓰면
    100억원대부터는 그 값이 float의 최소 단위(ulp)보다 작아져서, 잔고가
    충분한데도 '잔고 부족'으로 거부된다 — 백테스트 도중 계좌가 커지자
    실제로 거래가 막혔다.

    그래서 허용치를 ulp에 맞춘다. 금액이 커지면 같이 커지고, 작은 금액에서는
    1e-6 아래로 내려가지 않는다. 이 검사의 목적은 '수량보다 많이 팔았다'
    같은 논리 오류를 잡는 것이지 마지막 자리 먼지를 잡는 게 아니다.
    """
    return max(math.ulp(abs(scale)) * 16.0, 1e-6)


class SimulatedBroker(Broker):
    def __init__(
        self,
        market: str,
        cash: float,
        fee_rate: float = DEFAULT_FEE_RATE,
        slippage: float = 0.0005,
        min_order_krw: float = MIN_ORDER_KRW,
    ) -> None:
        if cash < 0:
            raise ValueError("초기 현금은 0 이상이어야 합니다")
        self.market = market
        self.initial_cash = float(cash)
        self.cash = float(cash)
        self.fee_rate = float(fee_rate)
        self.slippage = float(slippage)
        self.min_order_krw = float(min_order_krw)
        self.position = Position(market)
        self.fills: list[Fill] = []
        self.realized_pnl = 0.0
        self._price: float | None = None
        self._ts: datetime | None = None

    # ------------------------------------------------------------------ 시점
    def mark(self, ts: datetime, price: float) -> None:
        """엔진이 매 봉마다 호출해 현재 시각과 체결 기준가를 알려준다."""
        if price <= 0:
            raise ValueError(f"가격은 양수여야 합니다: {price}")
        self._ts = ts
        self._price = float(price)

    def now(self) -> datetime:
        if self._ts is None:
            raise RuntimeError("mark()가 먼저 호출되어야 합니다")
        return self._ts

    @property
    def price(self) -> float:
        if self._price is None:
            raise RuntimeError("mark()가 먼저 호출되어야 합니다")
        return self._price

    # ------------------------------------------------------------------ 조회
    def snapshot(self) -> AccountState:
        return AccountState(cash=self.cash, position=self.position, price=self.price)

    # ------------------------------------------------------------------ 주문
    def market_buy(self, krw_amount: float, reason: str = "") -> Fill | None:
        budget = min(float(krw_amount), self.cash)
        if budget < self.min_order_krw:
            return None

        # 시장가 매수는 호가를 위로 먹으며 체결된다.
        fill_price = self.price * (1 + self.slippage)
        # 수수료까지 포함해 budget을 넘지 않도록 수량을 정한다.
        volume = budget / (fill_price * (1 + self.fee_rate))
        if volume <= 0:
            return None

        gross = fill_price * volume
        fee = gross * self.fee_rate
        if gross + fee > self.cash + _tolerance(self.cash):
            raise OrderRejected(f"잔고 부족: 필요 {gross + fee:,.0f}, 보유 {self.cash:,.0f}")

        fill = Fill(
            market=self.market,
            side=Side.BUY,
            price=fill_price,
            volume=volume,
            fee=fee,
            ts=self.now(),
            reason=reason,
        )
        self._apply(fill)
        return fill

    def market_sell(self, volume: float, reason: str = "") -> Fill | None:
        volume = min(float(volume), self.position.volume)
        if volume <= 0:
            return None

        # 시장가 매도는 호가를 아래로 먹으며 체결된다.
        fill_price = self.price * (1 - self.slippage)
        if fill_price * volume < self.min_order_krw and volume < self.position.volume:
            # 최소 주문금액에 못 미치는 부분 매도는 거래소가 거부한다.
            return None

        gross = fill_price * volume
        fill = Fill(
            market=self.market,
            side=Side.SELL,
            price=fill_price,
            volume=volume,
            fee=gross * self.fee_rate,
            ts=self.now(),
            reason=reason,
        )
        self._apply(fill)
        return fill

    def _apply(self, fill: Fill) -> None:
        self.realized_pnl += self.position.apply(fill)
        # 허용치는 '거래 전 현금'과 '거래 규모' 중 큰 쪽을 기준으로 잡는다.
        # 뺄셈의 오차는 두 피연산자 중 큰 쪽의 ulp에서 나온다.
        scale = max(abs(self.cash), fill.gross + fill.fee)
        self.cash += fill.cash_delta
        if self.cash < -_tolerance(scale):
            raise OrderRejected(f"현금이 음수가 되었습니다: {self.cash}")
        self.cash = max(0.0, self.cash)
        self.fills.append(fill)
