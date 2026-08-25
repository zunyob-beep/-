"""CLI 인자 파싱 검증.

여기서 틀리면 사용자가 의도한 것과 다른 설정으로 봇이 돈다. 특히 리스크
옵션이 조용히 무시되는 경우가 위험하다 — 손절을 걸었다고 믿고 있는데
실제로는 안 걸려 있는 상황.
"""

from __future__ import annotations

import pytest

from btcbot.cli import (
    _coerce,
    build_parser,
    parse_grid,
    parse_params,
    settings_from_args,
)


def parse(argv: list[str]):
    return build_parser().parse_args(argv)


# ------------------------------------------------------------------ 값 변환
def test_coerce_types():
    assert _coerce("5") == 5
    assert isinstance(_coerce("5"), int)
    assert _coerce("0.5") == 0.5
    assert _coerce("true") is True
    assert _coerce("False") is False
    assert _coerce("ema") == "ema"


def test_parse_params():
    assert parse_params(["k=0.5", "dynamic_k=true", "kind=sma"]) == {
        "k": 0.5,
        "dynamic_k": True,
        "kind": "sma",
    }


def test_parse_params_rejects_bad_format():
    with pytest.raises(SystemExit):
        parse_params(["k"])


def test_parse_grid():
    assert parse_grid(["k=0.3,0.5", "ma_period=0,20"]) == {
        "k": [0.3, 0.5],
        "ma_period": [0, 20],
    }


def test_parse_grid_ignores_trailing_comma():
    assert parse_grid(["k=0.3,0.5,"]) == {"k": [0.3, 0.5]}


# ------------------------------------------------------------------ 설정 조립
def test_strategy_params_reach_settings():
    args = parse(["backtest", "--strategy", "vb", "-p", "k=0.4", "-p", "ma_period=20"])
    settings = settings_from_args(args)
    assert settings.strategy == "vb"
    assert settings.strategy_params == {"k": 0.4, "ma_period": 20}


def test_risk_flags_reach_settings():
    args = parse(
        [
            "live",
            "--max-weight", "0.3",
            "--stop-loss", "0.05",
            "--trailing-stop", "0.07",
            "--daily-loss-limit", "0.03",
            "--max-drawdown", "0.2",
            "--cooldown", "5",
        ]
    )
    risk = settings_from_args(args).risk
    assert risk.max_position_weight == 0.3
    assert risk.stop_loss_pct == 0.05
    assert risk.trailing_stop_pct == 0.07
    assert risk.daily_loss_limit_pct == 0.03
    assert risk.max_drawdown_pct == 0.2
    assert risk.cooldown_bars == 5


def test_unspecified_risk_flags_keep_defaults():
    settings = settings_from_args(parse(["backtest", "--stop-loss", "0.05"]))
    assert settings.risk.stop_loss_pct == 0.05
    assert settings.risk.max_position_weight == 1.0  # 건드리지 않은 값은 기본값
    assert settings.risk.max_drawdown_pct == 0.0


def test_invalid_risk_value_is_rejected():
    """1.5(=150%) 같은 값이 조용히 통과하면 안 된다."""
    with pytest.raises(ValueError):
        settings_from_args(parse(["backtest", "--stop-loss", "1.5"]))


def test_config_file_and_cli_merge(tmp_path):
    import json

    path = tmp_path / "c.json"
    path.write_text(
        json.dumps({"market": "KRW-ETH", "interval": "day", "risk": {"stop_loss_pct": 0.1}}),
        encoding="utf-8",
    )
    args = parse(["--config", str(path), "backtest", "--market", "KRW-BTC"])
    settings = settings_from_args(args)
    assert settings.market == "KRW-BTC"  # CLI가 이긴다
    assert settings.interval == "day"  # 파일 값이 남는다
    assert settings.risk.stop_loss_pct == 0.1


def test_cli_risk_flag_overrides_config_file(tmp_path):
    import json

    path = tmp_path / "c.json"
    path.write_text(json.dumps({"risk": {"stop_loss_pct": 0.1}}), encoding="utf-8")
    args = parse(["--config", str(path), "backtest", "--stop-loss", "0.02"])
    assert settings_from_args(args).risk.stop_loss_pct == 0.02


# ------------------------------------------------------------------ 파서 검증
def test_command_is_required():
    with pytest.raises(SystemExit):
        parse([])


def test_unknown_strategy_is_rejected_by_parser():
    with pytest.raises(SystemExit):
        parse(["backtest", "--strategy", "없는전략"])


def test_unknown_interval_is_rejected_by_parser():
    with pytest.raises(SystemExit):
        parse(["backtest", "--interval", "minute7"])


def test_live_has_dry_run_and_yes_flags():
    args = parse(["live", "--dry-run", "--yes"])
    assert args.dry_run is True
    assert args.yes is True


def test_live_defaults_to_confirmation_required():
    args = parse(["live"])
    assert args.yes is False
    assert args.dry_run is False


def test_paper_has_no_yes_flag():
    """페이퍼는 위험하지 않으므로 확인 절차 자체가 없다."""
    assert not hasattr(parse(["paper"]), "yes")


@pytest.mark.parametrize(
    "command", ["strategies", "fetch", "backtest", "optimize", "paper", "live", "status"]
)
def test_all_commands_parse(command):
    assert parse([command]).command == command
