"""모의 브로커. 백테스트와 페이퍼 트레이딩이 공유한다.

두 모드의 차이는 "가격이 어디서 오느냐"뿐이다:
백테스트는 과거 봉에서, 페이퍼는 실시간 시세에서 온다. 어느 쪽이든
엔진이 `mark(ts, price)`로 현재 시점을 알려주고, 체결은 그 가격에
수수료와 슬리피지를 얹어 계산한다.
"""

from __future__ import annotations

from datetime import datetime

from ..models import AccountState, Fill, Position, Side
from .base import DEFAULT_FEE_RATE, MIN_ORDER_KRW, Broker, OrderRejected


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
        if gross + fee > self.cash + 1e-6:
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
        self.cash += fill.cash_delta
        if self.cash < -1e-6:
            raise OrderRejected(f"현금이 음수가 되었습니다: {self.cash}")
        self.cash = max(0.0, self.cash)
        self.fills.append(fill)
