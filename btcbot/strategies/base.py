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

    def prepare(self, candles: Sequence[Candle]) -> None:
        """백테스트 시작 전에 한 번 불린다. 미리 계산해둘 게 있으면 여기서.

        백테스트는 `decide()`를 봉 개수만큼 부르는데, 매번 지표를 처음부터
        다시 계산하면 전체가 O(n^2)이 된다. 여기서 미리 계산해두면 O(n)이다.

        **중요**: 여기서 받는 `candles`에는 미래 봉이 들어있다. 지표는
        인과적(i번째 값이 i 이전 봉에만 의존)이어야만 미리 계산한 값을
        써도 미래를 훔쳐보지 않는다. 인과적이지 않은 지표를 추가한다면
        이 최적화를 쓰면 안 된다.

        라이브에서는 부르지 않는다 — 거기서는 봉마다 한 번씩만 판단하므로
        미리 계산할 이유가 없다.
        """
        return  # 기본은 아무것도 안 함 (선택적 훅)

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
