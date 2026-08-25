"""리스크 관리 — 전략보다 항상 우선한다.

전략이 아무리 사라고 해도 여기서 막으면 못 산다. 자동매매에서 계좌를
살리는 건 좋은 진입이 아니라 나쁜 상황에서의 강제 이탈이다.

상태(일일 시작 자산, 최고점, 트레일링 고점 등)는 직렬화해서 저장하므로
봇이 재시작해도 손절선과 일일 한도가 초기화되지 않는다.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .models import KST, AccountState, Fill, Side, Signal

log = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    #: 총자산 대비 최대 코인 비중
    max_position_weight: float = 1.0
    #: 평단 대비 손절 비율 (0.05 = -5%). 0이면 끔
    stop_loss_pct: float = 0.0
    #: 평단 대비 익절 비율. 0이면 끔
    take_profit_pct: float = 0.0
    #: 진입 후 고점 대비 하락 시 청산. 0이면 끔
    trailing_stop_pct: float = 0.0
    #: 하루 손실 한도(당일 시작 자산 대비). 넘으면 그날은 청산 후 진입 금지
    daily_loss_limit_pct: float = 0.0
    #: 최고 자산 대비 낙폭 한도. 넘으면 봇 자체를 정지(킬 스위치)
    max_drawdown_pct: float = 0.0
    #: 손절 후 쉬어가는 봉 개수
    cooldown_bars: int = 0

    def __post_init__(self) -> None:
        if not 0.0 < self.max_position_weight <= 1.0:
            raise ValueError("max_position_weight는 0 초과 1 이하여야 합니다")
        for name in (
            "stop_loss_pct",
            "take_profit_pct",
            "trailing_stop_pct",
            "daily_loss_limit_pct",
            "max_drawdown_pct",
        ):
            value = getattr(self, name)
            if not 0.0 <= value < 1.0:
                raise ValueError(f"{name}는 0 이상 1 미만이어야 합니다 (받은 값: {value})")
        if self.cooldown_bars < 0:
            raise ValueError("cooldown_bars는 0 이상이어야 합니다")


@dataclass
class RiskState:
    day: str | None = None
    day_start_equity: float = 0.0
    peak_equity: float = 0.0
    #: 포지션 진입 후 기록한 최고가(트레일링 스탑용)
    position_peak_price: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    #: 이 날짜 동안은 신규 진입 금지 (일일 손실 한도 발동)
    blocked_day: str | None = None
    cooldown_left: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RiskState:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class RiskDecision:
    target_weight: float | None
    reason: str
    #: 리스크 규칙이 전략 판단을 덮어썼는지
    overridden: bool = False
    halted: bool = False


@dataclass
class RiskManager:
    config: RiskConfig = field(default_factory=RiskConfig)
    state: RiskState = field(default_factory=RiskState)

    def evaluate(self, signal: Signal, account: AccountState, ts: datetime) -> RiskDecision:
        """전략 신호에 리스크 규칙을 씌운 최종 목표 비중."""
        self._roll_day(account, ts)
        self._track_peaks(account)

        cfg = self.config
        equity = account.equity

        # 1) 킬 스위치 — 최고점 대비 낙폭
        if cfg.max_drawdown_pct > 0 and self.state.peak_equity > 0:
            drawdown = 1 - equity / self.state.peak_equity
            if drawdown >= cfg.max_drawdown_pct or self.state.halted:
                if not self.state.halted:
                    self.state.halted = True
                    self.state.halt_reason = (
                        f"최대 낙폭 한도 초과 (MDD {drawdown:.1%} >= {cfg.max_drawdown_pct:.1%})"
                    )
                    log.error("킬 스위치 작동: %s", self.state.halt_reason)
                return RiskDecision(0.0, self.state.halt_reason, overridden=True, halted=True)
        if self.state.halted:
            return RiskDecision(0.0, self.state.halt_reason, overridden=True, halted=True)

        # 2) 일일 손실 한도
        if cfg.daily_loss_limit_pct > 0 and self.state.day_start_equity > 0:
            day_pnl = equity / self.state.day_start_equity - 1
            if day_pnl <= -cfg.daily_loss_limit_pct:
                self.state.blocked_day = self.state.day
                reason = f"일일 손실 한도 도달 ({day_pnl:.2%}) — 오늘은 관망"
                log.warning(reason)
                return RiskDecision(0.0, reason, overridden=True)

        # 3) 보유 중이면 손절/익절/트레일링
        if account.position.is_open:
            exit_reason = self._exit_reason(account)
            if exit_reason:
                self.state.cooldown_left = cfg.cooldown_bars
                log.info("리스크 청산: %s", exit_reason)
                return RiskDecision(0.0, exit_reason, overridden=True)

        # 4) 쿨다운 / 당일 진입 금지 — 신규 진입만 막고 보유는 유지
        target = signal.resolve_weight(account.weight)
        increasing = target > account.weight + 1e-9

        if increasing and self.state.blocked_day == self.state.day and self.state.blocked_day:
            return RiskDecision(
                account.weight, "일일 손실 한도로 오늘 신규 진입 금지", overridden=True
            )

        if increasing and self.state.cooldown_left > 0:
            return RiskDecision(
                account.weight,
                f"쿨다운 {self.state.cooldown_left}봉 남음 — 신규 진입 보류",
                overridden=True,
            )

        capped = min(target, cfg.max_position_weight)
        if capped < target - 1e-9:
            return RiskDecision(
                capped,
                f"{signal.reason} (비중 상한 {cfg.max_position_weight:.0%} 적용)",
                overridden=True,
            )
        return RiskDecision(capped, signal.reason)

    def on_bar_closed(self) -> None:
        """봉이 하나 닫힐 때마다 쿨다운을 줄인다."""
        if self.state.cooldown_left > 0:
            self.state.cooldown_left -= 1

    def on_fill(self, fill: Fill) -> None:
        """체결 반영 — 트레일링 스탑 기준점을 관리한다."""
        if fill.side is Side.BUY:
            self.state.position_peak_price = max(self.state.position_peak_price, fill.price)
        else:
            self.state.position_peak_price = 0.0

    def _roll_day(self, account: AccountState, ts: datetime) -> None:
        today = ts.astimezone(KST).strftime("%Y-%m-%d")
        if self.state.day != today:
            self.state.day = today
            self.state.day_start_equity = account.equity
            if self.state.blocked_day and self.state.blocked_day != today:
                self.state.blocked_day = None

    def _track_peaks(self, account: AccountState) -> None:
        self.state.peak_equity = max(self.state.peak_equity, account.equity)
        if account.position.is_open:
            self.state.position_peak_price = max(self.state.position_peak_price, account.price)
        else:
            self.state.position_peak_price = 0.0

    def _exit_reason(self, account: AccountState) -> str | None:
        cfg = self.config
        avg = account.position.avg_price
        price = account.price
        if avg <= 0:
            return None

        change = price / avg - 1
        if cfg.stop_loss_pct > 0 and change <= -cfg.stop_loss_pct:
            return f"손절 ({change:+.2%} <= -{cfg.stop_loss_pct:.2%})"
        if cfg.take_profit_pct > 0 and change >= cfg.take_profit_pct:
            return f"익절 ({change:+.2%} >= {cfg.take_profit_pct:.2%})"
        if cfg.trailing_stop_pct > 0 and self.state.position_peak_price > 0:
            drop = price / self.state.position_peak_price - 1
            if drop <= -cfg.trailing_stop_pct:
                return (
                    f"트레일링 스탑 (고점 {self.state.position_peak_price:,.0f} 대비 {drop:+.2%})"
                )
        return None
