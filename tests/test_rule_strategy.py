"""규칙 전략 검증.

UI에서 만든 전략이 여기서 잘못 해석되면, 사용자는 자기가 만든 규칙과
전혀 다른 매매를 보게 된다. 코드를 읽을 줄 모르는 사용자는 그 차이를
알아챌 방법이 없으므로 여기서 조인다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from btcbot.backtest import run_backtest
from btcbot.models import Action
from btcbot.strategies import get_strategy
from btcbot.strategies.rule import (
    PRESETS,
    SpecError,
    builder_metadata,
    describe_operand,
    required_warmup,
    series_for,
    validate_spec,
)
from tests.conftest import make_candle, series

KST_MIDNIGHT = datetime(2023, 12, 31, 15, tzinfo=timezone.utc)  # 2024-01-01 00:00 KST


def rule(spec):
    return get_strategy("rule", spec=spec)


def cond(left, op, right):
    return {"left": left, "op": op, "right": right}


NUM = lambda v: {"type": "number", "value": v}
CLOSE = {"type": "close"}


# ------------------------------------------------------------------ 검증
def test_spec_requires_entry_or_exit():
    with pytest.raises(SpecError, match="최소 하나"):
        validate_spec({"label": "빈 전략"})


def test_spec_rejects_unknown_indicator():
    with pytest.raises(SpecError, match="알 수 없는 지표"):
        validate_spec({"entry": {"all": [cond({"type": "마법지표"}, ">", NUM(1))]}})


def test_spec_rejects_unknown_operator():
    with pytest.raises(SpecError, match="알 수 없는 비교"):
        validate_spec({"entry": {"all": [cond(CLOSE, "≈", NUM(1))]}})


def test_spec_rejects_empty_condition_list():
    with pytest.raises(SpecError, match="최소 하나"):
        validate_spec({"entry": {"all": []}})


def test_spec_rejects_bad_weight():
    with pytest.raises(SpecError, match="비중"):
        validate_spec({"entry": {"all": [cond(CLOSE, ">", NUM(1))]}, "target_weight": 1.5})


def test_spec_rejects_zero_period():
    with pytest.raises(SpecError, match="기간"):
        validate_spec({"entry": {"all": [cond({"type": "sma", "period": 0}, ">", NUM(1))]}})


def test_spec_fills_default_params():
    clean = validate_spec({"entry": {"all": [cond({"type": "rsi"}, "<", NUM(30))]}})
    assert clean["entry"]["all"][0]["left"]["period"] == 14


def test_spec_accepts_bare_number():
    clean = validate_spec({"entry": {"all": [cond(CLOSE, ">", 100)]}})
    assert clean["entry"]["all"][0]["right"] == {"type": "number", "value": 100.0}


def test_error_messages_are_korean():
    """UI에 그대로 노출되므로 사용자가 읽을 수 있어야 한다."""
    with pytest.raises(SpecError) as exc:
        validate_spec({"entry": {"all": [cond({"type": "sma", "period": -1}, ">", NUM(1))]}})
    assert any("가" <= ch <= "힣" for ch in str(exc.value))


def test_warmup_follows_longest_period():
    spec = validate_spec(
        {
            "entry": {"all": [cond({"type": "sma", "period": 200}, ">", NUM(1))]},
            "exit": {"any": [cond({"type": "rsi", "period": 14}, ">", NUM(70))]},
        }
    )
    assert required_warmup(spec) == 202


def test_warmup_has_minimum_for_cross():
    spec = validate_spec({"entry": {"all": [cond(CLOSE, "cross_above", NUM(100))]}})
    assert required_warmup(spec) >= 3


# ------------------------------------------------------------------ 비교
def test_simple_greater_than():
    strategy = rule({"entry": {"all": [cond(CLOSE, ">", NUM(105))]}})
    assert strategy.decide(series([100.0] * 5)).target_weight is None
    assert strategy.decide(series([100.0, 101, 102, 103, 110])).action is Action.BUY


def test_exit_wins_over_entry():
    """둘 다 참이면 청산이 이긴다 — 애매할 때 빠져나오는 쪽이 안전하다."""
    strategy = rule(
        {
            "entry": {"all": [cond(CLOSE, ">", NUM(1))]},
            "exit": {"all": [cond(CLOSE, ">", NUM(1))]},
        }
    )
    signal = strategy.decide(series([100.0] * 5))
    assert signal.action is Action.SELL
    assert signal.target_weight == 0.0


def test_no_match_holds_current_position():
    strategy = rule({"entry": {"all": [cond(CLOSE, ">", NUM(9999))]}})
    signal = strategy.decide(series([100.0] * 5))
    assert signal.target_weight is None  # 청산이 아니라 '유지'


def test_all_requires_every_condition():
    strategy = rule(
        {"entry": {"all": [cond(CLOSE, ">", NUM(50)), cond(CLOSE, ">", NUM(9999))]}}
    )
    assert strategy.decide(series([100.0] * 5)).target_weight is None


def test_any_requires_one_condition():
    strategy = rule(
        {"entry": {"any": [cond(CLOSE, ">", NUM(9999)), cond(CLOSE, ">", NUM(50))]}}
    )
    assert strategy.decide(series([100.0] * 5)).action is Action.BUY


def test_not_inverts():
    strategy = rule({"entry": {"not": cond(CLOSE, ">", NUM(9999))}})
    assert strategy.decide(series([100.0] * 5)).action is Action.BUY


def test_nested_groups():
    strategy = rule(
        {
            "entry": {
                "all": [
                    cond(CLOSE, ">", NUM(50)),
                    {"any": [cond(CLOSE, ">", NUM(9999)), cond(CLOSE, "<", NUM(200))]},
                ]
            }
        }
    )
    assert strategy.decide(series([100.0] * 5)).action is Action.BUY


# ------------------------------------------------------------------ 크로스
def test_cross_above_fires_only_on_the_crossing_bar():
    strategy = rule({"entry": {"all": [cond(CLOSE, "cross_above", NUM(100))]}})
    # 99 -> 101 로 넘어가는 봉에서만 참
    crossing = series([90.0, 95.0, 99.0, 101.0])
    assert strategy.decide(crossing).action is Action.BUY

    # 이미 넘어선 뒤에는 참이 아니다
    after = series([90.0, 99.0, 101.0, 105.0])
    assert after[-1].close == 105.0
    assert strategy.decide(after).target_weight is None


def test_cross_below():
    strategy = rule({"entry": {"all": [cond(CLOSE, "cross_below", NUM(100))]}})
    assert strategy.decide(series([110.0, 105.0, 101.0, 99.0])).action is Action.BUY
    assert strategy.decide(series([110.0, 101.0, 99.0, 95.0])).target_weight is None


def test_ema_cross_matches_ma_cross_strategy(uptrend):
    """빌더로 만든 골든크로스가 코드 전략과 같은 방향을 봐야 한다."""
    built = rule(
        {
            "entry": {"all": [cond({"type": "ema", "period": 10}, ">", {"type": "ema", "period": 30})]},
            "exit": {"any": [cond({"type": "ema", "period": 10}, "<=", {"type": "ema", "period": 30})]},
        }
    )
    coded = get_strategy("ma_cross", fast=10, slow=30, kind="ema")
    assert built.decide(uptrend).action is coded.decide(uptrend).action


# ------------------------------------------------------------------ 지표 계산
def test_series_length_always_matches_candles(uptrend):
    for operand in (
        CLOSE,
        {"type": "sma", "period": 20},
        {"type": "rsi", "period": 14},
        {"type": "atr", "period": 14},
        {"type": "bb_upper", "period": 20, "mult": 2},
        {"type": "highest", "period": 10},
        {"type": "vb_target", "k": 0.5},
        NUM(3),
    ):
        assert len(series_for(operand, uptrend, {})) == len(uptrend)


def test_bollinger_bands_are_ordered(choppy):
    upper = series_for({"type": "bb_upper", "period": 20, "mult": 2}, choppy, {})
    mid = series_for({"type": "bb_mid", "period": 20}, choppy, {})
    lower = series_for({"type": "bb_lower", "period": 20, "mult": 2}, choppy, {})
    assert upper[-1] > mid[-1] > lower[-1]


def test_highest_and_lowest(uptrend):
    high = series_for({"type": "highest", "period": 10}, uptrend, {})
    low = series_for({"type": "lowest", "period": 10}, uptrend, {})
    assert high[-1] == max(c.high for c in uptrend[-10:])
    assert low[-1] == min(c.low for c in uptrend[-10:])
    assert high[8] is None  # 아직 10봉이 안 모였다


def test_vb_target_uses_previous_day_range():
    day1 = [
        make_candle(KST_MIDNIGHT + timedelta(hours=i), open_=100, high=120, low=100, close=110)
        for i in range(3)
    ]
    day2 = [make_candle(KST_MIDNIGHT + timedelta(days=1), open_=110, high=115, low=108, close=112)]
    targets = series_for({"type": "vb_target", "k": 0.5}, day1 + day2, {})

    assert targets[0] is None  # 첫날은 전일이 없다
    # 전일 변동폭 20, k=0.5 -> 110 + 10
    assert targets[-1] == pytest.approx(120.0)


def test_series_cache_is_reused(uptrend):
    cache = {}
    first = series_for({"type": "sma", "period": 20}, uptrend, cache)
    second = series_for({"type": "sma", "period": 20}, uptrend, cache)
    assert first is second  # 같은 객체 = 재계산 안 함


# ------------------------------------------------------------------ 설명 문구
def test_reason_is_human_readable():
    strategy = rule({"entry": {"all": [cond({"type": "rsi", "period": 14}, "<", NUM(30))]}})
    prices = [100.0 + i for i in range(40)] + [140.0 - i * 6 for i in range(12)]
    signal = strategy.decide(series(prices))
    assert "RSI" in signal.reason
    assert "보다 작다" in signal.reason


def test_describe_operand():
    assert describe_operand({"type": "close"}) == "종가"
    assert describe_operand({"type": "sma", "period": 20}) == "단순이동평균 SMA(20)"
    assert describe_operand({"type": "number", "value": 30}) == "30"


# ------------------------------------------------------------------ 프리셋
@pytest.mark.parametrize("preset", PRESETS, ids=[p["label"] for p in PRESETS])
def test_every_preset_is_valid_and_runs(preset):
    """UI 첫 화면에 뜨는 예시가 깨져 있으면 최악이다."""
    import math

    # SMA(200)을 쓰는 프리셋도 있으므로 넉넉히 준다.
    prices = [100.0 + 10 * math.sin(i / 25) + i * 0.05 for i in range(400)]
    strategy = rule(preset)
    result = run_backtest(series(prices), strategy, cash=1_000_000)
    assert result.performance.initial_equity == 1_000_000
    assert strategy.describe().startswith("rule(")


def test_short_history_gives_actionable_error(choppy):
    """봉이 모자랄 때 사용자가 뭘 해야 할지 알 수 있는 메시지가 나와야 한다."""
    strategy = rule(
        {"entry": {"all": [cond(CLOSE, ">", {"type": "sma", "period": 500})]}}
    )
    with pytest.raises(ValueError, match="봉이 부족합니다"):
        run_backtest(choppy, strategy)


def test_preset_metadata_is_complete():
    for preset in PRESETS:
        assert preset["label"]
        assert preset["note"]  # 사용자가 뭐 하는 전략인지 읽을 수 있어야 한다


def test_exactly_one_default_preset():
    """첫 화면에 열릴 예시는 하나여야 한다."""
    assert sum(1 for p in PRESETS if p.get("default")) == 1


def test_builder_metadata_shape():
    meta = builder_metadata()
    assert meta["operands"] and meta["operators"]
    for operand in meta["operands"]:
        assert operand["label"]
        for param in operand["params"]:
            assert {"key", "label", "default"} <= set(param)


def test_spec_file_loading(tmp_path):
    import json

    path = tmp_path / "my.json"
    path.write_text(json.dumps(PRESETS[0], ensure_ascii=False), encoding="utf-8")
    strategy = get_strategy("rule", spec_file=str(path))
    assert strategy.spec["label"] == PRESETS[0]["label"]


def test_missing_spec_raises():
    with pytest.raises(SpecError, match="전략 정의"):
        get_strategy("rule")
