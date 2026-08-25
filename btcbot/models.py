"""핵심 도메인 모델.

백테스트 / 페이퍼 / 실거래 세 모드가 모두 같은 자료구조를 쓴다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

KST = timezone(timedelta(hours=9))


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass(frozen=True)
class Candle:
    """OHLCV 봉 하나. `ts`는 봉이 *열린* 시각(UTC, tz-aware)."""

    market: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ValueError("Candle.ts must be timezone-aware")

    @property
    def kst_date(self) -> str:
        """봉이 속한 KST 날짜 (YYYY-MM-DD). 사람이 읽는 용도."""
        return self.ts.astimezone(KST).strftime("%Y-%m-%d")

    @property
    def kst_day(self) -> int:
        """KST 날짜를 정수 하나로. '같은 날인가'만 볼 때 쓴다.

        kst_date는 타임존 변환과 문자열 포맷팅을 하느라 비싸다. 봉마다
        부르는 코드에서는 그게 백테스트 전체 시간을 좌우한다(프로파일링에서
        strftime 하나가 68%를 차지했다). 이건 산술 연산 두 번이다.
        """
        return int((self.ts.timestamp() + 32400) // 86400)

    @property
    def range(self) -> float:
        return self.high - self.low

    def to_row(self) -> list[str]:
        return [
            self.market,
            self.ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            repr(self.open),
            repr(self.high),
            repr(self.low),
            repr(self.close),
            repr(self.volume),
        ]

    @classmethod
    def from_row(cls, row: list[str]) -> Candle:
        return cls(
            market=row[0],
            ts=datetime.strptime(row[1], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc),
            open=float(row[2]),
            high=float(row[3]),
            low=float(row[4]),
            close=float(row[5]),
            volume=float(row[6]),
        )


@dataclass(frozen=True)
class Signal:
    """전략이 내놓는 판단.

    `target_weight`는 "총자산 대비 코인 비중"의 목표치(0.0~1.0)다.
    매수/매도 수량이 아니라 비중으로 표현해야 백테스트와 실거래에서
    똑같은 체결 로직(execution.reconcile)을 공유할 수 있다.

    `None`은 "지금 비중을 그대로 두라"는 뜻이다. 0.0(=전량 청산)과 반드시
    구분해야 한다. 예를 들어 RSI가 진입선과 청산선 사이의 중립 구간에
    있으면 새로 사지도, 갖고 있는 걸 팔지도 않아야 한다.
    """

    action: Action = Action.HOLD
    target_weight: float | None = None
    reason: str = ""
    stop_price: float | None = None
    take_profit_price: float | None = None

    def __post_init__(self) -> None:
        if self.target_weight is not None and not 0.0 <= self.target_weight <= 1.0:
            raise ValueError(f"target_weight out of range: {self.target_weight}")

    def resolve_weight(self, current_weight: float) -> float:
        """`None`을 현재 비중으로 치환한 목표 비중."""
        return current_weight if self.target_weight is None else self.target_weight


@dataclass
class Fill:
    """체결 결과 한 건."""

    market: str
    side: Side
    price: float
    volume: float
    fee: float
    ts: datetime
    reason: str = ""
    order_id: str | None = None

    @property
    def gross(self) -> float:
        return self.price * self.volume

    @property
    def cash_delta(self) -> float:
        """이 체결로 원화 잔고가 변하는 양(수수료 포함)."""
        if self.side is Side.BUY:
            return -(self.gross + self.fee)
        return self.gross - self.fee


@dataclass
class Position:
    """단일 종목 포지션. 평단은 매수 체결 기준 가중평균."""

    market: str
    volume: float = 0.0
    avg_price: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.volume > 1e-12

    def apply(self, fill: Fill) -> float:
        """체결을 반영하고 실현손익(수수료 차감 후)을 돌려준다."""
        if fill.side is Side.BUY:
            total_cost = self.avg_price * self.volume + fill.gross + fill.fee
            self.volume += fill.volume
            self.avg_price = total_cost / self.volume if self.volume > 0 else 0.0
            return 0.0

        if fill.volume - self.volume > 1e-9:
            raise ValueError(f"매도 수량({fill.volume})이 보유 수량({self.volume})보다 큽니다")
        realized = (fill.price - self.avg_price) * fill.volume - fill.fee
        self.volume = max(0.0, self.volume - fill.volume)
        if not self.is_open:
            self.volume = 0.0
            self.avg_price = 0.0
        return realized

    def unrealized(self, price: float) -> float:
        if not self.is_open:
            return 0.0
        return (price - self.avg_price) * self.volume


@dataclass
class AccountState:
    """브로커가 보고하는 계좌 스냅샷."""

    cash: float
    position: Position
    price: float

    @property
    def coin_value(self) -> float:
        return self.position.volume * self.price

    @property
    def equity(self) -> float:
        return self.cash + self.coin_value

    @property
    def weight(self) -> float:
        eq = self.equity
        if eq <= 0:
            return 0.0
        return self.coin_value / eq


@dataclass
class EquityPoint:
    ts: datetime
    equity: float
    cash: float
    price: float
    weight: float


@dataclass
class TradeRecord:
    """진입~청산 한 사이클(라운드트립)."""

    market: str
    entry_ts: datetime
    entry_price: float
    exit_ts: datetime
    exit_price: float
    volume: float
    pnl: float
    pnl_pct: float
    reason: str = ""

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


@dataclass
class RunStats:
    """엔진이 누적하는 런타임 통계."""

    fills: list[Fill] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    errors: int = 0

    @property
    def realized_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)


def floor_to(value: float, step: float) -> float:
    """거래소 수량/가격 단위에 맞춰 내림."""
    if step <= 0:
        return value
    return math.floor(value / step + 1e-9) * step
