"""체결 알림 (슬랙/디스코드 호환 웹훅).

알림 실패가 매매를 막아서는 안 된다. 모든 예외를 삼키고 로그만 남긴다.
"""

from __future__ import annotations

import logging
from typing import Protocol

import requests

from .models import AccountState, Fill, Side, TradeRecord

log = logging.getLogger(__name__)


class Notifier(Protocol):
    def notify(self, text: str) -> None: ...


class NullNotifier:
    def notify(self, text: str) -> None:  # pragma: no cover - 기본 무동작
        pass


class WebhookNotifier:
    """`{"text": ...}` 형태를 받는 웹훅이면 무엇이든(슬랙, 디스코드 등)."""

    def __init__(self, url: str, timeout: float = 5.0) -> None:
        self.url = url
        self.timeout = timeout

    def notify(self, text: str) -> None:
        try:
            response = requests.post(
                self.url, json={"text": text, "content": text}, timeout=self.timeout
            )
            if response.status_code >= 400:
                log.warning("웹훅 응답 %s: %s", response.status_code, response.text[:200])
        except requests.RequestException as exc:
            log.warning("웹훅 전송 실패: %s", exc)


def build(url: str | None) -> Notifier:
    return WebhookNotifier(url) if url else NullNotifier()


def format_fill(fill: Fill, state: AccountState) -> str:
    side = "🟢 매수" if fill.side is Side.BUY else "🔴 매도"
    return (
        f"{side} {fill.market}\n"
        f"체결가 {fill.price:,.0f}원 × {fill.volume:.8f}\n"
        f"금액 {fill.gross:,.0f}원 (수수료 {fill.fee:,.0f}원)\n"
        f"사유: {fill.reason or '-'}\n"
        f"평가자산 {state.equity:,.0f}원 (코인 비중 {state.weight:.0%})"
    )


def format_trade(trade: TradeRecord) -> str:
    mark = "✅" if trade.is_win else "❌"
    return (
        f"{mark} 청산 {trade.market} {trade.pnl:+,.0f}원 ({trade.pnl_pct:+.2%})\n"
        f"{trade.entry_price:,.0f} → {trade.exit_price:,.0f}"
    )
