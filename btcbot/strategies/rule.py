"""규칙 기반 전략 — 코드 없이 조건을 조합해 만드는 전략.

    "RSI(14)가 30보다 작다"  그리고  "종가가 SMA(200)보다 크다"  → 매수
    "RSI(14)가 60보다 크다"                                    → 청산

이런 조건 묶음을 JSON으로 표현하고, 그대로 매매 신호로 바꾼다.
웹 UI의 전략 빌더가 만들어내는 것이 정확히 이 JSON이다.

설계 메모: 모든 피연산자를 '값 하나'가 아니라 '시계열'로 계산한다. 그래야
골든크로스처럼 직전 봉과 비교해야 하는 조건(cross_above)을 같은 방식으로
처리할 수 있다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..indicators import atr_series, ema_series, rsi_series, sma_series, stddev
from ..models import Action, Candle, Signal
from .base import Strategy, register

# ---------------------------------------------------------------- 피연산자 정의
#: UI가 그대로 읽어 폼을 그리는 메타데이터.
#: label은 화면에 보일 한글 이름, params는 사용자가 채워야 할 값.
OPERAND_SPECS: list[dict[str, Any]] = [
    {"type": "close", "label": "종가", "params": []},
    {"type": "open", "label": "시가", "params": []},
    {"type": "high", "label": "고가", "params": []},
    {"type": "low", "label": "저가", "params": []},
    {"type": "volume", "label": "거래량", "params": []},
    {"type": "number", "label": "숫자(직접 입력)", "params": [{"key": "value", "label": "값", "default": 0}]},
    {"type": "sma", "label": "단순이동평균 SMA", "params": [{"key": "period", "label": "기간", "default": 20}]},
    {"type": "ema", "label": "지수이동평균 EMA", "params": [{"key": "period", "label": "기간", "default": 20}]},
    {"type": "rsi", "label": "RSI", "params": [{"key": "period", "label": "기간", "default": 14}]},
    {"type": "atr", "label": "ATR(변동폭)", "params": [{"key": "period", "label": "기간", "default": 14}]},
    {
        "type": "bb_upper",
        "label": "볼린저 상단",
        "params": [{"key": "period", "label": "기간", "default": 20}, {"key": "mult", "label": "배수", "default": 2}],
    },
    {
        "type": "bb_mid",
        "label": "볼린저 중심",
        "params": [{"key": "period", "label": "기간", "default": 20}],
    },
    {
        "type": "bb_lower",
        "label": "볼린저 하단",
        "params": [{"key": "period", "label": "기간", "default": 20}, {"key": "mult", "label": "배수", "default": 2}],
    },
    {"type": "highest", "label": "N봉 최고가", "params": [{"key": "period", "label": "기간", "default": 20}]},
    {"type": "lowest", "label": "N봉 최저가", "params": [{"key": "period", "label": "기간", "default": 20}]},
    {
        "type": "vb_target",
        "label": "변동성 돌파 목표가",
        "params": [{"key": "k", "label": "k 계수", "default": 0.5}],
    },
]

OPERAND_TYPES = {spec["type"] for spec in OPERAND_SPECS}

#: 비교 연산자. UI의 드롭다운이 이 목록을 그대로 쓴다.
OPERATOR_SPECS: list[dict[str, str]] = [
    {"op": ">", "label": "보다 크다"},
    {"op": ">=", "label": "보다 크거나 같다"},
    {"op": "<", "label": "보다 작다"},
    {"op": "<=", "label": "보다 작거나 같다"},
    {"op": "cross_above", "label": "위로 돌파 (골든크로스)"},
    {"op": "cross_below", "label": "아래로 이탈 (데드크로스)"},
]

OPERATORS = {spec["op"] for spec in OPERATOR_SPECS}


class SpecError(ValueError):
    """전략 정의가 잘못됐을 때. 메시지는 UI에 그대로 보여줄 수 있게 쓴다."""


@register
class RuleStrategy(Strategy):
    """조건을 조합해 만드는 전략 (웹 UI 전략 빌더용)."""

    name = "rule"

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {
            "spec": None,  # dict — 아래 validate_spec() 형식
            "spec_file": None,  # 또는 JSON 파일 경로
        }

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        spec = self.params["spec"]
        if spec is None and self.params["spec_file"]:
            import json
            from pathlib import Path

            spec = json.loads(Path(self.params["spec_file"]).read_text(encoding="utf-8"))
        if spec is None:
            raise SpecError("전략 정의(spec 또는 spec_file)가 필요합니다")

        self.spec = validate_spec(spec)
        self.warmup = required_warmup(self.spec)
        self._operands = _collect_operands(self.spec)
        self._prepared: Sequence[Candle] | None = None
        self._prepared_cache: dict[str, list[float | None]] = {}

    def describe(self) -> str:
        return f"rule({self.spec.get('label') or '이름 없음'})"

    def prepare(self, candles: Sequence[Candle]) -> None:
        """모든 지표를 한 번에 계산해둔다 (백테스트 O(n^2) -> O(n))."""
        if len(candles) < 2:
            return
        self._prepared = list(candles)
        self._prepared_cache = {}
        for operand in self._operands:
            series_for(operand, self._prepared, self._prepared_cache)

    def _evaluation_context(
        self, candles: Sequence[Candle]
    ) -> tuple[Sequence[Candle], dict[str, list[float | None]], int]:
        """(시계열을 계산한 봉들, 캐시, 지금 판단하는 봉의 위치).

        미리 계산해둔 값은 지금 받은 봉들이 그때 그 봉들의 **앞부분**일
        때만 쓴다. 다른 데이터로 같은 전략 객체를 재사용하면 캐시를 버린다.
        """
        prepared = self._prepared
        n = len(candles)
        if prepared is not None and 0 < n <= len(prepared) and _same_prefix(candles, prepared, n):
            return prepared, self._prepared_cache, n - 1
        return candles, {}, n - 1

    def decide(self, candles: Sequence[Candle]) -> Signal:
        if len(candles) < self.warmup:
            return Signal(reason="warmup")

        source, cache, index = self._evaluation_context(candles)
        exit_group = self.spec.get("exit")
        entry_group = self.spec.get("entry")

        # 청산이 진입보다 우선한다. 둘 다 참인 애매한 상황에서 계속 들고 있는
        # 것보다 빠져나오는 쪽이 안전하다.
        if exit_group:
            hit, why = evaluate(exit_group, source, cache, index)
            if hit:
                return Signal(action=Action.SELL, target_weight=0.0, reason=f"청산: {why}")

        if entry_group:
            hit, why = evaluate(entry_group, source, cache, index)
            if hit:
                return Signal(
                    action=Action.BUY,
                    target_weight=float(self.spec.get("target_weight", 1.0)),
                    reason=f"진입: {why}",
                )

        return Signal(reason="조건 미충족 — 현재 상태 유지")


# ------------------------------------------------------------------ 검증
def validate_spec(spec: Any) -> dict[str, Any]:
    """전략 정의를 검사하고 정규화한다.

    UI에서 잘못 만든 전략이 백테스트 도중에 터지는 대신, 저장하는 순간
    한글 메시지로 막히게 하는 것이 목적이다.
    """
    if not isinstance(spec, dict):
        raise SpecError("전략 정의는 객체여야 합니다")

    if not spec.get("entry") and not spec.get("exit"):
        raise SpecError("진입 조건과 청산 조건 중 최소 하나는 있어야 합니다")

    weight = spec.get("target_weight", 1.0)
    if not isinstance(weight, (int, float)) or not 0 < float(weight) <= 1:
        raise SpecError("매수 비중은 0 초과 1 이하의 숫자여야 합니다")

    out: dict[str, Any] = {
        "label": str(spec.get("label") or "").strip(),
        "target_weight": float(weight),
    }
    for key in ("entry", "exit"):
        if spec.get(key):
            out[key] = _validate_group(spec[key], f"{'진입' if key == 'entry' else '청산'} 조건")
    return out


def _validate_group(group: Any, where: str) -> dict[str, Any]:
    if not isinstance(group, dict):
        raise SpecError(f"{where}: 조건 묶음은 객체여야 합니다")

    for key in ("all", "any"):
        if key in group:
            items = group[key]
            if not isinstance(items, list) or not items:
                raise SpecError(f"{where}: '{key}' 안에 조건이 최소 하나 있어야 합니다")
            return {key: [_validate_node(item, where) for item in items]}

    if "not" in group:
        return {"not": _validate_node(group["not"], where)}

    raise SpecError(f"{where}: 'all', 'any', 'not' 중 하나가 필요합니다")


def _validate_node(node: Any, where: str) -> dict[str, Any]:
    if isinstance(node, dict) and any(k in node for k in ("all", "any", "not")):
        return _validate_group(node, where)
    return _validate_condition(node, where)


def _validate_condition(cond: Any, where: str) -> dict[str, Any]:
    if not isinstance(cond, dict):
        raise SpecError(f"{where}: 조건은 객체여야 합니다")

    op = cond.get("op")
    if op not in OPERATORS:
        raise SpecError(f"{where}: 알 수 없는 비교 '{op}'")

    return {
        "left": _validate_operand(cond.get("left"), where),
        "op": op,
        "right": _validate_operand(cond.get("right"), where),
    }


def _validate_operand(operand: Any, where: str) -> dict[str, Any]:
    if isinstance(operand, (int, float)):  # 숫자를 그냥 쓴 경우 관대하게 받아준다
        return {"type": "number", "value": float(operand)}
    if not isinstance(operand, dict):
        raise SpecError(f"{where}: 값은 객체 또는 숫자여야 합니다")

    kind = operand.get("type")
    if kind not in OPERAND_TYPES:
        raise SpecError(f"{where}: 알 수 없는 지표 '{kind}'")

    out: dict[str, Any] = {"type": kind}
    if kind == "number":
        try:
            out["value"] = float(operand.get("value", 0))
        except (TypeError, ValueError):
            raise SpecError(f"{where}: 숫자 값이 올바르지 않습니다") from None
        return out

    spec = next(s for s in OPERAND_SPECS if s["type"] == kind)
    for param in spec["params"]:
        key = param["key"]
        value = operand.get(key, param["default"])
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise SpecError(f"{where}: {spec['label']}의 {param['label']} 값이 올바르지 않습니다") from None
        if key == "period":
            if value < 1:
                raise SpecError(f"{where}: {spec['label']}의 기간은 1 이상이어야 합니다")
            value = int(value)
        out[key] = value
    return out


def _same_prefix(candles: Sequence[Candle], prepared: Sequence[Candle], n: int) -> bool:
    """`candles`가 `prepared`의 앞 n개와 같은 봉들인지.

    시각만 비교하면 안 된다. 다른 종목의 같은 기간 봉은 시각이 완전히
    똑같아서(예: BTC 일봉과 ETH 일봉) 엉뚱한 지표값을 재사용하게 된다.
    백테스트는 같은 리스트를 잘라 쓰므로 객체 자체가 동일하다 — 그걸 본다.
    아니면 그냥 다시 계산하면 되므로 틀릴 때의 비용도 없다.
    """
    return (
        candles[0] is prepared[0]
        and candles[n - 1] is prepared[n - 1]
        and candles[n // 2] is prepared[n // 2]
    )


def _collect_operands(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """정의에 나오는 모든 지표를 모은다 (미리 계산용)."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        for key in ("all", "any"):
            if key in node:
                for item in node[key]:
                    walk(item)
                return
        if "not" in node:
            walk(node["not"])
            return
        for side in ("left", "right"):
            operand = node.get(side)
            if isinstance(operand, dict):
                found.append(operand)

    for key in ("entry", "exit"):
        if spec.get(key):
            walk(spec[key])
    return found


def required_warmup(spec: dict[str, Any]) -> int:
    """정의에 등장하는 가장 긴 기간 + 여유분."""
    longest = 0

    def walk(node: Any) -> None:
        nonlocal longest
        if not isinstance(node, dict):
            return
        for key in ("all", "any"):
            if key in node:
                for item in node[key]:
                    walk(item)
                return
        if "not" in node:
            walk(node["not"])
            return
        for side in ("left", "right"):
            operand = node.get(side)
            if isinstance(operand, dict):
                longest = max(longest, int(operand.get("period", 0)))

    for key in ("entry", "exit"):
        if spec.get(key):
            walk(spec[key])
    # cross 비교는 직전 봉 값도 필요하므로 최소 2봉은 확보한다.
    return max(longest + 2, 3)


# ------------------------------------------------------------------ 평가
def evaluate(
    group: dict[str, Any],
    candles: Sequence[Candle],
    cache: dict[str, list[float | None]] | None = None,
    index: int | None = None,
) -> tuple[bool, str]:
    """조건 묶음을 평가하고 (결과, 사람이 읽을 이유)를 돌려준다.

    `index`는 '지금 판단하는 봉'의 위치다. 미리 계산해둔 긴 시계열을
    그대로 쓰면서 특정 시점만 보기 위해 필요하다 (자르면 그 자체가
    봉마다 O(n) 복사라 최적화가 무의미해진다).
    """
    cache = {} if cache is None else cache
    if index is None:
        index = len(candles) - 1

    if "all" in group:
        reasons = []
        for node in group["all"]:
            hit, why = _evaluate_node(node, candles, cache, index)
            if not hit:
                return False, why
            reasons.append(why)
        return True, " 그리고 ".join(reasons)

    if "any" in group:
        reasons = []
        for node in group["any"]:
            hit, why = _evaluate_node(node, candles, cache, index)
            if hit:
                return True, why
            reasons.append(why)
        return False, " 또는 ".join(reasons)

    hit, why = _evaluate_node(group["not"], candles, cache, index)
    return not hit, f"NOT({why})"


def _evaluate_node(
    node: dict[str, Any],
    candles: Sequence[Candle],
    cache: dict[str, list[float | None]],
    index: int,
) -> tuple[bool, str]:
    if any(key in node for key in ("all", "any", "not")):
        return evaluate(node, candles, cache, index)
    return _evaluate_condition(node, candles, cache, index)


def _evaluate_condition(
    cond: dict[str, Any],
    candles: Sequence[Candle],
    cache: dict[str, list[float | None]],
    index: int,
) -> tuple[bool, str]:
    left = series_for(cond["left"], candles, cache)
    right = series_for(cond["right"], candles, cache)
    op = cond["op"]

    label = f"{describe_operand(cond['left'])} {_op_label(op)} {describe_operand(cond['right'])}"

    lnow, rnow = left[index], right[index]
    if lnow is None or rnow is None:
        return False, f"{label} (값 없음)"

    detail = f"{label} [{_fmt(lnow)} vs {_fmt(rnow)}]"

    if op == ">":
        return lnow > rnow, detail
    if op == ">=":
        return lnow >= rnow, detail
    if op == "<":
        return lnow < rnow, detail
    if op == "<=":
        return lnow <= rnow, detail

    if index < 1:
        return False, f"{label} (직전 봉 없음)"
    lprev, rprev = left[index - 1], right[index - 1]
    if lprev is None or rprev is None:
        return False, f"{label} (직전 값 없음)"

    if op == "cross_above":
        return lprev <= rprev and lnow > rnow, detail
    return lprev >= rprev and lnow < rnow, detail


def _op_label(op: str) -> str:
    return next(spec["label"] for spec in OPERATOR_SPECS if spec["op"] == op)


def describe_operand(operand: dict[str, Any]) -> str:
    kind = operand["type"]
    if kind == "number":
        return _fmt(operand["value"])
    spec = next(s for s in OPERAND_SPECS if s["type"] == kind)
    args = [str(operand[p["key"]]) for p in spec["params"] if p["key"] in operand]
    return f"{spec['label']}({','.join(args)})" if args else spec["label"]


def _fmt(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


# ------------------------------------------------------------ 시계열 계산
def series_for(
    operand: dict[str, Any], candles: Sequence[Candle], cache: dict[str, list[float | None]]
) -> list[float | None]:
    """피연산자를 캔들 길이와 같은 시계열로 만든다."""
    key = repr(sorted(operand.items()))
    if key in cache:
        return cache[key]

    kind = operand["type"]
    n = len(candles)

    if kind == "number":
        out: list[float | None] = [float(operand["value"])] * n
    elif kind in ("close", "open", "high", "low", "volume"):
        out = [float(getattr(c, kind)) for c in candles]
    elif kind == "sma":
        out = sma_series([c.close for c in candles], int(operand["period"]))
    elif kind == "ema":
        out = ema_series([c.close for c in candles], int(operand["period"]))
    elif kind == "rsi":
        out = rsi_series([c.close for c in candles], int(operand["period"]))
    elif kind == "atr":
        out = atr_series(candles, int(operand["period"]))
    elif kind in ("bb_upper", "bb_mid", "bb_lower"):
        out = _bollinger_series(candles, operand, kind)
    elif kind == "highest":
        out = _window_series(candles, int(operand["period"]), high=True)
    elif kind == "lowest":
        out = _window_series(candles, int(operand["period"]), high=False)
    elif kind == "vb_target":
        out = _vb_target_series(candles, float(operand["k"]))
    else:  # pragma: no cover - validate_spec이 먼저 막는다
        raise SpecError(f"알 수 없는 지표 '{kind}'")

    cache[key] = out
    return out


def _bollinger_series(
    candles: Sequence[Candle], operand: dict[str, Any], kind: str
) -> list[float | None]:
    period = int(operand["period"])
    mult = float(operand.get("mult", 2))
    closes = [c.close for c in candles]
    mids = sma_series(closes, period)

    out: list[float | None] = []
    for i, mid in enumerate(mids):
        if mid is None:
            out.append(None)
            continue
        if kind == "bb_mid":
            out.append(mid)
            continue
        sd = stddev(closes[: i + 1], period)
        if sd is None:
            out.append(None)
        else:
            out.append(mid + mult * sd if kind == "bb_upper" else mid - mult * sd)
    return out


def _window_series(candles: Sequence[Candle], period: int, high: bool) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(candles)):
        if i + 1 < period:
            out.append(None)
            continue
        window = candles[i + 1 - period : i + 1]
        out.append(max(c.high for c in window) if high else min(c.low for c in window))
    return out


def _vb_target_series(candles: Sequence[Candle], k: float) -> list[float | None]:
    """봉마다 '그날 시가 + 전일 변동폭 × k'를 계산한다."""
    # 날짜 판정은 봉당 한 번만 한다. 예전에는 kst_date(문자열 포맷팅)를
    # 봉마다 두 번씩 불렀는데, 프로파일링에서 그 strftime 하나가 전체
    # 실행 시간의 68%였다.
    days = [c.kst_day for c in candles]

    day_open: dict[int, float] = {}
    day_high: dict[int, float] = {}
    day_low: dict[int, float] = {}
    prev_day: dict[int, int] = {}
    last_day: int | None = None

    for candle, day in zip(candles, days, strict=True):
        if day not in day_open:
            day_open[day] = candle.open
            day_high[day] = candle.high
            day_low[day] = candle.low
            prev_day[day] = last_day if last_day is not None else day
            last_day = day
        else:
            if candle.high > day_high[day]:
                day_high[day] = candle.high
            if candle.low < day_low[day]:
                day_low[day] = candle.low

    out: list[float | None] = []
    for day in days:
        prev = prev_day[day]
        if prev == day:  # 첫날은 전일이 없다
            out.append(None)
            continue
        prev_range = day_high[prev] - day_low[prev]
        out.append(day_open[day] + prev_range * k if prev_range > 0 else None)
    return out


def builder_metadata() -> dict[str, Any]:
    """웹 UI 전략 빌더가 폼을 그리는 데 필요한 전부."""
    return {"operands": OPERAND_SPECS, "operators": OPERATOR_SPECS}


#: 처음 열었을 때 보여줄 예시들. 빈 화면에서 시작하면 뭘 해야 할지 모른다.
PRESETS: list[dict[str, Any]] = [
    {
        "label": "RSI 과매도 반등",
        "note": "RSI가 30 아래로 내려가면 사고, 55를 넘으면 판다. 장기 상승추세일 때만.",
        "target_weight": 1.0,
        "entry": {
            "all": [
                {"left": {"type": "rsi", "period": 14}, "op": "<", "right": {"type": "number", "value": 30}},
                {"left": {"type": "close"}, "op": ">", "right": {"type": "sma", "period": 200}},
            ]
        },
        "exit": {
            "any": [
                {"left": {"type": "rsi", "period": 14}, "op": ">", "right": {"type": "number", "value": 55}}
            ]
        },
    },
    {
        "label": "골든크로스 추세추종",
        "note": "단기 이평이 장기 이평을 위로 뚫으면 사고, 아래로 이탈하면 판다.",
        # 처음 화면에 열어둘 예시. 거래가 자주 나와서 결과를 바로 확인할 수 있다.
        "default": True,
        "target_weight": 1.0,
        "entry": {
            "all": [
                {"left": {"type": "ema", "period": 10}, "op": "cross_above", "right": {"type": "ema", "period": 30}}
            ]
        },
        "exit": {
            "any": [
                {"left": {"type": "ema", "period": 10}, "op": "cross_below", "right": {"type": "ema", "period": 30}}
            ]
        },
    },
    {
        "label": "변동성 돌파",
        "note": "종가가 '오늘 시가 + 전일 변동폭 × 0.5'를 넘으면 산다. 못 넘으면 현금.",
        "target_weight": 1.0,
        "entry": {
            "all": [{"left": {"type": "close"}, "op": ">", "right": {"type": "vb_target", "k": 0.5}}]
        },
        "exit": {
            "any": [{"left": {"type": "close"}, "op": "<=", "right": {"type": "vb_target", "k": 0.5}}]
        },
    },
    {
        "label": "볼린저 하단 매수",
        "note": "가격이 볼린저 하단 아래로 빠지면 사고, 중심선을 회복하면 판다.",
        "target_weight": 1.0,
        "entry": {
            "all": [{"left": {"type": "close"}, "op": "<", "right": {"type": "bb_lower", "period": 20, "mult": 2}}]
        },
        "exit": {
            "any": [{"left": {"type": "close"}, "op": ">", "right": {"type": "bb_mid", "period": 20}}]
        },
    },
]
