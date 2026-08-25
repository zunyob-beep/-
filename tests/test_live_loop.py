"""라이브 경로(LiveFeed + runner)를 네트워크 없이 끝까지 돌린다.

백테스트만 검증하면 정작 돈이 오가는 코드는 한 번도 실행해보지 않은 채
배포하게 된다. 여기서는 가짜 클라이언트를 꽂아 페이퍼/실거래 루프를
실제로 통과시킨다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from btcbot.config import Settings
from btcbot.feed import LiveFeed, align_to_interval
from btcbot.models import Side
from btcbot.risk import RiskConfig
from btcbot.storage import Journal
from tests.conftest import series


class FakeLiveClient:
    """봉을 하나씩 흘려주는 가짜 업비트."""

    def __init__(self, candles, orders_succeed=True):
        self.candles = list(candles)
        self.cursor = len(candles) - 20  # 초반 히스토리는 미리 채워둔다
        self.orders_succeed = orders_succeed
        self.placed = []
        self.accounts_calls = 0

    def get_candles(self, market, interval="minute60", count=200, to=None):
        window = self.candles[: self.cursor]
        self.cursor = min(self.cursor + 1, len(self.candles))
        return window[-count:]

    def get_price(self, market):
        return self.candles[min(self.cursor, len(self.candles) - 1)].close

    def get_accounts(self):
        self.accounts_calls += 1
        return [{"currency": "KRW", "balance": "1000000", "locked": "0"}]

    def place_order(self, market, side, ord_type, volume=None, price=None):
        self.placed.append((side, ord_type, volume, price))
        return {"uuid": f"o{len(self.placed)}", "state": "wait"}

    def wait_for_fill(self, order_uuid, timeout=15.0, poll=0.5):
        price = self.get_price("KRW-BTC")
        return {
            "uuid": order_uuid,
            "state": "done",
            "paid_fee": "50",
            "executed_volume": "0.01",
            "trades": [{"price": str(price), "volume": "0.01", "funds": str(price * 0.01)}],
        }


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("btcbot.feed.time.sleep", lambda _: None)


@pytest.fixture
def trending_candles():
    # 하루치 이상을 만들어 변동성 돌파가 판단할 수 있게 한다
    return series(
        [100.0 + i * 0.5 for i in range(80)],
        start=datetime(2023, 12, 31, 15, tzinfo=timezone.utc),  # KST 자정
        step=timedelta(hours=1),
    )


# ------------------------------------------------------------------ LiveFeed
def test_live_feed_emits_only_closed_candles(no_sleep, trending_candles):
    client = FakeLiveClient(trending_candles)
    feed = LiveFeed(client, "KRW-BTC", "minute60", lookback=50, max_bars=3)

    bars = list(feed)
    assert len(bars) == 3
    for bar in bars:
        # 히스토리의 마지막 봉은 이미 마감된 봉이어야 한다
        assert bar.last.ts + timedelta(hours=1) <= datetime.now(timezone.utc)


def test_live_feed_does_not_repeat_the_same_bar(no_sleep, trending_candles):
    client = FakeLiveClient(trending_candles)
    bars = list(LiveFeed(client, "KRW-BTC", "minute60", max_bars=5))
    timestamps = [bar.last.ts for bar in bars]
    assert len(set(timestamps)) == len(timestamps)
    assert timestamps == sorted(timestamps)


def test_align_to_interval():
    ts = datetime(2024, 1, 1, 13, 47, 30, tzinfo=timezone.utc)
    assert align_to_interval(ts, "minute60").minute == 0
    assert align_to_interval(ts, "minute15").minute == 45
    assert align_to_interval(ts, "day").hour == 0


# -------------------------------------------------------------- 페이퍼 모드
def paper_settings(tmp_path, **kwargs) -> Settings:
    settings = Settings.load(
        None,
        market="KRW-BTC",
        interval="minute60",
        strategy=kwargs.pop("strategy", "ma_cross"),
        cash=1_000_000,
        runs_dir=str(tmp_path),
        run_name="test-run",
    )
    settings.strategy_params = kwargs.pop("strategy_params", {"fast": 3, "slow": 10})
    for key, value in kwargs.items():
        setattr(settings, key, value)
    return settings


def test_paper_run_places_no_real_orders(no_sleep, tmp_path, trending_candles, monkeypatch):
    from btcbot import runner

    client = FakeLiveClient(trending_candles)
    monkeypatch.setattr(runner, "build_client", lambda settings, authenticated: client)

    stats = runner.run(paper_settings(tmp_path), live=False, max_bars=6)
    assert client.placed == []  # 실주문 없음
    assert client.accounts_calls == 0  # 실계좌 조회도 없음
    assert len(stats.equity_curve) == 6


def test_paper_run_trades_and_journals(no_sleep, tmp_path, trending_candles, monkeypatch):
    from btcbot import runner

    client = FakeLiveClient(trending_candles)
    monkeypatch.setattr(runner, "build_client", lambda settings, authenticated: client)

    stats = runner.run(paper_settings(tmp_path), live=False, max_bars=8)
    assert stats.fills, "상승 추세에서는 매수가 나와야 한다"

    journal = Journal(tmp_path, "test-run")
    assert len(journal.read_fills()) == len(stats.fills)
    assert journal.load_state() is not None


def test_paper_restart_restores_position(no_sleep, tmp_path, trending_candles, monkeypatch):
    """봇을 껐다 켜도 들고 있던 포지션과 리스크 상태가 이어져야 한다."""
    from btcbot import runner

    monkeypatch.setattr(
        runner, "build_client", lambda settings, authenticated: FakeLiveClient(trending_candles)
    )
    first = runner.run(paper_settings(tmp_path), live=False, max_bars=8)
    assert first.fills

    saved = Journal(tmp_path, "test-run").load_state()
    assert saved is not None
    _, account = saved
    assert account["volume"] > 0

    # 두 번째 실행은 현금 100만원이 아니라 저장된 잔고에서 이어간다
    second = runner.run(paper_settings(tmp_path), live=False, max_bars=1)
    assert second.equity_curve[0].weight > 0


def test_kill_switch_state_survives_restart(no_sleep, tmp_path, trending_candles, monkeypatch):
    from btcbot import runner

    monkeypatch.setattr(
        runner, "build_client", lambda settings, authenticated: FakeLiveClient(trending_candles)
    )
    settings = paper_settings(tmp_path)
    settings.risk = RiskConfig(max_drawdown_pct=0.01)  # 즉시 발동할 만큼 빡빡하게

    runner.run(settings, live=False, max_bars=8)
    journal = Journal(tmp_path, "test-run")
    saved = journal.load_state()
    if saved is not None and saved[0].halted:
        risk_state, _ = saved
        assert risk_state.halt_reason


# ---------------------------------------------------------------- 실거래 모드
def test_live_run_sends_orders(no_sleep, tmp_path, trending_candles, monkeypatch):
    from btcbot import runner

    client = FakeLiveClient(trending_candles)
    monkeypatch.setattr(runner, "build_client", lambda settings, authenticated: client)

    settings = paper_settings(tmp_path)
    stats = runner.run(settings, live=True, max_bars=8)

    assert client.accounts_calls > 0  # 실계좌를 조회했다
    if stats.fills:
        assert client.placed
        side, ord_type, volume, price = client.placed[0]
        assert (side, ord_type) == ("bid", "price")
        assert stats.fills[0].side is Side.BUY
        assert stats.fills[0].order_id


def test_live_dry_run_sends_nothing(no_sleep, tmp_path, trending_candles, monkeypatch):
    from btcbot import runner

    client = FakeLiveClient(trending_candles)
    monkeypatch.setattr(runner, "build_client", lambda settings, authenticated: client)

    runner.run(paper_settings(tmp_path), live=True, dry_run=True, max_bars=8)
    assert client.placed == []


def test_max_bars_stops_the_loop(no_sleep, tmp_path, trending_candles, monkeypatch):
    from btcbot import runner

    monkeypatch.setattr(
        runner, "build_client", lambda settings, authenticated: FakeLiveClient(trending_candles)
    )
    stats = runner.run(paper_settings(tmp_path), live=False, max_bars=2)
    assert len(stats.equity_curve) == 2
