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
