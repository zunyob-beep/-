"""자가진단 검증.

진단 도구가 지켜야 할 것은 두 가지다.

1. **절대 스스로 터지지 않는다.** 안 되는 이유를 알아보려고 부른 명령이
   트레이스백을 뱉으면, 사용자는 원래 문제 위에 문제 하나를 더 얻는다.
2. **다음에 할 일을 알려준다.** "업비트 연결 실패"만 찍고 끝나면
   "안 돼요"와 똑같다. 무엇을 치면 되는지가 같이 나와야 한다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from patternscan.cli import main
from patternscan.data import cache_path, save
from patternscan.doctor import BAD, OK, WARN, check_cache, check_python, check_upbit, run
from patternscan.models import Candle
from patternscan.upbit import UpbitError


def _candles(count: int) -> list[Candle]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            ts=start + timedelta(minutes=i),
            open=100.0, high=101.0, low=99.0, close=100.0, volume=1.0,
        )
        for i in range(count)
    ]


@pytest.fixture
def reachable(monkeypatch):
    """업비트가 정상 응답하는 상황."""
    monkeypatch.setattr(
        "patternscan.doctor.UpbitClient",
        lambda **kw: type("C", (), {"get_candles": lambda self, *a, **k: _candles(1)})(),
    )


@pytest.fixture
def blocked(monkeypatch):
    """업비트가 막힌 상황 (회사망·VPN에서 흔하다)."""
    def boom(self, *args, **kwargs):
        raise UpbitError("요청 거부(403)")

    monkeypatch.setattr(
        "patternscan.doctor.UpbitClient",
        lambda **kw: type("C", (), {"get_candles": boom})(),
    )


# ---------------------------------------------------------------- 개별 점검
def test_python_check_passes_on_the_running_interpreter():
    assert check_python().mark == OK


def test_blocked_upbit_is_reported_with_a_way_out(blocked):
    check = check_upbit()
    assert check.mark == BAD
    assert check.fix  # 무엇을 하라는 말이 반드시 붙는다


def test_a_network_layer_crash_becomes_a_diagnosis_not_a_traceback(monkeypatch):
    """업비트 계층이 아닌 곳에서 터져도 진단으로 바뀌어야 한다.

    UpbitError만 잡으면 DNS 실패·프록시 오류 같은 것이 그대로 올라간다.
    """
    def boom(self, *args, **kwargs):
        raise OSError("Name or service not known")

    monkeypatch.setattr(
        "patternscan.doctor.UpbitClient",
        lambda **kw: type("C", (), {"get_candles": boom})(),
    )
    check = check_upbit()
    assert check.mark == BAD
    assert "OSError" in check.detail


def test_the_real_reason_survives_the_wrapping(monkeypatch):
    """UpbitError는 "재시도 실패"까지만 말한다 — 그 아래가 진짜 원인이다.

    프록시가 막았는지, 이름을 못 찾았는지에 따라 할 일이 완전히 다른데
    겉껍데기만 보여 주면 사용자는 여전히 아무것도 모른다.
    """
    def boom(self, *args, **kwargs):
        try:
            raise OSError("Tunnel connection failed: 403 Forbidden")
        except OSError as cause:
            raise UpbitError("재시도 1회 실패") from cause

    monkeypatch.setattr(
        "patternscan.doctor.UpbitClient",
        lambda **kw: type("C", (), {"get_candles": boom})(),
    )
    detail = check_upbit().detail
    assert "재시도" in detail
    assert "403 Forbidden" in detail


def test_the_retry_logger_is_put_back_afterwards(blocked):
    """진단 중에 로그를 눌렀다가 그대로 두면, 이후 수집에서 경고가 사라진다."""
    logger = logging.getLogger("patternscan.upbit")
    logger.setLevel(logging.INFO)
    check_upbit()
    assert logger.level == logging.INFO


def test_empty_response_points_at_the_market_code(monkeypatch):
    monkeypatch.setattr(
        "patternscan.doctor.UpbitClient",
        lambda **kw: type("C", (), {"get_candles": lambda self, *a, **k: []})(),
    )
    check = check_upbit("KRW-NOPE")
    assert check.mark == WARN
    assert "KRW-NOPE" in check.detail


def test_missing_cache_names_the_command_that_fixes_it(tmp_path):
    checks = check_cache("KRW-BTC", str(tmp_path))
    assert checks and all(c.mark == WARN for c in checks)
    assert all("fetch" in c.fix for c in checks)


def test_a_thin_cache_is_flagged_even_though_the_file_exists(tmp_path):
    """봉이 30개뿐인데 초록불을 주면, 왜 결과가 안 나오는지 영원히 모른다."""
    save(cache_path("KRW-BTC", "minute1", tmp_path), _candles(30))
    thin = [c for c in check_cache("KRW-BTC", str(tmp_path)) if "1분봉" in c.title]
    assert thin and thin[0].mark == WARN


def test_a_full_cache_passes(tmp_path):
    save(cache_path("KRW-BTC", "minute1", tmp_path), _candles(2500))
    full = [c for c in check_cache("KRW-BTC", str(tmp_path)) if "1분봉" in c.title]
    assert full and full[0].mark == OK


def test_a_corrupt_file_is_named_so_it_can_be_deleted(tmp_path):
    path = cache_path("KRW-BTC", "minute1", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("이건 CSV가 아닙니다\n", encoding="utf-8")
    broken = [c for c in check_cache("KRW-BTC", str(tmp_path)) if "1분봉" in c.title]
    assert broken and broken[0].mark == BAD
    assert str(path) in broken[0].fix


# ------------------------------------------------------------------- 전체
def test_run_fails_loudly_when_upbit_is_blocked(blocked, tmp_path, capsys):
    assert run("KRW-BTC", str(tmp_path)) == 1
    assert "고쳐야" in capsys.readouterr().out


def test_run_still_succeeds_when_only_the_cache_is_missing(reachable, tmp_path, capsys):
    """시세를 아직 안 받은 건 고장이 아니다 — 다음 단계일 뿐이다."""
    assert run("KRW-BTC", str(tmp_path)) == 0
    assert "fetch" in capsys.readouterr().out


def test_all_green_tells_you_what_to_run_next(reachable, tmp_path, capsys):
    for timeframe in ("minute1", "minute3", "minute5"):
        save(cache_path("KRW-BTC", timeframe, tmp_path), _candles(2500))
    assert run("KRW-BTC", str(tmp_path)) == 0
    assert "ui" in capsys.readouterr().out


def test_doctor_is_reachable_from_the_command_line(reachable, tmp_path, capsys):
    assert main(["doctor", "--data-dir", str(tmp_path)]) == 0
    assert "자가진단" in capsys.readouterr().out
