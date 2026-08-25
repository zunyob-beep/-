"""페이퍼/실거래 실행 배선.

백테스트와 다른 부분은 딱 두 군데다: 봉이 실시간으로 들어온다는 것과,
브로커가 진짜 주문을 낸다는 것. 나머지(전략, 리스크, 체결 로직)는
`Engine`이 그대로 재사용한다.
"""

from __future__ import annotations

import logging

from .config import Settings
from .engine import Engine, EngineConfig
from .exchange.base import Broker
from .exchange.simulated import SimulatedBroker
from .exchange.upbit import UpbitBroker, UpbitClient
from .feed import LiveFeed
from .models import RunStats
from .notify import build as build_notifier
from .risk import RiskManager, RiskState
from .storage import Journal
from .strategies.base import get_strategy

log = logging.getLogger(__name__)


def build_client(settings: Settings, authenticated: bool) -> UpbitClient:
    if not authenticated:
        return UpbitClient()
    access, secret = Settings.require_api_keys()
    return UpbitClient(access_key=access, secret_key=secret)


def run(
    settings: Settings,
    *,
    live: bool = False,
    dry_run: bool = False,
    max_bars: int | None = None,
) -> RunStats:
    """`live=False`면 페이퍼(모의), `True`면 실주문."""
    strategy = get_strategy(settings.strategy, **settings.strategy_params)
    client = build_client(settings, authenticated=live)
    journal = Journal(settings.runs_dir, settings.run_name)

    risk_state = RiskState()
    saved = journal.load_state()
    if saved is not None:
        risk_state, account_snapshot = saved
        log.info("이전 상태를 복원했습니다 (%s)", journal.state_path)
    else:
        account_snapshot = {}

    broker = _build_broker(settings, client, live, dry_run, account_snapshot)

    lookback = max(strategy.warmup + 5, 30)
    feed = LiveFeed(
        client,
        market=settings.market,
        interval=settings.interval,
        lookback=lookback,
        max_bars=max_bars,
    )

    engine = Engine(
        feed=feed,
        broker=broker,
        strategy=strategy,
        risk=RiskManager(config=settings.risk, state=risk_state),
        config=EngineConfig(rebalance_band=settings.rebalance_band, verbose=settings.verbose),
        journal=journal,
        notifier=build_notifier(settings.webhook_url),
    )

    mode = "실거래" if live else "페이퍼"
    if dry_run:
        mode += "(DRY-RUN)"
    log.info(
        "%s 모드 시작 — %s %s / %s",
        mode,
        settings.market,
        settings.interval,
        strategy.describe(),
    )
    try:
        return engine.run()
    except KeyboardInterrupt:
        log.info("사용자 중단 — 포지션은 그대로 유지됩니다")
        return engine.stats


def _build_broker(
    settings: Settings,
    client: UpbitClient,
    live: bool,
    dry_run: bool,
    account_snapshot: dict,
) -> Broker:
    if live:
        return UpbitBroker(
            client,
            market=settings.market,
            fee_rate=settings.fee_rate,
            min_order_krw=settings.min_order_krw,
            dry_run=dry_run,
        )

    broker = SimulatedBroker(
        market=settings.market,
        cash=float(account_snapshot.get("cash", settings.cash)),
        fee_rate=settings.fee_rate,
        slippage=settings.slippage,
        min_order_krw=settings.min_order_krw,
    )
    # 페이퍼 모드도 재시작 시 이전 포지션을 이어받는다.
    broker.position.volume = float(account_snapshot.get("volume", 0.0))
    broker.position.avg_price = float(account_snapshot.get("avg_price", 0.0))
    if broker.position.is_open:
        log.info(
            "모의 포지션 복원: %.8f @ %s",
            broker.position.volume,
            f"{broker.position.avg_price:,.0f}",
        )
    return broker
