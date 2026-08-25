"""웹 UI에서 봇을 켜고 끄는 관리자.

봇은 별도 스레드에서 돈다. 웹 요청 스레드에서 직접 돌리면 브라우저가
멈춘 채로 매달려 있게 된다.

스레드는 강제로 죽일 수 없으므로, 중지는 '다음 봉에서 멈춰달라'는
협조적 신호로 처리한다. 매매 도중에 프로세스를 끊어 포지션 상태를
어중간하게 남기는 것보다 한 봉 기다리는 편이 훨씬 안전하다.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..config import Settings
from ..engine import Engine, EngineConfig
from ..exchange.simulated import SimulatedBroker
from ..exchange.upbit import UpbitBroker, UpbitClient
from ..feed import LiveFeed
from ..models import Side
from ..notify import build as build_notifier
from ..risk import RiskManager, RiskState
from ..storage import Journal
from ..strategies.base import get_strategy

log = logging.getLogger(__name__)


@dataclass
class LogEntry:
    ts: str
    level: str
    message: str


class LogBuffer(logging.Handler):
    """화면에 뿌릴 최근 로그를 메모리에 들고 있는다."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self.records: deque[LogEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - 포맷 실패가 매매를 막으면 안 된다
            message = str(record.msg)
        entry = LogEntry(
            ts=datetime.now().astimezone().strftime("%H:%M:%S"),
            level=record.levelname,
            message=message,
        )
        with self._lock:
            self.records.append(entry)

    def snapshot(self, limit: int = 200) -> list[dict[str, str]]:
        with self._lock:
            items = list(self.records)[-limit:]
        return [{"ts": e.ts, "level": e.level, "message": e.message} for e in items]

    def clear(self) -> None:
        with self._lock:
            self.records.clear()


@dataclass
class BotManager:
    """한 번에 하나의 봇만 돌린다.

    동시에 여러 봇을 돌리게 하면 같은 계좌를 두 전략이 서로 모른 채
    건드리게 된다. 그 상황은 사용자가 예측할 수 없다.
    """

    logs: LogBuffer = field(default_factory=LogBuffer)
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _engine: Engine | None = None
    _info: dict[str, Any] = field(default_factory=dict)
    _error: str | None = None

    def __post_init__(self) -> None:
        self.logs.setLevel(logging.INFO)
        root = logging.getLogger("btcbot")
        if self.logs not in root.handlers:
            root.addHandler(self.logs)
            root.setLevel(logging.INFO)

    # ------------------------------------------------------------------ 상태
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any]:
        engine = self._engine
        payload: dict[str, Any] = {
            "running": self.running,
            "stopping": self._stop.is_set() and self.running,
            "error": self._error,
            **self._info,
        }
        if engine is None:
            return payload

        stats = engine.stats
        payload["fills"] = len(stats.fills)
        payload["trades"] = len(stats.trades)
        payload["realized_pnl"] = stats.realized_pnl
        payload["errors"] = stats.errors

        if stats.equity_curve:
            last = stats.equity_curve[-1]
            payload["equity"] = last.equity
            payload["cash"] = last.cash
            payload["price"] = last.price
            payload["weight"] = last.weight
            payload["updated_at"] = last.ts.isoformat()
            first = stats.equity_curve[0].equity
            payload["return_pct"] = (last.equity / first - 1) if first > 0 else 0.0

        payload["risk"] = {
            "halted": engine.risk.state.halted,
            "halt_reason": engine.risk.state.halt_reason,
            "blocked_day": engine.risk.state.blocked_day,
            "cooldown_left": engine.risk.state.cooldown_left,
        }
        payload["recent_fills"] = [
            {
                "ts": f.ts.astimezone().strftime("%m-%d %H:%M"),
                "side": "매수" if f.side is Side.BUY else "매도",
                "price": f.price,
                "volume": f.volume,
                "reason": f.reason,
            }
            for f in stats.fills[-20:]
        ]
        payload["equity_curve"] = [
            {"ts": p.ts.isoformat(), "equity": p.equity, "price": p.price}
            for p in stats.equity_curve[-500:]
        ]
        return payload

    # ------------------------------------------------------------------ 제어
    def start(self, settings: Settings, live: bool, dry_run: bool = False) -> dict[str, Any]:
        with self._lock:
            if self.running:
                raise RuntimeError("이미 봇이 돌고 있습니다. 먼저 중지하세요.")

            if live:
                # 키가 없으면 여기서 걸러야 한다. 스레드 안에서 터지면
                # 사용자는 '시작됨'만 보고 왜 아무 일도 없는지 모른다.
                Settings.require_api_keys()

            self._stop.clear()
            self._error = None
            self.logs.clear()
            self._info = {
                "mode": "실거래" if live else "페이퍼",
                "dry_run": dry_run,
                "market": settings.market,
                "interval": settings.interval,
                "strategy": settings.strategy,
                "strategy_label": _strategy_label(settings),
                "run_name": settings.run_name,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            self._thread = threading.Thread(
                target=self._run, args=(settings, live, dry_run), daemon=True, name="btcbot-engine"
            )
            self._thread.start()
        return self.status()

    def stop(self, timeout: float = 5.0) -> dict[str, Any]:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            log.info("중지 요청 — 현재 봉 처리를 마치면 멈춥니다")
            thread.join(timeout=timeout)
        return self.status()

    # ------------------------------------------------------------------ 실행
    def _run(self, settings: Settings, live: bool, dry_run: bool) -> None:
        try:
            strategy = get_strategy(settings.strategy, **settings.strategy_params)
            client = _build_client(live)
            journal = Journal(settings.runs_dir, settings.run_name)

            risk_state = RiskState()
            account: dict[str, Any] = {}
            saved = journal.load_state()
            if saved is not None:
                risk_state, account = saved
                log.info("이전 상태를 복원했습니다")

            broker = self._make_broker(settings, client, live, dry_run, account)
            feed = LiveFeed(
                client,
                market=settings.market,
                interval=settings.interval,
                lookback=max(strategy.warmup + 5, 30),
            )

            # 봉이 모자라면 전략이 매 봉 "warmup"만 내놓고 영원히 거래하지
            # 않는다. 오류도 안 나므로 사용자는 봇이 도는 줄 안다. 시작 전에
            # 한 번 확인해서 화면에 이유를 띄운다.
            history = feed.fetch_history()
            if len(history) < strategy.warmup:
                raise RuntimeError(
                    f"이 전략은 판단을 시작하는 데 봉 {strategy.warmup}개가 필요한데 "
                    f"{len(history)}개만 받을 수 있습니다. 봉 간격을 더 짧게 잡거나, "
                    "전략의 기간 설정(이동평균 기간 등)을 줄이세요."
                )

            self._engine = Engine(
                feed=feed,
                broker=broker,
                strategy=strategy,
                risk=RiskManager(config=settings.risk, state=risk_state),
                config=EngineConfig(rebalance_band=settings.rebalance_band, verbose=True),
                journal=journal,
                notifier=build_notifier(settings.webhook_url),
            )

            log.info(
                "%s 시작 — %s %s / %s",
                self._info["mode"],
                settings.market,
                settings.interval,
                strategy.describe(),
            )
            # 루프는 Engine 안에 하나만 있다. 여기서 다시 구현하면 거래소
            # 오류 허용 같은 로직이 빠져 UI 봇만 조용히 약해진다.
            self._engine.run(should_stop=self._stop.is_set)
            log.info("봇을 멈췄습니다. 포지션은 그대로 유지됩니다.")

        except Exception as exc:  # 스레드에서 죽으면 화면에 이유가 보여야 한다
            self._error = str(exc)
            log.error("봇이 멈췄습니다: %s", exc)

    def _make_broker(
        self,
        settings: Settings,
        client: UpbitClient,
        live: bool,
        dry_run: bool,
        account: dict[str, Any],
    ):
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
            cash=float(account.get("cash", settings.cash)),
            fee_rate=settings.fee_rate,
            slippage=settings.slippage,
            min_order_krw=settings.min_order_krw,
        )
        broker.position.volume = float(account.get("volume", 0.0))
        broker.position.avg_price = float(account.get("avg_price", 0.0))
        return broker


def _build_client(live: bool) -> UpbitClient:
    if not live:
        return UpbitClient()
    access, secret = Settings.require_api_keys()
    return UpbitClient(access_key=access, secret_key=secret)


def _strategy_label(settings: Settings) -> str:
    spec = settings.strategy_params.get("spec")
    if isinstance(spec, dict) and spec.get("label"):
        return str(spec["label"])
    return settings.strategy
