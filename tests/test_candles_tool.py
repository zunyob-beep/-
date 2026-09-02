"""미리 받아 두는 도구(tools/candles.py)가 제대로 적는지.

이 도구가 틀리면 앱이 통째로 못 돈다 — 이제 앱은 업비트가 아니라 **이 도구가
적어 둔 파일**로 돈다. 그래서 여기가 조용히 깨지면 화면에는 "데이터를 못
받아옵니다"만 뜨고 원인은 저 멀리 있게 된다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from patternscan.models import Candle
from tools import candles as tool


def _candle(ts: int, close: float = 100.0) -> Candle:
    return Candle(
        ts=datetime.fromtimestamp(ts, tz=timezone.utc),
        open=close - 1,
        high=close + 2,
        low=close - 2,
        close=close,
        volume=1.5,
    )


class FakeClient:
    """업비트 대신 답한다. 진짜를 부르는 시험은 만들 수 없다 — 값이 매번 다르다."""

    def __init__(self, rows: list[Candle]) -> None:
        self.rows = rows
        self.asked: list[tuple[int, datetime | None]] = []

    def collect(self, market, timeframe, count, stop_at=None, **_):
        self.asked.append((count, stop_at))
        return self.rows[-count:] if count < len(self.rows) else list(self.rows)


def test_적은_것을_다시_읽으면_같다(tmp_path):
    path = tmp_path / "KRW-BTC.min1.json"
    now = int(datetime.now(timezone.utc).timestamp() // 60 * 60)
    rows = {ts: tool._row(_candle(ts, 100 + i)) for i, ts in enumerate(range(now - 600, now, 60))}

    tool.write(path, "KRW-BTC", rows, days=14)
    again = tool.read_existing(path)

    assert again == rows


def test_파일_모양이_앱이_읽는_모양이다(tmp_path):
    """앱(web/core/seed.js)이 이 다섯 칸을 그대로 읽는다."""
    path = tmp_path / "KRW-BTC.min1.json"
    now = int(datetime.now(timezone.utc).timestamp() // 60 * 60)
    rows = {ts: tool._row(_candle(ts)) for ts in range(now - 300, now, 60)}
    tool.write(path, "KRW-BTC", rows, days=14)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["market"] == "KRW-BTC"
    assert payload["timeframe"] == "minute1"
    assert payload["step"] == 60
    assert isinstance(payload["made"], int)
    # 봉 하나는 [시각, 시가, 고가, 저가, 종가, 거래량] 여섯 칸
    assert all(len(row) == 6 for row in payload["rows"])
    # 오래된 것부터
    stamps = [row[0] for row in payload["rows"]]
    assert stamps == sorted(stamps)


def test_깨진_파일은_없는_셈_친다(tmp_path):
    path = tmp_path / "KRW-BTC.min1.json"
    path.write_text("{이건 JSON이 아니다", encoding="utf-8")
    assert tool.read_existing(path) == {}


def test_가진_게_있으면_모자란_만큼만_받는다():
    """**여기가 이 도구의 핵심이다.**

    20분마다 도는데 매번 14일치(101쪽)를 처음부터 받으면 업비트에 폐가 되고,
    우리도 느리다. 지난번 파일에 이어 붙이면 보통 한 쪽이면 끝난다.
    """
    now = int(datetime.now(timezone.utc).timestamp() // 60 * 60)
    have = {ts: tool._row(_candle(ts)) for ts in range(now - 3600, now - 300, 60)}
    client = FakeClient([_candle(ts) for ts in range(now - 360, now, 60)])

    tool.fetch(client, "KRW-BTC", have, days=14, say=lambda *_: None)

    count, stop_at = client.asked[0]
    assert count < 100, f"이어 받아야 하는데 {count}개를 달라고 했습니다"
    assert stop_at is not None, "어디까지 받을지 안 알려 줬습니다"


def test_가진_게_없으면_전부_받는다():
    now = int(datetime.now(timezone.utc).timestamp() // 60 * 60)
    client = FakeClient([_candle(ts) for ts in range(now - 600, now, 60)])

    tool.fetch(client, "KRW-BTC", {}, days=2, say=lambda *_: None)

    count, stop_at = client.asked[0]
    assert count == 2 * 24 * 60
    assert stop_at is None


def test_오래된_것은_버린다():
    """안 버리면 파일이 끝없이 자란다. 20분마다 도는 것이라 금세 티가 난다."""
    now = int(datetime.now(timezone.utc).timestamp() // 60 * 60)
    old = now - int(timedelta(days=40).total_seconds())
    have = {ts: tool._row(_candle(ts)) for ts in range(old, old + 600, 60)}
    client = FakeClient([_candle(ts) for ts in range(now - 600, now, 60)])

    merged = tool.fetch(client, "KRW-BTC", have, days=14, say=lambda *_: None)

    assert all(ts > now - 15 * 86400 for ts in merged), "14일보다 오래된 것이 남았습니다"


def test_한_종목이_실패해도_나머지는_적는다(tmp_path, monkeypatch):
    """전부 실패했을 때만 판을 실패로 끝낸다 — 그래야 옛 파일이 그대로 남는다."""
    now = int(datetime.now(timezone.utc).timestamp() // 60 * 60)
    good = [_candle(ts) for ts in range(now - 600, now, 60)]

    def fake_fetch(client, market, have, days, say=print):
        if market == "KRW-ETH":
            raise tool.UpbitError("막혔습니다")
        return {int(c.ts.timestamp()): tool._row(c) for c in good}

    monkeypatch.setattr(tool, "fetch", fake_fetch)
    code = tool.main(["--out", str(tmp_path), "--markets", "KRW-BTC", "KRW-ETH"])

    assert code == 0
    assert (tmp_path / "KRW-BTC.min1.json").is_file()
    assert not (tmp_path / "KRW-ETH.min1.json").exists()


def test_전부_실패하면_실패로_끝낸다(tmp_path, monkeypatch):
    def always_fails(*_args, **_kwargs):
        raise tool.UpbitError("막혔습니다")

    monkeypatch.setattr(tool, "fetch", always_fails)
    assert tool.main(["--out", str(tmp_path), "--markets", "KRW-BTC"]) == 1


def test_앱이_아는_종목과_같다():
    """web/core/models.js의 MARKETS와 어긋나면 그 종목만 조용히 안 된다."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "web" / "core" / "models.js"
    text = source.read_text(encoding="utf-8")
    for market in tool.MARKETS:
        assert f"'{market}'" in text, f"{market}이 앱에 없습니다"


@pytest.mark.parametrize("days", [1, 7, 14])
def test_며칠치를_달라는지_그대로_센다(days):
    now = int(datetime.now(timezone.utc).timestamp() // 60 * 60)
    client = FakeClient([_candle(ts) for ts in range(now - 600, now, 60)])
    tool.fetch(client, "KRW-BTC", {}, days=days, say=lambda *_: None)
    assert client.asked[0][0] == days * 24 * 60
