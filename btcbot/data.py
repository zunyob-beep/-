"""과거 봉 수집과 CSV 캐시.

업비트는 한 번에 200개까지만 준다. `to` 파라미터로 과거로 거슬러 올라가며
페이지를 이어붙인다. 같은 구간을 반복해서 백테스트할 때 매번 API를 때리지
않도록 CSV로 캐시한다.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from .exchange.base import ExchangeError
from .exchange.upbit import UpbitClient, interval_length
from .models import Candle

log = logging.getLogger(__name__)

CACHE_DIR = Path("data")
HEADER = ["market", "ts", "open", "high", "low", "close", "volume"]


def fetch_history(
    client: UpbitClient,
    market: str,
    interval: str = "day",
    start: datetime | None = None,
    end: datetime | None = None,
    max_candles: int = 20_000,
) -> list[Candle]:
    """`start`부터 `end`까지의 봉을 오래된 순으로 모은다."""
    end = (end or datetime.now(timezone.utc)).astimezone(timezone.utc)
    collected: dict[datetime, Candle] = {}
    cursor = end

    while len(collected) < max_candles:
        batch = client.get_candles(market, interval, count=200, to=cursor)
        if not batch:
            break

        new = 0
        for candle in batch:
            if candle.ts not in collected:
                collected[candle.ts] = candle
                new += 1
        if new == 0:
            break  # 같은 페이지가 반복해서 오면 더 과거 데이터가 없는 것

        oldest = min(batch, key=lambda c: c.ts).ts
        if start and oldest <= start:
            break
        # `to`는 배타적이지 않으므로 한 봉 앞으로 당겨 무한루프를 막는다.
        cursor = oldest - interval_length(interval)
        log.debug("%s %s: %d개 수집, 커서 %s", market, interval, len(collected), cursor)

    candles = sorted(collected.values(), key=lambda c: c.ts)
    if start:
        candles = [c for c in candles if c.ts >= start]
    return [c for c in candles if c.ts <= end]


def cache_path(market: str, interval: str, directory: Path | str = CACHE_DIR) -> Path:
    return Path(directory) / f"{market}_{interval}.csv"


def save_csv(path: Path | str, candles: Iterable[Candle]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for candle in candles:
            writer.writerow(candle.to_row())
    return path


def load_csv(path: Path | str) -> list[Candle]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != HEADER:
            raise ValueError(f"{path}: 예상과 다른 CSV 헤더 {header}")
        return [Candle.from_row(row) for row in reader if row]


def merge(*groups: Sequence[Candle]) -> list[Candle]:
    """여러 묶음을 시각 기준으로 중복 없이 합친다(뒤쪽이 우선)."""
    merged: dict[datetime, Candle] = {}
    for group in groups:
        for candle in group:
            merged[candle.ts] = candle
    return sorted(merged.values(), key=lambda c: c.ts)


def load_or_fetch(
    client: UpbitClient,
    market: str,
    interval: str = "day",
    start: datetime | None = None,
    end: datetime | None = None,
    directory: Path | str = CACHE_DIR,
    refresh: bool = False,
) -> list[Candle]:
    """캐시를 우선 쓰되, 모자란 구간만 API로 채운다."""
    path = cache_path(market, interval, directory)
    cached = [] if refresh else load_csv(path)

    if cached and not refresh:
        have_start, have_end = cached[0].ts, cached[-1].ts
        need_older = start is not None and start < have_start
        need_newer = end is None or end > have_end
        if not need_older and not need_newer:
            log.info("캐시 사용: %s (%d개)", path, len(cached))
            return _slice(cached, start, end)

    try:
        fetched = fetch_history(client, market, interval, start=start, end=end)
    except ExchangeError as exc:
        # 네트워크가 끊겼다고 이미 받아둔 데이터로 하는 백테스트까지 막을
        # 이유는 없다. 다만 최신 구간이 빠졌을 수 있다는 건 알려준다.
        if not cached:
            raise
        log.warning("시세 조회 실패(%s) — 캐시 %d개로 진행합니다", exc, len(cached))
        return _slice(cached, start, end)

    candles = merge(cached, fetched)
    save_csv(path, candles)
    log.info("%s에 %d개 저장", path, len(candles))
    return _slice(candles, start, end)


def _slice(
    candles: Sequence[Candle], start: datetime | None, end: datetime | None
) -> list[Candle]:
    result = list(candles)
    if start:
        result = [c for c in result if c.ts >= start]
    if end:
        result = [c for c in result if c.ts <= end]
    return result


def parse_date(text: str) -> datetime:
    """'2024-01-01' 또는 '2024-01-01T09:00:00'을 UTC datetime으로."""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"날짜 형식을 알 수 없습니다: {text!r} (예: 2024-01-01)")
