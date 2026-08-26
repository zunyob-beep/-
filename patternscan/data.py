"""시세 수집과 CSV 캐시.

1분봉은 하루가 1,440개다. 한 달이면 43,200개이고 업비트는 한 번에 200개씩
주므로 216번 요청해야 한다. 매번 다시 받으면 분석할 때마다 몇 분씩 기다리게
되므로 반드시 캐시한다.
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Candle, Series, timeframe_length
from .upbit import UpbitClient, UpbitError

log = logging.getLogger(__name__)

CACHE_DIR = Path("data")
HEADER = ["ts", "open", "high", "low", "close", "volume"]


def cache_path(market: str, timeframe: str, directory: Path | str = CACHE_DIR) -> Path:
    return Path(directory) / f"{market}_{timeframe}.csv"


def _row(candle: Candle) -> list[object]:
    """repr()로 쓴다 — 소수점을 잘라 저장하면 읽을 때 값이 달라진다."""
    return [
        int(candle.ts.timestamp()),
        repr(candle.open),
        repr(candle.high),
        repr(candle.low),
        repr(candle.close),
        repr(candle.volume),
    ]


def save(path: Path | str, candles: list[Candle]) -> Path:
    """**통째로 바꿔치기한다.** 절대 제자리에서 고쳐 쓰지 않는다.

    예전에는 `open(path, "w")`로 열었다. 그 순간 파일이 0바이트가 되고,
    수십만 줄을 쓰는 몇 초 동안 파일은 계속 반쯤 쓰인 상태다. 그동안
    누가 읽으면 빈 파일이나 잘린 줄을 보게 된다.

    그리고 실제로 읽는 쪽이 있었다 — 수집이 도는 동안 화면은 0.5초마다
    상태를 물어보고, 서버는 그때마다 이 CSV를 읽는다. 재현해 보니
    **300번 중 94번이 깨졌다.** 사용자에게는 "예상과 다른 CSV 헤더 None"
    이라는 영문 모를 오류로 보였다.

    임시 파일에 다 쓴 뒤 os.replace로 갈아끼우면 이 창이 아예 없어진다.
    같은 폴더에 쓰는 이유는, 파일시스템이 다르면 replace가 원자적이지
    않기 때문이다. 프로세스가 중간에 죽어도 원본은 멀쩡히 남는다.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(HEADER)
            for candle in candles:
                writer.writerow(_row(candle))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def append(path: Path | str, candles: list[Candle]) -> Path:
    """이미 있는 파일 **뒤에만** 붙인다.

    과거 봉은 안 바뀐다. 새 봉 세 개를 얻자고 8년치 420만 줄을 통째로
    읽고 다시 쓰는 건 낭비다 — 재보니 버튼 한 번에 41초였다.

    붙이는 도중에 죽으면 마지막 줄이 잘릴 수 있다. 그건 load가 알아서
    버린다(아래) — 봉 하나는 다음번에 다시 받으면 그만이다.
    """
    path = Path(path)
    if not candles:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(HEADER)
        for candle in candles:
            writer.writerow(_row(candle))
    return path


def load(path: Path | str) -> list[Candle]:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        # 빈 파일은 '봉이 하나도 없다'는 뜻이지 고장이 아니다.
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != HEADER:
            raise ValueError(f"{path}: 예상과 다른 CSV 헤더 {header}")
        return [
            Candle(
                ts=datetime.fromtimestamp(int(row[0]), tz=timezone.utc),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in reader
            if row
        ]


def load_tail(path: Path | str, count: int) -> list[Candle]:
    """마지막 `count`개만. 파일 끝에서부터 필요한 만큼만 읽는다.

    분석은 전 구간을 봐야 하지만, "새 봉을 붙이고 최근 것만 돌려주기"는
    끝만 있으면 된다. 8년치 420만 줄을 파싱해서 뒤 5만 줄만 쓰는 건
    낭비다.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size == 0 or count <= 0:
        return []

    # 한 줄이 대략 60바이트. 넉넉히 잡고, 모자라면 두 배씩 늘린다.
    want = count + 1
    block = min(size, max(1 << 16, want * 96))
    with path.open("rb") as handle:
        while True:
            handle.seek(max(0, size - block))
            chunk = handle.read()
            lines = chunk.splitlines()
            if block >= size or len(lines) > want:
                break
            block *= 2

    if block < size:
        lines = lines[1:]      # 처음 줄은 중간에서 잘렸을 수 있다
    rows = [line for line in lines if line and not line.startswith(b"ts,")]
    # 잘린 줄은 _parse_rows가 버리므로, 자르기 전에 파싱해야 개수가 맞는다.
    # 먼저 잘라 버리면 잘린 줄 하나 때문에 결과가 하나 모자라게 된다.
    return _parse_rows(rows)[-count:]


def _parse_rows(rows: list[bytes]) -> list[Candle]:
    out: list[Candle] = []
    for row in rows:
        parts = row.decode("utf-8").rstrip("\r\n").split(",")
        if len(parts) != len(HEADER):
            continue      # 붙이다 만 마지막 줄. 그 봉은 다음번에 다시 받는다.
        try:
            out.append(
                Candle(
                    ts=datetime.fromtimestamp(int(parts[0]), tz=timezone.utc),
                    open=float(parts[1]), high=float(parts[2]), low=float(parts[3]),
                    close=float(parts[4]), volume=float(parts[5]),
                )
            )
        except (ValueError, OSError, OverflowError):
            continue
    return out


def merge(*groups: list[Candle]) -> list[Candle]:
    merged: dict[int, Candle] = {}
    for group in groups:
        merged.update({int(c.ts.timestamp()): c for c in group})
    return [merged[key] for key in sorted(merged)]


def _load_or_set_aside(path: Path) -> list[Candle]:
    """캐시가 못 읽을 상태면 옆으로 치우고 처음부터 받는다.

    예전 판이 남긴 반쯤 쓰인 파일 하나 때문에 수집이 통째로 죽었다.
    받아오면 그만인 것을 두고 사용자가 손으로 파일을 지우게 만들 이유가
    없다. 다만 **조용히 지우지는 않는다** — 지우는 게 맞는 판단이었는지
    나중에 확인할 수 있어야 하므로 이름만 바꿔 남긴다.
    """
    try:
        return load(path)
    except (ValueError, IndexError, UnicodeDecodeError) as exc:
        broken = path.with_name(path.name + ".broken")
        try:
            os.replace(path, broken)
        except OSError:
            log.warning("%s를 읽을 수 없고 치우지도 못했습니다 (%s)", path, exc)
            return []
        log.warning("%s를 읽을 수 없어 %s로 옮기고 새로 받습니다 (%s)", path, broken.name, exc)
        return []


def _just_the_new_ones(
    client: UpbitClient,
    market: str,
    timeframe: str,
    count: int,
    directory: Path | str,
    step: timedelta,
    progress: object,
) -> int | None:
    """가진 게 이미 충분하면, 새로 생긴 봉만 **뒤에 붙이고** 끝낸다.

    이게 거의 모든 경우다. 과거 봉은 안 바뀌므로 다시 받을 것도, 다시
    쓸 것도 없다. 그런데 예전에는 새 봉 세 개를 붙이자고 파일 전체를
    읽고(3.1초) 합치고(0.8초) 다시 썼다(1.3초). 1년치에서 5초, 8년치면
    **버튼 한 번에 41초**다. 1분마다 자동 갱신을 켜 두면 그 짓을 계속한다.

    쓸 수 없는 경우(캐시가 모자라거나 없을 때)에는 None을 돌려주고
    원래 경로로 넘긴다.
    """
    path = cache_path(market, timeframe, directory)
    span = span_cached(market, timeframe, directory)
    if span is None or count_cached(market, timeframe, directory) < count:
        return None      # 더 과거로 늘려야 한다 — 붙이기로는 안 된다

    last = span[1]
    behind = int((datetime.now(timezone.utc) - last) / step)
    fresh: list[Candle] = []
    if behind > 0:
        try:
            got = client.collect(
                market, timeframe, min(behind + 1, count),
                stop_at=last, progress=progress,
            )
        except UpbitError as exc:
            log.warning("새 봉을 못 받았습니다(%s) — 가진 것으로 갑니다", exc)
            got = []
        # collect는 이미 가진 구간에 '닿으면' 멈출 뿐, 걸러 주지는 않는다.
        fresh = [c for c in got if c.ts > last]

    if fresh:
        append(path, fresh)
        log.info("%s에 %d개 이어 붙였습니다", path, len(fresh))
    return count_cached(market, timeframe, directory)


def fetch(
    client: UpbitClient,
    market: str,
    timeframe: str,
    count: int,
    directory: Path | str = CACHE_DIR,
    refresh: bool = False,
    progress: object = None,
) -> Series:
    """`count`개의 최신 봉을 확보한다. **이미 받은 건 다시 받지 않는다.**

    예전에는 캐시가 있어도 매번 처음부터 다시 받았다. 8년치(420만 봉,
    40분 이상)를 받아둔 사람이 한 봉을 더 받으려 해도 8년을 통째로 다시
    받았고, 중간에 끊기면 받은 것이 전부 날아갔다. 그래서 지금은:

    1. 캐시 **뒤쪽**(새 봉)만 받아 이어 붙이고,
    2. 개수가 모자라면 캐시 **앞쪽**(더 과거)으로만 늘리고,
    3. 페이지를 받을 때마다 저장해 중간에 끊겨도 남긴다.
    """
    update(client, market, timeframe, count, directory, refresh, progress)
    return load_cached(market, timeframe, directory, count)


def update(
    client: UpbitClient,
    market: str,
    timeframe: str,
    count: int,
    directory: Path | str = CACHE_DIR,
    refresh: bool = False,
    progress: object = None,
) -> int:
    """캐시가 `count`개를 갖도록 맞추고, 몇 개가 됐는지만 돌려준다.

    시세를 받는 쪽은 대부분 봉 자체가 필요 없다 — 파일만 최신이면 된다.
    그런데 예전에는 fetch가 늘 Series를 만들어 돌려줬고, 화면은 그걸
    받자마자 버렸다. 8년치면 **아무도 안 쓰는 420만 개를 만드느라**
    30초가 넘게 걸렸다.
    """
    path = cache_path(market, timeframe, directory)
    step = timeframe_length(timeframe)

    if not refresh:
        quick = _just_the_new_ones(client, market, timeframe, count, directory, step, progress)
        if quick is not None:
            return quick

    cached = _load_or_set_aside(path) if not refresh else []

    def keep(candles: list[Candle]) -> None:
        """받는 도중에도 저장해 둔다 — 끊겨도 다시 받지 않게."""
        save(path, merge(cached, candles))

    gathered: list[Candle] = []
    try:
        if cached:
            # ① 새로 생긴 봉만 (캐시 마지막 시각 이후)
            behind = int((datetime.now(timezone.utc) - cached[-1].ts) / step)
            if behind > 0:
                gathered += client.collect(
                    market, timeframe, min(behind + 1, count),
                    stop_at=cached[-1].ts, progress=progress, on_batch=keep,
                )
            # ② 개수가 모자라면 더 과거로만
            have = len({int(c.ts.timestamp()) for c in cached + gathered})
            if have < count:
                gathered += client.collect(
                    market, timeframe, count - have,
                    end=cached[0].ts - step, progress=progress, on_batch=keep,
                )
        else:
            gathered = client.collect(market, timeframe, count, progress=progress, on_batch=keep)
    except UpbitError as exc:
        if not cached and not gathered:
            raise
        log.warning("시세 조회가 중단됐습니다(%s) — 받은 만큼으로 진행합니다", exc)

    candles = merge(cached, gathered)
    if candles:
        save(path, candles)
        log.info("%s에 %d개 저장", path, len(candles))
    return len(candles)


#: 경로 → (크기, 수정시각, 봉 개수). 파일이 바뀌면 앞의 둘이 안 맞아 다시 센다.
_COUNTS: dict[str, tuple[int, float, int]] = {}


def count_cached(market: str, timeframe: str, directory: Path | str = CACHE_DIR) -> int:
    """캐시에 봉이 몇 개 있는지만. **파싱하지 않는다.**

    화면은 수집이 도는 동안 0.5초마다 상태를 물어보고, 서버는 그때마다
    "받아둔 시세: 1분봉 33,400개"를 답해야 한다. 그 숫자 하나를 위해
    예전에는 CSV 전체를 읽어 Candle 33,400개를 만들고 즉시 버렸다.
    8년치(420만 봉)라면 0.5초마다 그 짓을 한다.

    줄 수만 세면 충분하고, 파일이 그대로면 그것도 다시 안 센다.
    """
    path = cache_path(market, timeframe, directory)
    try:
        stat = path.stat()
    except OSError:
        return 0
    key = str(path)
    seen = _COUNTS.get(key)
    if seen is not None and seen[:2] == (stat.st_size, stat.st_mtime):
        return seen[2]

    try:
        with path.open("rb") as handle:
            rows = sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 20), b""))
    except OSError:
        return 0
    total = max(rows - 1, 0)  # 머리말 한 줄
    _COUNTS[key] = (stat.st_size, stat.st_mtime, total)
    return total


def span_cached(
    market: str, timeframe: str, directory: Path | str = CACHE_DIR
) -> tuple[datetime, datetime] | None:
    """캐시가 **언제부터 언제까지**인지. 첫 줄과 마지막 줄만 읽는다.

    "1분봉 33,400개"만 보여주면 그게 많은 건지 적은 건지 알 수가 없다.
    "2026-07-27 ~ 2026-08-26"이라고 하면 한눈에 온다. 그 숫자 하나 때문에
    파일 전체를 읽을 수는 없으므로 양 끝만 집는다.

    개수로 날짜를 짐작하면 안 된다 — 업비트는 거래가 없는 분의 봉을 아예
    안 주므로, 33,400개가 꼭 23일치인 것은 아니다.
    """
    path = cache_path(market, timeframe, directory)
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None

    try:
        with path.open("rb") as handle:
            handle.readline()                     # 머리말
            first = handle.readline()
            if not first.strip():
                return None
            # 마지막 줄: 끝에서 조금만 읽어 뒤에서 찾는다
            handle.seek(max(0, size - 4096))
            tail = [line for line in handle.read().splitlines() if line.strip()]
            last = tail[-1] if tail else first
    except OSError:
        return None

    def stamp(row: bytes) -> datetime | None:
        head = row.split(b",", 1)[0]
        try:
            return datetime.fromtimestamp(int(head), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None

    start, end = stamp(first), stamp(last)
    if start is None or end is None:
        return None
    return (start, end) if start <= end else (end, start)


def load_cached(
    market: str, timeframe: str, directory: Path | str = CACHE_DIR, count: int | None = None
) -> Series:
    candles = load(cache_path(market, timeframe, directory))
    if count is not None:
        candles = candles[-count:]
    return Series.from_candles(market, timeframe, candles)
