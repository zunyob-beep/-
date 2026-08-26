"""명령줄 동작 검증. 네트워크는 타지 않는다.

여기서 지키는 것은 하나다: **일부만 있어도 있는 것으로 답한다.**
예전에는 1분봉이 멀쩡히 캐시에 있는데도 3분봉을 못 받았다는 이유로
판정 전체가 죽었다. 수집이 중간에 끊기거나 업비트가 잠깐 죽으면 바로
그 상황이 되는데, 사용자 입장에서는 아무 이유 없이 도구가 고장난 것처럼 보인다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from patternscan.cli import main
from patternscan.data import cache_path, save
from patternscan.models import Candle
from patternscan.upbit import UpbitError


@pytest.fixture
def cache(tmp_path):
    """1분봉만 캐시에 넣는다 (3분·5분봉은 없다)."""
    rng = np.random.default_rng(4)
    closes = 40_000_000 * np.exp(np.cumsum(rng.normal(0, 0.0008, 2500)))
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    candles = [
        Candle(
            ts=start + timedelta(minutes=i),
            open=float(c), high=float(c) * 1.001, low=float(c) * 0.999,
            close=float(c), volume=1.0,
        )
        for i, c in enumerate(closes)
    ]
    save(cache_path("KRW-BTC", "minute1", tmp_path), candles)
    return tmp_path


@pytest.fixture
def offline(monkeypatch):
    """업비트에 못 닿는 상황을 흉내낸다."""
    def boom(*args, **kwargs):
        raise UpbitError("연결할 수 없습니다")

    monkeypatch.setattr("patternscan.cli.fetch", boom)


def test_scan_runs_with_only_some_timeframes_cached(cache, offline, capsys):
    """1분봉만 있어도 판정은 나와야 한다."""
    assert main(["scan", "--data-dir", str(cache)]) == 0
    out = capsys.readouterr().out
    assert "1분봉" in out
    assert "들어가" in out  # 판정 문장이 찍혔다


def test_missing_timeframes_are_announced(cache, offline, capsys):
    """빠진 간격을 조용히 넘기면 3종을 다 본 줄 안다."""
    main(["scan", "--data-dir", str(cache)])
    out = capsys.readouterr().out
    assert "3분봉" in out and "5분봉" in out
    assert "빠집니다" in out


def test_scan_without_any_cache_says_what_to_do(tmp_path, offline, capsys):
    """아무것도 없으면 다음에 뭘 해야 하는지 알려줘야 한다."""
    assert main(["scan", "--data-dir", str(tmp_path)]) == 1
    assert "fetch" in capsys.readouterr().out


def test_similarity_flag_reaches_the_scan(cache, offline, capsys):
    main(["scan", "--data-dir", str(cache), "--similarity", "0.95"])
    assert "0.95" in capsys.readouterr().out


def test_max_distance_replaces_the_similarity_line(cache, offline, capsys):
    """거리로 자를 때 유사도 기준을 같이 찍으면 거짓말이 된다."""
    main(["scan", "--data-dir", str(cache), "--max-distance", "0.3"])
    assert "'같은 모양' 기준" not in capsys.readouterr().out


def test_unknown_command_is_rejected():
    with pytest.raises(SystemExit):
        main(["nonsense"])
