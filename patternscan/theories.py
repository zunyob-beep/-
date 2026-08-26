"""차트 이론들을 실제로 계산해 본다.

다우 이론, 엘리어트 파동, 캔들 패턴, 이동평균, RSI, MACD, 볼린저 밴드,
머리어깨형, 이중천장/바닥, 삼각수렴, 거래량 확인.

**여기서 지키는 태도.** 이 이론들은 대부분 실증 근거가 약하다. 특히
엘리어트 파동은 같은 차트를 놓고 사람마다 다르게 세고, 나중에 보면 언제나
맞아떨어지게 다시 셀 수 있다 — 그건 맞히는 게 아니라 설명하는 것이다.

그래서 이 파일은 **판단을 팔지 않는다.** 각 이론이 지금 무엇을 가리키는지
계산해 주고, 끝이다. 그 신호가 실제로 맞았는지는 `score()`가 **사용자의
데이터로 직접 세어** 답한다. "엘리어트가 3파라고 합니다"가 아니라
"엘리어트식 셈이 상승을 가리킨 47번 중 23번(49%) 올랐습니다"가 이 도구의
말투다. 후자만이 검증 가능하다.

규칙은 널리 쓰이는 정의를 그대로 옮겼다. 임의로 고른 숫자에는 왜 그
숫자인지 적어 두었다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .levels import SWING, atr, swings
from .models import Series

#: 신호가 가리키는 방향.
UP, DOWN, FLAT = "상승", "하락", "중립"


@dataclass(frozen=True)
class Reading:
    """이론 하나가 지금 시점에 내놓은 읽기."""

    theory: str
    says: str            # UP / DOWN / FLAT
    detail: str          # 사람이 읽을 한 줄
    #: 규칙이 얼마나 또렷하게 맞았는지 (0~1). 확률이 아니라 '선명도'다.
    clarity: float = 1.0

    @property
    def directional(self) -> bool:
        return self.says in (UP, DOWN)


# ------------------------------------------------------------------ 다우 이론
def dow(series: Series, reach: int = SWING) -> Reading:
    """고점과 저점이 함께 높아지면 상승 추세, 함께 낮아지면 하락 추세.

    다우가 실제로 말한 것 중 계산할 수 있는 건 이게 거의 전부다. 나머지
    (시장은 모든 것을 반영한다 같은 말)는 셀 수가 없다.
    """
    highs, lows = swings(series, reach)
    if highs.size < 2 or lows.size < 2:
        return Reading("다우 이론", FLAT, "꼭짓점이 모자라 추세를 못 봅니다", 0.0)

    high_up = series.high[highs[-1]] > series.high[highs[-2]]
    low_up = series.low[lows[-1]] > series.low[lows[-2]]

    if high_up and low_up:
        return Reading("다우 이론", UP, "고점과 저점이 함께 높아지고 있습니다 (상승 추세)")
    if not high_up and not low_up:
        return Reading("다우 이론", DOWN, "고점과 저점이 함께 낮아지고 있습니다 (하락 추세)")
    return Reading(
        "다우 이론", FLAT,
        "고점과 저점이 서로 다른 방향입니다 — 추세가 아니라 눌림/반등 구간", 0.5,
    )


def dow_confirmation(by_timeframe: dict[str, Reading]) -> Reading:
    """다우의 '상호 확인'. 여러 지표가 같은 말을 해야 믿는다.

    원래는 산업평균과 운송평균이 서로를 확인해야 한다는 이야기였다.
    여기서는 1·3·5분봉이 서로를 확인하는지로 옮겼다.
    """
    votes = [r.says for r in by_timeframe.values() if r.directional]
    if not votes:
        return Reading("상호 확인", FLAT, "방향을 말하는 봉 간격이 없습니다", 0.0)
    agree = max(set(votes), key=votes.count)
    same = votes.count(agree)
    if same == len(by_timeframe) and same > 1:
        return Reading("상호 확인", agree, f"{same}개 봉 간격이 모두 {agree}을 가리킵니다")
    return Reading(
        "상호 확인", FLAT,
        f"봉 간격끼리 엇갈립니다 ({same}/{len(by_timeframe)}만 {agree}) — 다우는 이때 믿지 말라고 합니다",
        same / max(1, len(by_timeframe)),
    )


# --------------------------------------------------------------- 엘리어트 파동
def elliott(series: Series, reach: int = SWING) -> Reading:
    """마지막 다섯 꼭짓점이 충격파의 **어길 수 없는 규칙**에 맞는지만 본다.

    엘리어트 셈은 사람마다 다르고, 지나고 나면 언제나 맞게 다시 셀 수
    있다. 그러니 '몇 파인지'를 단정하지 않는다. 대신 반박 가능한 세
    규칙만 확인한다.

      · 2파는 1파의 시작을 되돌리지 않는다
      · 3파는 1·3·5파 중 가장 짧지 않다
      · 4파는 1파의 영역을 침범하지 않는다

    셋 다 맞으면 "충격파 모양과 어긋나지 않는다"까지만 말한다. 그게
    정직하게 말할 수 있는 전부다.
    """
    highs, lows = swings(series, reach)
    points = np.sort(np.concatenate([highs, lows]))
    if points.size < 5:
        return Reading("엘리어트 파동", FLAT, "꼭짓점이 다섯 개가 안 됩니다", 0.0)

    idx = points[-5:]
    price = np.array([
        series.high[i] if i in set(highs.tolist()) else series.low[i] for i in idx
    ])
    rising = price[1] > price[0]
    direction = 1.0 if rising else -1.0
    # 방향을 위로 뒤집어 놓고 한 벌의 규칙으로 검사한다
    p = price * direction

    if not (p[1] > p[0] and p[2] < p[1] and p[3] > p[2] and p[4] < p[3]):
        return Reading("엘리어트 파동", FLAT, "꼭짓점이 지그재그 모양이 아닙니다", 0.0)

    wave1, wave3 = p[1] - p[0], p[3] - p[2]
    broke = []
    if p[2] <= p[0]:
        broke.append("2파가 1파 시작을 되돌렸습니다")
    if wave3 < wave1:
        broke.append("3파가 1파보다 짧습니다")
    if p[4] <= p[1]:
        broke.append("4파가 1파 영역을 침범했습니다")

    where = "상승" if rising else "하락"
    if broke:
        return Reading(
            "엘리어트 파동", FLAT,
            f"{where} 충격파로 보기 어렵습니다 — {broke[0]}",
            1.0 - len(broke) / 3.0,
        )
    return Reading(
        "엘리어트 파동", UP if rising else DOWN,
        f"{where} 충격파 규칙에 어긋나지 않습니다 (4파로 볼 수 있는 자리, 5파가 남았다면)",
        0.7,   # 규칙을 안 어겼을 뿐 맞다는 뜻이 아니다
    )


# ------------------------------------------------------------------ 캔들 패턴
def _bodies(series: Series, n: int = 3) -> tuple[np.ndarray, ...]:
    return (series.open[-n:], series.high[-n:], series.low[-n:], series.close[-n:])


def candles(series: Series) -> Reading:
    """마지막 한두 개 봉의 생김새. 널리 쓰이는 정의를 그대로 옮겼다."""
    if len(series) < 3:
        return Reading("캔들 패턴", FLAT, "봉이 모자랍니다", 0.0)

    o, high, low, c = _bodies(series, 3)
    body = abs(c[-1] - o[-1])
    span = high[-1] - low[-1]
    if span <= 0:
        return Reading("캔들 패턴", FLAT, "움직임이 없는 봉입니다", 0.0)

    upper = high[-1] - max(o[-1], c[-1])
    lower = min(o[-1], c[-1]) - low[-1]
    prev_body = abs(c[-2] - o[-2])
    up_now, up_before = c[-1] > o[-1], c[-2] > o[-2]

    # 장악형: 앞 봉 몸통을 통째로 덮는다
    if prev_body > 0 and body > prev_body:
        if up_now and not up_before and c[-1] >= o[-2] and o[-1] <= c[-2]:
            return Reading("캔들 패턴", UP, "상승 장악형 — 앞의 음봉을 통째로 덮었습니다")
        if not up_now and up_before and o[-1] >= c[-2] and c[-1] <= o[-2]:
            return Reading("캔들 패턴", DOWN, "하락 장악형 — 앞의 양봉을 통째로 덮었습니다")

    # 망치형/교수형: 아래꼬리가 몸통의 두 배 넘고 위꼬리는 짧다
    if body > 0 and lower > body * 2 and upper < body * 0.5:
        return Reading("캔들 패턴", UP, "망치형 — 아래로 밀렸다가 되돌아왔습니다", 0.7)
    if body > 0 and upper > body * 2 and lower < body * 0.5:
        return Reading("캔들 패턴", DOWN, "역망치·유성형 — 위로 밀었다가 되밀렸습니다", 0.7)

    # 도지: 몸통이 전체 폭의 10% 미만
    if body < span * 0.1:
        return Reading("캔들 패턴", FLAT, "도지 — 사려는 쪽과 팔려는 쪽이 팽팽합니다", 0.5)

    # 샛별/저녁별: 큰 봉 → 작은 봉 → 반대쪽 큰 봉
    small = abs(c[-2] - o[-2]) < abs(c[-3] - o[-3]) * 0.4
    if small and c[-3] < o[-3] and up_now and c[-1] > (o[-3] + c[-3]) / 2:
        return Reading("캔들 패턴", UP, "샛별형 — 바닥에서 방향이 바뀌는 모양")
    if small and c[-3] > o[-3] and not up_now and c[-1] < (o[-3] + c[-3]) / 2:
        return Reading("캔들 패턴", DOWN, "저녁별형 — 꼭대기에서 방향이 바뀌는 모양")

    which = "양봉" if up_now else "음봉"
    return Reading("캔들 패턴", FLAT, f"특별한 모양 없는 {which}입니다", 0.0)


# ----------------------------------------------------------------- 이동평균
def moving_averages(series: Series, spans: tuple[int, ...] = (5, 20, 60)) -> Reading:
    """정배열이면 상승, 역배열이면 하락. 짧은 선이 위에 있는지로 본다."""
    if len(series) < max(spans) + 1:
        return Reading("이동평균 배열", FLAT, "봉이 모자랍니다", 0.0)
    lines = [float(np.mean(series.close[-span:])) for span in spans]
    if all(lines[i] > lines[i + 1] for i in range(len(lines) - 1)):
        return Reading("이동평균 배열", UP, f"정배열 ({'>'.join(str(s) for s in spans)})")
    if all(lines[i] < lines[i + 1] for i in range(len(lines) - 1)):
        return Reading("이동평균 배열", DOWN, f"역배열 ({'<'.join(str(s) for s in spans)})")
    return Reading("이동평균 배열", FLAT, "이동평균들이 얽혀 있습니다 — 방향이 없습니다", 0.3)


def rsi(series: Series, window: int = 14) -> Reading:
    """상대강도. 70 위는 과매수, 30 아래는 과매도로 보는 게 관례다.

    주의: 과매수는 '곧 떨어진다'가 아니다. 강한 추세에서는 과매수인 채로
    한참 더 오른다. 그래서 방향을 **되돌림 쪽**으로 말하되 선명도를 낮게 둔다.
    """
    if len(series) < window + 1:
        return Reading("RSI", FLAT, "봉이 모자랍니다", 0.0)
    change = np.diff(series.close[-(window + 1) :])
    gain = float(np.mean(np.clip(change, 0, None)))
    loss = float(np.mean(np.clip(-change, 0, None)))
    if gain + loss == 0:
        return Reading("RSI", FLAT, "움직임이 없습니다", 0.0)
    value = 100.0 * gain / (gain + loss)

    if value >= 70:
        return Reading("RSI", DOWN, f"RSI {value:.0f} — 과매수 구간 (되돌림이 잦은 자리)", 0.5)
    if value <= 30:
        return Reading("RSI", UP, f"RSI {value:.0f} — 과매도 구간 (반등이 잦은 자리)", 0.5)
    return Reading("RSI", FLAT, f"RSI {value:.0f} — 치우치지 않았습니다", 0.2)


def _ema(values: np.ndarray, span: int) -> float:
    alpha = 2.0 / (span + 1.0)
    weights = (1 - alpha) ** np.arange(values.size)[::-1]
    return float(np.sum(values * weights) / np.sum(weights))


def macd(series: Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Reading:
    """빠른 이평과 느린 이평의 차이가 신호선 위인지 아래인지."""
    need = slow + signal + 1
    if len(series) < need:
        return Reading("MACD", FLAT, "봉이 모자랍니다", 0.0)
    closes = series.close
    line = [
        _ema(closes[: len(closes) - k][-slow * 3 :], fast)
        - _ema(closes[: len(closes) - k][-slow * 3 :], slow)
        for k in range(signal)
    ][::-1]
    now = line[-1]
    mark = float(np.mean(line))
    if now > mark and now > 0:
        return Reading("MACD", UP, "MACD가 신호선 위에 있습니다 (상승 탄력)")
    if now < mark and now < 0:
        return Reading("MACD", DOWN, "MACD가 신호선 아래에 있습니다 (하락 탄력)")
    return Reading("MACD", FLAT, "MACD가 신호선 근처입니다 — 방향이 갈리는 자리", 0.3)


def bollinger(series: Series, window: int = 20, width: float = 2.0) -> Reading:
    """20봉 평균에서 표준편차 2배 밖으로 나갔는지."""
    if len(series) < window:
        return Reading("볼린저 밴드", FLAT, "봉이 모자랍니다", 0.0)
    recent = series.close[-window:]
    mid = float(np.mean(recent))
    spread = float(np.std(recent))
    if spread <= 0:
        return Reading("볼린저 밴드", FLAT, "변동이 없습니다", 0.0)
    now = float(series.close[-1])
    z = (now - mid) / spread
    if z >= width:
        return Reading("볼린저 밴드", DOWN, f"위 밴드를 벗어났습니다 ({z:.1f}σ) — 되돌림이 잦습니다", 0.5)
    if z <= -width:
        return Reading("볼린저 밴드", UP, f"아래 밴드를 벗어났습니다 ({z:.1f}σ) — 반등이 잦습니다", 0.5)
    return Reading("볼린저 밴드", FLAT, f"밴드 안입니다 ({z:+.1f}σ)", 0.2)


# --------------------------------------------------------------- 모양 패턴
def head_and_shoulders(series: Series, reach: int = SWING) -> Reading:
    """머리어깨형: 가운데 봉우리가 양옆보다 높고, 양 어깨는 비슷한 높이."""
    highs, lows = swings(series, reach)
    if highs.size < 3 or lows.size < 2:
        return Reading("머리어깨형", FLAT, "꼭짓점이 모자랍니다", 0.0)

    left, head, right = series.high[highs[-3:]]
    span = atr(series)
    if span <= 0:
        return Reading("머리어깨형", FLAT, "변동폭을 못 잽니다", 0.0)

    shoulders_even = abs(left - right) < span * 1.5
    if head > left and head > right and shoulders_even:
        neck = float(np.mean(series.low[lows[-2:]]))
        return Reading(
            "머리어깨형", DOWN,
            f"머리어깨형 — 목선 {neck:,.0f}원을 깨면 하락으로 보는 모양", 0.7,
        )

    left, head, right = series.low[lows[-3:]] if lows.size >= 3 else (0.0, 0.0, 0.0)
    if lows.size >= 3 and head < left and head < right and abs(left - right) < span * 1.5:
        neck = float(np.mean(series.high[highs[-2:]]))
        return Reading(
            "머리어깨형", UP,
            f"역머리어깨형 — 목선 {neck:,.0f}원을 넘으면 상승으로 보는 모양", 0.7,
        )
    return Reading("머리어깨형", FLAT, "머리어깨 모양이 아닙니다", 0.0)


def double_top_bottom(series: Series, reach: int = SWING) -> Reading:
    """이중천장/이중바닥: 비슷한 높이를 두 번 찍고 못 넘어선 자리."""
    highs, lows = swings(series, reach)
    span = atr(series)
    if span <= 0:
        return Reading("이중천장·바닥", FLAT, "변동폭을 못 잽니다", 0.0)

    if highs.size >= 2 and abs(series.high[highs[-1]] - series.high[highs[-2]]) < span * 0.8:
        price = float(np.mean(series.high[highs[-2:]]))
        return Reading("이중천장·바닥", DOWN, f"이중천장 — {price:,.0f}원을 두 번 못 넘었습니다", 0.7)
    if lows.size >= 2 and abs(series.low[lows[-1]] - series.low[lows[-2]]) < span * 0.8:
        price = float(np.mean(series.low[lows[-2:]]))
        return Reading("이중천장·바닥", UP, f"이중바닥 — {price:,.0f}원에서 두 번 버텼습니다", 0.7)
    return Reading("이중천장·바닥", FLAT, "두 번 같은 자리를 찍은 모양이 아닙니다", 0.0)


def squeeze(series: Series, reach: int = SWING) -> Reading:
    """삼각수렴: 고점은 낮아지고 저점은 높아지며 폭이 좁아지는 모양.

    방향은 말하지 않는다. 삼각수렴은 어느 쪽으로든 터지는 모양이고,
    방향을 아는 척하면 그건 지어내는 것이다.
    """
    highs, lows = swings(series, reach)
    if highs.size < 2 or lows.size < 2:
        return Reading("삼각수렴", FLAT, "꼭짓점이 모자랍니다", 0.0)
    narrowing = (
        series.high[highs[-1]] < series.high[highs[-2]]
        and series.low[lows[-1]] > series.low[lows[-2]]
    )
    if narrowing:
        return Reading(
            "삼각수렴", FLAT,
            "폭이 좁아지고 있습니다 — 곧 크게 움직이되 방향은 이 모양이 말해주지 않습니다",
            0.6,
        )
    return Reading("삼각수렴", FLAT, "수렴하는 모양이 아닙니다", 0.0)


def volume_confirms(series: Series, window: int = 20) -> Reading:
    """다우의 거래량 원칙: 추세는 거래량이 받쳐줘야 한다."""
    if len(series) < window + 1:
        return Reading("거래량 확인", FLAT, "봉이 모자랍니다", 0.0)
    recent = float(np.mean(series.volume[-3:]))
    usual = float(np.mean(series.volume[-window:]))
    if usual <= 0:
        return Reading("거래량 확인", FLAT, "거래량이 없습니다", 0.0)
    ratio = recent / usual
    rising = series.close[-1] > series.close[-4]
    if ratio > 1.5:
        return Reading(
            "거래량 확인", UP if rising else DOWN,
            f"거래량이 평소의 {ratio:.1f}배입니다 — 움직임에 힘이 실렸습니다", 0.6,
        )
    if ratio < 0.6:
        return Reading(
            "거래량 확인", FLAT,
            f"거래량이 평소의 {ratio:.1f}배뿐입니다 — 지금 움직임은 힘이 약합니다", 0.5,
        )
    return Reading("거래량 확인", FLAT, f"거래량은 평소 수준입니다 ({ratio:.1f}배)", 0.2)


# ------------------------------------------------------------------ 전부 읽기
#: 순서대로 화면에 나온다. 계산할 수 있는 것만 넣었다.
THEORIES = (
    dow,
    elliott,
    candles,
    moving_averages,
    macd,
    rsi,
    bollinger,
    head_and_shoulders,
    double_top_bottom,
    squeeze,
    volume_confirms,
)


def read_all(series: Series, reach: int = SWING) -> list[Reading]:
    """모든 이론을 한 번씩 돌린다."""
    out = []
    for theory in THEORIES:
        try:
            out.append(theory(series))
        except (ValueError, IndexError, ZeroDivisionError, FloatingPointError):
            # 이론 하나가 이상한 데이터에 걸려도 나머지는 나와야 한다.
            out.append(Reading(theory.__name__, FLAT, "계산하지 못했습니다", 0.0))
    return out


def tally(readings: list[Reading]) -> tuple[int, int, int]:
    """(상승, 하락, 중립) 개수. **다수결이 곧 답은 아니다.**

    이론끼리 독립이 아니다 — 이동평균·MACD·다우는 결국 같은 추세를 세
    번 세는 것에 가깝다. 그래서 이 숫자는 '얼마나 한목소리인가'를 보는
    용도지, 확률로 바꿔 읽으면 안 된다.
    """
    ups = sum(1 for r in readings if r.says == UP)
    downs = sum(1 for r in readings if r.says == DOWN)
    return ups, downs, len(readings) - ups - downs


# ------------------------------------------------------- 그래서 맞기는 하나
#
# 여기가 이 파일의 핵심이다. 위의 이론들은 "지금 무엇처럼 보이는가"를
# 말할 뿐이고, 그건 검증할 수 없는 말이다. 검증할 수 있는 말은 하나뿐이다 —
# **이 신호가 나왔던 과거에 실제로 무슨 일이 있었나.**
#
# 그걸 사용자의 데이터로 직접 센다. 남의 백테스트를 인용하지 않는다.

#: 한 시점을 판단할 때 볼 과거 길이. 이론들이 보는 최대 창(이동평균 60,
#: MACD 26+9, 꼭짓점 몇 개)을 넉넉히 덮는다. 더 길게 잡아도 답이 안 변한다.
LOOKBACK = 400


@dataclass(frozen=True)
class Score:
    """이론 하나가 과거에 얼마나 맞았는지."""

    theory: str
    calls: int           # 방향을 말한 횟수
    hits: int            # 그중 맞은 횟수
    base: float          # 아무 때나 찍었으면 맞았을 비율
    #: 수수료까지 넘긴 경우만 센 적중률. 진짜 중요한 건 이쪽이다.
    beat: int = 0

    @property
    def rate(self) -> float:
        return self.hits / self.calls if self.calls else 0.0

    @property
    def beat_rate(self) -> float:
        return self.beat / self.calls if self.calls else 0.0

    @property
    def edge(self) -> float:
        """평소보다 얼마나 나은가. 0 근처면 이 이론은 여기서 아무 말도 안 한 것이다."""
        return self.rate - self.base

    @property
    def enough(self) -> bool:
        """표본이 이보다 적으면 적중률은 우연과 구분되지 않는다."""
        return self.calls >= 30

    @property
    def worth_believing(self) -> bool:
        """우연으로 보기 어려운가. 표준오차 두 배를 넘어야 한다."""
        if not self.enough:
            return False
        error = (0.25 / self.calls) ** 0.5
        return self.edge > 2 * error


def score(
    series: Series,
    horizon: int = 10,
    points: int = 300,
    cost: float = 0.0014,
    seed: int = 0,
) -> list[Score]:
    """과거 여러 시점으로 돌아가 이론별 적중률을 잰다.

    각 시점에서 **그 이전 데이터만** 보고 읽은 뒤, 실제로 어떻게 됐는지
    맞춰본다. 미래를 보지 않으므로 여기서 나온 숫자는 실제로 그때 얻을
    수 있었던 성적이다.
    """
    usable = len(series) - horizon
    if usable <= LOOKBACK + 1:
        return []

    rng = np.random.default_rng(seed)
    spots = np.arange(LOOKBACK, usable)
    if spots.size > points:
        spots = np.sort(rng.choice(spots, size=points, replace=False))

    closes = series.close
    after = closes[spots + horizon] / closes[spots] - 1.0
    rose = after > 0
    base_up = float(np.mean(rose)) if rose.size else 0.0

    tally_by: dict[str, list[int]] = {}
    for offset, spot in enumerate(spots):
        window = series.slice(int(spot) - LOOKBACK, int(spot) + 1)
        for reading in read_all(window):
            if not reading.directional:
                continue
            said_up = reading.says == UP
            correct = said_up == bool(rose[offset])
            beat = after[offset] > cost if said_up else after[offset] < -cost
            got = tally_by.setdefault(reading.theory, [0, 0, 0])
            got[0] += 1
            got[1] += int(correct)
            got[2] += int(beat)

    out = []
    for name, (calls, hits, beat) in tally_by.items():
        # 비교 대상은 '그 이론이 말한 방향으로 늘 찍었을 때'가 아니라
        # '아무 때나 그 방향으로 찍었을 때'다.
        out.append(Score(name, calls, hits, max(base_up, 1 - base_up), beat))
    return sorted(out, key=lambda s: -s.edge)
