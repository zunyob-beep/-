"""봉을 **작게 적는 방법.** 파이썬과 자바스크립트가 같은 규칙을 쓴다.

왜 형식을 따로 만들었나
---------------------
처음에는 봉 하나를 배열 하나로 적었다::

    [1754006400,150000000,150200000,149900000,150100000,1.23456789]

한 줄에 60자쯤이다. 14일치(20,160봉)면 1.4MB이고, 이 정도는 괜찮다.
그런데 **4년치는 2,102,400봉이라 한 종목에 143MB**가 된다. 깃허브의 파일
상한이 100MB이므로 아예 올라가지 않고, 올라간다 해도 아이패드에서
내려받게 할 수 있는 크기가 아니다.

그래서 두 가지를 바꾼다.

1. **열 단위로 적는다.** 봉마다 배열을 만들면 대괄호와 쉼표가 봉 수만큼
   늘어난다. 값끼리 모아 두면 그 껍데기가 한 번으로 끝난다.

2. **차이만 적는다.** 1분 사이에 가격은 거의 안 변한다. 150000000을
   매번 적는 대신 이전 종가와의 차이(-300, +120)를 적으면 자릿수가
   여섯에서 두셋으로 준다. 시가·고가·저가는 **같은 봉의 종가**와의
   차이로 적는다 — 이쪽이 더 작다.

합쳐서 한 줄에 30자 안쪽이 된다. 절반이다.

정확도
------
차이를 소수로 더해 나가면 오차가 쌓인다. 그래서 **정수로 바꿔서** 적는다.
몇 배를 곱할지는 종목마다 고른다 — 값이 정수가 되는 가장 작은 배수를 찾아
`scale`에 적어 둔다. 비트코인·이더리움 원화 호가는 정수라 1이면 되고,
잘게 쪼개지는 종목만 10·100·1000·10000을 쓴다. 되돌려 읽으면 처음 값과
정확히 같다.

거래량만 차이를 안 쓴다. 거래량은 옆 봉과 닮지 않아서 차이가 더 커진다.
소수 셋째 자리까지 반올림해 그대로 적는다 — 거래량을 쓰는 곳은 이론 하나뿐
(최근 3봉 평균 대 20봉 평균)이라 그 정도면 충분하다.

빠진 봉
------
업비트는 거래가 한 건도 없던 분에는 봉을 안 준다. 그래서 시각을 index로
대신할 수 없다. `t`에 **이전 봉과의 간격**(step 단위)을 적는다 — 대개 1이라
두 글자면 끝난다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

#: 가격을 정수로 바꿀 때 곱해 볼 값들. **작은 것부터** 써 본다.
#:
#: 늘 100을 곱하면 15000000000 같은 수가 되어 자릿수가 둘씩 늘고, 그게
#: 봉마다 네 번씩이라 파일이 8%쯤 커진다. 비트코인·이더리움 원화 호가는
#: 정수라 1이면 충분하고, 잘게 쪼개지는 종목만 큰 배수를 쓴다.
#:
#: 10000까지 두는 이유: 업비트 원화 호가는 1원 미만 종목에서 0.0001까지
#: 내려간다. 100에서 끊으면 그런 종목의 값이 **조용히 반올림돼** 파일과
#: 원본이 달라진다 — 화면에는 그럴듯한 숫자가 그대로 찍히므로 아무도
#: 눈치채지 못한다. 안 쓰는 배수는 파일을 키우지도 않는다.
SCALES = (1, 10, 100, 1000, 10000)

#: 거래량 소수 자릿수.
VOLUME_DIGITS = 3


def pick_scale(rows: Sequence[Sequence[float]]) -> int:
    """값이 정수가 되는 **가장 작은** 배수를 고른다."""
    for scale in SCALES:
        if all(abs(float(v) * scale - round(float(v) * scale)) < 1e-6
               for row in rows for v in row[1:5]):
            return scale
    return SCALES[-1]


def pack(market: str, rows: Sequence[Sequence[float]], step: int = 60,
         made: int | None = None) -> dict[str, Any]:
    """`[[시각, 시가, 고가, 저가, 종가, 거래량], ...]`를 작은 모양으로.

    `rows`는 **오래된 것부터** 정렬돼 있어야 한다.
    """
    if not rows:
        return {"m": market, "step": step, "from": 0, "n": 0, "scale": 1,
                "t": [], "c": [], "o": [], "h": [], "l": [], "v": [], "made": made or 0}

    scale = pick_scale(rows)

    gaps: list[int] = []
    closes: list[int] = []
    opens: list[int] = []
    highs: list[int] = []
    lows: list[int] = []
    volumes: list[float] = []

    previous_ts = int(rows[0][0])
    previous_close = 0
    for i, row in enumerate(rows):
        ts = int(row[0])
        gaps.append(0 if i == 0 else (ts - previous_ts) // step)
        previous_ts = ts

        close = round(float(row[4]) * scale)
        closes.append(close if i == 0 else close - previous_close)
        previous_close = close

        # 시가·고가·저가는 **같은 봉의 종가**와의 차이. 1분 안의 움직임이라
        # 대개 몇 원 단위다.
        opens.append(round(float(row[1]) * scale) - close)
        highs.append(round(float(row[2]) * scale) - close)
        lows.append(round(float(row[3]) * scale) - close)
        volumes.append(round(float(row[5]), VOLUME_DIGITS))

    return {
        "m": market,
        "step": step,
        "from": int(rows[0][0]),
        "n": len(rows),
        "scale": scale,
        "made": made or 0,
        "t": gaps,
        "c": closes,
        "o": opens,
        "h": highs,
        "l": lows,
        "v": volumes,
    }


def unpack(packed: Mapping[str, Any]) -> list[list[float]]:
    """되돌려 읽는다. 시험이 왕복을 대조하는 데 쓴다."""
    step = int(packed.get("step", 60))
    scale = int(packed.get("scale", 1))
    ts = int(packed.get("from", 0))
    close = 0
    out: list[list[float]] = []
    gaps: Iterable[int] = packed.get("t", [])
    for i, gap in enumerate(gaps):
        ts = ts if i == 0 else ts + int(gap) * step
        close = int(packed["c"][i]) if i == 0 else close + int(packed["c"][i])
        out.append([
            ts,
            (close + int(packed["o"][i])) / scale,
            (close + int(packed["h"][i])) / scale,
            (close + int(packed["l"][i])) / scale,
            close / scale,
            float(packed["v"][i]),
        ])
    return out
