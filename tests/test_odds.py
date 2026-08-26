"""확률 안내 도구 검증.

이 도구는 판단을 내리지 않는다. 대신 확률을 말한다 — 그래서 **숫자를 어떻게
보여주느냐가 전부**다. "56%"만 보여주면 사용자는 그게 좋은 건지 알 수 없다.
평소 확률이 55%라면 56%는 아무 의미가 없기 때문이다.

그래서 여기서 지키는 것은 셋이다.
1. 미래를 안 본다
2. 확률 옆에 **평소 확률**과 **불확실성**이 반드시 붙는다
3. 결과가 없는 봉 간격도 그렇다고 **말한다** (조용히 빼지 않는다)
"""

from __future__ import annotations

import numpy as np
import pytest

from patternscan.odds import MIN_SAMPLES, Odds, format_odds, odds_all, odds_for
from patternscan.scan import round_trip_cost
from tests.conftest import make_series, planted_signal


@pytest.fixture
def walk():
    rng = np.random.default_rng(5)
    closes = 40_000_000 * np.exp(np.cumsum(rng.normal(0, 0.0009, 20_000)))
    return make_series(closes.tolist())


# ------------------------------------------------------------------ 미래 참조
def test_matches_never_come_from_the_future(walk):
    """확률의 근거가 되는 과거 구간은 질의 구간 전에 끝나야 한다."""
    length, query_end = 60, len(walk) - 1
    rows = odds_for(walk, length, query_end=query_end, horizons=(1, 20))
    assert rows
    # odds_for는 위치를 돌려주지 않으므로, 뒤 데이터를 잘라도 같은지로 확인한다
    trimmed = walk.slice(0, query_end + 1)
    same = odds_for(trimmed, length, query_end=query_end, horizons=(1, 20))
    assert [r.up for r in rows] == [r.up for r in same]
    assert [r.samples for r in rows] == [r.samples for r in same]


def test_later_data_does_not_change_the_answer(walk):
    length = 40
    early = len(walk) // 2
    a = odds_for(walk, length, query_end=early, horizons=(1, 5))
    b = odds_for(walk.slice(0, early + 1), length, query_end=early, horizons=(1, 5))
    assert [r.up_rate for r in a] == [r.up_rate for r in b]


# ------------------------------------------------------- 숫자를 혼자 두지 않기
def test_every_probability_carries_its_baseline(walk):
    """평소 확률이 없으면 '56%'가 좋은 건지 나쁜 건지 알 수 없다."""
    for row in odds_for(walk, 60, horizons=(1, 5, 20)):
        assert 0.0 <= row.base_up <= 1.0
        assert 0.0 <= row.base_beat <= 1.0
        assert row.up_edge == pytest.approx(row.up_rate - row.base_up)


def test_interval_widens_when_samples_are_few():
    """표본이 적으면 확률이 흔들린다는 걸 숫자로 말해야 한다."""
    few = Odds("minute1", 60, 5, samples=20, up=11, beat_cost=4, base_up=0.5,
               base_beat=0.2, median_return=0.0, best=0.0, worst=0.0,
               min_similarity=0.9, query_linearity=0.3)
    many = Odds("minute1", 60, 5, samples=2000, up=1100, beat_cost=400, base_up=0.5,
                base_beat=0.2, median_return=0.0, best=0.0, worst=0.0,
                min_similarity=0.9, query_linearity=0.3)
    width = lambda o: o.interval[1] - o.interval[0]
    assert width(few) > width(many) * 5


def test_a_probability_inside_its_error_bars_is_marked_uninformative():
    """불확실 범위가 평소를 품으면 '구분 안 됨'이어야 한다."""
    noise = Odds("minute1", 60, 5, samples=100, up=52, beat_cost=20, base_up=0.50,
                 base_beat=0.20, median_return=0.0, best=0.0, worst=0.0,
                 min_similarity=0.9, query_linearity=0.3)
    assert not noise.tells_us_anything

    strong = Odds("minute1", 60, 5, samples=500, up=350, beat_cost=200, base_up=0.50,
                  base_beat=0.20, median_return=0.0, best=0.0, worst=0.0,
                  min_similarity=0.9, query_linearity=0.3)
    assert strong.tells_us_anything


def test_report_always_shows_baseline_and_uncertainty(walk):
    rows = odds_for(walk, 60, horizons=(1, 5))
    text = format_odds(rows, round_trip_cost())
    assert "평소" in text
    assert "불확실 범위" in text
    assert "매수를 권하지 않습니다" in text


def test_report_names_timeframes_that_found_nothing():
    """조용히 빼면 사용자는 1·3·5분봉을 다 본 줄 안다."""
    text = format_odds([], round_trip_cost(), expected=["minute1", "minute3", "minute5"])
    for label in ("1분봉", "3분봉", "5분봉"):
        assert label in text


def test_thin_samples_are_refused_not_reported():
    """표본이 모자라면 확률을 말하지 않는다."""
    thin = Odds("minute1", 60, 5, samples=3, up=3, beat_cost=3, base_up=0.5,
                base_beat=0.2, median_return=0.0, best=0.0, worst=0.0,
                min_similarity=0.9, query_linearity=0.3)
    text = format_odds([thin], round_trip_cost())
    assert "확률을 말할 수 없습니다" in text
    assert "100%" not in text


# ------------------------------------------------------------------ 셈 규칙
def test_horizon_is_reported_in_real_minutes():
    """5분봉의 '1봉 뒤'는 5분 뒤다 — 이걸 헷갈리면 표를 잘못 읽는다."""
    for timeframe, horizon, minutes in (
        ("minute1", 20, 20), ("minute3", 7, 21), ("minute5", 4, 20)
    ):
        row = Odds(timeframe, 60, horizon, samples=50, up=25, beat_cost=10, base_up=0.5,
                   base_beat=0.2, median_return=0.0, best=0.0, worst=0.0,
                   min_similarity=0.9, query_linearity=0.3)
        assert row.minutes == minutes


def test_beating_cost_is_rarer_than_merely_rising(walk):
    """수수료까지 넘기는 건 그냥 오르는 것보다 드물어야 한다."""
    for row in odds_for(walk, 60, horizons=(1, 5, 20)):
        assert row.beat_cost <= row.up
        assert row.base_beat <= row.base_up


def test_counts_never_exceed_the_sample(walk):
    for row in odds_for(walk, 60, horizons=(1, 5, 20)):
        assert 0 <= row.up <= row.samples
        assert 0 <= row.beat_cost <= row.samples


def test_flat_query_returns_nothing():
    flat = make_series([100.0] * 3000)
    assert odds_for(flat, 60) == []


def test_not_enough_history_returns_nothing():
    short = make_series([100.0 + i * 0.01 for i in range(200)])
    assert odds_for(short, 180) == []


def test_odds_all_covers_every_timeframe(walk):
    rows = odds_all({"minute1": walk}, 60, horizons=(1, 5))
    assert {r.timeframe for r in rows} == {"minute1"}
    assert {r.horizon for r in rows} == {1, 5}


# ------------------------------------------------------------ 신호가 있을 때
def test_a_planted_signal_shows_a_high_probability():
    """표식 뒤에 반드시 오르게 심으면 확률이 평소보다 훨씬 높아야 한다."""
    series = make_series(planted_signal(seed=1))
    rows = odds_for(series, 5, horizons=(1, 3), similarity=0.85)
    assert rows
    best = max(rows, key=lambda r: r.up_edge)
    assert best.samples >= MIN_SAMPLES
    assert best.up_rate > 0.9, f"심어둔 신호인데 확률이 {best.up_rate:.0%}입니다"
    assert best.tells_us_anything
