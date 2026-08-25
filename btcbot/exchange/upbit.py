"""업비트 REST 클라이언트와 실거래 브로커.

인증은 JWT(HS256)다. PyJWT 대신 표준 라이브러리 `hmac`으로 직접 서명한다 —
서명 알고리즘이 HS256 하나뿐이라 20줄이면 되고, 의존성이 하나 줄어든다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlencode

import requests

from ..models import AccountState, Candle, Fill, Position, Side, floor_to
from .base import (
    DEFAULT_FEE_RATE,
    MIN_ORDER_KRW,
    AuthError,
    Broker,
    ExchangeError,
    InsufficientFunds,
    OrderRejected,
    RateLimited,
)

log = logging.getLogger(__name__)

API_BASE = "https://api.upbit.com"

#: 지원하는 봉 간격 -> (엔드포인트 경로, 봉 길이)
INTERVALS: dict[str, tuple[str, timedelta]] = {
    "minute1": ("/v1/candles/minutes/1", timedelta(minutes=1)),
    "minute3": ("/v1/candles/minutes/3", timedelta(minutes=3)),
    "minute5": ("/v1/candles/minutes/5", timedelta(minutes=5)),
    "minute10": ("/v1/candles/minutes/10", timedelta(minutes=10)),
    "minute15": ("/v1/candles/minutes/15", timedelta(minutes=15)),
    "minute30": ("/v1/candles/minutes/30", timedelta(minutes=30)),
    "minute60": ("/v1/candles/minutes/60", timedelta(hours=1)),
    "minute240": ("/v1/candles/minutes/240", timedelta(hours=4)),
    "day": ("/v1/candles/days", timedelta(days=1)),
    "week": ("/v1/candles/weeks", timedelta(weeks=1)),
    "month": ("/v1/candles/months", timedelta(days=30)),
}


def interval_length(interval: str) -> timedelta:
    try:
        return INTERVALS[interval][1]
    except KeyError:
        raise ValueError(
            f"모르는 봉 간격 '{interval}'. 사용 가능: {', '.join(INTERVALS)}"
        ) from None


def make_jwt(access_key: str, secret_key: str, params: Mapping[str, Any] | None = None) -> str:
    """업비트 인증용 JWT(HS256)를 만든다.

    파라미터가 있으면 query string의 SHA512 해시를 payload에 넣어야 한다.
    urlencode 결과를 unquote하는 것은 업비트 공식 예제와 동일한 처리로,
    `states[]` 같은 배열 파라미터가 퍼센트 인코딩되어 해시가 어긋나는 것을
    막기 위함이다. 요청도 반드시 같은 문자열로 보내야 한다.
    """
    payload: dict[str, Any] = {"access_key": access_key, "nonce": str(uuid.uuid4())}
    if params:
        query = unquote(urlencode(params, doseq=True))
        payload["query_hash"] = hashlib.sha512(query.encode()).hexdigest()
        payload["query_hash_alg"] = "SHA512"

    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = _b64(header) + b"." + _b64(payload)
    signature = hmac.new(secret_key.encode(), signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()


def _b64(data: Mapping[str, Any]) -> bytes:
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


class RateLimiter:
    """초당 N회 토큰 버킷. 업비트는 그룹별로 한도가 다르다."""

    def __init__(self, per_second: int) -> None:
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
    """업비트 REST API 얇은 래퍼."""

    def __init__(
        self,
        access_key: str | None = None,
        secret_key: str | None = None,
        base_url: str = API_BASE,
        timeout: float = 10.0,
        max_retries: int = 4,
        session: requests.Session | None = None,
    ) -> None:
        self.access_key = access_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self._quote_limiter = RateLimiter(8)
        self._order_limiter = RateLimiter(6)

    @property
    def authenticated(self) -> bool:
        return bool(self.access_key and self.secret_key)

    # ------------------------------------------------------------- 저수준 호출
    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        auth: bool = False,
    ) -> Any:
        limiter = self._order_limiter if auth else self._quote_limiter
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            limiter.acquire()
            headers = {"Accept": "application/json"}
            if auth:
                if not self.authenticated:
                    raise AuthError(
                        "API 키가 없습니다. UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY를 설정하세요."
                    )
                token = make_jwt(self.access_key, self.secret_key, params)
                headers["Authorization"] = f"Bearer {token}"

            try:
                if method == "GET":
                    response = self.session.get(
                        url, params=params, headers=headers, timeout=self.timeout
                    )
                elif method == "DELETE":
                    response = self.session.delete(
                        url, params=params, headers=headers, timeout=self.timeout
                    )
                else:
                    response = self.session.post(
                        url, json=params, headers=headers, timeout=self.timeout
                    )
            except requests.RequestException as exc:
                last_error = exc
                self._sleep_backoff(attempt, f"네트워크 오류: {exc}")
                continue

            if response.status_code == 429:
                last_error = RateLimited("요청 한도 초과")
                self._sleep_backoff(attempt, "429 요청 한도 초과")
                continue
            if response.status_code >= 500:
                last_error = ExchangeError(f"업비트 서버 오류 {response.status_code}")
                self._sleep_backoff(attempt, f"{response.status_code} 서버 오류")
                continue
            if response.status_code in (401, 403):
                raise AuthError(f"인증 실패({response.status_code}): {_error_message(response)}")
            if response.status_code >= 400:
                raise _client_error(response)

            if not response.content:
                return None
            try:
                return response.json()
            except ValueError as exc:
                raise ExchangeError(f"JSON 파싱 실패: {response.text[:200]}") from exc

        raise ExchangeError(f"{method} {path} 재시도 {self.max_retries}회 실패") from last_error

    def _sleep_backoff(self, attempt: int, why: str) -> None:
        if attempt >= self.max_retries:
            return
        delay = min(2.0**attempt, 16.0)
        log.warning("%s — %.1f초 후 재시도(%d/%d)", why, delay, attempt + 1, self.max_retries)
        time.sleep(delay)

    # ------------------------------------------------------------------ 시세
    def get_candles(
        self,
        market: str,
        interval: str = "day",
        count: int = 200,
        to: datetime | None = None,
    ) -> list[Candle]:
        """최신순으로 오는 응답을 **오래된 것부터**로 뒤집어 돌려준다."""
        if interval not in INTERVALS:
            raise ValueError(f"모르는 봉 간격 '{interval}'. 사용 가능: {', '.join(INTERVALS)}")
        path = INTERVALS[interval][0]
        params: dict[str, Any] = {"market": market, "count": min(max(count, 1), 200)}
        if to is not None:
            params["to"] = to.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = self._request("GET", path, params) or []
        candles = [_parse_candle(market, row) for row in rows]
        candles.sort(key=lambda c: c.ts)
        return candles

    def get_price(self, market: str) -> float:
        rows = self._request("GET", "/v1/ticker", {"markets": market}) or []
        if not rows:
            raise ExchangeError(f"{market} 시세를 가져오지 못했습니다")
        return float(rows[0]["trade_price"])

    def get_markets(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/market/all", {"isDetails": "false"}) or []

    # ------------------------------------------------------------------ 계좌
    def get_accounts(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/accounts", auth=True) or []

    def get_orders_chance(self, market: str) -> dict[str, Any]:
        return self._request("GET", "/v1/orders/chance", {"market": market}, auth=True) or {}

    def place_order(
        self,
        market: str,
        side: str,
        ord_type: str,
        volume: str | None = None,
        price: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"market": market, "side": side, "ord_type": ord_type}
        if volume is not None:
            params["volume"] = volume
        if price is not None:
            params["price"] = price
        return self._request("POST", "/v1/orders", params, auth=True) or {}

    def get_order(self, order_uuid: str) -> dict[str, Any]:
        return self._request("GET", "/v1/order", {"uuid": order_uuid}, auth=True) or {}

    def cancel_order(self, order_uuid: str) -> dict[str, Any]:
        return self._request("DELETE", "/v1/order", {"uuid": order_uuid}, auth=True) or {}

    def wait_for_fill(
        self, order_uuid: str, timeout: float = 15.0, poll: float = 0.5
    ) -> dict[str, Any]:
        """주문이 체결/취소로 끝날 때까지 폴링한다.

        시장가 주문은 보통 즉시 끝나지만, 응답 직후에는 체결 내역(trades)이
        비어 있을 수 있어 한 번은 반드시 다시 조회한다.
        """
        deadline = time.monotonic() + timeout
        order = self.get_order(order_uuid)
        while time.monotonic() < deadline:
            state = order.get("state")
            if state in ("done", "cancel") and order.get("trades"):
                return order
            time.sleep(poll)
            order = self.get_order(order_uuid)
        log.warning("주문 %s 체결 확인 시간 초과 — 마지막 상태: %s", order_uuid, order.get("state"))
        return order


def _parse_candle(market: str, row: Mapping[str, Any]) -> Candle:
    ts = datetime.strptime(row["candle_date_time_utc"], "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc
    )
    return Candle(
        market=market,
        ts=ts,
        open=float(row["opening_price"]),
        high=float(row["high_price"]),
        low=float(row["low_price"]),
        close=float(row["trade_price"]),
        volume=float(row["candle_acc_trade_volume"]),
    )


def _error_message(response: requests.Response) -> str:
    try:
        body = response.json()
        return str(body.get("error", {}).get("message", body))
    except ValueError:
        return response.text[:200]


def _client_error(response: requests.Response) -> ExchangeError:
    message = _error_message(response)
    lowered = message.lower()
    if "insufficient" in lowered or "잔고" in message:
        return InsufficientFunds(message)
    return OrderRejected(f"주문 거부({response.status_code}): {message}")


class UpbitBroker(Broker):
    """실제 돈이 오가는 브로커. 시장가 주문만 사용한다."""

    def __init__(
        self,
        client: UpbitClient,
        market: str,
        fee_rate: float = DEFAULT_FEE_RATE,
        min_order_krw: float = MIN_ORDER_KRW,
        dry_run: bool = False,
    ) -> None:
        if not market.startswith("KRW-"):
            raise ValueError("원화 마켓(KRW-...)만 지원합니다")
        self.client = client
        self.market = market
        self.fee_rate = fee_rate
        self.min_order_krw = min_order_krw
        self.dry_run = dry_run
        self.currency = market.split("-", 1)[1]
        self._price: float | None = None

    def mark(self, ts: datetime, price: float) -> None:
        """엔진과의 인터페이스를 모의 브로커와 맞추기 위한 훅.

        실거래에서 신뢰할 값은 거래소가 돌려주는 체결가이므로, 여기서 받은
        가격은 평가금액 계산에만 쓰고 주문가로는 쓰지 않는다.
        """
        self._price = float(price)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def snapshot(self) -> AccountState:
        cash = 0.0
        position = Position(self.market)
        for account in self.client.get_accounts():
            balance = float(account.get("balance", 0)) + float(account.get("locked", 0))
            if account.get("currency") == "KRW":
                cash = balance
            elif account.get("currency") == self.currency:
                position.volume = balance
                position.avg_price = float(account.get("avg_buy_price", 0) or 0)

        price = self._price if self._price else self.client.get_price(self.market)
        return AccountState(cash=cash, position=position, price=float(price))

    def market_buy(self, krw_amount: float, reason: str = "") -> Fill | None:
        amount = float(krw_amount)
        if amount < self.min_order_krw:
            log.info("매수 건너뜀: %.0f원은 최소 주문금액 미만", amount)
            return None
        if self.dry_run:
            log.info("[DRY-RUN] 시장가 매수 %.0f원 (%s)", amount, reason)
            return None

        order = self.client.place_order(
            self.market, side="bid", ord_type="price", price=f"{amount:.0f}"
        )
        return self._settle(order, Side.BUY, reason)

    def market_sell(self, volume: float, reason: str = "") -> Fill | None:
        # 업비트는 8자리까지만 받는다. 올림하면 잔고 초과로 거부되므로 내림.
        vol = floor_to(float(volume), 1e-8)
        if vol <= 0:
            return None
        if self.dry_run:
            log.info("[DRY-RUN] 시장가 매도 %.8f (%s)", vol, reason)
            return None

        order = self.client.place_order(
            self.market, side="ask", ord_type="market", volume=f"{vol:.8f}"
        )
        return self._settle(order, Side.SELL, reason)

    def _settle(self, order: Mapping[str, Any], side: Side, reason: str) -> Fill | None:
        order_uuid = order.get("uuid")
        if not order_uuid:
            raise OrderRejected(f"주문 응답에 uuid가 없습니다: {order}")

        final = self.client.wait_for_fill(order_uuid)
        volume, gross = _aggregate_trades(final.get("trades") or [])
        if volume <= 0:
            executed = float(final.get("executed_volume") or 0)
            if executed <= 0:
                log.warning("주문 %s 미체결 (state=%s)", order_uuid, final.get("state"))
                return None
            # 체결 내역은 아직 안 왔지만 체결 수량은 확정된 경우
            volume = executed
            gross = volume * (self._price or 0.0)

        fee = float(final.get("paid_fee") or 0.0)
        return Fill(
            market=self.market,
            side=side,
            price=gross / volume if volume else 0.0,
            volume=volume,
            fee=fee,
            ts=self.now(),
            reason=reason,
            order_id=str(order_uuid),
        )


def _aggregate_trades(trades: Iterable[Mapping[str, Any]]) -> tuple[float, float]:
    """체결 내역들을 (총수량, 총금액)으로 합산한다."""
    volume = gross = 0.0
    for trade in trades:
        vol = float(trade.get("volume") or 0)
        funds = trade.get("funds")
        volume += vol
        gross += float(funds) if funds is not None else vol * float(trade.get("price") or 0)
    return volume, gross
