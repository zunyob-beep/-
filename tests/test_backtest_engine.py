"""엔진과 백테스트 통합 검증.

여기서 지키려는 것은 두 가지다:
1. 백테스트가 미래를 훔쳐보지 않는다.
2. 계좌 항등식(현금 + 코인평가 = 자산)이 언제나 성립한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from btcbot.backtest import grid_search, run_backtest, walk_forward
from btcbot.engine import Engine, EngineConfig
from btcbot.exchange.simulated import SimulatedBroker
from btcbot.feed import BacktestFeed
from btcbot.metrics import analyze
from btcbot.models import Action, Candle, EquityPoint, Signal
from btcbot.risk import RiskConfig, RiskManager
from btcbot.strategies import get_strategy
from btcbot.strategies.base import Strategy
from tests.conftest import series


class AlwaysLong(Strategy):
    name = "always_long"
    warmup = 1

    def decide(self, candles):
        return Signal(action=Action.BUY, target_weight=1.0, reason="테스트")


class AlwaysFlat(Strategy):
    name = "always_flat"
    warmup = 1

    def decide(self, candles):
        return Signal(action=Action.SELL, target_weight=0.0, reason="테스트")


class PeekAhead(Strategy):
    """마지막 봉의 종가를 기록해 엔진이 넘겨준 범위를 검사한다."""

    name = "peek"
    warmup = 1

    def __init__(self, **params):
        super().__init__(**params)
        self.seen: list[Candle] = []

    def decide(self, candles):
        self.seen.append(candles[-1])
        return Signal(target_weight=0.0, reason="관측만")


# ---------------------------------------------------------------- 미래 참조 방지
def test_engine_never_sees_the_execution_candle(uptrend):
    strategy = PeekAhead()
    feed = BacktestFeed(uptrend, warmup=1)
    broker = SimulatedBroker("KRW-BTC", cash=1_000_000)
    engine = Engine(feed=feed, broker=broker, strategy=strategy)

    exec_prices = []
    for bar in feed:
        exec_prices.append(bar.exec_price)
        engine.step(bar)

    # 전략이 본 마지막 봉의 시각은 항상 체결 봉보다 앞선다
    assert len(strategy.seen) == len(uptrend) - 1
    for i, candle in enumerate(strategy.seen):
        assert candle.ts == uptrend[i].ts
        assert exec_prices[i] == uptrend[i + 1].open


def test_backtest_feed_requires_two_candles():
    with pytest.raises(ValueError):
        BacktestFeed(series([100.0]))


def test_feed_respects_warmup(uptrend):
    feed = BacktestFeed(uptrend, warmup=30)
    bars = list(feed)
    assert len(bars) == len(uptrend) - 30
    assert len(bars[0].history) == 30


# ------------------------------------------------------------------ 계좌 항등식
def test_equity_identity_holds_every_bar(choppy):
    result = run_backtest(choppy, get_strategy("ma_cross", fast=3, slow=10), cash=1_000_000)
    for point in result.stats.equity_curve:
        assert point.equity == pytest.approx(point.cash + point.weight * point.equity, rel=1e-9)
        assert point.cash >= -1e-6
        assert 0 <= point.weight <= 1 + 1e-9


def test_always_long_tracks_buy_and_hold(uptrend):
    """수수료·슬리피지가 없으면 항상 매수는 바이앤홀드와 거의 같아야 한다."""
    result = run_backtest(uptrend, AlwaysLong(), cash=1_000_000, fee_rate=0.0, slippage=0.0)
    perf = result.performance
    assert perf.total_return == pytest.approx(perf.buy_and_hold_return, rel=0.02)
    assert perf.exposure > 0.9


def test_always_flat_never_trades(uptrend):
    result = run_backtest(uptrend, AlwaysFlat(), cash=1_000_000)
    assert result.stats.fills == []
    assert result.performance.total_return == 0.0
    assert result.performance.exposure == 0.0


def test_fees_make_churn_expensive(choppy):
    """수수료를 올리면 같은 전략의 성과는 반드시 나빠진다."""
    strategy = lambda: get_strategy("ma_cross", fast=2, slow=5)
    cheap = run_backtest(choppy, strategy(), fee_rate=0.0, slippage=0.0)
    pricey = run_backtest(choppy, strategy(), fee_rate=0.005, slippage=0.002)
    assert pricey.performance.total_return < cheap.performance.total_return
    assert pricey.performance.total_fees > cheap.performance.total_fees


def test_rebalance_band_reduces_trade_count(choppy):
    tight = run_backtest(choppy, get_strategy("rsi", trend_ma=0), rebalance_band=0.0)
    loose = run_backtest(choppy, get_strategy("rsi", trend_ma=0), rebalance_band=0.30)
    assert len(loose.stats.fills) <= len(tight.stats.fills)


# ------------------------------------------------------------------ 리스크 연동
def test_stop_loss_limits_loss_in_crash():
    """급락장에서 손절이 실제로 낙폭을 줄이는지.

    비교 대상은 계속 들고 있는 전략이다. 교차 전략은 자기 규칙으로 먼저
    빠져나가 버려서 손절의 효과가 드러나지 않는다.
    """
    prices = [100.0 + i for i in range(40)] + [140.0 * (0.93**i) for i in range(1, 25)]
    candles = series(prices)

    naked = run_backtest(candles, AlwaysLong(), cash=1_000_000)
    guarded = run_backtest(
        candles,
        AlwaysLong(),
        cash=1_000_000,
        # 손절 후 곧바로 재진입하면 같은 하락을 계속 맞는다 -> 쿨다운 필수
        risk_config=RiskConfig(stop_loss_pct=0.05, cooldown_bars=10),
    )
    assert guarded.performance.max_drawdown < naked.performance.max_drawdown
    assert guarded.performance.final_equity > naked.performance.final_equity


def test_kill_switch_stops_the_engine():
    prices = [100.0 * (0.95**i) for i in range(60)]
    result = run_backtest(
        series(prices),
        AlwaysLong(),
        cash=1_000_000,
        risk_config=RiskConfig(max_drawdown_pct=0.15),
    )
    # 정지 이후로는 포지션이 없어야 한다
    assert result.stats.equity_curve[-1].weight == pytest.approx(0.0)
    assert result.performance.max_drawdown < 0.5


def test_max_weight_limits_exposure(uptrend):
    """비중 상한은 '주문 시점'에 걸린다.

    산 뒤에 가격이 오르면 비중은 상한을 조금 넘어 표류하다가, 재조정
    밴드를 벗어나는 순간 다시 깎인다. 따라서 허용 범위는 상한 + 밴드다.
    """
    band = 0.05
    result = run_backtest(
        uptrend,
        AlwaysLong(),
        cash=1_000_000,
        risk_config=RiskConfig(max_position_weight=0.4),
        rebalance_band=band,
    )
    assert max(p.weight for p in result.stats.equity_curve) <= 0.4 + band + 0.01


# -------------------------------------------------------------------- 라운드트립
def test_round_trips_are_recorded(choppy):
    # 수수료·슬리피지를 끄면 손익이 (청산가 - 진입가) × 수량과 정확히 같아야 한다
    result = run_backtest(
        choppy, get_strategy("ma_cross", fast=3, slow=10), fee_rate=0.0, slippage=0.0
    )
    assert result.stats.trades
    for trade in result.stats.trades:
        assert trade.exit_ts >= trade.entry_ts
        assert trade.volume > 0
        expected = (trade.exit_price - trade.entry_price) * trade.volume
        assert trade.pnl == pytest.approx(expected, rel=1e-6, abs=1e-6)


def test_round_trip_pnl_is_net_of_fees(choppy):
    """수수료가 붙으면 기록된 손익은 반드시 그만큼 작아진다."""
    free = run_backtest(choppy, get_strategy("ma_cross", fast=3, slow=10), fee_rate=0.0, slippage=0.0)
    charged = run_backtest(choppy, get_strategy("ma_cross", fast=3, slow=10), fee_rate=0.005, slippage=0.0)
    assert charged.stats.realized_pnl < free.stats.realized_pnl


def test_win_rate_matches_trade_records(choppy):
    result = run_backtest(choppy, get_strategy("ma_cross", fast=3, slow=10))
    trades = result.stats.trades
    wins = sum(1 for t in trades if t.pnl > 0)
    assert result.performance.win_rate == pytest.approx(wins / len(trades))


# ---------------------------------------------------------------------- 지표
def test_metrics_on_known_curve():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    equities = [100.0, 120.0, 60.0, 90.0]
    curve = [
        EquityPoint(start + timedelta(days=i), e, cash=e, price=100.0, weight=0.0)
        for i, e in enumerate(equities)
    ]
    perf = analyze(curve, trades=[])
    assert perf.total_return == pytest.approx(-0.10)
    assert perf.max_drawdown == pytest.approx(0.5)  # 120 -> 60


def test_metrics_require_two_points():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        analyze([EquityPoint(start, 100, 100, 100, 0.0)], trades=[])


# ------------------------------------------------------------------- 탐색/검증
def test_grid_search_sorts_by_metric(choppy):
    results = grid_search(
        choppy, "ma_cross", {"fast": [2, 3, 5], "slow": [10, 20]}, metric="sharpe", top=3
    )
    assert len(results) == 3
    sharpes = [r.performance.sharpe for r in results]
    assert sharpes == sorted(sharpes, reverse=True)


def test_grid_search_skips_invalid_combos(choppy):
    # fast >= slow 조합은 전략이 거부하므로 결과에 없어야 한다
    results = grid_search(choppy, "ma_cross", {"fast": [5, 30], "slow": [10]}, top=10)
    assert all(r.params["fast"] < r.params["slow"] for r in results)


def test_walk_forward_returns_both_windows(choppy):
    test, train = walk_forward(choppy, "ma_cross", {"fast": [2, 3], "slow": [10, 15]})
    assert test is not None and train is not None
    assert test.stats.equity_curve[0].ts > train.stats.equity_curve[0].ts


def test_walk_forward_rejects_bad_ratio(choppy):
    with pytest.raises(ValueError):
        walk_forward(choppy, "ma_cross", {"fast": [2]}, train_ratio=0.99)


# ------------------------------------------------------------------ 엔진 설정
def test_engine_reuses_same_path_as_live(uptrend):
    """엔진을 직접 조립해도 run_backtest와 같은 결과가 나와야 한다."""
    direct = Engine(
        feed=BacktestFeed(uptrend, warmup=1),
        broker=SimulatedBroker("KRW-BTC", cash=1_000_000, fee_rate=0.0005, slippage=0.0005),
        strategy=AlwaysLong(),
        risk=RiskManager(RiskConfig()),
        config=EngineConfig(rebalance_band=0.05),
    ).run()
    packaged = run_backtest(uptrend, AlwaysLong(), cash=1_000_000)
    assert len(direct.fills) == len(packaged.stats.fills)
