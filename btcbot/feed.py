"""봉 공급자.

엔진은 `Bar`를 받아 판단하고 주문한다. 백테스트는 과거 봉을 순회하고,
라이브는 봉이 닫힐 때까지 기다렸다가 같은 모양의 `Bar`를 내놓는다.
엔진 입장에서는 둘이 구분되지 않는다.

핵심 규칙 하나: `history`에는 **닫힌 봉만** 들어간다. 그리고 체결은
그 다음 봉의 시가(`exec_price`)에서 일어난다. 백테스트에서 미래를
훔쳐보지 않게 만드는 장치이자, 라이브 봇의 실제 동작과 일치시키는 장치다.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .exchange.upbit import UpbitClient, interval_length
from .models import Candle

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Bar:
    #: 닫힌 봉들 (오래된 것부터). history[-1]이 방금 닫힌 봉
    history: Sequence[Candle]
    #: 이번 판단에 따른 주문이 체결될 가격
    exec_price: float
    #: 체결 시각
    exec_ts: datetime

    @property
    def last(self) -> Candle:
        return self.history[-1]


class Feed(ABC):
    market: str
    interval: str

    @abstractmethod
    def __iter__(self) -> Iterator[Bar]: ...


class BacktestFeed(Feed):
    """과거 봉을 순회한다. candles[i]까지 보고 candles[i+1].open에 체결."""

    def __init__(
        self, candles: Sequence[Candle], warmup: int = 1, interval: str = "day"
    ) -> None:
        if len(candles) < 2:
            raise ValueError("백테스트에는 최소 2개의 봉이 필요합니다")
        self.candles = list(candles)
        self.market = self.candles[0].market
        self.interval = interval
        self.warmup = max(1, warmup)

    def __len__(self) -> int:
        return max(0, len(self.candles) - max(self.warmup, 1))

    def __iter__(self) -> Iterator[Bar]:
        for i in range(self.warmup - 1, len(self.candles) - 1):
            nxt = self.candles[i + 1]
            yield Bar(history=self.candles[: i + 1], exec_price=nxt.open, exec_ts=nxt.ts)


class LiveFeed(Feed):
    """봉이 닫힐 때까지 기다렸다가 최신 히스토리를 내놓는다.

    닫힘 직후 곧바로 조회하면 마지막 봉이 아직 확정되지 않은 경우가 있어
    `settle_delay`만큼 여유를 둔다. 그래도 새 봉이 안 보이면 짧게 재시도한다.
    """

    def __init__(
        self,
        client: UpbitClient,
        market: str,
        interval: str = "minute60",
        lookback: int = 200,
        settle_delay: float = 3.0,
        max_bars: int | None = None,
    ) -> None:
        self.client = client
        self.market = market
        self.interval = interval
        # 업비트는 한 번에 200개까지만 준다. 그보다 많이 필요하면 여러 번
        # 나눠 받는다. 예전에는 여기서 200으로 잘라버렸는데, 그러면 warmup이
        # 200을 넘는 전략(예: SMA 200)이 영원히 "warmup"만 내놓고 한 번도
        # 거래하지 않는다 — 오류도 안 나서 알아채기가 어렵다.
        self.lookback = max(int(lookback), 2)
        self.settle_delay = settle_delay
        self.max_bars = max_bars
        self._length = interval_length(interval)

    def __iter__(self) -> Iterator[Bar]:
        emitted = 0
        last_ts: datetime | None = None

        while self.max_bars is None or emitted < self.max_bars:
            candles = self.fetch_history()
            if not candles:
                log.warning("봉 조회 결과가 비었습니다 — 잠시 후 재시도")
                time.sleep(5)
                continue

            newest = candles[-1]
            # 아직 진행 중인 봉은 판단에서 제외한다.
            closed = candles if self._is_closed(newest) else candles[:-1]
            if not closed:
                time.sleep(self._sleep_seconds())
                continue

            if last_ts is not None and closed[-1].ts <= last_ts:
                time.sleep(self._sleep_seconds())
                continue

            last_ts = closed[-1].ts
            price = self.client.get_price(self.market)
            emitted += 1
            yield Bar(history=closed, exec_price=price, exec_ts=datetime.now(timezone.utc))

            time.sleep(self._sleep_seconds())

    #: 한 번에 요청할 수 있는 최대 봉 수 (업비트 제한)
    PAGE = 200

    def fetch_history(self) -> list[Candle]:
        """`lookback`개가 모일 때까지 과거로 거슬러 올라가며 받는다."""
        if self.lookback <= self.PAGE:
            return self.client.get_candles(self.market, self.interval, self.lookback)

        collected: dict[datetime, Candle] = {}
        cursor: datetime | None = None
        while len(collected) < self.lookback:
            batch = self.client.get_candles(self.market, self.interval, self.PAGE, to=cursor)
            if not batch:
                break
            before = len(collected)
            collected.update({c.ts: c for c in batch})
            if len(collected) == before:
                break  # 같은 페이지가 반복되면 더 과거 데이터가 없는 것
            cursor = min(batch, key=lambda c: c.ts).ts - self._length

        candles = sorted(collected.values(), key=lambda c: c.ts)
        if len(candles) < self.lookback:
            log.warning(
                "%s %s: 봉 %d개를 요청했지만 %d개만 받았습니다",
                self.market, self.interval, self.lookback, len(candles),
            )
        return candles[-self.lookback :]

    def _is_closed(self, candle: Candle) -> bool:
        return datetime.now(timezone.utc) >= candle.ts + self._length

    def _next_close(self) -> datetime:
        now = datetime.now(timezone.utc)
        seconds = self._length.total_seconds()
        epoch = now.timestamp()
        return datetime.fromtimestamp(
            (int(epoch // seconds) + 1) * seconds, tz=timezone.utc
        )

    def _sleep_seconds(self) -> float:
        """다음 봉 마감까지 대기. 너무 길게 자지 않도록 상한을 둔다."""
        remaining = (self._next_close() - datetime.now(timezone.utc)).total_seconds()
        return max(1.0, min(remaining + self.settle_delay, 60.0))


class ReplayFeed(Feed):
    """과거 봉을 라이브처럼 흘려보내는 드라이런 피드(엔진 점검용)."""

    def __init__(
        self, candles: Sequence[Candle], warmup: int = 1, delay: float = 0.0
    ) -> None:
        self._inner = BacktestFeed(candles, warmup=warmup)
        self.market = self._inner.market
        self.interval = self._inner.interval
        self.delay = delay

    def __iter__(self) -> Iterator[Bar]:
        for bar in self._inner:
            if self.delay:
                time.sleep(self.delay)
            yield bar


def align_to_interval(ts: datetime, interval: str) -> datetime:
    """`ts`를 봉 경계로 내림."""
    length = interval_length(interval)
    if length >= timedelta(days=1):
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds = int(length.total_seconds())
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=timezone.utc)
