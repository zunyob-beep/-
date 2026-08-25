from __future__ import annotations

from datetime import datetime, timezone

import pytest

from btcbot.exchange.simulated import SimulatedBroker
from btcbot.execution import reconcile
from btcbot.models import Fill, Position, Side, Signal

TS = datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_candle_requires_timezone():
    from btcbot.models import Candle

    with pytest.raises(ValueError):
        Candle("KRW-BTC", datetime(2024, 1, 1), 1, 1, 1, 1, 1)


def test_kst_date_crosses_utc_midnight():
    from btcbot.models import Candle

    # 2024-01-01 00:00 UTC == 2024-01-01 09:00 KST
    assert Candle("KRW-BTC", TS, 1, 1, 1, 1, 1).kst_date == "2024-01-01"
    # 2024-01-01 15:00 UTC == 2024-01-02 00:00 KST
    late = TS.replace(hour=15)
    assert Candle("KRW-BTC", late, 1, 1, 1, 1, 1).kst_date == "2024-01-02"


def test_position_average_price_includes_fees():
    position = Position("KRW-BTC")
    position.apply(Fill("KRW-BTC", Side.BUY, price=100, volume=1, fee=10, ts=TS))
    assert position.avg_price == pytest.approx(110.0)

    position.apply(Fill("KRW-BTC", Side.BUY, price=200, volume=1, fee=0, ts=TS))
    assert position.volume == pytest.approx(2.0)
    assert position.avg_price == pytest.approx(155.0)


def test_position_realized_pnl_and_close():
    position = Position("KRW-BTC")
    position.apply(Fill("KRW-BTC", Side.BUY, price=100, volume=2, fee=0, ts=TS))
    realized = position.apply(Fill("KRW-BTC", Side.SELL, price=150, volume=2, fee=5, ts=TS))
    assert realized == pytest.approx(95.0)
    assert not position.is_open
    assert position.avg_price == 0.0


def test_position_rejects_oversell():
    position = Position("KRW-BTC")
    position.apply(Fill("KRW-BTC", Side.BUY, price=100, volume=1, fee=0, ts=TS))
    with pytest.raises(ValueError):
        position.apply(Fill("KRW-BTC", Side.SELL, price=100, volume=2, fee=0, ts=TS))


def test_signal_none_weight_means_hold():
    assert Signal().target_weight is None
    assert Signal().resolve_weight(0.4) == 0.4
    assert Signal(target_weight=0.0).resolve_weight(0.4) == 0.0


def test_signal_rejects_out_of_range_weight():
    with pytest.raises(ValueError):
        Signal(target_weight=1.5)


# ------------------------------------------------------------------ reconcile
def broker_at(price: float, cash: float = 1_000_000.0) -> SimulatedBroker:
    broker = SimulatedBroker("KRW-BTC", cash=cash, fee_rate=0.0005, slippage=0.0)
    broker.mark(TS, price)
    return broker


def test_reconcile_buys_full_weight():
    broker = broker_at(100.0)
    fill = reconcile(broker, broker.snapshot(), 1.0)
    assert fill is not None and fill.side is Side.BUY
    state = broker.snapshot()
    assert state.weight == pytest.approx(1.0, abs=0.01)
    assert state.cash >= 0


def test_reconcile_skips_small_drift_inside_band():
    broker = broker_at(100.0)
    reconcile(broker, broker.snapshot(), 0.5)
    before = broker.snapshot().position.volume
    # 0.5 -> 0.52는 밴드(5%) 안이라 주문이 나가면 안 된다
    assert reconcile(broker, broker.snapshot(), 0.52, band=0.05) is None
    assert broker.snapshot().position.volume == pytest.approx(before)


def test_reconcile_always_exits_fully_regardless_of_band():
    broker = broker_at(100.0)
    reconcile(broker, broker.snapshot(), 0.02)  # 아주 작은 포지션
    assert broker.snapshot().position.is_open
    reconcile(broker, broker.snapshot(), 0.0)
    assert not broker.snapshot().position.is_open


def test_reconcile_respects_min_order_amount():
    broker = broker_at(100.0, cash=4_000.0)
    assert reconcile(broker, broker.snapshot(), 1.0) is None


def test_reconcile_never_spends_more_than_cash():
    broker = broker_at(100.0, cash=10_000.0)
    reconcile(broker, broker.snapshot(), 1.0)
    assert broker.cash >= -1e-9


def test_reconcile_clamps_weight():
    broker = broker_at(100.0)
    reconcile(broker, broker.snapshot(), 5.0)
    assert broker.snapshot().weight <= 1.0 + 1e-9


# ------------------------------------------------------------------ 모의 체결
def test_simulated_slippage_moves_price_against_you():
    broker = SimulatedBroker("KRW-BTC", cash=1_000_000, fee_rate=0.0, slippage=0.01)
    broker.mark(TS, 100.0)
    buy = broker.market_buy(100_000)
    assert buy.price == pytest.approx(101.0)
    sell = broker.market_sell(buy.volume)
    assert sell.price == pytest.approx(99.0)


def test_simulated_round_trip_loses_fees():
    broker = SimulatedBroker("KRW-BTC", cash=1_000_000, fee_rate=0.0005, slippage=0.0)
    broker.mark(TS, 100.0)
    buy = broker.market_buy(1_000_000)
    broker.market_sell(buy.volume)
    assert broker.cash < 1_000_000
    assert broker.cash > 990_000


def test_simulated_sell_capped_at_position():
    broker = SimulatedBroker("KRW-BTC", cash=1_000_000, fee_rate=0.0, slippage=0.0)
    broker.mark(TS, 100.0)
    broker.market_buy(100_000)  # 100원에 1,000개
    fill = broker.market_sell(9_999.0)  # 보유량보다 많이 요청
    assert fill.volume == pytest.approx(1_000.0)
    assert not broker.position.is_open


def test_simulated_requires_mark_before_use():
    broker = SimulatedBroker("KRW-BTC", cash=1_000)
    with pytest.raises(RuntimeError):
        broker.snapshot()


# ------------------------------------------------- 큰 계좌에서의 부동소수 오차
def test_full_cash_buy_works_at_any_account_size():
    """계좌가 커지면 float 오차가 고정 허용치(1e-6)를 넘는다.

    실제로 백테스트 도중 자산이 100억을 넘자 '잔고 부족: 필요 X, 보유 X'
    (같은 금액인데 거부)가 나면서 거래가 막혔다.
    """
    from btcbot.exchange.base import OrderRejected

    for cash in (1_234_567.89, 23_855_793_146.13, 2_477_753_057_752.31, 9.9e14):
        for price in (41_234_567.13, 39_876_543.21, 137.7):
            broker = SimulatedBroker("KRW-BTC", cash=cash, fee_rate=0.0005, slippage=0.0005)
            broker.mark(TS, price)
            try:
                fill = broker.market_buy(cash)
            except OrderRejected as exc:  # pragma: no cover - 회귀 시에만
                pytest.fail(f"보유 {cash:,.2f} @ {price:,.2f}: {exc}")
            assert fill is not None
            assert broker.cash >= 0


def test_tolerance_tracks_float_precision():
    from btcbot.exchange.simulated import _tolerance

    # 작은 금액에서는 최소값을 유지하고, 커지면 같이 커진다
    assert _tolerance(1_000_000) == pytest.approx(1e-6)
    assert _tolerance(1e12) > _tolerance(1e9) > 0
    # 그래도 '의미 있는 금액'을 놓칠 만큼 커지지는 않는다
    assert _tolerance(1e12) < 1.0


def test_oversell_is_still_rejected():
    """허용치를 늘렸다고 진짜 논리 오류까지 통과시키면 안 된다."""
    position = Position("KRW-BTC", volume=1.0, avg_price=100.0)
    with pytest.raises(ValueError):
        position.apply(Fill("KRW-BTC", Side.SELL, price=100, volume=2.0, fee=0, ts=TS))
