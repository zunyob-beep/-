"""자연어 → 조건 변환 검증 (Claude API 호출 없이).

이 기능의 유일한 실패 모드는 "잘못 읽은 전략이 조용히 적용되는 것"이다.
그래서 모델이 뭘 돌려주든 우리 검증기를 통과해야만 spec이 나가는지,
표현할 수 없는 요청을 지어내지 않고 거절하는지를 집중적으로 본다.
"""

from __future__ import annotations

import json

import pytest

from btcbot.nlstrategy import (
    ENV_API_KEY,
    Translation,
    TranslationError,
    _clean_operand,
    _clean_risk,
    build_client,
    response_schema,
    system_prompt,
    translate,
)
from btcbot.strategies import get_strategy
from btcbot.strategies.rule import OPERAND_SPECS, OPERATOR_SPECS


# ------------------------------------------------------------------ 가짜 API
class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class RawResponse:
    """content를 직접 지정하는 응답 (형식이 깨진 경우 시험용)."""

    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class FakeResponse:
    def __init__(self, payload, stop_reason="end_turn"):
        self.content = [FakeBlock(json.dumps(payload, ensure_ascii=False))]
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, owner, beta=False):
        self.owner = owner
        self.beta = beta

    def create(self, **kwargs):
        self.owner.calls.append({"beta": self.beta, **kwargs})
        if self.beta and self.owner.beta_error:
            raise self.owner.beta_error
        result = self.owner.result
        if isinstance(result, Exception):
            raise result
        return result


class FakeClient:
    """anthropic.Anthropic의 최소 흉내."""

    def __init__(self, payload=None, stop_reason="end_turn", beta_error=None, result=None):
        self.calls = []
        self.beta_error = beta_error
        self.result = result if result is not None else FakeResponse(payload or {}, stop_reason)
        self.messages = FakeMessages(self)
        self.beta = type("Beta", (), {"messages": FakeMessages(self, beta=True)})()


def payload(**overrides):
    """모델이 돌려줄 법한 완전한 응답."""
    base = {
        "understood": True,
        "message": "RSI가 30 아래로 내려가면 사고, 55를 넘으면 파는 전략입니다.",
        "label": "RSI 반등",
        "note": "과매도에서 진입",
        "target_weight": 1.0,
        "entry_join": "all",
        "entry": [
            {
                "left": {"type": "rsi", "period": 14, "value": 0, "mult": 0, "k": 0},
                "op": "<",
                "right": {"type": "number", "period": 0, "value": 30, "mult": 0, "k": 0},
            }
        ],
        "exit_join": "any",
        "exit": [
            {
                "left": {"type": "rsi", "period": 14, "value": 0, "mult": 0, "k": 0},
                "op": ">",
                "right": {"type": "number", "period": 0, "value": 55, "mult": 0, "k": 0},
            }
        ],
        "risk": {
            "stop_loss_pct": 0,
            "take_profit_pct": 0,
            "trailing_stop_pct": 0,
            "max_position_weight": 1,
        },
        "warnings": [],
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------ 스키마
def test_schema_covers_every_indicator_and_operator():
    """지표를 추가하고 스키마를 안 고치면 모델이 그걸 쓸 수 없다."""
    schema = response_schema()
    operand = schema["properties"]["entry"]["items"]["properties"]["left"]
    assert set(operand["properties"]["type"]["enum"]) == {s["type"] for s in OPERAND_SPECS}
    ops = schema["properties"]["entry"]["items"]["properties"]["op"]["enum"]
    assert set(ops) == {s["op"] for s in OPERATOR_SPECS}


def test_schema_is_strict():
    """구조화된 출력은 모든 속성이 required여야 하고 추가 속성을 막아야 한다."""

    def check(node):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node.get("required", [])) == set(node["properties"])
            for child in node["properties"].values():
                check(child)
        elif node.get("type") == "array":
            check(node["items"])

    check(response_schema())


def test_system_prompt_lists_available_indicators():
    prompt = system_prompt()
    for spec in OPERAND_SPECS:
        assert spec["type"] in prompt
    assert "지어내지" in prompt or "몰래 바꾸지" in prompt


# ------------------------------------------------------------------ 정상 변환
def test_translates_to_runnable_strategy():
    client = FakeClient(payload())
    result = translate("RSI 30 아래면 사고 55 넘으면 팔아", client=client)

    assert result.understood
    assert result.spec["label"] == "RSI 반등"
    # 실제로 전략으로 만들어져야 한다 — 검증만 통과하고 못 도는 건 소용없다
    strategy = get_strategy("rule", spec=result.spec)
    assert strategy.warmup >= 3


def test_uses_opus_5_and_the_schema():
    client = FakeClient(payload())
    translate("아무거나", client=client)
    call = client.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["thinking"] == {"type": "adaptive"}


def test_asks_with_server_side_fallback_first():
    client = FakeClient(payload())
    translate("아무거나", client=client)
    assert client.calls[0]["beta"] is True
    assert client.calls[0]["fallbacks"] == "default"


def test_falls_back_to_plain_call_when_beta_unavailable():
    """폴백 베타를 못 쓰는 계정에서도 기능이 죽으면 안 된다."""

    class BadRequestError(Exception):
        status_code = 400

    client = FakeClient(payload(), beta_error=BadRequestError("unsupported beta"))
    result = translate("아무거나", client=client)
    assert result.understood
    assert [c["beta"] for c in client.calls] == [True, False]


def test_non_400_errors_are_not_retried():
    class ServerError(Exception):
        status_code = 500

    client = FakeClient(payload(), beta_error=ServerError("boom"))
    with pytest.raises(TranslationError, match="호출 실패"):
        translate("아무거나", client=client)
    assert len(client.calls) == 1


# ------------------------------------------------------------------ 거절
def test_unsupported_request_is_refused_not_invented():
    """지원하지 않는 개념을 비슷한 지표로 슬쩍 바꾸면 안 된다."""
    client = FakeClient(
        payload(understood=False, message="뉴스 감성 지표는 지원하지 않습니다.", entry=[], exit=[])
    )
    result = translate("뉴스가 좋으면 사줘", client=client)
    assert result.understood is False
    assert result.spec is None
    assert "뉴스" in result.message


def test_risk_only_answer_is_rejected_with_guidance():
    """손절만 말하고 진입 조건이 없으면 무엇이 빠졌는지 알려준다."""
    client = FakeClient(
        payload(
            entry=[],
            exit=[],
            risk={
                "stop_loss_pct": 0.05,
                "take_profit_pct": 0.1,
                "trailing_stop_pct": 0,
                "max_position_weight": 1,
            },
        )
    )
    result = translate("5% 빠지면 손절하고 10% 오르면 팔아", client=client)
    assert result.understood is False
    assert "진입 조건" in result.message
    assert result.risk["stop_loss_pct"] == 0.05  # 값은 살려서 돌려준다


def test_model_output_must_pass_our_validator():
    """모델이 이상한 걸 만들어도 그대로 나가지 않는다."""
    bad = payload()
    bad["entry"][0]["left"]["type"] = "존재하지않는지표"
    client = FakeClient(bad)
    with pytest.raises(TranslationError, match="지원하지 않는 지표"):
        translate("아무거나", client=client)


def test_refusal_stop_reason_is_surfaced():
    client = FakeClient(payload(), stop_reason="refusal")
    with pytest.raises(TranslationError, match="답하지 않았"):
        translate("아무거나", client=client)


def test_broken_json_is_reported():
    client = FakeClient(result=RawResponse([FakeBlock("{이건 JSON이 아님")]))
    with pytest.raises(TranslationError, match="해석하지 못"):
        translate("아무거나", client=client)


def test_empty_response_is_reported():
    client = FakeClient(result=RawResponse([]))
    with pytest.raises(TranslationError, match="비어 있"):
        translate("아무거나", client=client)


# ------------------------------------------------------------------ 입력 검증
def test_empty_text_is_rejected():
    with pytest.raises(TranslationError, match="입력하세요"):
        translate("   ", client=FakeClient(payload()))


def test_overly_long_text_is_rejected():
    with pytest.raises(TranslationError, match="너무 깁니다"):
        translate("가" * 4001, client=FakeClient(payload()))


def test_missing_api_key_gives_actionable_message(monkeypatch):
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    with pytest.raises(TranslationError, match=ENV_API_KEY):
        build_client()


# ------------------------------------------------------------- 값 정리
def test_schema_filler_zeros_are_stripped():
    """스키마가 강제로 채운 period=0이 그대로 가면 검증기가 거부한다."""
    assert _clean_operand({"type": "close", "period": 0, "value": 0, "mult": 0, "k": 0}) == {
        "type": "close"
    }
    assert _clean_operand({"type": "rsi", "period": 0, "value": 0, "mult": 0, "k": 0}) == {
        "type": "rsi",
        "period": 14,  # 0 대신 기본값
    }
    assert _clean_operand({"type": "sma", "period": 200, "value": 0, "mult": 0, "k": 0}) == {
        "type": "sma",
        "period": 200,
    }


def test_number_operand_keeps_zero():
    """숫자 0은 의미 있는 값이므로 지우면 안 된다."""
    assert _clean_operand({"type": "number", "period": 0, "value": 0, "mult": 0, "k": 0}) == {
        "type": "number",
        "value": 0.0,
    }


def test_risk_drops_unset_and_out_of_range():
    risk = _clean_risk(
        {
            "stop_loss_pct": 0.05,
            "take_profit_pct": 0,  # 미지정
            "trailing_stop_pct": 0,
            "max_position_weight": 1,  # 제한 없음
        }
    )
    assert risk == {"stop_loss_pct": 0.05}


def test_risk_rejects_impossible_values():
    """모델이 '손절 500%' 같은 값을 내도 리스크 설정에 들어가면 안 된다."""
    assert _clean_risk({"stop_loss_pct": 5, "take_profit_pct": 0,
                        "trailing_stop_pct": 0, "max_position_weight": 1}) == {}


def test_target_weight_is_clamped():
    client = FakeClient(payload(target_weight=3))
    assert translate("아무거나", client=client).spec["target_weight"] == 1.0


def test_warnings_are_passed_through():
    client = FakeClient(payload(warnings=["RSI 기간을 14로 가정했습니다"]))
    assert translate("아무거나", client=client).warnings == ["RSI 기간을 14로 가정했습니다"]


def test_translation_serializes_for_the_web():
    result = Translation(understood=True, message="ok", spec={"a": 1}, risk={"b": 0.1})
    assert json.loads(json.dumps(result.to_dict(), ensure_ascii=False))["message"] == "ok"


def test_join_defaults_when_model_omits_it():
    client = FakeClient(payload(entry_join="이상한값"))
    spec = translate("아무거나", client=client).spec
    assert "all" in spec["entry"]
