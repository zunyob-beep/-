"""미리 받아 두는 도구(tools/candles.py)가 제대로 적는지.

이 도구가 틀리면 앱이 통째로 못 돈다 — 이제 앱은 업비트가 아니라 **이 도구가
적어 둔 파일**로 돈다. 그래서 여기가 조용히 깨지면 화면에는 "데이터를 못
받아옵니다"만 뜨고 원인은 저 멀리 있게 된다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from patternscan.models import Candle
from tools import candles as tool
from tools.pack import unpack


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

    def __init__(self, rows: list[Candle] | None = None) -> None:
        self.rows = rows or []
        self.asked: list[tuple[int, datetime | None, datetime | None]] = []

    def collect(self, market, timeframe, count, end=None, stop_at=None, **_):
        self.asked.append((count, end, stop_at))
        return list(self.rows)


def _minutes(first: int, n: int) -> list[Candle]:
    return [_candle(first + i * 60, 100 + i) for i in range(n)]


# ------------------------------------------------------------------ 왕복
def test_적은_것을_다시_읽으면_같다(tmp_path):
    path = tmp_path / "KRW-BTC.json"
    now = int(datetime.now(timezone.utc).timestamp() // 60 * 60)
    rows = {ts: [ts, 99.0, 102.0, 98.0, 100.0, 1.5] for ts in range(now - 600, now, 60)}

    tool.write_rows(path, "KRW-BTC", rows)
    again = tool.read_rows(path)

    assert sorted(again) == sorted(rows)
    for ts in rows:
        assert again[ts][4] == rows[ts][4]


def test_파일_모양이_앱이_읽는_모양이다(tmp_path):
    """앱(web/core/seed.js)의 unpackSeed가 이 칸들을 그대로 읽는다."""
    path = tmp_path / "KRW-BTC.json"
    now = int(datetime.now(timezone.utc).timestamp() // 60 * 60)
    rows = {ts: [ts, 99.0, 102.0, 98.0, 100.0, 1.5] for ts in range(now - 300, now, 60)}
    tool.write_rows(path, "KRW-BTC", rows)

    payload = json.loads(path.read_text(encoding="utf-8"))
    for key in ("m", "step", "from", "n", "scale", "made", "t", "c", "o", "h", "l", "v"):
        assert key in payload, f"{key} 칸이 없습니다"
    assert payload["m"] == "KRW-BTC"
    assert payload["step"] == 60
    # 오래된 것부터
    stamps = [r[0] for r in unpack(payload)]
    assert stamps == sorted(stamps)


def test_깨진_파일은_없는_셈_친다(tmp_path):
    path = tmp_path / "KRW-BTC.json"
    path.write_text("{이건 JSON이 아니다", encoding="utf-8")
    assert tool.read_rows(path) == {}


# -------------------------------------------------------------- 이어 받기
def test_가진_게_있으면_모자란_만큼만_받는다():
    """**여기가 이 도구의 핵심이다.**

    10분마다 도는데 매번 처음부터 받으면 업비트에 폐가 되고 우리도 느리다.
    지난번 파일에 이어 붙이면 보통 한 쪽이면 끝난다.
    """
    now = datetime.now(timezone.utc)
    have = {int((now - timedelta(minutes=i)).timestamp()) // 60 * 60:
            [0, 1.0, 1.0, 1.0, 1.0, 1.0] for i in range(30, 120)}
    for ts in list(have):
        have[ts][0] = ts
    client = FakeClient(_minutes(int(now.timestamp()) - 3600, 10))

    tool.fetch_span(client, "KRW-BTC", now - timedelta(days=2), now, have)

    count, _, stop_at = client.asked[0]
    assert count < 200, f"이어 받아야 하는데 {count}개를 달라고 했습니다"
    assert stop_at is not None, "어디까지 받을지 안 알려 줬습니다"


def test_가진_게_없으면_구간을_전부_받는다():
    now = datetime.now(timezone.utc)
    client = FakeClient(_minutes(int(now.timestamp()) - 600, 10))
    tool.fetch_span(client, "KRW-BTC", now - timedelta(days=2), now, {})
    count, _, stop_at = client.asked[0]
    assert count >= 2 * 24 * 60
    assert stop_at == now - timedelta(days=2)


def test_구간_밖의_봉은_안_담는다():
    """달 조각에 옆 달 봉이 섞이면 조각끼리 겹쳐서 두 번 저장된다."""
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    end = datetime(2026, 6, 1, tzinfo=timezone.utc)
    inside = int(start.timestamp()) + 600
    client = FakeClient([
        _candle(int(start.timestamp()) - 600),   # 앞 달
        _candle(inside),
        _candle(int(end.timestamp()) + 600),     # 다음 달
    ])
    rows = tool.fetch_span(client, "KRW-BTC", start, end)
    assert list(rows) == [inside]


# ------------------------------------------------------------------ 달 나누기
def test_달_경계를_제대로_짚는다():
    start, following = tool.month_range(datetime(2026, 5, 17, 13, 5, tzinfo=timezone.utc))
    assert start == datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert following == datetime(2026, 6, 1, tzinfo=timezone.utc)
    # 12월에서 넘어가는 자리
    start, following = tool.month_range(datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc))
    assert following == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_지나간_달을_새것부터_늘어놓는다():
    months = tool.months_back(4, datetime(2026, 3, 15, tzinfo=timezone.utc))
    assert months[0] == "2026-02", "이번 달이 섞여 있습니다"
    assert months[1] == "2026-01"
    assert months[2] == "2025-12"
    assert len(months) == 48
    assert months == sorted(months, reverse=True)


# ------------------------------------------------------------------ 과거 채우기
def test_이미_있는_조각은_건너뛴다(tmp_path, monkeypatch):
    """한 번 적은 달은 다시 안 건드린다 — 지나간 봉은 변하지 않는다."""
    now = datetime(2026, 3, 15, tzinfo=timezone.utc)
    (tmp_path / "KRW-BTC").mkdir()
    (tmp_path / "KRW-BTC" / "2026-02.json").write_text("{}", encoding="utf-8")

    asked = []

    def fake_span(client, market, start, end, have=None):
        asked.append((market, f"{start:%Y-%m}"))
        return {int(start.timestamp()): [int(start.timestamp()), 1, 1, 1, 1, 1]}

    monkeypatch.setattr(tool, "fetch_span", fake_span)
    tool.do_history(FakeClient(), tmp_path, ["KRW-BTC"], years=1, budget=3, now=now)

    assert ("KRW-BTC", "2026-02") not in asked, "이미 있는 달을 또 받았습니다"
    assert ("KRW-BTC", "2026-01") in asked


def test_한_판에_정해진_만큼만_채운다(tmp_path, monkeypatch):
    """4년치 192조각을 한 판에 다 하면 90분이고, 실패하면 통째로 날아간다."""
    now = datetime(2026, 3, 15, tzinfo=timezone.utc)

    def fake_span(client, market, start, end, have=None):
        return {int(start.timestamp()): [int(start.timestamp()), 1, 1, 1, 1, 1]}

    monkeypatch.setattr(tool, "fetch_span", fake_span)
    tool.do_history(FakeClient(), tmp_path, ["KRW-BTC"], years=4, budget=5, now=now)

    made = list(tmp_path.rglob("*.json"))
    assert len(made) == 5, f"{len(made)}조각을 만들었습니다"


def test_목록에_어느_달이_있는지_적는다(tmp_path):
    """앱이 404를 더듬지 않게 하려는 것이다."""
    (tmp_path / "KRW-BTC").mkdir()
    for name in ["2026-01", "2025-12", "2025-11"]:
        (tmp_path / "KRW-BTC" / f"{name}.json").write_text("{}", encoding="utf-8")

    tool.write_manifest(tmp_path, ["KRW-BTC", "KRW-ETH"])

    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert payload["months"]["KRW-BTC"] == ["2025-11", "2025-12", "2026-01"]
    assert "KRW-ETH" not in payload["months"], "없는 종목을 있다고 적었습니다"


# ------------------------------------------------------------------ 그 밖
def test_앱이_아는_종목과_같다():
    """web/core/models.js의 MARKETS와 어긋나면 그 종목만 조용히 안 된다."""
    source = Path(__file__).resolve().parents[1] / "web" / "core" / "models.js"
    text = source.read_text(encoding="utf-8")
    for market in tool.MARKETS:
        assert f"'{market}'" in text, f"{market}이 앱에 없습니다"


def test_꼬리는_짧게_담는다():
    """10분마다 새로 올리는 파일이라 작아야 한다. 이번 달은 따로 담는다."""
    assert tool.TAIL_DAYS <= 3, "꼬리가 너무 깁니다 — 10분마다 그만큼을 올리게 됩니다"


def test_워크플로가_부르는_방식으로_돌아간다():
    """**이걸 안 해서 판이 한 번 죽었다.**

    `python tools/candles.py`로 부르면 파이썬이 저장소 뿌리가 아니라
    `tools/`를 경로에 넣는다. 그러면 `from tools.pack import ...`이
    `ModuleNotFoundError`로 터진다 — 로컬에서 PYTHONPATH를 붙여 돌리면
    안 보이는 종류의 실패다.

    그래서 워크플로가 실제로 쓰는 그 명령을, 아무것도 안 붙이고 돌려 본다.
    """
    root = Path(__file__).resolve().parents[1]
    made = subprocess.run(
        [sys.executable, "-m", "tools.candles", "--help"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert made.returncode == 0, f"돌지 않습니다:\n{made.stderr}"
    assert "--mode" in made.stdout


def test_워크플로가_부르는_명령이_실제로_그것이다():
    """시험은 `-m`으로 도는데 워크플로가 스크립트로 부르면 아무 소용이 없다."""
    root = Path(__file__).resolve().parents[1]
    for name in ("candles.yml", "history.yml"):
        text = (root / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "python -m tools.candles" in text, f"{name}이 -m으로 안 부릅니다"
        assert "python tools/candles.py" not in text, f"{name}이 스크립트로 부릅니다"
