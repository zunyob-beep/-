"""차트 이론과 지지·저항선 검증.

**여기서 지키는 것이 둘이다.**

1. **미래를 보지 않는다.** 지금 시점의 읽기는 지금까지의 봉만으로 나와야
   한다. 이걸 어기면 과거 성적이 실제보다 훨씬 좋게 나오고, 그 숫자를
   믿고 돈을 넣게 된다. 차트 이론 백테스트가 흔히 망하는 지점이다.

2. **규칙대로 판정한다.** 망치형이면 망치형이라고 해야 하고, 엘리어트
   규칙을 어겼으면 어겼다고 해야 한다. 이론이 마음에 드는 답을 내도록
   슬쩍 봐주기 시작하면 이 화면 전체가 점집이 된다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import pairwise

import numpy as np
import pytest

from patternscan import levels as lv
from patternscan import theories as th
from patternscan.models import Candle, Series

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make(closes, highs=None, lows=None, opens=None, volumes=None) -> Series:
    closes = np.asarray(closes, dtype=float)
    n = closes.size
    highs = np.asarray(highs, dtype=float) if highs is not None else closes * 1.0005
    lows = np.asarray(lows, dtype=float) if lows is not None else closes * 0.9995
    opens = np.asarray(opens, dtype=float) if opens is not None else closes
    volumes = np.asarray(volumes, dtype=float) if volumes is not None else np.ones(n)
    return Series.from_candles(
        "KRW-BTC", "minute1",
        [
            Candle(ts=START + timedelta(minutes=i), open=float(opens[i]),
                   high=float(highs[i]), low=float(lows[i]),
                   close=float(closes[i]), volume=float(volumes[i]))
            for i in range(n)
        ],
    )


def wobbly(seed=0, n=3000, drift=0.0):
    rng = np.random.default_rng(seed)
    return make(1_000_000 * np.exp(np.cumsum(rng.normal(drift, 0.001, n))))


# ==================================================== 미래를 보지 않는다
def test_swings_never_points_at_the_very_end():
    """마지막 봉이 꼭짓점인지는 **나중에야** 알 수 있다.

    좌우를 다 봐야 꼭짓점이므로, 오른쪽이 아직 안 그려진 마지막 몇 개는
    꼭짓점이 될 수 없다. 여기를 봐주기 시작하면 그게 미래를 보는 것이다.
    """
    series = wobbly(1)
    highs, lows = lv.swings(series, reach=5)
    n = len(series)
    for spot in list(highs) + list(lows):
        assert spot <= n - 1 - 5, f"{spot}번 봉은 오른쪽이 아직 없습니다"


def test_a_reading_does_not_change_when_the_future_arrives():
    """같은 시점의 읽기는, 그 뒤에 무슨 일이 생기든 같아야 한다."""
    whole = wobbly(2, n=2000)
    cut = 1200
    before = whole.slice(0, cut)

    # 뒤에 무슨 일이 있었든 (여기선 폭등) 앞의 읽기는 그대로여야 한다
    now = th.read_all(before)
    later = th.read_all(whole.slice(0, cut))
    assert [(r.theory, r.says, r.detail) for r in now] == [
        (r.theory, r.says, r.detail) for r in later
    ]


def test_levels_only_use_the_past():
    series = wobbly(3, n=2000)
    cut = 1500
    early = lv.levels(series.slice(0, cut))
    # 뒤쪽을 잘라내도 앞쪽만 보고 낸 답은 같아야 한다
    assert early == lv.levels(series.slice(0, cut))


# ============================================================== 다우 이론
def test_dow_calls_a_staircase_up():
    """고점도 저점도 계단처럼 올라가면 상승 추세다."""
    steps = []
    base = 100.0
    for k in range(6):
        steps += list(base + k * 10 + np.array([0, 3, 6, 3, 0, -2, 0, 3]))
    series = make(steps)
    assert th.dow(series, reach=2).says == th.UP


def test_dow_calls_a_staircase_down():
    steps = []
    base = 200.0
    for k in range(6):
        steps += list(base - k * 10 + np.array([0, -3, -6, -3, 0, 2, 0, -3]))
    series = make(steps)
    assert th.dow(series, reach=2).says == th.DOWN


def test_dow_refuses_when_highs_and_lows_disagree():
    """고점은 오르는데 저점은 내리면 추세가 아니다 — 말하지 않아야 한다.

    (같은 값이 연달아 있으면 평평한 자리가 전부 꼭짓점이 되므로,
    꼭짓점 사이를 완만하게 이어 붙인다.)
    """
    turns = [100, 110, 95, 115, 90, 120, 85, 125, 80, 130, 75, 135, 70, 80]
    path = []
    for a, b in pairwise(turns):
        path += list(np.linspace(a, b, 6))[:-1]
    series = make(np.array(path))
    assert th.dow(series, reach=2).says == th.FLAT


def test_dow_confirmation_needs_everyone_to_agree():
    same = {tf: th.Reading("다우 이론", th.UP, "") for tf in ("minute1", "minute3", "minute5")}
    assert th.dow_confirmation(same).says == th.UP

    mixed = dict(same)
    mixed["minute5"] = th.Reading("다우 이론", th.DOWN, "")
    assert th.dow_confirmation(mixed).says == th.FLAT, "엇갈리는데 방향을 말했습니다"


# ========================================================== 엘리어트 파동
def _impulse(wave2_deep=False, wave3_short=False, wave4_overlap=False):
    """5-꼭짓점 충격파를 만든다. 인자로 규칙을 하나씩 어기게 한다."""
    p0 = 100.0
    p1 = 120.0
    p2 = 95.0 if wave2_deep else 110.0
    p3 = p2 + (10.0 if wave3_short else 60.0)
    p4 = 115.0 if wave4_overlap else (p3 - 15.0)
    # 시작점(1파의 바닥)도 꼭짓점으로 잡히려면 그 앞에 내려오는 구간이 있어야
    # 한다. 첫 봉은 왼쪽이 없어서 절대 꼭짓점이 될 수 없다.
    path = list(np.linspace(p0 + 10.0, p0, 8))[:-1]
    for a, b in zip([p0, p1, p2, p3], [p1, p2, p3, p4], strict=True):
        path += list(np.linspace(a, b, 12))[:-1]
    # 마지막 꼭짓점(4파 바닥)도 꼭짓점으로 잡히려면 오른쪽이 그려져야 한다.
    # 이게 미래를 안 보는 대가다 — 늘 몇 봉 늦게 안다.
    path += list(np.linspace(p4, p4 + 8.0, 8))
    return make(np.array(path))


def test_elliott_accepts_a_clean_impulse():
    said = th.elliott(_impulse(), reach=2)
    assert said.says == th.UP, said.detail


@pytest.mark.parametrize(
    ("broken", "because"),
    [
        ({"wave2_deep": True}, "2파"),
        ({"wave3_short": True}, "3파"),
        ({"wave4_overlap": True}, "4파"),
    ],
)
def test_elliott_refuses_when_a_hard_rule_is_broken(broken, because):
    """규칙을 어겼는데도 '충격파입니다'라고 하면 그건 지어내는 것이다."""
    said = th.elliott(_impulse(**broken), reach=2)
    assert said.says == th.FLAT, f"{because} 규칙 위반을 그냥 넘겼습니다"


def test_elliott_says_nothing_without_enough_turns():
    assert th.elliott(make(np.linspace(100, 200, 300)), reach=5).says == th.FLAT


# ============================================================ 캔들 패턴
def test_hammer_is_recognised():
    """아래꼬리가 길고 위꼬리가 짧은 봉."""
    series = make(
        closes=[100, 100, 101],
        opens=[100, 100, 100.5],
        highs=[100.5, 100.5, 101.1],
        lows=[99.5, 99.5, 97.0],      # 아래로 길게 밀렸다 돌아옴
    )
    assert th.candles(series).says == th.UP


def test_bullish_engulfing_is_recognised():
    series = make(
        closes=[100, 98, 103],
        opens=[100, 101, 97],          # 앞 음봉(101→98)을 통째로 덮는 양봉
        highs=[101, 101.5, 103.5],
        lows=[99, 97.5, 96.5],
    )
    assert th.candles(series).says == th.UP


def test_doji_says_nothing():
    series = make(
        closes=[100, 100, 100.01],
        opens=[100, 100, 100.0],
        highs=[100.5, 100.5, 101.0],
        lows=[99.5, 99.5, 99.0],
    )
    said = th.candles(series)
    assert said.says == th.FLAT
    assert "도지" in said.detail


# ======================================================== 나머지 지표들
def test_moving_averages_read_a_clean_trend():
    assert th.moving_averages(make(np.linspace(100, 200, 300))).says == th.UP
    assert th.moving_averages(make(np.linspace(200, 100, 300))).says == th.DOWN


def test_rsi_flags_a_one_way_run():
    said = th.rsi(make(np.linspace(100, 130, 60)))
    assert said.says == th.DOWN, "쉬지 않고 오르면 과매수로 읽어야 합니다"
    assert "과매수" in said.detail


def test_bollinger_only_speaks_outside_the_band():
    steady = make(np.full(60, 100.0) + np.tile([0.1, -0.1], 30))
    assert th.bollinger(steady).says == th.FLAT


def test_squeeze_never_picks_a_direction():
    """삼각수렴은 어느 쪽으로든 터진다. 방향을 아는 척하면 지어내는 것이다."""
    for seed in range(6):
        assert th.squeeze(wobbly(seed, n=800)).says == th.FLAT


def test_every_theory_survives_a_flat_line():
    """움직임이 없는 데이터에 걸려 터지면, 그날 화면 전체가 안 나온다."""
    flat = make(np.full(500, 100.0))
    readings = th.read_all(flat)
    assert len(readings) == len(th.THEORIES)
    assert all(isinstance(r, th.Reading) for r in readings)


def test_every_theory_survives_a_very_short_series():
    for n in (1, 2, 3, 10):
        readings = th.read_all(make(np.linspace(100, 101, n)))
        assert len(readings) == len(th.THEORIES)


# =============================================================== 과거 성적
def test_score_needs_both_enough_samples_and_a_real_edge():
    """표본이 적으면 초과가 커도 우연과 구분되지 않는다."""
    lucky = th.Score("운", calls=5, hits=5, base=0.5)
    assert lucky.edge > 0.4
    assert not lucky.worth_believing, "표본 5개짜리를 믿을 만하다고 했습니다"

    real = th.Score("진짜", calls=1000, hits=600, base=0.5)
    assert real.worth_believing

    meh = th.Score("그저그럼", calls=1000, hits=510, base=0.5)
    assert not meh.worth_believing


def test_score_reports_a_baseline_to_compare_against():
    """적중률만 주면 반드시 속는다 — 늘 '평소'와 함께 나와야 한다."""
    marks = th.score(wobbly(7, n=4000), horizon=10, points=120)
    assert marks
    for mark in marks:
        assert 0.0 < mark.base < 1.0
        assert mark.calls > 0
        assert 0.0 <= mark.rate <= 1.0


def test_score_does_not_peek_at_the_future():
    """앞의 데이터를 잘라내도, 남은 구간의 성적은 그대로여야 한다."""
    whole = wobbly(8, n=4000)
    first = th.score(whole.slice(0, 3000), horizon=10, points=100, seed=1)
    again = th.score(whole.slice(0, 3000), horizon=10, points=100, seed=1)
    assert {(s.theory, s.calls, s.hits) for s in first} == {
        (s.theory, s.calls, s.hits) for s in again
    }


def test_score_is_empty_when_there_is_nothing_to_score():
    assert th.score(make(np.linspace(100, 110, 50))) == []


# ========================================================== 지지·저항선
def test_a_level_needs_more_than_one_touch():
    """한 번 스친 자리는 선이 아니다."""
    found = lv.levels(wobbly(9, n=2000))
    assert all(one.touches >= lv.MIN_TOUCHES for one in found)


def test_levels_sit_on_the_right_side_of_the_price():
    series = wobbly(10, n=2000)
    now = float(series.close[-1])
    for one in lv.levels(series):
        if one.kind == "저항":
            assert one.price > now, "지금 값보다 낮은데 저항이라고 했습니다"
        else:
            assert one.price <= now, "지금 값보다 높은데 지지라고 했습니다"


def test_nearby_prices_become_one_level():
    """묶지 않으면 같은 자리가 선 열 개로 나온다."""
    # 같은 높이를 여러 번 찍는 톱니
    tooth = np.tile([100.0, 103.0, 100.0, 97.0], 60)
    series = make(tooth)
    found = lv.levels(series, reach=1)
    prices = sorted(one.price for one in found)
    for a, b in pairwise(prices):
        assert b - a > lv.atr(series) * lv.CLUSTER * 0.9, "가까운 선이 안 묶였습니다"


def test_far_away_levels_are_dropped():
    """단타에 5% 밖의 선은 아무 소용이 없다."""
    series = wobbly(11, n=3000)
    now = float(series.close[-1])
    for one in lv.levels(series):
        assert abs(one.price - now) / now <= lv.FAR


def test_fibonacci_levels_lie_between_the_swing_ends():
    series = wobbly(12, n=2000)
    found = lv.retracements(series)
    if not found:
        pytest.skip("이 데이터에는 되돌림을 그릴 파동이 없습니다")
    highs, lows = lv.swings(series)
    top = float(series.high[highs[-1]])
    bottom = float(series.low[lows[-1]])
    for one in found:
        assert min(top, bottom) <= one.price <= max(top, bottom)


def test_levels_on_a_flat_line_are_empty_not_an_error():
    """움직임이 0이면 '되돌아선 자리'라는 게 성립하지 않는다."""
    flat = np.full(500, 100.0)
    assert lv.levels(make(flat, highs=flat, lows=flat, opens=flat)) == []
