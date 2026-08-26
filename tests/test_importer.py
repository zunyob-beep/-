"""외부 CSV 들여오기 검증.

여기서 지키는 것은 하나다: **없던 봉을 지어내지 않는다.**
3분봉 하나는 1분봉 정확히 3개로 만들어져야 한다. 2개로 만들면 실제로는
거래가 없던 시간을 있었던 것처럼 꾸미는 것이고, 그 자리가 '과거에 있던
모양'으로 잡히면 통계가 통째로 거짓이 된다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from patternscan.importer import ImportError_, describe, read_csv, resample
from patternscan.models import Candle

START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def write(path, header, rows):
    lines = [",".join(header)] + [",".join(str(v) for v in r) for r in rows]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ------------------------------------------------------------------ 읽기
def test_reads_unix_seconds(tmp_path):
    path = write(
        tmp_path / "a.csv",
        ["Timestamp", "Open", "High", "Low", "Close", "Volume"],
        [[1704067200, 100, 101, 99, 100.5, 1.0], [1704067260, 100.5, 102, 100, 101, 2.0]],
    )
    candles = read_csv(path)
    assert len(candles) == 2
    assert candles[0].ts == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert candles[1].close == 101


def test_reads_unix_milliseconds(tmp_path):
    """바이낸스류는 밀리초로 준다 — 초로 읽으면 서기 55000년이 된다."""
    path = write(
        tmp_path / "a.csv",
        ["open_time", "open", "high", "low", "close", "volume"],
        [[1704067200000, 100, 101, 99, 100.5, 1.0]],
    )
    assert read_csv(path)[0].ts == datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_reads_iso_timestamps(tmp_path):
    path = write(
        tmp_path / "a.csv",
        ["datetime", "open", "high", "low", "close"],
        [["2024-01-01T00:00:00Z", 100, 101, 99, 100.5]],
    )
    assert read_csv(path)[0].ts == datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_accepts_upbit_column_names(tmp_path):
    path = write(
        tmp_path / "a.csv",
        ["candle_date_time_utc", "opening_price", "high_price", "low_price", "trade_price"],
        [["2024-01-01T00:00:00", 100, 101, 99, 100.5]],
    )
    assert read_csv(path)[0].close == 100.5


def test_rows_with_missing_values_are_skipped(tmp_path):
    """공개 데이터에는 빈 줄이 흔하다. 0으로 채우면 없던 폭락이 생긴다."""
    path = write(
        tmp_path / "a.csv",
        ["Timestamp", "Open", "High", "Low", "Close", "Volume"],
        [
            [1704067200, 100, 101, 99, 100.5, 1.0],
            [1704067260, "", "", "", "", ""],
            [1704067320, 101, 102, 100, 101.5, 1.0],
        ],
    )
    candles = read_csv(path)
    assert len(candles) == 2
    assert all(c.close > 0 for c in candles)


def test_rows_are_sorted_by_time(tmp_path):
    path = write(
        tmp_path / "a.csv",
        ["Timestamp", "Open", "High", "Low", "Close"],
        [[1704067320, 1, 1, 1, 3], [1704067200, 1, 1, 1, 1], [1704067260, 1, 1, 1, 2]],
    )
    assert [c.close for c in read_csv(path)] == [1, 2, 3]


def test_limit_stops_early(tmp_path):
    rows = [[1704067200 + 60 * i, 1, 1, 1, 1 + i] for i in range(50)]
    path = write(tmp_path / "a.csv", ["Timestamp", "Open", "High", "Low", "Close"], rows)
    assert len(read_csv(path, limit=10)) == 10


def test_missing_time_column_is_explained(tmp_path):
    path = write(tmp_path / "a.csv", ["a", "b", "c"], [[1, 2, 3]])
    with pytest.raises(ImportError_, match="시각 칸"):
        read_csv(path)


def test_missing_price_column_is_explained(tmp_path):
    path = write(tmp_path / "a.csv", ["Timestamp", "Open"], [[1704067200, 100]])
    with pytest.raises(ImportError_, match="'high'"):
        read_csv(path)


def test_file_with_no_usable_rows_is_rejected(tmp_path):
    path = write(
        tmp_path / "a.csv",
        ["Timestamp", "Open", "High", "Low", "Close"],
        [[1704067200, "", "", "", ""]],
    )
    with pytest.raises(ImportError_, match="봉이 하나도"):
        read_csv(path)


# ------------------------------------------------------------------ 묶기
def _minutes(count, start=START, skip=()):
    return [
        Candle(
            ts=start + timedelta(minutes=i),
            open=100.0 + i, high=100.0 + i + 0.5, low=100.0 + i - 0.5,
            close=100.0 + i, volume=1.0,
        )
        for i in range(count)
        if i not in skip
    ]


def test_resample_groups_by_the_clock():
    """3분봉은 0·3·6분에 시작해야 한다 — 업비트가 주는 봉과 같아야 한다."""
    grouped = resample(_minutes(9), 3)
    assert len(grouped) == 3
    for candle in grouped:
        assert candle.ts.minute % 3 == 0


def test_resample_takes_open_high_low_close_correctly():
    grouped = resample(_minutes(3), 3)
    assert len(grouped) == 1
    only = grouped[0]
    assert only.open == 100.0          # 첫 봉의 시가
    assert only.close == 102.0         # 마지막 봉의 종가
    assert only.high == pytest.approx(102.5)
    assert only.low == pytest.approx(99.5)
    assert only.volume == 3.0


def test_resample_drops_incomplete_groups():
    """1분봉이 2개뿐인 3분 구간으로 3분봉을 만들면 없던 봉을 지어내는 것이다."""
    grouped = resample(_minutes(9, skip={4}), 3)
    starts = [c.ts.minute for c in grouped]
    assert 3 not in starts, "빠진 봉이 있는 묶음이 만들어졌습니다"
    assert len(grouped) == 2


def test_resample_of_factor_one_is_a_copy():
    candles = _minutes(5)
    assert resample(candles, 1) == candles


def test_five_minute_grouping():
    grouped = resample(_minutes(20), 5)
    assert len(grouped) == 4
    assert all(c.ts.minute % 5 == 0 for c in grouped)


# ------------------------------------------------------------------ 요약
def test_describe_counts_gaps():
    text = describe(_minutes(10, skip={5}))
    assert "봉 9개" in text
    assert "끊긴 구간 1곳" in text


def test_describe_handles_empty():
    assert "봉이 없습니다" in describe([])
