"""백테스트 러너와 파라미터 탐색.

실거래와 **같은 엔진**을 돌린다. 백테스트 전용 매매 로직을 따로 쓰면
그 코드가 실거래와 어긋나는 순간 백테스트 결과는 아무 의미가 없어진다.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .engine import Engine, EngineConfig
from .exchange.base import DEFAULT_FEE_RATE
from .exchange.simulated import SimulatedBroker
from .feed import BacktestFeed
from .metrics import Performance, analyze
from .models import Candle, EquityPoint, RunStats
from .risk import RiskConfig, RiskManager
from .strategies.base import Strategy, get_strategy

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    market: str
    interval: str
    strategy: str
    params: dict[str, Any]
    performance: Performance
    stats: RunStats = field(repr=False, default_factory=RunStats)

    def report(self) -> str:
        header = f"{self.market} · {self.interval} · {self.strategy}"
        curve = self.stats.equity_curve
        period = ""
        if curve:
            period = f"{curve[0].ts:%Y-%m-%d} ~ {curve[-1].ts:%Y-%m-%d}"
        lines = [
            "=" * 60,
            f"  {header}",
            f"  기간: {period}  (봉 {len(curve)}개)",
            "=" * 60,
            self.performance.format(),
        ]
        return "\n".join(lines)


def run_backtest(
    candles: Sequence[Candle],
    strategy: Strategy,
    *,
    cash: float = 1_000_000,
    fee_rate: float = DEFAULT_FEE_RATE,
    slippage: float = 0.0005,
    risk_config: RiskConfig | None = None,
    rebalance_band: float = 0.05,
    interval: str = "day",
    verbose: bool = False,
) -> BacktestResult:
    if len(candles) <= strategy.warmup:
        # 여기서 막지 않으면 "자산 곡선이 2개 이상이어야 합니다" 같은 엉뚱한
        # 오류가 뒤에서 터진다. 무엇을 어떻게 고쳐야 하는지 알려준다.
        raise ValueError(
            f"봉이 부족합니다. 이 전략은 판단을 시작하는 데 {strategy.warmup}개가 필요한데 "
            f"{len(candles)}개뿐입니다. 조회 기간을 늘리거나, 더 짧은 봉 간격을 쓰거나, "
            "전략의 기간 설정(이동평균 기간 등)을 줄이세요."
        )

    # 지표를 미리 계산해둔다(O(n^2) -> O(n)). 인과적 지표만 쓰므로
    # 미래 봉을 넘겨도 판단에는 과거만 반영된다 — Strategy.prepare 참고.
    strategy.prepare(candles)

    feed = BacktestFeed(candles, warmup=strategy.warmup, interval=interval)
    broker = SimulatedBroker(
        market=feed.market, cash=cash, fee_rate=fee_rate, slippage=slippage
    )
    engine = Engine(
        feed=feed,
        broker=broker,
        strategy=strategy,
        risk=RiskManager(config=risk_config or RiskConfig()),
        config=EngineConfig(rebalance_band=rebalance_band, verbose=verbose),
    )
    stats = engine.run()

    # 마지막 봉 기준 평가금액을 곡선 끝에 한 번 더 찍어 미청산 포지션을 반영
    _append_final_point(stats, broker, candles[-1])

    performance = analyze(
        stats.equity_curve,
        stats.trades,
        total_fees=sum(f.fee for f in stats.fills),
        interval=interval,
    )
    return BacktestResult(
        market=feed.market,
        interval=interval,
        strategy=strategy.describe(),
        params=dict(strategy.params),
        performance=performance,
        stats=stats,
    )


def _append_final_point(stats: RunStats, broker: SimulatedBroker, last: Candle) -> None:
    broker.mark(last.ts, last.close)
    state = broker.snapshot()
    stats.equity_curve.append(
        EquityPoint(
            ts=last.ts,
            equity=state.equity,
            cash=state.cash,
            price=state.price,
            weight=state.weight,
        )
    )


def grid_search(
    candles: Sequence[Candle],
    strategy_name: str,
    grid: Mapping[str, Iterable[Any]],
    *,
    metric: str = "sharpe",
    top: int = 10,
    **backtest_kwargs: Any,
) -> list[BacktestResult]:
    """파라미터 조합을 전수 탐색해 성과순으로 돌려준다.

    경고: 같은 데이터로 파라미터를 고르면 거의 항상 과최적화된다.
    최소한 기간을 둘로 나눠(in-sample / out-of-sample) 검증하라.
    `walk_forward()`가 그 작업을 대신 해준다.
    """
    keys = list(grid)
    combos = list(itertools.product(*(list(grid[k]) for k in keys)))
    log.info("%d개 조합 탐색 중...", len(combos))

    results: list[BacktestResult] = []
    for combo in combos:
        params = dict(zip(keys, combo, strict=True))
        try:
            strategy = get_strategy(strategy_name, **params)
            results.append(run_backtest(candles, strategy, **backtest_kwargs))
        except (ValueError, KeyError) as exc:
            log.debug("조합 %s 건너뜀: %s", params, exc)

    results.sort(key=lambda r: getattr(r.performance, metric), reverse=True)
    return results[:top]


def walk_forward(
    candles: Sequence[Candle],
    strategy_name: str,
    grid: Mapping[str, Iterable[Any]],
    *,
    train_ratio: float = 0.7,
    metric: str = "sharpe",
    **backtest_kwargs: Any,
) -> tuple[BacktestResult | None, BacktestResult | None]:
    """앞 구간에서 파라미터를 고르고, 뒤 구간에서 그대로 검증한다.

    (검증 결과, 학습 결과)를 돌려준다. 학습 성과보다 검증 성과가 크게
    나쁘면 그 파라미터는 과거에만 맞춰진 것이다.
    """
    if not 0.1 <= train_ratio <= 0.9:
        raise ValueError("train_ratio는 0.1~0.9 사이여야 합니다")
    split = int(len(candles) * train_ratio)
    train, test = candles[:split], candles[split:]
    if len(train) < 10 or len(test) < 10:
        raise ValueError("구간을 나누기에 봉이 너무 적습니다")

    best = grid_search(train, strategy_name, grid, metric=metric, top=1, **backtest_kwargs)
    if not best:
        return None, None

    winner = best[0]
    strategy = get_strategy(strategy_name, **winner.params)
    return run_backtest(test, strategy, **backtest_kwargs), winner
