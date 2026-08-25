"""매매 엔진.

봉 하나마다 항상 같은 순서를 밟는다:

    시세 반영 -> 계좌 확인 -> 전략 판단 -> 리스크 심사 -> 주문 -> 기록

백테스트/페이퍼/실거래의 차이는 생성자에 어떤 Feed와 Broker를 꽂느냐뿐이다.
같은 코드가 돌아야 백테스트 결과를 믿을 수 있다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from .exchange.base import Broker, ExchangeError
from .execution import reconcile
from .feed import Bar, Feed
from .models import AccountState, EquityPoint, Fill, RunStats, Side, TradeRecord
from .notify import Notifier, NullNotifier, format_fill, format_trade
from .risk import RiskManager
from .storage import Journal
from .strategies.base import Strategy

log = logging.getLogger(__name__)


@dataclass
class EngineConfig:
    #: 재조정 허용 오차(총자산 대비). 잦은 잔주문으로 수수료가 새는 걸 막는다
    rebalance_band: float = 0.05
    #: 매 봉 상태를 로그로 남길지
    verbose: bool = False
    #: 라이브에서 예외가 이만큼 연속되면 중단
    max_consecutive_errors: int = 5


class TradeTracker:
    """진입~청산 한 사이클을 라운드트립으로 묶는다."""

    def __init__(self) -> None:
        self.entry_ts: datetime | None = None
        self.entry_price = 0.0
        self.entry_volume = 0.0
        self.entry_cost = 0.0

    def on_fill(self, fill: Fill, avg_price_before: float, volume_after: float) -> TradeRecord | None:
        if fill.side is Side.BUY:
            if self.entry_ts is None:
                self.entry_ts = fill.ts
            self.entry_volume += fill.volume
            self.entry_cost += fill.gross + fill.fee
            self.entry_price = self.entry_cost / self.entry_volume if self.entry_volume else 0.0
            return None

        if self.entry_ts is None:
            return None

        proceeds = fill.gross - fill.fee
        cost = avg_price_before * fill.volume
        pnl = proceeds - cost
        record = TradeRecord(
            market=fill.market,
            entry_ts=self.entry_ts,
            entry_price=avg_price_before,
            exit_ts=fill.ts,
            exit_price=fill.price,
            volume=fill.volume,
            pnl=pnl,
            pnl_pct=(pnl / cost) if cost > 0 else 0.0,
            reason=fill.reason,
        )
        if volume_after <= 1e-12:
            self.reset()
        else:
            self.entry_volume = volume_after
            self.entry_cost = avg_price_before * volume_after
        return record

    def reset(self) -> None:
        self.entry_ts = None
        self.entry_price = 0.0
        self.entry_volume = 0.0
        self.entry_cost = 0.0


@dataclass
class Engine:
    feed: Feed
    broker: Broker
    strategy: Strategy
    risk: RiskManager = field(default_factory=RiskManager)
    config: EngineConfig = field(default_factory=EngineConfig)
    journal: Journal | None = None
    notifier: Notifier = field(default_factory=NullNotifier)
    stats: RunStats = field(default_factory=RunStats)

    def __post_init__(self) -> None:
        self._tracker = TradeTracker()
        self._consecutive_errors = 0

    def run(self, should_stop: Callable[[], bool] | None = None) -> RunStats:
        """봉을 끝까지 돌린다.

        `should_stop`은 봉 사이마다 확인하는 중단 신호다. 웹 UI의 '중지'
        버튼이 이걸 쓴다. 예전에는 UI가 이 루프를 따로 구현했는데, 그러다
        거래소 오류 허용 로직이 빠져서 일시적인 429 하나에 봇이 죽었다.
        루프는 여기 하나만 둔다.
        """
        log.info(
            "엔진 시작 — %s %s / %s",
            self.feed.market,
            self.feed.interval,
            self.strategy.describe(),
        )
        for bar in self.feed:
            if should_stop is not None and should_stop():
                log.info("중단 요청 — 다음 봉을 기다리지 않고 멈춥니다")
                break
            try:
                self.step(bar)
                self._consecutive_errors = 0
            except ExchangeError as exc:
                self.stats.errors += 1
                self._consecutive_errors += 1
                log.error("거래소 오류 (%d회 연속): %s", self._consecutive_errors, exc)
                if self._consecutive_errors >= self.config.max_consecutive_errors:
                    log.critical("연속 오류 한도 초과 — 엔진을 멈춥니다")
                    raise
            if self.risk.state.halted:
                log.critical("리스크 정지: %s", self.risk.state.halt_reason)
                break
            if should_stop is not None and should_stop():
                log.info("중단 요청 — 이번 봉까지 처리하고 멈춥니다")
                break
        return self.stats

    def step(self, bar: Bar) -> Fill | None:
        self.broker.mark(bar.exec_ts, bar.exec_price)
        state = self.broker.snapshot()

        self.stats.equity_curve.append(
            EquityPoint(
                ts=bar.exec_ts,
                equity=state.equity,
                cash=state.cash,
                price=state.price,
                weight=state.weight,
            )
        )

        signal = self.strategy.decide(bar.history)
        decision = self.risk.evaluate(signal, state, bar.exec_ts)
        target = signal.resolve_weight(state.weight) if decision.target_weight is None else decision.target_weight

        if self.config.verbose:
            log.info(
                "%s | 종가 %s | 비중 %.0f%% -> %.0f%% | %s",
                bar.last.ts.isoformat(),
                f"{bar.last.close:,.0f}",
                state.weight * 100,
                target * 100,
                decision.reason,
            )

        avg_before = state.position.avg_price
        fill = reconcile(
            self.broker,
            state,
            target,
            band=self.config.rebalance_band,
            reason=decision.reason,
        )

        if fill is not None:
            self._record(fill, avg_before)

        self.risk.on_bar_closed()
        return fill

    def _record(self, fill: Fill, avg_before: float) -> None:
        self.risk.on_fill(fill)
        self.stats.fills.append(fill)

        after = self.broker.snapshot()
        trade = self._tracker.on_fill(fill, avg_before, after.position.volume)
        if trade is not None:
            self.stats.trades.append(trade)

        log.info(
            "체결 %s %.8f @ %s (수수료 %s) — %s",
            "매수" if fill.side is Side.BUY else "매도",
            fill.volume,
            f"{fill.price:,.0f}",
            f"{fill.fee:,.0f}",
            fill.reason,
        )
        if trade is not None:
            log.info("라운드트립 손익 %s원 (%+.2f%%)", f"{trade.pnl:,.0f}", trade.pnl_pct * 100)

        if self.journal is not None:
            self.journal.write_fill(fill, after)
            if trade is not None:
                self.journal.write_trade(trade)
            self.journal.save_state(self.risk.state, after)

        self.notifier.notify(format_fill(fill, after))
        if trade is not None:
            self.notifier.notify(format_trade(trade))

    def final_state(self) -> AccountState:
        return self.broker.snapshot()
