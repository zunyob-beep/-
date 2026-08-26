"""남이 만든 CSV를 이 도구 형식으로 들여온다.

업비트에서 직접 받는 게 기본이지만, 다른 데서 구한 과거 데이터를 쓰고
싶을 때가 있다. 예를 들어 업비트는 2017년 10월 개장이라 그 이전이 없고,
공개된 Bitstamp/Coinbase 데이터는 2012년까지 거슬러 올라간다.

주의: **서로 다른 거래소 데이터를 이어 붙이지 마라.**
거래소가 다르면 통화도, 유동성도, 프리미엄도 다르다. 이어 붙이면 이음매에서
없던 모양이 생기고, 그 자리를 '과거에 있던 모양'이라며 세게 된다.
비교하고 싶으면 **따로 들여와 따로 돌려서 결과를 맞춰보는** 것이 맞다.
서로 다른 거래소에서 같은 답이 나오면 그게 훨씬 강한 근거다.
"""

from __future__ import annotations

import csv as csvmod
import logging
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path

from .models import Candle, timeframe_length

log = logging.getLogger(__name__)

#: 시각 칸으로 인정하는 이름들 (소문자 비교)
TIME_COLUMNS = ("timestamp", "time", "date", "datetime", "open_time", "candle_date_time_utc")

#: 값 칸 이름 후보
COLUMN_ALIASES = {
    "open": ("open", "opening_price", "o"),
    "high": ("high", "high_price", "h"),
    "low": ("low", "low_price", "l"),
    "close": ("close", "trade_price", "closing_price", "c"),
    "volume": ("volume", "candle_acc_trade_volume", "v", "base_volume"),
}


class ImportError_(ValueError):
    """들여오기 실패."""


def _find_column(header: list[str], names: tuple[str, ...]) -> int | None:
    lowered = [h.strip().lower() for h in header]
    for name in names:
        if name in lowered:
            return lowered.index(name)
    return None


def _parse_time(raw: str) -> datetime:
    """유닉스 초/밀리초, ISO 문자열을 모두 받는다."""
    text = raw.strip()
    try:
        number = float(text)
    except ValueError:
        pass
    else:
        # 밀리초로 준 경우 (2001년 이후를 초로 읽으면 말이 안 되는 값이 된다)
        if number > 1e11:
            number /= 1000.0
        return datetime.fromtimestamp(number, tz=timezone.utc)

    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ImportError_(f"시각을 읽을 수 없습니다: {raw!r}") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def read_csv(path: Path | str, limit: int | None = None) -> list[Candle]:
    """OHLCV CSV를 읽는다. 칸 이름은 흔한 변형들을 알아서 맞춘다.

    값이 비었거나 숫자가 아닌 줄은 건너뛴다 (공개 데이터에는 흔하다).
    """
    path = Path(path)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csvmod.reader(handle)
        header = next(reader, None)
        if not header:
            raise ImportError_(f"{path}: 빈 파일입니다")

        time_index = _find_column(header, TIME_COLUMNS)
        if time_index is None:
            raise ImportError_(
                f"{path}: 시각 칸을 찾지 못했습니다. "
                f"머리글: {header[:8]} · 인정하는 이름: {', '.join(TIME_COLUMNS)}"
            )
        indexes = {}
        for field, names in COLUMN_ALIASES.items():
            found = _find_column(header, names)
            if found is None and field != "volume":
                raise ImportError_(f"{path}: '{field}' 칸을 찾지 못했습니다. 머리글: {header[:8]}")
            indexes[field] = found

        candles: list[Candle] = []
        skipped = 0
        for row in reader:
            if not row or len(row) <= time_index:
                continue
            try:
                close = float(row[indexes["close"]])
                if close <= 0:
                    raise ValueError
                candles.append(
                    Candle(
                        ts=_parse_time(row[time_index]),
                        open=float(row[indexes["open"]]),
                        high=float(row[indexes["high"]]),
                        low=float(row[indexes["low"]]),
                        close=close,
                        volume=(
                            float(row[indexes["volume"]])
                            if indexes["volume"] is not None and row[indexes["volume"]].strip()
                            else 0.0
                        ),
                    )
                )
            except (ValueError, IndexError, ImportError_):
                skipped += 1
                continue
            if limit is not None and len(candles) >= limit:
                break

    if skipped:
        log.info("값이 없거나 잘못된 줄 %d개를 건너뛰었습니다", skipped)
    if not candles:
        raise ImportError_(f"{path}: 쓸 수 있는 봉이 하나도 없습니다")

    candles.sort(key=lambda c: c.ts)
    return candles


def resample(candles: list[Candle], factor: int, source: str = "minute1") -> list[Candle]:
    """1분봉을 3분봉·5분봉으로 묶는다.

    두 가지를 지킨다.

    1. **시계에 맞춰 자른다.** 3분봉은 0·3·6분에 시작한다. 아무 데서나
       묶으면 업비트가 주는 봉과 다른 봉이 되어, 나중에 실제 시세로
       돌렸을 때 결과가 안 맞는다.
    2. **빠진 봉이 있으면 그 묶음을 버린다.** 3분봉 하나는 1분봉 3개로
       만들어져야 한다. 2개로 만들면 없던 봉을 지어내는 것이다.
    """
    if factor <= 1:
        return list(candles)

    step = int(timeframe_length(source).total_seconds())
    span = step * factor
    buckets: dict[int, list[Candle]] = {}
    for candle in candles:
        key = (int(candle.ts.timestamp()) // span) * span
        buckets.setdefault(key, []).append(candle)

    out: list[Candle] = []
    for key in sorted(buckets):
        group = sorted(buckets[key], key=lambda c: c.ts)
        if len(group) != factor:
            continue  # 빠진 봉이 있는 묶음은 만들지 않는다
        out.append(
            Candle(
                ts=datetime.fromtimestamp(key, tz=timezone.utc),
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            )
        )
    return out


def describe(candles: list[Candle], timeframe: str = "minute1") -> str:
    """들여온 데이터가 멀쩡한지 사람이 눈으로 볼 수 있게."""
    if not candles:
        return "  봉이 없습니다"
    step = int(timeframe_length(timeframe).total_seconds())
    stamps = [int(c.ts.timestamp()) for c in candles]
    gaps = sum(1 for a, b in pairwise(stamps) if b - a != step)
    closes = [c.close for c in candles]
    return "\n".join(
        [
            f"  봉 {len(candles):,}개",
            f"  기간 {candles[0].ts:%Y-%m-%d %H:%M} ~ {candles[-1].ts:%Y-%m-%d %H:%M} UTC",
            f"  가격 {min(closes):,.2f} ~ {max(closes):,.2f}  "
            f"(처음 {closes[0]:,.2f} → 마지막 {closes[-1]:,.2f})",
            f"  끊긴 구간 {gaps:,}곳",
        ]
    )
