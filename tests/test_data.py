"""시세 수집과 캐시 검증.

여기서 지키는 것은 하나다: **이미 받은 것을 다시 받지 않는다.**

8년치 1분봉은 420만 개이고, 업비트가 200개씩 주므로 21,000번 요청해야 한다.
40분이 넘는다. 예전에는 캐시가 있어도 매번 처음부터 다시 받았고, 중간에
끊기면 받은 것이 전부 날아갔다. 40분을 기다렸다가 39분에 끊겨 아무것도 안
남는 도구는 아무도 두 번 쓰지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from patternscan.data import cache_path, fetch, load, merge, save
from patternscan.models import Candle
from patternscan.upbit import UpbitError

START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def candles(count: int, start: datetime = START, step_minutes: int = 1) -> list[Candle]:
    return [
        Candle(
            ts=start + timedelta(minutes=step_minutes * i),
            open=100.0 + i, high=100.5 + i, low=99.5 + i, close=100.0 + i, volume=1.0,
        )
        for i in range(count)
    ]


class FakeUpbit:
    """업비트 흉내. 요청한 페이지를 기록해 '다시 받았는지'를 검사한다."""

    def __init__(self, available: list[Candle], fail_after: int | None = None) -> None:
        self.available = sorted(available, key=lambda c: c.ts)
        self.requests: list[datetime | None] = []
        self.fail_after = fail_after

    def collect(self, market, timeframe, count, end=None, progress=None,
                stop_at=None, on_batch=None):
        self.requests.append(end)
        if self.fail_after is not None and len(self.requests) > self.fail_after:
            raise UpbitError("연결이 끊겼습니다")
        pool = self.available
        if end is not None:
            pool = [c for c in pool if c.ts <= end]
        if stop_at is not None:
            pool = [c for c in pool if c.ts > stop_at]
        picked = pool[-count:] if count < len(pool) else pool
        if on_batch is not None and picked:
            on_batch(picked)
        return picked

    @property
    def fetched(self) -> int:
        return len(self.requests)


# ------------------------------------------------------------------ 이어받기
def test_cached_candles_are_not_downloaded_again(tmp_path):
    """캐시가 이미 충분하면 새 봉만 물어봐야 한다."""
    have = candles(500)
    save(cache_path("KRW-BTC", "minute1", tmp_path), have)

    client = FakeUpbit(have)  # 서버에도 같은 것뿐 = 새 봉 없음
    series = fetch(client, "KRW-BTC", "minute1", 500, directory=tmp_path)

    assert len(series) == 500
    # 새 봉을 확인하는 요청만 있거나, 아예 없어야 한다
    assert client.fetched <= 1, f"이미 가진 구간을 {client.fetched}번이나 다시 받았습니다"


def test_only_new_candles_are_downloaded(tmp_path):
    """뒤에 봉이 생겼으면 그 부분만 받아야 한다."""
    have = candles(500)
    save(cache_path("KRW-BTC", "minute1", tmp_path), have)
    server = candles(520)  # 20개가 더 생겼다

    client = FakeUpbit(server)
    series = fetch(client, "KRW-BTC", "minute1", 520, directory=tmp_path)

    assert len(series) == 520
    # 새 봉을 받을 때 stop_at으로 멈추므로 앞쪽을 다시 훑지 않는다
    assert client.fetched <= 2


def test_extending_further_back_keeps_what_we_have(tmp_path):
    """더 긴 과거를 원하면 앞쪽만 늘려야 한다."""
    old = candles(300, start=START - timedelta(minutes=300))
    recent = candles(200)
    save(cache_path("KRW-BTC", "minute1", tmp_path), recent)

    client = FakeUpbit(old + recent)
    series = fetch(client, "KRW-BTC", "minute1", 500, directory=tmp_path)

    assert len(series) == 500
    # 과거 방향 요청은 캐시의 첫 봉보다 이전을 가리켜야 한다
    backward = [r for r in client.requests if r is not None]
    assert backward, "과거로 확장하는 요청이 없습니다"
    assert all(r < recent[0].ts for r in backward)


def test_interrupted_download_keeps_what_arrived(tmp_path):
    """40분짜리 수집이 끊겨도 받은 만큼은 남아야 한다."""
    server = candles(1000)
    client = FakeUpbit(server, fail_after=1)

    # 첫 페이지는 성공하고 두 번째에서 끊긴다
    series = fetch(client, "KRW-BTC", "minute1", 1000, directory=tmp_path)

    saved = load(cache_path("KRW-BTC", "minute1", tmp_path))
    assert saved, "끊겼다고 받은 것까지 버리면 안 됩니다"
    assert len(series) == len(saved)


def test_a_failed_first_download_still_raises(tmp_path):
    """아무것도 못 받았으면 조용히 넘어가면 안 된다."""
    client = FakeUpbit([], fail_after=0)
    with pytest.raises(UpbitError):
        fetch(client, "KRW-BTC", "minute1", 100, directory=tmp_path)


def test_refresh_ignores_the_cache(tmp_path):
    have = candles(300)
    save(cache_path("KRW-BTC", "minute1", tmp_path), have)
    client = FakeUpbit(candles(300))

    fetch(client, "KRW-BTC", "minute1", 300, directory=tmp_path, refresh=True)
    assert client.requests == [None], "refresh면 처음부터 다시 받아야 합니다"


# ------------------------------------------------------------------ 저장/읽기
def test_round_trip_preserves_values(tmp_path):
    original = candles(50)
    path = save(tmp_path / "a.csv", original)
    back = load(path)
    assert len(back) == len(original)
    for a, b in zip(original, back, strict=True):
        assert a.ts == b.ts
        assert a.close == b.close  # repr()로 저장하므로 정확히 같아야 한다


def test_merge_removes_duplicates_and_sorts():
    a, b = candles(10), candles(10, start=START + timedelta(minutes=5))
    merged = merge(a, b)
    stamps = [c.ts for c in merged]
    assert stamps == sorted(stamps)
    assert len(stamps) == len(set(stamps))


def test_loading_a_foreign_csv_is_refused(tmp_path):
    path = tmp_path / "x.csv"
    path.write_text("time,price\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="헤더"):
        load(path)


def test_missing_file_is_empty_not_an_error(tmp_path):
    assert load(tmp_path / "없음.csv") == []
