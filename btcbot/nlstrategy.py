"""말로 설명한 전략을 조건으로 바꾼다 (Claude API 연동).

    "RSI가 30 아래로 내려가면 사고, 10% 오르면 팔아. 단 200일선 위일 때만."
        ↓
    entry: RSI(14) < 30  그리고  종가 > SMA(200)
    exit:  (익절 10%)

핵심 원칙 두 가지:

1. **모델이 만든 것을 그대로 실행하지 않는다.** 결과는 반드시
   `validate_spec()`을 통과해야 하고, 사용자에게 한국어로 무엇을 이해했는지
   보여준 뒤 사람이 확인해야 적용된다. 잘못 읽은 전략이 조용히 실거래에
   들어가는 것이 이 기능의 유일한 실패 모드다.

2. **표현할 수 없으면 지어내지 않는다.** 지원하지 않는 지표(예: 뉴스 감성,
   거래소 간 차익)를 요구받으면 `understood=false`로 돌려주고 무엇이
   부족한지 말한다. 비슷해 보이는 다른 조건으로 슬쩍 바꾸면 사용자는
   자기가 요청한 것과 다른 전략을 돌리게 된다.

UI 빌더가 만드는 구조와 똑같이 **평평한 조건 목록**만 생성한다. 중첩 그룹은
JSON을 직접 쓸 때만 쓰고, 여기서는 만들지 않는다 — 화면에서 편집할 수 없는
전략을 만들어내면 사용자가 손댈 수 없게 된다.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from .risk import RiskConfig
from .strategies.rule import (
    OPERAND_SPECS,
    OPERATOR_SPECS,
    SpecError,
    validate_spec,
)

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"
ENV_API_KEY = "ANTHROPIC_API_KEY"

#: 말로 지정할 수 있는 리스크 항목. 나머지(킬 스위치 등)는 화면에서만 만진다.
RISK_KEYS = ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct", "max_position_weight")


class TranslationError(RuntimeError):
    """사용자에게 그대로 보여줄 오류."""


@dataclass
class Translation:
    """변환 결과. `spec`이 None이면 만들지 못한 것이다."""

    understood: bool
    message: str
    spec: dict[str, Any] | None = None
    risk: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "understood": self.understood,
            "message": self.message,
            "spec": self.spec,
            "risk": self.risk,
            "warnings": self.warnings,
        }


# ------------------------------------------------------------------ 스키마
def _operand_schema() -> dict[str, Any]:
    # 구조화된 출력은 `required`에 모든 속성이 들어가야 한다. 그래서 지표와
    # 무관한 값도 항상 채워지지만, validate_spec이 타입별로 필요한 것만
    # 골라 쓰므로 남는 값은 버려진다.
    return {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": [spec["type"] for spec in OPERAND_SPECS],
                "description": "지표 종류",
            },
            "period": {"type": "integer", "description": "기간. 기간이 없는 지표면 0"},
            "value": {"type": "number", "description": "type이 number일 때의 값. 아니면 0"},
            "mult": {"type": "number", "description": "볼린저 밴드 배수. 아니면 0"},
            "k": {"type": "number", "description": "변동성 돌파 계수. 아니면 0"},
        },
        "required": ["type", "period", "value", "mult", "k"],
        "additionalProperties": False,
    }


def _condition_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "left": _operand_schema(),
            "op": {"type": "string", "enum": [spec["op"] for spec in OPERATOR_SPECS]},
            "right": _operand_schema(),
        },
        "required": ["left", "op", "right"],
        "additionalProperties": False,
    }


def response_schema() -> dict[str, Any]:
    condition = _condition_schema()
    return {
        "type": "object",
        "properties": {
            "understood": {
                "type": "boolean",
                "description": "요청을 지원되는 조건으로 표현할 수 있으면 true",
            },
            "message": {
                "type": "string",
                "description": "사용자에게 보여줄 한국어 설명. 이해한 내용을 한두 문장으로 요약하거나, "
                "표현할 수 없다면 무엇이 부족한지 설명한다.",
            },
            "label": {"type": "string", "description": "전략 이름 (짧은 한국어)"},
            "note": {"type": "string", "description": "전략을 한 줄로 설명하는 메모"},
            "target_weight": {
                "type": "number",
                "description": "매수 시 총자산 대비 비중. 0 초과 1 이하. 언급이 없으면 1",
            },
            "entry_join": {"type": "string", "enum": ["all", "any"]},
            "entry": {"type": "array", "items": condition},
            "exit_join": {"type": "string", "enum": ["all", "any"]},
            "exit": {"type": "array", "items": condition},
            "risk": {
                "type": "object",
                "properties": {
                    "stop_loss_pct": {"type": "number", "description": "손절 비율. 0.05 = -5%. 없으면 0"},
                    "take_profit_pct": {"type": "number", "description": "익절 비율. 없으면 0"},
                    "trailing_stop_pct": {"type": "number", "description": "트레일링 스탑. 없으면 0"},
                    "max_position_weight": {"type": "number", "description": "최대 비중. 없으면 1"},
                },
                "required": list(RISK_KEYS),
                "additionalProperties": False,
            },
            "warnings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "사용자가 알아야 할 주의사항(추측해서 채운 값, 위험한 설정 등). 한국어.",
            },
        },
        "required": [
            "understood", "message", "label", "note", "target_weight",
            "entry_join", "entry", "exit_join", "exit", "risk", "warnings",
        ],
        "additionalProperties": False,
    }


def system_prompt() -> str:
    operands = "\n".join(
        f"  - {s['type']}: {s['label']}"
        + (f" (파라미터: {', '.join(p['key'] for p in s['params'])})" if s["params"] else "")
        for s in OPERAND_SPECS
    )
    operators = "\n".join(f"  - {s['op']}: {s['label']}" for s in OPERATOR_SPECS)

    return f"""당신은 한국어로 설명된 암호화폐 매매 전략을 정해진 조건 형식으로 옮기는 번역기입니다.
업비트 원화 마켓에서 도는 자동매매 봇이 이 결과로 실제 주문을 냅니다.

# 쓸 수 있는 지표
{operands}

# 쓸 수 있는 비교
{operators}

# 규칙

1. 사용자가 말한 것만 옮깁니다. 말하지 않은 조건을 보태지 마세요.
   "RSI 30 아래면 매수"라고만 했으면 추세 필터를 마음대로 넣지 않습니다.

2. 목록에 없는 지표나 개념(뉴스, 거래량 급증률, 다른 거래소 가격, 김치프리미엄,
   호가창, 체결강도 등)을 요구하면 understood=false로 돌려주고, message에
   무엇을 지원하지 않는지와 대신 무엇을 쓸 수 있는지 적으세요.
   비슷해 보이는 다른 지표로 몰래 바꾸지 마세요. 사용자는 자기가 요청한 것과
   다른 전략이 돌아가는 줄 모릅니다.

3. "몇 % 오르면 판다 / 떨어지면 손절" 같은 말은 조건이 아니라 risk에 넣습니다.
   - "10% 오르면 팔아" → risk.take_profit_pct = 0.10
   - "5% 빠지면 손절" → risk.stop_loss_pct = 0.05
   - "고점 대비 7% 빠지면" → risk.trailing_stop_pct = 0.07
   이런 값은 진입가 대비로 계산되므로 exit 조건으로 옮기면 안 됩니다.

4. 기간을 말하지 않으면 통용되는 기본값을 쓰고, warnings에 무엇을 가정했는지
   적으세요. (RSI 14, 이동평균은 사용자가 말한 일수)
   "5일선", "20일 이동평균" 같은 말은 period로 옮깁니다.

5. exit 조건이 없어도 됩니다(리스크 설정만으로 청산할 수 있음). 다만 진입도
   청산도 없으면 안 됩니다.

6. "골든크로스"는 cross_above, "데드크로스"는 cross_below입니다.
   단순히 "위에 있으면"은 > 입니다. 돌파(순간)와 상태(계속)를 구분하세요.

7. 여러 조건을 "그리고"로 이으면 entry_join="all", "또는"이면 "any"입니다.

8. message는 반드시 한국어로, 사용자가 자기 전략이 제대로 옮겨졌는지 확인할 수
   있게 씁니다. 지표 이름과 숫자를 그대로 언급하세요.

9. 위험해 보이는 설정(손절 없이 전액 매수 등)은 warnings에 적으세요."""


# ------------------------------------------------------------------ 변환
def translate(
    text: str,
    *,
    client: Any = None,
    model: str = MODEL,
    api_key: str | None = None,
) -> Translation:
    """자연어 설명을 규칙 전략으로 옮긴다.

    `client`를 주면 그대로 쓴다(테스트용). 아니면 환경변수의 키로 만든다.
    """
    text = (text or "").strip()
    if not text:
        raise TranslationError("전략 설명을 입력하세요")
    if len(text) > 4000:
        raise TranslationError("설명이 너무 깁니다. 4000자 이내로 줄여주세요.")

    client = client or build_client(api_key)
    raw = _ask(client, model, text)
    return _to_translation(raw)


def build_client(api_key: str | None = None) -> Any:
    try:
        import anthropic
    except ImportError:
        raise TranslationError(
            "이 기능을 쓰려면 anthropic 패키지가 필요합니다: pip install anthropic"
        ) from None

    key = api_key or os.getenv(ENV_API_KEY)
    if not key:
        raise TranslationError(
            f"Claude API 키가 없습니다. {ENV_API_KEY}를 환경변수나 .env에 넣어주세요. "
            "키는 https://console.anthropic.com 에서 발급합니다."
        )
    return anthropic.Anthropic(api_key=key)


def _ask(client: Any, model: str, text: str) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": 16000,
        "system": system_prompt(),
        "messages": [{"role": "user", "content": text}],
        "output_config": {"format": {"type": "json_schema", "schema": response_schema()}},
        "thinking": {"type": "adaptive"},
    }

    try:
        # 안전 분류기가 요청을 거절하면 다른 모델로 이어받게 한다.
        response = client.beta.messages.create(
            **request,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except Exception as exc:
        if not _is_bad_request(exc):
            raise TranslationError(f"Claude API 호출 실패: {exc}") from exc
        # 이 계정/배포에서 폴백 베타를 못 쓰는 경우 한 번만 그냥 호출한다.
        log.info("서버측 폴백을 쓸 수 없어 일반 호출로 재시도합니다: %s", exc)
        try:
            response = client.messages.create(**request)
        except Exception as retry_exc:
            raise TranslationError(f"Claude API 호출 실패: {retry_exc}") from retry_exc

    if getattr(response, "stop_reason", None) == "refusal":
        raise TranslationError(
            "Claude가 이 요청에 답하지 않았습니다. 전략 설명을 다시 써 보세요."
        )

    text_block = next(
        (b.text for b in response.content if getattr(b, "type", None) == "text"), None
    )
    if not text_block:
        raise TranslationError("Claude 응답이 비어 있습니다. 잠시 후 다시 시도하세요.")

    try:
        return json.loads(text_block)
    except json.JSONDecodeError as exc:
        raise TranslationError(f"Claude 응답을 해석하지 못했습니다: {exc}") from exc


def _is_bad_request(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 400 or type(exc).__name__ == "BadRequestError"


def _to_translation(raw: dict[str, Any]) -> Translation:
    warnings = [str(w) for w in raw.get("warnings") or []]
    message = str(raw.get("message") or "").strip()

    if not raw.get("understood"):
        return Translation(
            understood=False,
            message=message or "이 설명은 지원하는 조건으로 옮길 수 없습니다.",
            warnings=warnings,
        )

    spec: dict[str, Any] = {
        "label": str(raw.get("label") or "").strip() or "이름 없는 전략",
        "note": str(raw.get("note") or "").strip(),
        "target_weight": _weight(raw.get("target_weight")),
    }
    for side in ("entry", "exit"):
        conditions = [_clean_condition(c) for c in raw.get(side) or []]
        if conditions:
            join = raw.get(f"{side}_join") or ("all" if side == "entry" else "any")
            spec[side] = {join if join in ("all", "any") else "all": conditions}

    risk = _clean_risk(raw.get("risk") or {})

    # 모델이 만든 것을 그대로 믿지 않는다. 우리 검증기를 통과해야만 내보낸다.
    try:
        validate_spec(spec)
    except SpecError as exc:
        if not spec.get("entry") and not spec.get("exit") and risk:
            return Translation(
                understood=False,
                message=(
                    "손절·익절 같은 리스크 설정만 있고 언제 사고팔지가 없습니다. "
                    "'RSI가 30 아래로 내려가면 산다'처럼 진입 조건을 함께 말해주세요."
                ),
                risk=risk,
                warnings=warnings,
            )
        return Translation(
            understood=False,
            message=f"조건을 만들었지만 검증에 실패했습니다: {exc}",
            warnings=warnings,
        )

    return Translation(
        understood=True,
        message=message or "전략을 만들었습니다.",
        spec=spec,
        risk=risk,
        warnings=warnings,
    )


def _clean_condition(cond: Any) -> dict[str, Any]:
    if not isinstance(cond, dict):
        raise TranslationError(f"조건 형식이 올바르지 않습니다: {cond!r}")
    return {
        "left": _clean_operand(cond.get("left")),
        "op": cond.get("op"),
        "right": _clean_operand(cond.get("right")),
    }


def _clean_operand(operand: Any) -> dict[str, Any]:
    """스키마가 강제로 채운 무의미한 값을 걷어낸다.

    구조화된 출력은 모든 속성을 요구하므로 종가에도 period=0이 붙어 온다.
    그대로 두면 검증기가 기간 0을 거부한다.
    """
    if not isinstance(operand, dict):
        raise TranslationError(f"지표 형식이 올바르지 않습니다: {operand!r}")

    kind = operand.get("type")
    spec = next((s for s in OPERAND_SPECS if s["type"] == kind), None)
    if spec is None:
        raise TranslationError(f"지원하지 않는 지표입니다: {kind!r}")

    out: dict[str, Any] = {"type": kind}
    if kind == "number":
        out["value"] = float(operand.get("value", 0))
        return out
    for param in spec["params"]:
        key = param["key"]
        value = operand.get(key)
        # 0이나 누락은 기본값으로 되돌린다 (기간 0은 유효한 값이 아니다)
        out[key] = value if isinstance(value, (int, float)) and value else param["default"]
    return out


def _clean_risk(risk: Any) -> dict[str, float]:
    if not isinstance(risk, dict):
        return {}
    out: dict[str, float] = {}
    for key in RISK_KEYS:
        try:
            value = float(risk.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
        if key == "max_position_weight":
            if 0 < value < 1:  # 1은 '제한 없음'이라 굳이 전달하지 않는다
                out[key] = value
        elif 0 < value < 1:
            out[key] = value

    # 우리 리스크 설정이 받아들이는 값인지 확인한다.
    try:
        RiskConfig(**out)
    except ValueError as exc:
        log.warning("리스크 값이 범위를 벗어나 무시합니다: %s", exc)
        return {}
    return out


def _weight(value: Any) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError):
        return 1.0
    return weight if 0 < weight <= 1 else 1.0
