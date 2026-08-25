"""성과 지표.

수익률만 보면 안 된다. 같은 수익률이라도 MDD가 50%인 전략은 실제로
운용할 수 없다 — 그 낙폭을 견디지 못하고 사람이 먼저 봇을 끈다.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass

from .exchange.upbit import interval_length
from .models import EquityPoint, TradeRecord


@dataclass
class Performance:
    initial_equity: float
    final_equity: float
    total_return: float
    cagr: float
    max_drawdown: float
    max_drawdown_days: float
    sharpe: float
    volatility: float
    trades: int
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    best_trade: float
    worst_trade: float
    total_fees: float
    buy_and_hold_return: float
    exposure: float

    def format(self) -> str:
        rows = [
            ("초기 자산", f"{self.initial_equity:,.0f}원"),
            ("최종 자산", f"{self.final_equity:,.0f}원"),
            ("총 수익률", f"{self.total_return:+.2%}"),
            ("바이앤홀드", f"{self.buy_and_hold_return:+.2%}"),
            ("초과 수익", f"{self.total_return - self.buy_and_hold_return:+.2%}"),
            ("연환산(CAGR)", f"{self.cagr:+.2%}"),
            ("최대 낙폭(MDD)", f"{self.max_drawdown:.2%}"),
            ("최대 낙폭 지속", f"{self.max_drawdown_days:.1f}일"),
            ("연환산 변동성", f"{self.volatility:.2%}"),
            ("샤프 지수", f"{self.sharpe:.2f}"),
            ("거래 횟수", f"{self.trades}회"),
            ("승률", f"{self.win_rate:.1%}"),
            ("손익비(PF)", f"{self.profit_factor:.2f}" if self.profit_factor != math.inf else "∞"),
            ("평균 수익 거래", f"{self.avg_win:+,.0f}원"),
            ("평균 손실 거래", f"{self.avg_loss:+,.0f}원"),
            ("최고 거래", f"{self.best_trade:+,.0f}원"),
            ("최악 거래", f"{self.worst_trade:+,.0f}원"),
            ("총 수수료", f"{self.total_fees:,.0f}원"),
            ("시장 노출", f"{self.exposure:.1%}"),
        ]
        width = max(len(label) for label, _ in rows)
        return "\n".join(f"  {label.ljust(width)}  {value}" for label, value in rows)


def analyze(
    curve: Sequence[EquityPoint],
    trades: Sequence[TradeRecord],
    total_fees: float = 0.0,
    interval: str = "day",
) -> Performance:
    if len(curve) < 2:
        raise ValueError("성과를 계산하려면 자산 곡선이 2개 이상이어야 합니다")

    equities = [p.equity for p in curve]
    initial, final = equities[0], equities[-1]
    total_return = final / initial - 1 if initial > 0 else 0.0

    span_days = max((curve[-1].ts - curve[0].ts).total_seconds() / 86400, 1e-9)
    years = span_days / 365.25
    cagr = (final / initial) ** (1 / years) - 1 if initial > 0 and years > 0 else 0.0

    mdd, mdd_days = _drawdown(curve)
    returns = _period_returns(equities)
    periods_per_year = _periods_per_year(interval)
    volatility, sharpe = _risk_adjusted(returns, periods_per_year)

    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)

    first_price, last_price = curve[0].price, curve[-1].price
    buy_and_hold = last_price / first_price - 1 if first_price > 0 else 0.0
    exposure = sum(1 for p in curve if p.weight > 1e-6) / len(curve)

    return Performance(
        initial_equity=initial,
        final_equity=final,
        total_return=total_return,
        cagr=cagr,
        max_drawdown=mdd,
        max_drawdown_days=mdd_days,
        sharpe=sharpe,
        volatility=volatility,
        trades=len(trades),
        win_rate=len(wins) / len(trades) if trades else 0.0,
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0),
        avg_win=gross_profit / len(wins) if wins else 0.0,
        avg_loss=sum(losses) / len(losses) if losses else 0.0,
        best_trade=max((t.pnl for t in trades), default=0.0),
        worst_trade=min((t.pnl for t in trades), default=0.0),
        total_fees=total_fees,
        buy_and_hold_return=buy_and_hold,
        exposure=exposure,
    )


def _drawdown(curve: Sequence[EquityPoint]) -> tuple[float, float]:
    """(최대 낙폭 비율, 최대 낙폭이 회복되지 않은 최장 기간(일))."""
    peak = curve[0].equity
    peak_ts = curve[0].ts
    max_dd = 0.0
    longest = 0.0

    for point in curve:
        if point.equity >= peak:
            longest = max(longest, (point.ts - peak_ts).total_seconds() / 86400)
            peak = point.equity
            peak_ts = point.ts
        elif peak > 0:
            max_dd = max(max_dd, 1 - point.equity / peak)

    longest = max(longest, (curve[-1].ts - peak_ts).total_seconds() / 86400)
    return max_dd, longest


def _period_returns(equities: Sequence[float]) -> list[float]:
    out = []
    for prev, cur in itertools.pairwise(equities):
        if prev > 0:
            out.append(cur / prev - 1)
    return out


def _risk_adjusted(returns: Sequence[float], periods_per_year: float) -> tuple[float, float]:
    if len(returns) < 2:
        return 0.0, 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0, 0.0
    volatility = sd * math.sqrt(periods_per_year)
    sharpe = (mean / sd) * math.sqrt(periods_per_year)
    return volatility, sharpe


def _periods_per_year(interval: str) -> float:
    try:
        seconds = interval_length(interval).total_seconds()
    except ValueError:
        seconds = 86400.0
    return (365.25 * 86400) / seconds
