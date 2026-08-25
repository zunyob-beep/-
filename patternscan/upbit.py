"""업비트 공개 시세 API.

인증이 없다. 이 프로그램은 주문을 내지 않으므로 API 키도, JWT 서명도,
계좌 조회도 필요 없다. 시세만 읽는다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import requests

from .models import Candle, timeframe_length

log = logging.getLogger(__name__)

API_BASE = "https://api.upbit.com"

#: 봉 간격 -> 엔드포인트
ENDPOINTS = {
    "minute1": "/v1/candles/minutes/1",
    "minute3": "/v1/candles/minutes/3",
    "minute5": "/v1/candles/minutes/5",
}

#: 한 번에 받을 수 있는 최대 봉 수
PAGE = 200


class UpbitError(RuntimeError):
    """시세 조회 실패."""


class RateLimiter:
    """초당 N회 토큰 버킷.

    1분봉 한 달치를 받으려면 200개씩 200번 넘게 요청해야 한다. 한도를
    넘기면 429가 오고, 그러면 수집이 중간에 끊긴다.
    """

    def __init__(self, per_second: int = 8) -> None:
        self.per_second = max(1, per_second)
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._hits and now - self._hits[0] >= 1.0:
                    self._hits.popleft()
                if len(self._hits) < self.per_second:
                    self._hits.append(now)
                    return
                wait = 1.0 - (now - self._hits[0])
            time.sleep(max(wait, 0.01))


class UpbitClient:
    def __init__(
        self,
        base_url: str = API_BASE,
        timeout: float = 10.0,
        max_retries: int = 4,
        per_second: int = 8,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.limiter = RateLimiter(per_second)

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.acquire()
            try:
                response = self.session.get(
                    f"{self.base_url}{path}",
                    params=params,
                    headers={"Accept": "application/json"},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                self._backoff(attempt, f"네트워크 오류: {exc}")
                continue

            if response.status_code == 429:
                last_error = UpbitError("요청 한도 초과")
                self._backoff(attempt, "429 요청 한도 초과")
                continue
            if response.status_code >= 500:
                last_error = UpbitError(f"업비트 서버 오류 {response.status_code}")
                self._backoff(attempt, f"{response.status_code} 서버 오류")
                continue
            if response.status_code >= 400:
                raise UpbitError(f"요청 거부({response.status_code}): {response.text[:200]}")

            try:
                return response.json()
            except ValueError as exc:
                raise UpbitError(f"JSON 파싱 실패: {response.text[:200]}") from exc

        raise UpbitError(f"GET {path} 재시도 {self.max_retries}회 실패") from last_error

    def _backoff(self, attempt: int, why: str) -> None:
        if attempt >= self.max_retries:
            return
        delay = min(2.0**attempt, 16.0)
        log.warning("%s — %.1f초 후 재시도(%d/%d)", why, delay, attempt + 1, self.max_retries)
        time.sleep(delay)

    # ------------------------------------------------------------------ 시세
    def get_candles(
        self, market: str, timeframe: str, count: int = PAGE, to: datetime | None = None
    ) -> list[Candle]:
        """오래된 것부터 정렬해 돌려준다 (업비트는 최신순으로 준다)."""
        if timeframe not in ENDPOINTS:
            raise ValueError(f"모르는 봉 간격 '{timeframe}'. 사용 가능: {', '.join(ENDPOINTS)}")

        params: dict[str, Any] = {"market": market, "count": min(max(count, 1), PAGE)}
        if to is not None:
            params["to"] = to.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = self._get(ENDPOINTS[timeframe], params) or []
        candles = [_parse(row) for row in rows]
        candles.sort(key=lambda c: c.ts)
        return candles

    def get_markets(self) -> list[dict[str, Any]]:
        return self._get("/v1/market/all", {"isDetails": "false"}) or []

    def get_ticker(self, market: str) -> dict[str, Any]:
        rows = self._get("/v1/ticker", {"markets": market}) or []
        if not rows:
            raise UpbitError(f"{market} 시세가 없습니다")
        return rows[0]

    def collect(
        self,
        market: str,
        timeframe: str,
        count: int,
        end: datetime | None = None,
        progress: Any = None,
    ) -> list[Candle]:
        """`count`개가 모일 때까지 과거로 거슬러 올라가며 받는다.

        1분봉은 하루가 1,440개다. 한 달을 받으려면 200개씩 216번 요청해야
        하므로 시간이 걸린다 — 그래서 받은 건 CSV로 캐시한다(data.py).
        """
        collected: dict[int, Candle] = {}
        cursor = end
        step = timeframe_length(timeframe)

        while len(collected) < count:
            batch = self.get_candles(market, timeframe, PAGE, to=cursor)
            if not batch:
                break
            before = len(collected)
            collected.update({int(c.ts.timestamp()): c for c in batch})
            if len(collected) == before:
                break  # 같은 페이지가 반복되면 더 과거 데이터가 없는 것
            if progress is not None:
                progress(len(collected), count)
            cursor = min(batch, key=lambda c: c.ts).ts - step

        candles = sorted(collected.values(), key=lambda c: c.ts)
        return candles[-count:]


def _parse(row: Mapping[str, Any]) -> Candle:
    ts = datetime.strptime(row["candle_date_time_utc"], "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc
    )
    return Candle(
        ts=ts,
        open=float(row["opening_price"]),
        high=float(row["high_price"]),
        low=float(row["low_price"]),
        close=float(row["trade_price"]),
        volume=float(row["candle_acc_trade_volume"]),
    )
