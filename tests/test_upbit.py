"""업비트 클라이언트 검증 — 네트워크 없이.

인증 서명이 틀리면 실거래에서 401만 계속 받는다. 그 순간에 디버깅하지
않으려고 여기서 서명 규칙을 직접 재현해 대조한다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib.parse import unquote, urlencode

import pytest
import requests

from btcbot.exchange.base import AuthError, ExchangeError, OrderRejected, krw_tick_size
from btcbot.exchange.upbit import (
    RateLimiter,
    UpbitBroker,
    UpbitClient,
    _aggregate_trades,
    interval_length,
    make_jwt,
)

ACCESS = "test-access-key"
SECRET = "test-secret-key"


def decode_jwt(token: str) -> tuple[dict, dict, bool]:
    header_b64, payload_b64, signature_b64 = token.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)
    header = json.loads(base64.urlsafe_b64decode(pad(header_b64)))
    payload = json.loads(base64.urlsafe_b64decode(pad(payload_b64)))
    expected = hmac.new(
        SECRET.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256
    ).digest()
    valid = base64.urlsafe_b64decode(pad(signature_b64)) == expected
    return header, payload, valid


# --------------------------------------------------------------------- JWT
def test_jwt_header_and_signature():
    header, payload, valid = decode_jwt(make_jwt(ACCESS, SECRET))
    assert header == {"alg": "HS256", "typ": "JWT"}
    assert payload["access_key"] == ACCESS
    assert "nonce" in payload
    assert valid


def test_jwt_nonce_is_unique():
    _, first, _ = decode_jwt(make_jwt(ACCESS, SECRET))
    _, second, _ = decode_jwt(make_jwt(ACCESS, SECRET))
    assert first["nonce"] != second["nonce"]


def test_jwt_without_params_has_no_query_hash():
    _, payload, _ = decode_jwt(make_jwt(ACCESS, SECRET))
    assert "query_hash" not in payload


def test_jwt_query_hash_matches_upbit_rule():
    params = {"market": "KRW-BTC", "side": "bid", "ord_type": "price", "price": "10000"}
    _, payload, valid = decode_jwt(make_jwt(ACCESS, SECRET, params))
    assert valid
    assert payload["query_hash_alg"] == "SHA512"
    expected = hashlib.sha512(unquote(urlencode(params)).encode()).hexdigest()
    assert payload["query_hash"] == expected


def test_jwt_query_hash_does_not_percent_encode():
    """`states[]` 같은 키가 인코딩되면 해시가 어긋나 인증이 깨진다."""
    params = {"states[]": ["wait", "done"]}
    _, payload, _ = decode_jwt(make_jwt(ACCESS, SECRET, params))
    expected = hashlib.sha512(
        unquote(urlencode(params, doseq=True)).encode()
    ).hexdigest()
    assert payload["query_hash"] == expected
    assert "%5B" not in unquote(urlencode(params, doseq=True))


# ------------------------------------------------------------------ 가짜 세션
class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """미리 정한 응답을 순서대로 돌려주는 세션."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"예상보다 많은 요청: {method} {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, url, **kwargs):
        return self._next("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._next("POST", url, **kwargs)

    def delete(self, url, **kwargs):
        return self._next("DELETE", url, **kwargs)


CANDLE_ROW = {
    "market": "KRW-BTC",
    "candle_date_time_utc": "2024-01-02T00:00:00",
    "candle_date_time_kst": "2024-01-02T09:00:00",
    "opening_price": 100.0,
    "high_price": 120.0,
    "low_price": 90.0,
    "trade_price": 110.0,
    "candle_acc_trade_volume": 5.0,
}


def older(row, day):
    return {**row, "candle_date_time_utc": f"2024-01-0{day}T00:00:00"}


def test_candles_are_returned_oldest_first():
    session = FakeSession([FakeResponse(payload=[older(CANDLE_ROW, 3), older(CANDLE_ROW, 1)])])
    client = UpbitClient(session=session)
    candles = client.get_candles("KRW-BTC", "day", count=2)
    assert [c.ts.day for c in candles] == [1, 3]
    assert candles[0].open == 100.0
    assert candles[0].close == 110.0  # trade_price가 종가


def test_candle_count_is_clamped_to_200():
    session = FakeSession([FakeResponse(payload=[])])
    UpbitClient(session=session).get_candles("KRW-BTC", "day", count=9999)
    assert session.calls[0][2]["params"]["count"] == 200


def test_unknown_interval_rejected():
    with pytest.raises(ValueError):
        UpbitClient().get_candles("KRW-BTC", "minute7")
    with pytest.raises(ValueError):
        interval_length("minute7")


def test_to_parameter_is_utc_iso():
    session = FakeSession([FakeResponse(payload=[])])
    client = UpbitClient(session=session)
    client.get_candles("KRW-BTC", "day", to=datetime(2024, 5, 1, 12, tzinfo=timezone.utc))
    assert session.calls[0][2]["params"]["to"] == "2024-05-01T12:00:00Z"


def test_retries_on_server_error(monkeypatch):
    monkeypatch.setattr("btcbot.exchange.upbit.time.sleep", lambda _: None)
    session = FakeSession([FakeResponse(500, text="boom"), FakeResponse(payload=[CANDLE_ROW])])
    client = UpbitClient(session=session)
    assert len(client.get_candles("KRW-BTC", "day")) == 1
    assert len(session.calls) == 2


def test_retries_on_network_error(monkeypatch):
    monkeypatch.setattr("btcbot.exchange.upbit.time.sleep", lambda _: None)
    session = FakeSession(
        [requests.ConnectionError("network down"), FakeResponse(payload=[CANDLE_ROW])]
    )
    assert len(UpbitClient(session=session).get_candles("KRW-BTC", "day")) == 1


def test_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr("btcbot.exchange.upbit.time.sleep", lambda _: None)
    session = FakeSession([FakeResponse(500) for _ in range(3)])
    client = UpbitClient(session=session, max_retries=2)
    with pytest.raises(ExchangeError):
        client.get_candles("KRW-BTC", "day")


def test_auth_error_is_not_retried(monkeypatch):
    monkeypatch.setattr("btcbot.exchange.upbit.time.sleep", lambda _: None)
    session = FakeSession([FakeResponse(401, payload={"error": {"message": "invalid key"}})])
    client = UpbitClient(ACCESS, SECRET, session=session)
    with pytest.raises(AuthError, match="invalid key"):
        client.get_accounts()
    assert len(session.calls) == 1


def test_private_call_without_keys_fails_fast():
    with pytest.raises(AuthError):
        UpbitClient().get_accounts()


def test_order_rejection_surfaces_message():
    session = FakeSession(
        [FakeResponse(400, payload={"error": {"message": "최소주문금액 미달"}})]
    )
    client = UpbitClient(ACCESS, SECRET, session=session)
    with pytest.raises(OrderRejected, match="최소주문금액"):
        client.place_order("KRW-BTC", "bid", "price", price="1000")


def test_authorization_header_is_sent():
    session = FakeSession([FakeResponse(payload=[])])
    UpbitClient(ACCESS, SECRET, session=session).get_accounts()
    headers = session.calls[0][2]["headers"]
    assert headers["Authorization"].startswith("Bearer ")


# -------------------------------------------------------------------- 브로커
ORDER_ACK = {"uuid": "order-1", "state": "wait"}


def order_done(volume: float, price: float, fee: float = 0.0):
    return {
        "uuid": "order-1",
        "state": "done",
        "paid_fee": str(fee),
        "executed_volume": str(volume),
        "trades": [{"price": str(price), "volume": str(volume), "funds": str(price * volume)}],
    }


def test_broker_market_buy_uses_price_ord_type():
    session = FakeSession([FakeResponse(payload=ORDER_ACK), FakeResponse(payload=order_done(0.01, 100_000_000, fee=500))])
    client = UpbitClient(ACCESS, SECRET, session=session)
    broker = UpbitBroker(client, "KRW-BTC")

    fill = broker.market_buy(1_000_000, reason="테스트")
    body = session.calls[0][2]["json"]
    assert body["side"] == "bid"
    assert body["ord_type"] == "price"  # 시장가 매수는 '금액' 지정
    assert body["price"] == "1000000"
    assert "volume" not in body
    assert fill.price == pytest.approx(100_000_000)
    assert fill.fee == 500
    assert fill.order_id == "order-1"


def test_broker_market_sell_uses_market_ord_type():
    session = FakeSession([FakeResponse(payload=ORDER_ACK), FakeResponse(payload=order_done(0.5, 100.0))])
    client = UpbitClient(ACCESS, SECRET, session=session)
    broker = UpbitBroker(client, "KRW-BTC")

    broker.market_sell(0.5)
    body = session.calls[0][2]["json"]
    assert body["side"] == "ask"
    assert body["ord_type"] == "market"  # 시장가 매도는 '수량' 지정
    assert body["volume"] == "0.50000000"
    assert "price" not in body


def test_broker_floors_volume_to_eight_decimals():
    session = FakeSession([FakeResponse(payload=ORDER_ACK), FakeResponse(payload=order_done(0.1, 100.0))])
    client = UpbitClient(ACCESS, SECRET, session=session)
    broker = UpbitBroker(client, "KRW-BTC")

    broker.market_sell(0.123456789)  # 9자리 -> 8자리로 내림
    assert session.calls[0][2]["json"]["volume"] == "0.12345678"


def test_broker_skips_orders_below_minimum():
    session = FakeSession([])
    client = UpbitClient(ACCESS, SECRET, session=session)
    broker = UpbitBroker(client, "KRW-BTC")
    assert broker.market_buy(4_999) is None
    assert session.calls == []  # 아예 요청하지 않는다


def test_broker_dry_run_never_sends_orders():
    session = FakeSession([])
    client = UpbitClient(ACCESS, SECRET, session=session)
    broker = UpbitBroker(client, "KRW-BTC", dry_run=True)
    assert broker.market_buy(1_000_000) is None
    assert broker.market_sell(1.0) is None
    assert session.calls == []


def test_broker_snapshot_reads_balances():
    accounts = [
        {"currency": "KRW", "balance": "500000", "locked": "0"},
        {"currency": "BTC", "balance": "0.01", "locked": "0.002", "avg_buy_price": "90000000"},
    ]
    session = FakeSession([FakeResponse(payload=accounts)])
    client = UpbitClient(ACCESS, SECRET, session=session)
    broker = UpbitBroker(client, "KRW-BTC")
    broker.mark(datetime.now(timezone.utc), 100_000_000)

    state = broker.snapshot()
    assert state.cash == 500_000
    assert state.position.volume == pytest.approx(0.012)  # 묶인 수량도 보유로 계산
    assert state.position.avg_price == 90_000_000
    assert state.equity == pytest.approx(500_000 + 0.012 * 100_000_000)


def test_broker_rejects_non_krw_market():
    with pytest.raises(ValueError):
        UpbitBroker(UpbitClient(), "BTC-ETH")


def test_aggregate_trades_weighted_average():
    volume, gross = _aggregate_trades(
        [
            {"price": "100", "volume": "1", "funds": "100"},
            {"price": "200", "volume": "3", "funds": "600"},
        ]
    )
    assert volume == 4.0
    assert gross / volume == pytest.approx(175.0)


def test_aggregate_trades_without_funds_field():
    volume, gross = _aggregate_trades([{"price": "100", "volume": "2"}])
    assert (volume, gross) == (2.0, 200.0)


# ---------------------------------------------------------------- 부가 기능
def test_rate_limiter_blocks_when_exhausted(monkeypatch):
    slept = []
    monkeypatch.setattr("btcbot.exchange.upbit.time.sleep", lambda s: slept.append(s))

    limiter = RateLimiter(per_second=2)
    times = iter([0.0, 0.1, 0.2, 1.5, 1.6])
    monkeypatch.setattr("btcbot.exchange.upbit.time.monotonic", lambda: next(times))
    for _ in range(3):
        limiter.acquire()
    assert slept  # 세 번째 호출에서 대기했다


def test_krw_tick_size_steps():
    assert krw_tick_size(150_000_000) == 1_000
    assert krw_tick_size(1_500_000) == 500
    assert krw_tick_size(50_000) == 10
    assert krw_tick_size(5_000) == 1
    assert krw_tick_size(0.05) == 0.0001


def test_tick_size_is_monotonic():
    """가격이 오를수록 호가 단위가 작아지는 일은 없어야 한다."""
    prices = [0.05, 5, 50, 500, 5_000, 50_000, 500_000, 5_000_000]
    ticks = [krw_tick_size(p) for p in prices]
    assert ticks == sorted(ticks)
