"""전략 인터페이스와 레지스트리."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar

from ..models import Candle, Signal


class Strategy(ABC):
    """전략의 계약.

    - `decide()`는 **이미 종료된 봉들만** 받는다. candles[-1]은 마지막으로
      닫힌 봉이고, 실제 체결은 그 다음 봉의 시가에서 일어난다고 가정한다.
      백테스트와 실거래가 동일한 가정 위에서 돌기 때문에 미래 참조
      (look-ahead bias)가 구조적으로 발생하지 않는다.
    - `decide()`는 순수 함수처럼 동작해야 한다. 내부 상태를 쌓아두면
      백테스트를 재실행할 때 결과가 달라진다. 상태가 필요하면
      `warmup` 만큼의 봉에서 매번 다시 계산하라.
    """

    name: ClassVar[str] = "base"
    #: 판단에 필요한 최소 봉 개수
    warmup: int = 1

    def __init__(self, **params: Any) -> None:
        unknown = set(params) - set(self.defaults())
        if unknown:
            raise ValueError(f"{self.name}: 알 수 없는 파라미터 {sorted(unknown)}")
        self.params: dict[str, Any] = {**self.defaults(), **params}

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {}

    @abstractmethod
    def decide(self, candles: Sequence[Candle]) -> Signal:
        """마지막 봉 기준으로 목표 비중을 결정한다."""

    def describe(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.name}({args})"

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<{self.describe()}>"


_REGISTRY: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    key = cls.name.lower()
    if key in _REGISTRY and _REGISTRY[key] is not cls:
        raise ValueError(f"중복된 전략 이름: {cls.name}")
    _REGISTRY[key] = cls
    return cls


def get_strategy(name: str, **params: Any) -> Strategy:
    key = name.lower()
    if key not in _REGISTRY:
        raise KeyError(f"모르는 전략 '{name}'. 사용 가능: {', '.join(available())}")
    return _REGISTRY[key](**params)


def available() -> list[str]:
    return sorted(_REGISTRY)


def strategy_class(name: str) -> type[Strategy]:
    key = name.lower()
    if key not in _REGISTRY:
        raise KeyError(f"모르는 전략 '{name}'. 사용 가능: {', '.join(available())}")
    return _REGISTRY[key]
