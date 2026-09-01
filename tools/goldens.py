"""파이썬 결과를 '정답지'로 뽑아 둔다.

왜 필요한가
-----------
같은 계산이 파이썬과 자바스크립트 두 벌로 존재하게 됐다. 코드스페이스 없이
아이패드에서 쓰려면 브라우저 안에서 계산해야 하는데, 그렇다고 검증이 끝난
파이썬 쪽을 버릴 이유는 없다.

두 벌이 있으면 반드시 갈린다. 갈렸다는 걸 **알아채는 장치**가 이것이다.
여기서 뽑은 JSON을 자바스크립트 테스트가 읽어 같은 입력에 같은 숫자가
나오는지 대조한다. 포팅이 맞았다는 증거는 이것뿐이다.

입력 봉도 함께 저장한다. 양쪽에서 따로 만들면 난수기가 달라 애초에 같은
입력이 아니게 되고, 그러면 대조가 아무 의미도 없다.

    python3 tools/goldens.py            정답지를 다시 뽑는다
    python3 tools/goldens.py --check    지금 파이썬이 정답지와 같은 답을 내는지만 본다
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from patternscan.levels import atr, levels, retracements, swings
from patternscan.models import HORIZONS, Candle, Series
from patternscan.odds import (
    Odds,
    examples_for,
    find_matches,
    odds_for,
    project,
)
from patternscan.scan import round_trip_cost
from patternscan.search import MIN_CANDIDATES, distances_within
from patternscan.shape import (
    distances_to,
    flat_mask,
    is_flat,
    linearity,
    normalize_window,
    similarity_to_distance,
)
from patternscan.stats import wilson_interval
from patternscan.theories import read_all, score, tally
from patternscan.webui.server import (
    Analysis,
    _analysis_json,
    _examples_json,
    _verdict,
)

OUT = Path(__file__).resolve().parent.parent / "tests" / "js" / "goldens"

BARS = 5_000

#: 유사도. 0.85로는 5,000봉에서 닮은 구간이 7개밖에 안 나온다 — 표본이
#: 모자라 예상 그림도, 판정도 계산되지 않아 정답지로 쓸 수가 없다. 0.6이면
#: 57개가 나와서 계산이 끝까지 간다.
SIMILARITY = 0.6

#: score()에 넘길 시점 수.
#:
#: theories.score는 볼 시점이 이보다 많으면 numpy 난수로 추려낸다.
#: 브라우저에는 같은 난수기가 없어서(자바스크립트 쪽은 구간을 고르게 나눠
#: 고른다) 추려내기 시작하면 두 구현이 **다른 시점**을 보게 되고, 그러면
#: 대조가 성립하지 않는다. 그래서 정답지에서는 추려내지 않도록 크게 준다.
#: 실제 화면은 300을 쓴다.
ALL_POINTS = 1_000_000


def make_candles(
    count: int, seed: int, start_price: float = 50_000_000.0, step_minutes: int = 1
) -> list[Candle]:
    """분석에 쓸 만한 가짜 봉. 실제 시세를 흉내만 낸다.

    진짜 시세로 정답지를 만들 수는 없다 — 이 환경에서는 업비트가 막혀 있고,
    설령 받을 수 있어도 매번 값이 달라져 정답지가 될 수 없다.
    """
    # RandomState를 쓴다(default_rng가 아니라). numpy는 Generator의 난수
    # 흐름이 판올림 때 바뀔 수 있다고 밝혀 두었지만, RandomState는 재현성을
    # 보장한다. 정답지가 numpy 판올림 한 번에 통째로 바뀌면 "계산이 갈렸다"와
    # "numpy가 바뀌었다"를 구분할 수 없게 된다.
    rng = np.random.RandomState(seed)
    steps = rng.normal(0.0, 0.0009, size=count)
    # 아주 가끔 큰 봉을 섞는다. 장악형·망치형 같은 캔들 규칙과 변곡점
    # 판정이 실제로 걸리는 구간이 있어야 대조에 의미가 있다.
    shocks = rng.random_sample(count) < 0.02
    steps[shocks] *= 6.0
    closes = start_price * np.exp(np.cumsum(steps))

    opens = np.concatenate(([start_price], closes[:-1]))
    reach = np.abs(rng.normal(0.0, 0.0006, size=count)) * closes
    highs = np.maximum(opens, closes) + reach
    lows = np.minimum(opens, closes) - reach * rng.random_sample(count)
    volumes = np.abs(rng.normal(3.0, 1.2, size=count)) + 0.05

    base = datetime(2024, 3, 1, tzinfo=timezone.utc)
    return [
        Candle(
            ts=base + timedelta(minutes=i * step_minutes),
            open=float(opens[i]),
            high=float(highs[i]),
            low=float(lows[i]),
            close=float(closes[i]),
            volume=float(volumes[i]),
        )
        for i in range(count)
    ]


def candle_rows(candles: list[Candle]) -> list[dict[str, float]]:
    return [
        {
            "ts": int(c.ts.timestamp()),
            "open": c.open, "high": c.high, "low": c.low,
            "close": c.close, "volume": c.volume,
        }
        for c in candles
    ]


def shape_goldens(series: Series) -> dict:
    closes = series.close
    window = closes[100:140]
    flat = np.full(30, 42_000_000.0)
    return {
        "normalizeShape": normalize_window(window).tolist(),
        "normalizeAmplitude": normalize_window(window, "amplitude").tolist(),
        "isFlatWindow": bool(is_flat(window)),
        "isFlatConstant": bool(is_flat(flat)),
        "linearity": linearity(window),
        "linearityRising": linearity(np.arange(1.0, 41.0)),
        "similarityToDistance": [
            similarity_to_distance(v) for v in (1.0, 0.9, 0.8, 0.5, 0.0, -1.0, 2.0)
        ],
        "distancesTo": distances_to(closes[200:220], closes[:400], 20).tolist(),
        "flatMask": [bool(v) for v in flat_mask(closes[:400], 20)],
        "wilson": [list(wilson_interval(k, n)) for k, n in ((0, 0), (5, 10), (1, 3), (97, 100))],
    }


def verdict_cases(cost: float) -> list[dict]:
    """판정의 네 관문을 하나씩 건드려 본다.

    실제 데이터로는 '살 만합니다' 가지에 닿기가 어렵다 — 이 방법이 수수료를
    못 넘기기 때문이다. 그런데 바로 그 가지에 예전에 버그가 있었으므로
    (확률만 보고 매수를 권하면서 옆에 손실을 적어 뒀다) 손으로 만든 값으로
    반드시 짚고 넘어가야 한다.
    """
    def row(**kw) -> Odds:
        fields = dict(
            timeframe="minute1", length=20, horizon=10, samples=100, up=50, beat_cost=40,
            base_up=0.5, base_beat=0.4, median_return=0.0, best=0.02, worst=-0.02,
            min_similarity=0.9, query_linearity=0.3,
        )
        fields.update(kw)
        return Odds(**fields)

    cases = {
        # 표본이 모자라 아무 말도 못 하는 경우
        "표본부족": [row(samples=5, up=3, beat_cost=2)],
        # 네 관문을 모두 넘는 경우 — '살 만합니다'
        "통과": [row(up=70, beat_cost=60, median_return=cost * 3)],
        # 확률은 넘겼는데 돈이 안 되는 경우 — 예전에 여기서 사고가 났다
        "돈안됨": [row(up=70, beat_cost=60, median_return=cost * 0.3)],
        # 평소와 구분이 안 되는 경우
        "우연": [row(up=52, beat_cost=45, median_return=cost * 3)],
    }
    return [
        {"name": name, "rows": [
            {
                "timeframe": r.timeframe, "length": r.length, "horizon": r.horizon,
                "samples": r.samples, "up": r.up, "beatCost": r.beat_cost,
                "baseUp": r.base_up, "baseBeat": r.base_beat,
                "medianReturn": r.median_return, "best": r.best, "worst": r.worst,
                "minSimilarity": r.min_similarity, "queryLinearity": r.query_linearity,
            } for r in rows
        ], "verdict": _verdict(rows, cost)}
        for name, rows in cases.items()
    ]


def one_timeframe(
    market: str, timeframe: str, bars: int, seed: int, *, start_price: float,
    step_minutes: int, length: int, similarity: float, fee: float, slippage: float,
) -> tuple[Series, dict]:
    """한 봉 간격에 대해 계산을 끝까지 돌린 조각. Analysis를 짜맞출 재료다."""
    candles = make_candles(bars, seed=seed, start_price=start_price, step_minutes=step_minutes)
    series = Series.from_candles(market, timeframe, candles)
    cost = round_trip_cost(fee, slippage)
    matches = find_matches(
        series, length, max_horizon=max(HORIZONS), similarity=similarity, top_k=100
    )
    return series, {
        "candles": candle_rows(candles),
        "matches": matches,
        "odds": odds_for(series, length, horizons=HORIZONS, similarity=similarity,
                         top_k=100, fee=fee, slippage=slippage),
        "readings": read_all(series),
        "scores": score(series, horizon=10, points=ALL_POINTS, cost=cost),
        "levels": levels(series),
        "fibonacci": retracements(series),
        "projection": None if matches is None else project(series, matches, ahead=max(HORIZONS)),
    }


def build_full() -> dict:
    """1분봉 하나로 깊게. 모든 갈래를 한 번씩 밟는 기본 정답지."""
    market, timeframe, length = "KRW-BTC", "minute1", 20
    fee, slippage = 0.0005, 0.0002
    cost = round_trip_cost(fee, slippage)

    series, part = one_timeframe(
        market, timeframe, BARS, 7, start_price=50_000_000.0, step_minutes=1,
        length=length, similarity=SIMILARITY, fee=fee, slippage=slippage,
    )
    matches = part["matches"]
    if matches is None or len(matches.ends) < 20:
        raise SystemExit("닮은 구간이 모자랍니다 — 정답지로 쓸 수 없습니다.")
    if part["projection"] is None:
        raise SystemExit("예상 그림이 안 나왔습니다 — 정답지로 쓸 수 없습니다.")

    analysis = Analysis(
        market=market, cost=cost, similarity=SIMILARITY, length=length,
        series={timeframe: series},
        odds=part["odds"],
        matches={timeframe: matches},
        readings={timeframe: part["readings"]},
        scores={timeframe: part["scores"]},
        levels={timeframe: part["levels"]},
        fibonacci={timeframe: part["fibonacci"]},
        projection={timeframe: part["projection"]},
        missing=("minute3", "minute5"),
        updated_at="00:00:00",
    )
    highs, lows = swings(series)
    rose, fell = examples_for(series, matches, 10, cost=cost, count=3)
    return {
        "note": "tools/goldens.py가 만든 파일입니다. 손으로 고치지 마세요.",
        "candles": part["candles"],
        "market": market,
        "timeframe": timeframe,
        "length": length,
        "similarity": SIMILARITY,
        "fee": fee,
        "slippage": slippage,
        "cost": cost,
        "points": ALL_POINTS,
        "shape": shape_goldens(series),
        "levels": {
            "atr": atr(series),
            "swingHighs": [int(v) for v in highs],
            "swingLows": [int(v) for v in lows],
        },
        "theories": {"tally": list(tally(part["readings"]))},
        "matches": {
            "ends": [int(v) for v in matches.ends],
            "distances": [float(v) for v in matches.distances],
            "limit": int(matches.limit),
        },
        "examples": _examples_json(analysis, timeframe, 10),
        "examplesRaw": {
            "rose": [{"at": e.at, "outcome": e.outcome} for e in rose],
            "fell": [{"at": e.at, "outcome": e.outcome} for e in fell],
        },
        "verdictCases": verdict_cases(cost),
        # 이게 진짜 정답지다. 위의 것들은 어디서 갈렸는지 짚기 위한 이정표고,
        # 화면이 실제로 받는 것은 이 통짜 JSON이다.
        "analysis": _analysis_json(analysis),
    }


#: 봉 간격 셋을 한꺼번에 볼 때 쓸 길이. 이론 성적을 추려내지 않으려면
#: 볼 시점이 ALL_POINTS보다 적어야 하는데, 그건 어차피 만족한다.
MANY_BARS = 900
MANY_SIMILARITY = 0.5


def build_many() -> dict:
    """봉 간격 셋을 한꺼번에.

    왜 따로 두는가 — full.json은 1분봉 하나뿐이라 **봉 간격끼리 비교하는
    코드가 한 번도 안 돌아간다.** 다우의 '상호 확인'이 대표적이다. 그건
    1·3·5분봉이 서로 같은 말을 하는지 세는 것이라, 간격이 하나면 셀 것이
    없어서 통째로 검증 밖에 있었다.
    """
    market, length = "KRW-ETH", 20
    fee, slippage = 0.0005, 0.0002
    cost = round_trip_cost(fee, slippage)

    plan = (
        ("minute1", 1, 21, 4_100_000.0),
        ("minute3", 3, 22, 4_100_000.0),
        ("minute5", 5, 23, 4_100_000.0),
    )
    series_by: dict[str, Series] = {}
    parts: dict[str, dict] = {}
    candles_by: dict[str, list] = {}
    for timeframe, step_minutes, seed, price in plan:
        series, part = one_timeframe(
            market, timeframe, MANY_BARS, seed, start_price=price,
            step_minutes=step_minutes, length=length, similarity=MANY_SIMILARITY,
            fee=fee, slippage=slippage,
        )
        series_by[timeframe] = series
        parts[timeframe] = part
        candles_by[timeframe] = part["candles"]

    analysis = Analysis(
        market=market, cost=cost, similarity=MANY_SIMILARITY, length=length,
        series=series_by,
        odds=[row for part in parts.values() for row in part["odds"]],
        matches={tf: p["matches"] for tf, p in parts.items() if p["matches"] is not None},
        readings={tf: p["readings"] for tf, p in parts.items()},
        scores={tf: p["scores"] for tf, p in parts.items()},
        levels={tf: p["levels"] for tf, p in parts.items()},
        fibonacci={tf: p["fibonacci"] for tf, p in parts.items()},
        projection={tf: p["projection"] for tf, p in parts.items() if p["projection"] is not None},
        missing=(),
        updated_at="00:00:00",
    )
    out = _analysis_json(analysis)
    if out["theories"]["confirmation"]["says"] is None:
        raise SystemExit("상호 확인이 안 나왔습니다 — 정답지로 쓸 수 없습니다.")
    return {
        "note": "tools/goldens.py가 만든 파일입니다. 손으로 고치지 마세요.",
        "market": market,
        "length": length,
        "similarity": MANY_SIMILARITY,
        "fee": fee,
        "slippage": slippage,
        "points": ALL_POINTS,
        "candles": candles_by,
        "analysis": out,
    }


def build_paa() -> dict:
    """하한 거르기(PAA) 경로 대조.

    구간이 MIN_CANDIDATES(2만)보다 많아야 그 경로를 탄다. 거기서 버려지는
    후보가 정말 '확실히 먼' 것들뿐인지는 전수 계산과 대조해야 알 수 있다.
    종가만 있으면 되므로 봉 전체를 저장하지 않는다.
    """
    big = make_candles(MIN_CANDIDATES + 5_000, seed=11, start_price=3_100.0)
    closes = np.array([c.close for c in big])
    within = {}
    for name, win, sim in (("short", 20, 0.9), ("long", 60, 0.8)):
        threshold = similarity_to_distance(sim)
        positions, distances = distances_within(
            closes[-win:], closes[: closes.size - win], win, threshold
        )
        within[name] = {
            "length": win, "similarity": sim, "threshold": threshold,
            "positions": [int(v) for v in positions],
            "distances": [float(v) for v in distances],
        }
    return {
        "note": "tools/goldens.py가 만든 파일입니다. 손으로 고치지 마세요.",
        "closes": closes.tolist(),
        "within": within,
    }


FILES = {"full.json": build_full, "many.json": build_many, "paa.json": build_paa}


# --------------------------------------------------------------- 대조
#
# **바이트가 같기를 요구하면 안 된다.**
#
# 처음에는 정답지를 다시 뽑아 `git diff`로 봤는데, numpy 판이 다르면
# (내 환경 2.4.6, CI 2.5.2) 마지막 자릿수가 달라져서 파일이 매번 바뀐다.
# 실제로 CI가 그것 때문에 빨간불이 났다 — 계산이 갈린 게 아니라 빌드가
# 다른 것뿐인데.
#
# 그래서 값으로 비교한다. 정수·참거짓·글자는 정확히 같아야 하고(계산이
# 갈리면 여기가 먼저 움직인다 — 이론이 방향을 말한 횟수 같은 것들),
# 소수는 여유를 둔다. 진짜로 식이 바뀌면 그 여유를 훌쩍 넘는다.

#: 소수에 둘 상대 여유. numpy 판 차이는 1e-12 언저리이고, 식이 바뀌면
#: 보통 1e-3 이상 움직인다. 그 사이면 어디를 잡아도 되지만 넉넉히 둔다.
DRIFT = 1e-6


def differences(fresh: object, saved: object, path: str = "") -> list[str]:
    """값이 갈린 자리를 전부 찾아 경로와 함께 돌려준다."""
    if isinstance(fresh, bool) or isinstance(saved, bool):
        return [] if fresh == saved else [f"{path}: {fresh} ≠ {saved}"]
    if isinstance(fresh, (int, float)) and isinstance(saved, (int, float)):
        if isinstance(fresh, int) and isinstance(saved, int):
            return [] if fresh == saved else [f"{path}: {fresh} ≠ {saved}"]
        gap = abs(float(fresh) - float(saved))
        allowed = DRIFT * max(1.0, abs(float(fresh)), abs(float(saved)))
        return [] if gap <= allowed else [f"{path}: {fresh} ≠ {saved} (차이 {gap})"]
    if isinstance(fresh, dict) and isinstance(saved, dict):
        if fresh.keys() != saved.keys():
            return [f"{path}: 키가 다릅니다 {sorted(fresh)} ≠ {sorted(saved)}"]
        out = []
        for key in fresh:
            out += differences(fresh[key], saved[key], f"{path}.{key}" if path else str(key))
        return out
    if isinstance(fresh, list) and isinstance(saved, list):
        if len(fresh) != len(saved):
            return [f"{path}: 길이 {len(fresh)} ≠ {len(saved)}"]
        out = []
        for i, (a, b) in enumerate(zip(fresh, saved, strict=True)):
            out += differences(a, b, f"{path}[{i}]")
        return out
    return [] if fresh == saved else [f"{path}: {fresh!r} ≠ {saved!r}"]


def check() -> int:
    """지금 파이썬이 커밋된 정답지와 같은 답을 내는가."""
    problems = []
    for name, build in FILES.items():
        path = OUT / name
        if not path.is_file():
            problems.append(f"{name}: 파일이 없습니다 — python3 tools/goldens.py 를 돌리세요")
            continue
        saved = json.loads(path.read_text(encoding="utf-8"))
        found = differences(json.loads(json.dumps(build())), saved, name)
        if found:
            problems.append(f"{name}: {len(found)}군데가 다릅니다")
            problems += [f"    {line}" for line in found[:10]]
            if len(found) > 10:
                problems.append(f"    … 그 밖에 {len(found) - 10}군데")
        else:
            print(f"  {name}: 같습니다")

    if problems:
        print("\n정답지와 지금 계산이 갈렸습니다:")
        for line in problems:
            print(line)
        print("\n계산을 일부러 고치셨다면 python3 tools/goldens.py 로 정답지를 다시 뽑으세요.")
        return 1
    print("정답지가 지금 계산과 맞습니다.")
    return 0


def write() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in FILES.items():
        payload = build()
        (OUT / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"{name:<12} {(OUT / name).stat().st_size:>10,} 바이트")

    full = json.loads((OUT / "full.json").read_text(encoding="utf-8"))
    many = json.loads((OUT / "many.json").read_text(encoding="utf-8"))
    print(f"full: 닮은 과거 {len(full['matches']['ends'])}개 · "
          f"확률 {len(full['analysis']['odds'])}줄 · "
          f"판정 '{full['analysis']['verdict']['headline']}'")
    for case in full["verdictCases"]:
        print(f"  판정 사례 {case['name']}: {case['verdict']['headline']}")
    theories = many["analysis"]["theories"]
    print(f"many: 봉 간격 {len([k for k in theories if k != 'confirmation'])}종 · "
          f"예상 그림 {len(many['analysis']['projection'])}개")
    print(f"  상호 확인: {theories['confirmation']['says']} — {theories['confirmation']['detail']}")


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else (write() or 0))
