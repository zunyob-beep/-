"""브로커 인터페이스와 거래소 규칙.

엔진은 이 인터페이스만 안다. 그래서 백테스트(SimulatedBroker)와
실거래(UpbitBroker)에서 완전히 같은 코드가 돈다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..models import AccountState, Fill


class ExchangeError(RuntimeError):
    """거래소 호출 실패 전반."""


class AuthError(ExchangeError):
    """API 키 문제(누락/무효/IP 미등록)."""


class RateLimited(ExchangeError):
    """요청 한도 초과. 호출부가 백오프해야 한다."""


class InsufficientFunds(ExchangeError):
    """잔고 부족."""


class OrderRejected(ExchangeError):
    """거래소가 주문을 거부(최소 주문금액 미달 등)."""


#: 업비트 원화 마켓 최소 주문 금액
MIN_ORDER_KRW = 5_000.0

#: 업비트 기본 수수료율(원화 마켓 지정가/시장가 0.05%).
#: 이벤트나 등급에 따라 달라지므로 설정에서 덮어쓸 수 있다.
DEFAULT_FEE_RATE = 0.0005


class Broker(ABC):
    """주문 실행 주체.

    구현체는 두 가지만 책임진다: 계좌 상태를 알려주는 것과, 시장가 주문을
    넣고 체결 결과(Fill)를 돌려주는 것. 얼마를 살지 결정하는 로직은
    `execution.reconcile`에 한 벌만 존재한다.
    """

    market: str
    fee_rate: float = DEFAULT_FEE_RATE
    min_order_krw: float = MIN_ORDER_KRW

    @abstractmethod
    def snapshot(self) -> AccountState:
        """현재 현금/포지션/가격."""

    @abstractmethod
    def market_buy(self, krw_amount: float, reason: str = "") -> Fill | None:
        """원화 `krw_amount`어치 시장가 매수. 체결이 없으면 None."""

    @abstractmethod
    def market_sell(self, volume: float, reason: str = "") -> Fill | None:
        """코인 `volume`만큼 시장가 매도. 체결이 없으면 None."""

    def now(self) -> datetime:
        """브로커 기준 현재 시각. 백테스트에서는 봉 시각이다."""
        raise NotImplementedError


def krw_tick_size(price: float) -> float:
    """원화 마켓 호가 단위(지정가 주문에만 필요).

    이 봇은 기본적으로 시장가 주문을 쓰므로 호가 단위를 맞출 일이 없다.
    지정가로 확장한다면 이 표 대신 `GET /v1/orders/chance`가 돌려주는
    실시간 제약을 쓰는 편이 안전하다 — 업비트가 호가 단위를 개정하면
    하드코딩된 표는 조용히 틀린 값이 된다.
    """
    table = [
        (2_000_000, 1_000),
        (1_000_000, 500),
        (500_000, 100),
        (100_000, 50),
        (10_000, 10),
        (1_000, 1),
        (100, 0.1),
        (10, 0.01),
        (1, 0.001),
        (0, 0.0001),
    ]
    for threshold, tick in table:
        if price >= threshold:
            return float(tick)
    return 0.0001
