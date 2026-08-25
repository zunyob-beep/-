from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from btcbot.models import AccountState, Position, Signal
from btcbot.risk import RiskConfig, RiskManager, RiskState

TS = datetime(2024, 1, 1, 3, 0, tzinfo=timezone.utc)  # 12:00 KST


def account(cash: float, volume: float = 0.0, avg: float = 0.0, price: float = 100.0):
    position = Position("KRW-BTC", volume=volume, avg_price=avg)
    return AccountState(cash=cash, position=position, price=price)


def buy_signal(weight: float = 1.0) -> Signal:
    return Signal(target_weight=weight, reason="전략 매수")


# ------------------------------------------------------------------ 설정 검증
def test_config_rejects_invalid_values():
    with pytest.raises(ValueError):
        RiskConfig(max_position_weight=0.0)
    with pytest.raises(ValueError):
        RiskConfig(stop_loss_pct=1.5)
    with pytest.raises(ValueError):
        RiskConfig(cooldown_bars=-1)


# ------------------------------------------------------------------ 비중 상한
def test_caps_target_weight():
    risk = RiskManager(RiskConfig(max_position_weight=0.3))
    decision = risk.evaluate(buy_signal(1.0), account(cash=1_000_000), TS)
    assert decision.target_weight == pytest.approx(0.3)
    assert decision.overridden


def test_passes_through_when_within_cap():
    risk = RiskManager(RiskConfig(max_position_weight=1.0))
    decision = risk.evaluate(buy_signal(0.5), account(cash=1_000_000), TS)
    assert decision.target_weight == pytest.approx(0.5)
    assert not decision.overridden
    assert decision.reason == "전략 매수"


# ------------------------------------------------------------------ 손절/익절
def test_stop_loss_forces_exit():
    risk = RiskManager(RiskConfig(stop_loss_pct=0.05))
    state = account(cash=0, volume=10, avg=100, price=94)  # -6%
    decision = risk.evaluate(buy_signal(1.0), state, TS)
    assert decision.target_weight == 0.0
    assert "손절" in decision.reason


def test_stop_loss_not_triggered_above_threshold():
    risk = RiskManager(RiskConfig(stop_loss_pct=0.05))
    state = account(cash=0, volume=10, avg=100, price=96)  # -4%
    assert risk.evaluate(buy_signal(1.0), state, TS).target_weight == 1.0


def test_take_profit_forces_exit():
    risk = RiskManager(RiskConfig(take_profit_pct=0.10))
    state = account(cash=0, volume=10, avg=100, price=115)
    decision = risk.evaluate(buy_signal(1.0), state, TS)
    assert decision.target_weight == 0.0
    assert "익절" in decision.reason


def test_trailing_stop_uses_peak_since_entry():
    risk = RiskManager(RiskConfig(trailing_stop_pct=0.10))
    # 고점 150까지 갔다가
    risk.evaluate(buy_signal(1.0), account(cash=0, volume=10, avg=100, price=150), TS)
    assert risk.state.position_peak_price == pytest.approx(150)

    # 고점 대비 -10% 하락 -> 청산 (평단 대비로는 아직 +34%)
    decision = risk.evaluate(
        buy_signal(1.0), account(cash=0, volume=10, avg=100, price=134), TS
    )
    assert decision.target_weight == 0.0
    assert "트레일링" in decision.reason


def test_trailing_peak_resets_when_flat():
    risk = RiskManager(RiskConfig(trailing_stop_pct=0.10))
    risk.evaluate(buy_signal(1.0), account(cash=0, volume=10, avg=100, price=150), TS)
    risk.evaluate(buy_signal(1.0), account(cash=1_000), TS)  # 포지션 없음
    assert risk.state.position_peak_price == 0.0


# ------------------------------------------------------------- 일일 손실 한도
def test_daily_loss_limit_blocks_rest_of_day():
    risk = RiskManager(RiskConfig(daily_loss_limit_pct=0.03))
    risk.evaluate(buy_signal(1.0), account(cash=1_000_000), TS)  # 당일 기준 자산 기록
    assert risk.state.day_start_equity == pytest.approx(1_000_000)

    decision = risk.evaluate(buy_signal(1.0), account(cash=960_000), TS)  # -4%
    assert decision.target_weight == 0.0
    assert "일일 손실 한도" in decision.reason

    # 같은 날에는 다시 사려 해도 막힌다
    later = risk.evaluate(buy_signal(1.0), account(cash=960_000), TS + timedelta(hours=1))
    assert later.target_weight == 0.0


def test_daily_block_clears_next_day():
    risk = RiskManager(RiskConfig(daily_loss_limit_pct=0.03))
    risk.evaluate(buy_signal(1.0), account(cash=1_000_000), TS)
    risk.evaluate(buy_signal(1.0), account(cash=960_000), TS)
    assert risk.state.blocked_day is not None

    tomorrow = TS + timedelta(days=1)
    decision = risk.evaluate(buy_signal(1.0), account(cash=960_000), tomorrow)
    assert decision.target_weight == 1.0
    assert risk.state.blocked_day is None


def test_daily_limit_uses_kst_day_boundary():
    risk = RiskManager(RiskConfig(daily_loss_limit_pct=0.03))
    # 2024-01-01 14:00 UTC == 23:00 KST (같은 날)
    risk.evaluate(buy_signal(), account(cash=1_000_000), datetime(2024, 1, 1, 14, tzinfo=timezone.utc))
    assert risk.state.day == "2024-01-01"
    # 15:00 UTC == 다음 날 00:00 KST
    risk.evaluate(buy_signal(), account(cash=1_000_000), datetime(2024, 1, 1, 15, tzinfo=timezone.utc))
    assert risk.state.day == "2024-01-02"


# ------------------------------------------------------------------ 킬 스위치
def test_max_drawdown_halts_permanently():
    risk = RiskManager(RiskConfig(max_drawdown_pct=0.20))
    risk.evaluate(buy_signal(1.0), account(cash=1_000_000), TS)
    decision = risk.evaluate(buy_signal(1.0), account(cash=750_000), TS)
    assert decision.halted
    assert decision.target_weight == 0.0
    assert risk.state.halted

    # 자산이 회복되어도 다시 켜지지 않는다 — 사람이 확인해야 한다
    recovered = risk.evaluate(buy_signal(1.0), account(cash=1_200_000), TS + timedelta(days=5))
    assert recovered.halted


# -------------------------------------------------------------------- 쿨다운
def test_cooldown_blocks_new_entries_only():
    risk = RiskManager(RiskConfig(stop_loss_pct=0.05, cooldown_bars=2))
    risk.evaluate(buy_signal(1.0), account(cash=0, volume=10, avg=100, price=90), TS)  # 손절
    assert risk.state.cooldown_left == 2

    blocked = risk.evaluate(buy_signal(1.0), account(cash=1_000_000), TS)
    assert blocked.target_weight == 0.0
    assert "쿨다운" in blocked.reason

    risk.on_bar_closed()
    risk.on_bar_closed()
    allowed = risk.evaluate(buy_signal(1.0), account(cash=1_000_000), TS)
    assert allowed.target_weight == 1.0


# ------------------------------------------------------------- 상태 직렬화
def test_state_round_trips_through_dict():
    state = RiskState(
        day="2024-01-01",
        day_start_equity=1_000_000,
        peak_equity=1_200_000,
        position_peak_price=95_000_000,
        halted=True,
        halt_reason="테스트",
        blocked_day="2024-01-01",
        cooldown_left=3,
    )
    restored = RiskState.from_dict(state.to_dict())
    assert restored == state


def test_state_ignores_unknown_keys():
    restored = RiskState.from_dict({"day": "2024-01-01", "미래에_추가된_필드": 1})
    assert restored.day == "2024-01-01"


def test_hold_signal_keeps_current_weight():
    risk = RiskManager(RiskConfig())
    state = account(cash=500_000, volume=5_000, avg=100, price=100)
    decision = risk.evaluate(Signal(target_weight=None, reason="유지"), state, TS)
    assert decision.target_weight == pytest.approx(state.weight)
