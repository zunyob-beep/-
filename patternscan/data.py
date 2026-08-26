"""시세 수집과 CSV 캐시.

1분봉은 하루가 1,440개다. 한 달이면 43,200개이고 업비트는 한 번에 200개씩
주므로 216번 요청해야 한다. 매번 다시 받으면 분석할 때마다 몇 분씩 기다리게
되므로 반드시 캐시한다.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

from .models import Candle, Series, timeframe_length
from .upbit import UpbitClient, UpbitError

log = logging.getLogger(__name__)

CACHE_DIR = Path("data")
HEADER = ["ts", "open", "high", "low", "close", "volume"]


def cache_path(market: str, timeframe: str, directory: Path | str = CACHE_DIR) -> Path:
    return Path(directory) / f"{market}_{timeframe}.csv"


def save(path: Path | str, candles: list[Candle]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for candle in candles:
            writer.writerow(
                [
                    int(candle.ts.timestamp()),
                    repr(candle.open),
                    repr(candle.high),
                    repr(candle.low),
                    repr(candle.close),
                    repr(candle.volume),
                ]
            )
    return path


def load(path: Path | str) -> list[Candle]:
    path = Path(path)
    if not path.exists():
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


def merge(*groups: list[Candle]) -> list[Candle]:
    merged: dict[int, Candle] = {}
    for group in groups:
        merged.update({int(c.ts.timestamp()): c for c in group})
    return [merged[key] for key in sorted(merged)]


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
    path = cache_path(market, timeframe, directory)
    cached = [] if refresh else load(path)
    step = timeframe_length(timeframe)

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
    return Series.from_candles(market, timeframe, candles[-count:])


def load_cached(
    market: str, timeframe: str, directory: Path | str = CACHE_DIR, count: int | None = None
) -> Series:
    candles = load(cache_path(market, timeframe, directory))
    if count is not None:
        candles = candles[-count:]
    return Series.from_candles(market, timeframe, candles)
