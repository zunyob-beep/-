"""핵심 자료구조.

주문도 계좌도 없다. 이 프로그램은 시세를 읽고 통계를 내기만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

KST = timezone(timedelta(hours=9))

#: 다루는 봉 간격. 단타만 보므로 여기서 더 늘리지 않는다.
TIMEFRAMES: dict[str, timedelta] = {
    "minute1": timedelta(minutes=1),
    "minute3": timedelta(minutes=3),
    "minute5": timedelta(minutes=5),
}

TIMEFRAME_LABELS = {"minute1": "1분봉", "minute3": "3분봉", "minute5": "5분봉"}

#: 다루는 종목. 아무거나 칠 수 있게 두면 오타 하나로 "없는 종목입니다"를
#: 만나게 되고, 그게 오타 때문인지 업비트가 막힌 건지 알 수가 없다.
#: 거래량이 많은 넷으로 좁힌다 — 얇은 종목은 빈 봉이 많아 모양이 왜곡된다.
MARKETS: dict[str, str] = {
    "KRW-BTC": "비트코인",
    "KRW-ETH": "이더리움",
    "KRW-XRP": "엑스알피",
    "KRW-SOL": "솔라나",
}


def market_label(market: str) -> str:
    return MARKETS.get(market, market)

#: 살펴볼 '모양'의 길이(직전 몇 개 봉인지).
#: 5, 10 그리고 20부터 180까지 10칸씩.
WINDOW_LENGTHS: tuple[int, ...] = (5, 10, *range(20, 181, 10))

#: 진입 후 몇 봉 뒤를 볼지. 봉 간격 기준이므로 1분봉이면 1/3/5/10/20분 뒤다.
HORIZONS: tuple[int, ...] = (1, 3, 5, 10, 20)


def timeframe_label(timeframe: str) -> str:
    return TIMEFRAME_LABELS.get(timeframe, timeframe)


def timeframe_length(timeframe: str) -> timedelta:
    try:
        return TIMEFRAMES[timeframe]
    except KeyError:
        raise ValueError(
            f"모르는 봉 간격 '{timeframe}'. 사용 가능: {', '.join(TIMEFRAMES)}"
        ) from None


@dataclass(frozen=True)
class Candle:
    """OHLCV 봉 하나. `ts`는 봉이 *열린* 시각(UTC, tz-aware)."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ValueError("Candle.ts에는 타임존이 있어야 합니다")

    @property
    def kst(self) -> datetime:
        return self.ts.astimezone(KST)


@dataclass
class Series:
    """한 종목·한 봉 간격의 연속된 봉들.

    분석은 전부 numpy 배열 위에서 돈다. 1분봉 한 달이 4만 개가 넘고,
    그걸 봉마다 슬라이딩하며 비교하기 때문에 파이썬 반복문으로는
    감당이 안 된다.
    """

    market: str
    timeframe: str
    ts: np.ndarray  # int64, 유닉스 초
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    def __len__(self) -> int:
        return int(self.close.size)

    @classmethod
    def from_candles(cls, market: str, timeframe: str, candles: list[Candle]) -> Series:
        candles = sorted(candles, key=lambda c: c.ts)
        return cls(
            market=market,
            timeframe=timeframe,
            ts=np.array([int(c.ts.timestamp()) for c in candles], dtype=np.int64),
            open=np.array([c.open for c in candles], dtype=np.float64),
            high=np.array([c.high for c in candles], dtype=np.float64),
            low=np.array([c.low for c in candles], dtype=np.float64),
            close=np.array([c.close for c in candles], dtype=np.float64),
            volume=np.array([c.volume for c in candles], dtype=np.float64),
        )

    @classmethod
    def empty(cls, market: str, timeframe: str) -> Series:
        """봉이 하나도 없는 시계열. '못 받았다'를 예외 대신 값으로 표현할 때 쓴다."""
        return cls.from_candles(market, timeframe, [])

    def time_at(self, index: int) -> datetime:
        return datetime.fromtimestamp(int(self.ts[index]), tz=timezone.utc)

    def kst_at(self, index: int) -> datetime:
        return self.time_at(index).astimezone(KST)

    @property
    def span(self) -> tuple[datetime, datetime] | None:
        if len(self) == 0:
            return None
        return self.time_at(0), self.time_at(len(self) - 1)

    def gaps(self) -> int:
        """봉이 비어 있는 구간 수.

        업비트는 거래가 없는 분의 봉을 아예 주지 않는다. 그 자리를 이어붙여
        비교하면 실제로는 떨어져 있는 두 시점을 연속된 모양으로 착각한다.
        """
        if len(self) < 2:
            return 0
        step = int(timeframe_length(self.timeframe).total_seconds())
        return int(np.count_nonzero(np.diff(self.ts) != step))

    def slice(self, start: int, stop: int) -> Series:
        return Series(
            market=self.market,
            timeframe=self.timeframe,
            ts=self.ts[start:stop],
            open=self.open[start:stop],
            high=self.high[start:stop],
            low=self.low[start:stop],
            close=self.close[start:stop],
            volume=self.volume[start:stop],
        )
