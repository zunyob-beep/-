"""작게 적는 형식이 **되돌려 읽어도 같은가.**

이게 틀리면 화면에 그럴듯한 숫자가 그대로 찍힌다 — 값이 조금씩 어긋난
채로 계산이 돌아가고, 아무도 눈치채지 못한다. 그래서 왕복을 정확히 대조한다.
"""

from __future__ import annotations

import json

import pytest

from tools.pack import SCALES, pack, pick_scale, unpack


def walk(start: float, tick: float, n: int, first: int = 1700000000) -> list[list[float]]:
    """봉처럼 생긴 값. 값이 시각만으로 정해지므로 몇 번을 만들어도 같다."""
    rows = []
    price = float(start)
    for i in range(n):
        price = max(tick, price + tick * ((i * 7919) % 11 - 5))
        rows.append([
            first + i * 60,
            price - tick, price + 2 * tick, price - 2 * tick, price,
            round(((i * 37) % 100) / 8, 3),
        ])
    return rows


def test_되돌려_읽으면_그대로다():
    rows = walk(150_000_000, 1000, 500)
    back = unpack(pack("KRW-BTC", rows))
    assert len(back) == len(rows)
    for a, b in zip(rows, back, strict=True):
        assert a[0] == b[0]
        for i in range(1, 5):
            assert abs(a[i] - b[i]) < 1e-6, f"{i}번 칸이 어긋났습니다: {a[i]} → {b[i]}"
        assert abs(a[5] - b[5]) < 1e-3


def test_차이가_쌓여도_안_어긋난다():
    """**여기가 이 형식의 유일한 위험이다.**

    차이를 계속 더해 나가므로, 소수로 더하면 오차가 조금씩 쌓여 만 봉쯤
    가면 눈에 보이게 어긋난다. 그래서 정수로 바꿔서 더한다.
    """
    rows = walk(150_000_000, 1000, 20000)
    back = unpack(pack("KRW-BTC", rows))
    assert back[-1][4] == pytest.approx(rows[-1][4], abs=1e-6)
    assert back[-1][0] == rows[-1][0]


def test_소수가_있는_종목도_정확하다():
    rows = [[1700000000 + i * 60, 0.51, 0.53, 0.49, 0.5 + i * 0.01, 1.5] for i in range(50)]
    back = unpack(pack("KRW-DOGE", rows))
    for a, b in zip(rows, back, strict=True):
        assert abs(a[4] - b[4]) < 1e-6


def test_정수인_종목은_배수를_안_쓴다():
    """늘 100을 곱하면 자릿수가 둘씩 늘고, 그게 봉마다 네 번씩이다."""
    assert pick_scale(walk(150_000_000, 1000, 100)) == 1
    assert pick_scale([[0, 0.51, 0.53, 0.49, 0.5, 1.0]]) == 100
    assert pick_scale([[0, 1.1, 1.3, 0.9, 1.2, 1.0]]) == 10


def test_빠진_봉이_있어도_시각이_맞다():
    """업비트는 거래가 한 건도 없던 분에는 봉을 안 준다."""
    rows = [
        [1700000000, 1, 2, 0, 1, 1.0],
        [1700000060, 1, 2, 0, 1, 1.0],
        [1700000600, 1, 2, 0, 1, 1.0],   # 아홉 분이 빈다
        [1700000660, 1, 2, 0, 1, 1.0],
    ]
    back = unpack(pack("KRW-BTC", rows))
    assert [r[0] for r in back] == [r[0] for r in rows]


def test_빈_것도_읽힌다():
    assert unpack(pack("KRW-BTC", [])) == []


def test_한_달이_충분히_작다():
    """**이 형식을 만든 이유가 이 숫자다.**

    봉마다 배열 하나로 적으면 한 줄에 60자쯤이고, 4년치(2,102,400봉)면
    한 종목에 143MB가 된다. 깃허브 파일 상한이 100MB라 아예 안 올라간다.
    """
    rows = walk(150_000_000, 1000, 43200)
    text = json.dumps(pack("KRW-BTC", rows), separators=(",", ":"))
    per_row = len(text) / len(rows)
    assert per_row < 40, f"한 봉에 {per_row:.0f}자입니다 — 4년치가 너무 커집니다"


def test_배수_후보는_작은_것부터():
    assert list(SCALES) == sorted(SCALES)
    assert SCALES[0] == 1
