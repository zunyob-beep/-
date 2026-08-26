"""시세 수집과 캐시 검증.

여기서 지키는 것은 하나다: **이미 받은 것을 다시 받지 않는다.**

8년치 1분봉은 420만 개이고, 업비트가 200개씩 주므로 21,000번 요청해야 한다.
40분이 넘는다. 예전에는 캐시가 있어도 매번 처음부터 다시 받았고, 중간에
끊기면 받은 것이 전부 날아갔다. 40분을 기다렸다가 39분에 끊겨 아무것도 안
남는 도구는 아무도 두 번 쓰지 않는다.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone

import pytest

from patternscan.data import cache_path, count_cached, fetch, load, merge, save
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


# ------------------------------------------------- 쓰는 동안 읽어도 안 깨진다
#
# 실제로 겪은 일이다. 코드스페이스에서 시세를 받는 동안 화면에
# "data/KRW-BTC_minute1.csv: 예상과 다른 CSV 헤더 None" 이 떴다.
#
# 원인은 save가 open(path, "w")로 열던 것. 그 순간 파일이 0바이트가 되고
# 수만 줄을 쓰는 몇 초 동안 반쯤 쓰인 상태로 남는다. 그런데 화면은 수집이
# 도는 동안 0.5초마다 상태를 물어보고, 서버는 그때마다 이 CSV를 읽었다.
def _fake(count, start=None):
    start = start or datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(ts=start + timedelta(minutes=i), open=1.0, high=1.0, low=1.0,
               close=1.0 + i, volume=1.0)
        for i in range(count)
    ]


def test_reading_while_writing_never_sees_a_torn_file(tmp_path):
    """고치기 전에는 300번 중 94번이 깨졌다."""
    import threading

    path = tmp_path / "KRW-BTC_minute1.csv"
    candles = _fake(4000)
    save(path, candles)

    stop = threading.Event()

    def writer():
        while not stop.is_set():
            save(path, candles)

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            # 어느 시점에 읽어도 '완전한 파일'이어야 한다.
            assert len(load(path)) == len(candles)
    finally:
        stop.set()
        thread.join(timeout=5)


def test_a_crash_while_saving_leaves_the_old_file_intact(tmp_path, monkeypatch):
    """제자리에서 고쳐 쓰면 죽는 순간 원본이 날아간다."""
    path = tmp_path / "KRW-BTC_minute1.csv"
    save(path, _fake(100))

    real = csv.writer

    class DiesPartway:
        """스무 줄쯤 쓰다가 디스크가 꽉 찬 상황."""

        def __init__(self, *args, **kwargs):
            self._writer = real(*args, **kwargs)
            self._written = 0

        def writerow(self, row):
            self._written += 1
            if self._written > 20:
                raise OSError("디스크가 꽉 찼습니다")
            return self._writer.writerow(row)

    monkeypatch.setattr(csv, "writer", DiesPartway)
    with pytest.raises(OSError):
        save(path, _fake(500))

    monkeypatch.undo()
    assert len(load(path)) == 100, "죽는 바람에 예전 캐시까지 잃었습니다"


def test_no_leftover_temp_files(tmp_path):
    """임시 파일이 쌓이면 사용자는 data 폴더가 왜 이러나 하게 된다."""
    path = tmp_path / "KRW-BTC_minute1.csv"
    save(path, _fake(50))
    save(path, _fake(60))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["KRW-BTC_minute1.csv"]


def test_an_empty_file_means_no_candles_not_a_crash(tmp_path):
    """빈 파일은 '봉이 없다'는 뜻이지 고장이 아니다."""
    path = tmp_path / "KRW-BTC_minute1.csv"
    path.write_bytes(b"")
    assert load(path) == []


def test_a_truly_corrupt_cache_is_set_aside_instead_of_killing_the_fetch(tmp_path, caplog):
    """옛 판이 남긴 파일 하나 때문에 수집이 통째로 죽을 이유가 없다."""
    from patternscan.data import _load_or_set_aside

    path = tmp_path / "KRW-BTC_minute1.csv"
    path.write_text("이건 CSV가 아닙니다\n", encoding="utf-8")
    assert _load_or_set_aside(path) == []
    # 조용히 지우지는 않는다 — 판단이 맞았는지 확인할 수 있어야 한다.
    assert (tmp_path / "KRW-BTC_minute1.csv.broken").exists()
    assert not path.exists()


# ------------------------------------------------------------ 개수만 세기
def test_counting_does_not_parse_the_whole_file(tmp_path):
    save(cache_path("KRW-BTC", "minute1", tmp_path), _fake(1234))
    assert count_cached("KRW-BTC", "minute1", tmp_path) == 1234


def test_counting_notices_when_the_file_grows(tmp_path):
    """기억해 둔 값을 계속 돌려주면 수집 진행 상황이 멈춰 보인다."""
    save(cache_path("KRW-BTC", "minute1", tmp_path), _fake(100))
    assert count_cached("KRW-BTC", "minute1", tmp_path) == 100
    save(cache_path("KRW-BTC", "minute1", tmp_path), _fake(300))
    assert count_cached("KRW-BTC", "minute1", tmp_path) == 300


def test_counting_several_timeframes_does_not_evict_each_other(tmp_path):
    """세 간격을 번갈아 물어보므로, 하나만 기억하면 캐시가 무용지물이다."""
    for tf, n in (("minute1", 900), ("minute3", 300), ("minute5", 180)):
        save(cache_path("KRW-BTC", tf, tmp_path), _fake(n))
    for _ in range(3):
        assert count_cached("KRW-BTC", "minute1", tmp_path) == 900
        assert count_cached("KRW-BTC", "minute3", tmp_path) == 300
        assert count_cached("KRW-BTC", "minute5", tmp_path) == 180


def test_counting_a_missing_file_is_zero_not_an_error(tmp_path):
    assert count_cached("KRW-BTC", "minute1", tmp_path) == 0


# --------------------------------------------------- 언제부터 언제까지인지
def test_span_reads_only_the_two_ends(tmp_path):
    """"33,400개"만 보여주면 많은 건지 적은 건지 알 수가 없다."""
    from patternscan.data import span_cached

    start = datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc)
    save(cache_path("KRW-BTC", "minute1", tmp_path), _fake(33_400, start))
    span = span_cached("KRW-BTC", "minute1", tmp_path)
    assert span is not None
    assert span[0] == start
    assert span[1] == start + timedelta(minutes=33_399)


def test_span_of_a_single_candle(tmp_path):
    """마지막 줄을 뒤에서 찾는데, 줄이 하나뿐이면 앞뒤가 같은 줄이다."""
    from patternscan.data import span_cached

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    save(cache_path("KRW-BTC", "minute1", tmp_path), _fake(1, start))
    assert span_cached("KRW-BTC", "minute1", tmp_path) == (start, start)


def test_span_of_nothing_is_none(tmp_path):
    from patternscan.data import span_cached

    assert span_cached("KRW-BTC", "minute1", tmp_path) is None
    cache_path("KRW-BTC", "minute3", tmp_path).parent.mkdir(parents=True, exist_ok=True)
    cache_path("KRW-BTC", "minute3", tmp_path).write_bytes(b"")
    assert span_cached("KRW-BTC", "minute3", tmp_path) is None


def test_span_does_not_read_the_whole_file(tmp_path, monkeypatch):
    """8년치면 420만 줄이다. 날짜 두 개 때문에 그걸 다 읽으면 안 된다."""
    from patternscan import data as data_module

    save(cache_path("KRW-BTC", "minute1", tmp_path), _fake(50_000))

    def forbidden(*args, **kwargs):
        raise AssertionError("span_cached가 파일 전체를 파싱했습니다")

    monkeypatch.setattr(data_module, "load", forbidden)
    assert data_module.span_cached("KRW-BTC", "minute1", tmp_path) is not None
