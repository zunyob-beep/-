"""목표 비중 -> 실제 주문으로 바꾸는 단 하나의 경로.

백테스트든 실거래든 이 함수만 거친다. "얼마를 살까"를 두 군데에 구현하면
백테스트 성적과 실계좌 성적이 갈리는데, 그 차이는 아주 늦게 발견된다.
"""

from __future__ import annotations

import logging

from .exchange.base import Broker
from .models import AccountState, Fill

log = logging.getLogger(__name__)


def reconcile(
    broker: Broker,
    state: AccountState,
    target_weight: float,
    *,
    band: float = 0.05,
    reason: str = "",
) -> Fill | None:
    """현재 비중을 목표 비중에 맞춘다. 주문이 나갔으면 Fill을 돌려준다.

    `band`는 재조정을 건너뛰는 허용 오차(총자산 대비)다. 이게 없으면
    가격이 조금만 움직여도 매 봉 잔주문이 나가 수수료로 계좌가 녹는다.
    단, **완전 청산(target 0)과 신규 진입은 밴드와 무관하게** 실행한다.
    """
    target_weight = max(0.0, min(1.0, target_weight))
    equity = state.equity
    if equity <= 0:
        log.warning("평가자산이 0 이하입니다 — 주문하지 않습니다")
        return None

    target_value = equity * target_weight
    delta = target_value - state.coin_value

    # 청산은 잔량이 남지 않도록 밴드를 무시하고 전량 매도한다.
    if target_weight <= 0.0:
        if not state.position.is_open:
            return None
        return broker.market_sell(state.position.volume, reason=reason or "청산")

    threshold = max(broker.min_order_krw, equity * band)
    entering = not state.position.is_open
    if abs(delta) < threshold and not entering:
        return None

    if delta > 0:
        budget = min(delta, state.cash)
        if budget < broker.min_order_krw:
            return None
        return broker.market_buy(budget, reason=reason)

    volume = min(state.position.volume, -delta / state.price)
    if volume <= 0:
        return None
    return broker.market_sell(volume, reason=reason)
