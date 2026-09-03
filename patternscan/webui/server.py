"""127.0.0.1에만 붙는 작은 분석 화면.

주문 기능은 없다. 이 서버가 하는 일은 세 가지뿐이다.

1. 시세를 받아 캐시에 저장한다
2. 지금 모양을 과거와 비교해 판정한다
3. 그 결과를 그림과 표로 보여준다

분석은 몇십 초 걸릴 수 있으므로 백그라운드 스레드에서 돌리고, 화면은
진행 상황을 폴링한다. 요청 스레드에서 바로 돌리면 브라우저가 그동안
아무것도 못 그린다.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np

from ..data import count_cached, load_cached, span_cached, update
from ..levels import Level, levels, retracements
from ..models import HORIZONS, KST, MARKETS, Series, market_label, timeframe_label
from ..odds import MIN_SAMPLES as ODDS_MIN_SAMPLES
from ..odds import Odds, Projection, examples_for, find_matches, odds_for, project
from ..scan import (
    DEFAULT_FEE,
    DEFAULT_SIMILARITY,
    DEFAULT_SLIPPAGE,
    round_trip_cost,
)
from ..shape import normalize_window
from ..theories import LOOKBACK as THEORY_LOOKBACK
from ..theories import Score, dow, dow_confirmation, read_all, score, tally
from ..upbit import Ticker, UpbitClient, UpbitError

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"

#: 화면에 겹쳐 그릴 매치 개수 상한. 이보다 많으면 선이 뭉개져서 못 읽는다.
CHART_MATCHES = 40

#: 봉 간격별 기본 수집량 (1분봉 30일치 기준).
DEFAULT_COUNT = {"minute1": 43_200, "minute3": 14_400, "minute5": 8_640}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}

#: 캐시를 걸면 안 되는 것. 계산 결과와 화면 코드는 늘 새로 받아야 한다.
#: 아이콘은 안 바뀌므로 한 번 받아두면 홈 화면에서 여는 게 빨라진다.
CACHEABLE = {".png", ".ico"}

#: 탭 아이콘. 없으면 브라우저가 /favicon.ico를 찾다 404를 받고
#: 콘솔에 오류를 남긴다 — 진짜 오류를 찾을 때 방해가 된다.
FAVICON = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    b'<rect width="32" height="32" rx="7" fill="#050807"/>'
    b'<polyline points="5,21 11,13 16,17 21,7 27,12" fill="none" '
    b'stroke="#00ff66" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
    b"</svg>"
)


# ---------------------------------------------------------------- 분석 상태
@dataclass
class Analysis:
    """마지막으로 끝난 계산 한 건."""

    market: str
    cost: float
    similarity: float
    length: int
    series: dict[str, Series]
    odds: list[Odds]
    #: 시세가 없어 빠진 봉 간격. 조용히 빼면 3종을 다 본 줄 안다.
    missing: tuple[str, ...] = ()
    #: 봉 간격별로 찾아둔 매치. 사례를 볼 때마다 다시 찾으면 몇 초씩 걸리고,
    #: 그동안 브라우저가 요청을 취소해 서버에 BrokenPipe가 쌓인다.
    matches: dict[str, Any] = field(default_factory=dict)
    #: 봉 간격별 차트 이론 읽기와, 그 이론들이 과거에 얼마나 맞았는지.
    readings: dict[str, Any] = field(default_factory=dict)
    scores: dict[str, list[Score]] = field(default_factory=dict)
    #: 지지·저항선과 피보나치 되돌림 (봉 간격별).
    levels: dict[str, list[Level]] = field(default_factory=dict)
    fibonacci: dict[str, list[Level]] = field(default_factory=dict)
    #: 닮았던 과거들이 그 다음에 간 길.
    projection: dict[str, Projection] = field(default_factory=dict)
    #: 이 계산이 언제 끝났는지 (KST 문자열). 화면이 "몇 분 전 기준"을 보여준다.
    updated_at: str = ""


@dataclass
class Job:
    """진행 중인 작업. 화면은 이걸 폴링한다."""

    kind: str = "idle"
    running: bool = False
    message: str = ""
    done: int = 0
    total: int = 0
    error: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "kind": self.kind,
                "running": self.running,
                "message": self.message,
                "done": self.done,
                "total": self.total,
                "error": self.error,
            }

    def update(self, **kwargs: Any) -> None:
        with self.lock:
            for key, value in kwargs.items():
                setattr(self, key, value)


class Prices:
    """현재가를 잠깐 들고 있는다.

    화면 맨 위 숫자는 몇 초마다 갱신돼야 살아 있어 보인다. 그런데 창을
    여러 개 열어두면 그만큼 업비트를 두드리게 되고, 수집이 도는 중이면
    같은 한도를 나눠 쓰게 되어 **수집이 느려진다**. 화면 숫자 하나 때문에
    본업이 밀리면 안 되므로, 한 번 받아 잠깐 돌려 쓴다.
    """

    def __init__(self, client: UpbitClient | None = None, ttl: float = 3.0) -> None:
        self.client = client or UpbitClient(max_retries=1, timeout=5.0)
        self.ttl = ttl
        self._lock = threading.Lock()
        self._seen: dict[str, tuple[float, Ticker]] = {}

    def get(self, market: str) -> Ticker:
        with self._lock:
            found = self._seen.get(market)
            if found and time.monotonic() - found[0] < self.ttl:
                return found[1]
        ticker = self.client.get_ticker(market)
        with self._lock:
            self._seen[market] = (time.monotonic(), ticker)
        return ticker


class Cancelled(RuntimeError):
    """사용자가 멈췄다. 고장이 아니므로 오류로 취급하지 않는다."""


class State:
    def __init__(self, market: str, data_dir: str) -> None:
        self.market = market
        self.data_dir = data_dir
        self.prices = Prices()
        self.job = Job()
        #: 8년치 수집은 40분이 걸린다. 잘못 눌렀을 때 빠져나올 길이 없으면
        #: 서버를 껐다 켜는 수밖에 없다 — 아이패드에서는 그게 제일 어렵다.
        self.cancel = threading.Event()
        #: 미리 받기를 멈추라는 신호. 사용자가 뭘 누르면 곧바로 세운다.
        self._warm_stop = threading.Event()
        self.analysis: Analysis | None = None
        #: 분석이 새로 끝날 때마다 올라간다. 화면은 이 번호가 바뀔 때만
        #: 표를 다시 그린다 — 매번 다시 그리면 사용자가 누르던 행이
        #: 클릭 도중에 사라진다.
        self.analysis_id = 0
        self._start_lock = threading.Lock()

    def publish(self, analysis: Analysis) -> None:
        self.analysis = analysis
        self.analysis_id += 1

    def start(self, kind: str, target: Any, *args: Any) -> bool:
        """작업을 시작한다. 이미 돌고 있으면 False."""
        with self._start_lock:
            if self.job.running:
                return False
            # 미리 받던 게 있으면 비키게 한다. 본업이 먼저다.
            self._warm_stop.set()
            self.cancel.clear()
            self.job.update(kind=kind, running=True, message="시작하는 중…", done=0, total=0, error="")
        thread = threading.Thread(target=self._run, args=(target, args), daemon=True)
        thread.start()
        return True

    def warm(self, market: str, count: int) -> None:
        """다른 종목들을 미리 받아둔다. **작업 자리를 차지하지 않는다.**

        사용자가 버튼을 누르면 곧바로 비켜야 하므로 job과 따로 돈다.
        job 자리를 쓰면 미리 받는 동안 버튼이 잠겨 버린다.
        """
        self._warm_stop.set()
        self._warm_stop = threading.Event()
        mine = self._warm_stop
        thread = threading.Thread(
            target=_do_warm, args=(self, market, count, mine), daemon=True
        )
        thread.start()

    def stop(self) -> bool:
        """돌고 있으면 멈추라고 알린다. 받아둔 것은 이미 저장돼 있다."""
        if not self.job.running:
            return False
        self.cancel.set()
        self.job.update(message="멈추는 중…")
        return True

    def checkpoint(self) -> None:
        """멈추라고 했으면 여기서 빠져나간다. 오래 도는 자리에서 부른다."""
        if self.cancel.is_set():
            raise Cancelled("멈췄습니다")

    def _run(self, target: Any, args: tuple[Any, ...]) -> None:
        try:
            target(*args)
        except Cancelled:
            # 사용자가 멈춘 것이므로 빨간 오류로 띄우지 않는다.
            self.job.update(message="멈췄습니다 — 받아둔 만큼은 저장돼 있습니다", error="")
        except (UpbitError, RuntimeError, ValueError, KeyError, OSError) as exc:
            log.exception("작업 실패")
            self.job.update(error=str(exc), message="실패했습니다")
        except Exception as exc:  # 예상 못 한 것까지 — 조용히 죽으면 원인을 못 찾는다
            log.exception("작업이 예상 못 한 이유로 실패")
            self.job.update(error=f"{type(exc).__name__}: {exc}", message="실패했습니다")
        finally:
            self.cancel.clear()
            self.job.update(running=False)


def _do_warm(state: State, market: str, count: int, stop: threading.Event) -> None:
    """지금 안 보고 있는 종목들을 조용히 받아둔다.

    이더리움을 처음 누르면 거기서 몇 분을 기다리게 된다. 비트코인을
    보는 동안 나머지를 미리 받아두면 누르는 즉시 나온다.

    **본업을 밀어내지 않는 게 조건이다.** 그래서
      · 사용자가 뭘 누르면 곧바로 비켜준다 (checkpoint)
      · 봉 간격당 한 번씩만 확인한다
      · 실패해도 조용히 넘어간다 — 이건 미리 해두는 일이지 시킨 일이 아니다
    """
    client = UpbitClient()
    others = [code for code in MARKETS if code != market]
    for code in others:
        for timeframe in DEFAULT_COUNT:
            if stop.is_set():
                return
            try:
                update(client, code, timeframe, count // _RATIO[timeframe],
                       directory=state.data_dir)
            except Exception as exc:
                # **무엇이 나든** 조용히 넘어간다. 이건 미리 해두는 일이지
                # 시킨 일이 아니다. 여기서 새는 예외는 사용자가 부탁한 적
                # 없는 작업 때문에 화면에 오류를 띄우게 된다.
                log.debug("%s %s 미리 받기 실패 — 넘어갑니다 (%s)", code, timeframe, exc)
        state.job.update(message=f"{market_label(code)} 미리 받아두는 중…")
    state.job.update(message="")


# ---------------------------------------------------------------- 작업 본체
def _do_fetch(state: State, market: str, refresh: bool) -> None:
    client = UpbitClient()
    for timeframe, count in DEFAULT_COUNT.items():
        label = timeframe_label(timeframe)
        state.job.update(message=f"{label} 시세 받는 중…", done=0, total=count)

        def progress(done: int, total: int) -> None:
            state.checkpoint()
            state.job.update(done=done, total=total)

        state.checkpoint()
        # 봉은 여기서 안 쓴다. 파일만 최신이면 된다.
        update(
            client, market, timeframe, count,
            directory=state.data_dir, refresh=refresh, progress=progress,
        )
    state.job.update(message="시세 수집을 마쳤습니다", done=0, total=0)


def _do_live(
    state: State, market: str, similarity: float, fee: float, slippage: float,
    length: int, count: int,
) -> None:
    """업비트에서 새 봉을 받고 곧바로 다시 계산한다.

    캐시가 있으면 새로 생긴 봉만 받으므로 몇 초면 끝난다 (data.fetch 참고).
    """
    client = UpbitClient()
    for timeframe in DEFAULT_COUNT:
        state.checkpoint()
        label = timeframe_label(timeframe)
        wanted = count // _RATIO[timeframe]
        state.job.update(message=f"{label} 받는 중…", done=0, total=wanted)

        # 8년치는 40분이 걸린다. 3칸짜리 막대로는 멈춘 것과 구분이 안 된다.
        # 멈추라는 신호도 여기서 본다 — 페이지마다 불리는 유일한 자리다.
        def progress(done: int, total: int, name: str = label) -> None:
            state.checkpoint()
            state.job.update(message=f"{name} 받는 중…", done=done, total=total)

        try:
            update(client, market, timeframe, wanted,
                   directory=state.data_dir, progress=progress)
        except UpbitError as exc:
            log.warning("%s 갱신 실패(%s) — 가진 것으로 계산합니다", timeframe, exc)
    _do_odds(state, market, similarity, fee, slippage, length)
    # 보고 있는 종목이 끝났으니, 나머지는 조용히 받아둔다.
    state.warm(market, count)


#: 1분봉 개수를 기준으로 각 간격이 몇 분의 1인지
_RATIO = {"minute1": 1, "minute3": 3, "minute5": 5}


def _do_odds(
    state: State, market: str, similarity: float, fee: float, slippage: float, length: int,
) -> None:
    """확률만 계산한다.

    예전에는 여기서 285개 조합 전체 판정(scan_all)을 돌렸는데, 화면이
    확률 중심으로 바뀐 뒤로는 쓰지도 않으면서 몇 분씩 잡아먹고 있었다.
    확률 계산은 빠른 탐색(search.py)을 타므로 초 단위로 끝난다.
    """
    state.job.update(message="캐시에서 시세를 읽는 중…")
    series = {}
    for timeframe in DEFAULT_COUNT:
        loaded = load_cached(market, timeframe, state.data_dir)
        if len(loaded):
            series[timeframe] = loaded

    if not series:
        raise RuntimeError("시세가 없습니다. 먼저 '시세 받기'를 눌러 주세요.")

    cost = round_trip_cost(fee, slippage)
    total = len(series)
    state.job.update(message="닮은 과거를 찾는 중…", done=0, total=total)

    rows: list[Odds] = []
    found: dict[str, Any] = {}
    seen: dict[str, Any] = {}
    marks: dict[str, list[Score]] = {}
    lines: dict[str, list[Level]] = {}
    fibs: dict[str, list[Level]] = {}
    ahead: dict[str, Projection] = {}
    for done, one in enumerate(series.values(), start=1):
        state.checkpoint()
        rows.extend(
            odds_for(one, length, horizons=HORIZONS, similarity=similarity,
                     top_k=100, fee=fee, slippage=slippage)
        )
        matched = find_matches(
            one, length, max_horizon=max(HORIZONS), similarity=similarity, top_k=100
        )
        if matched is not None:
            found[one.timeframe] = matched
            forward = project(one, matched, ahead=max(HORIZONS))
            if forward is not None:
                ahead[one.timeframe] = forward

        state.job.update(message=f"{timeframe_label(one.timeframe)} 차트 이론 보는 중…")
        seen[one.timeframe] = read_all(one)
        lines[one.timeframe] = levels(one)
        fibs[one.timeframe] = retracements(one)
        state.checkpoint()
        # 이론이 과거에 맞았는지도 **사용자 데이터로 직접** 센다.
        marks[one.timeframe] = score(one, horizon=10, points=300, cost=cost)
        state.job.update(done=done, message=f"{timeframe_label(one.timeframe)} 완료")

    state.publish(
        Analysis(
            market=market, cost=cost, similarity=similarity, length=length,
            series=series, odds=rows, matches=found,
            readings=seen, scores=marks, levels=lines, fibonacci=fibs, projection=ahead,
            missing=tuple(tf for tf in DEFAULT_COUNT if tf not in series),
            updated_at=datetime.now(KST).strftime("%H:%M:%S"),
        )
    )
    state.job.update(message="계산을 마쳤습니다", done=total, total=total)


def _why_nothing_matched(rows: list[Odds]) -> list[str]:
    """왜 표본이 안 모였는지, 그리고 **무엇을 바꾸면 되는지**.

    예전에는 "데이터를 더 받거나 기준을 낮춰 보세요" 한 줄이 전부였다.
    이미 기준을 낮춘 사람에게는 아무 말도 안 한 것과 같고, 얼마나 모자란
    건지도 알 수 없다. 몇 개가 모였는지 숫자로 말해 준다.

    **어느 손잡이를 먼저 말할지는 지금 값에 달렸다.**

    직전 봉이 180개처럼 길면 그게 가장 큰 원인이다 — 긴 모양이 과거에
    그대로 반복될 일은 드물어서, 유사도를 아무리 낮춰도 잘 안 늘어난다.

    그런데 이미 20~40이면 그 말은 아무 소용이 없다. 그때 듣는 것은 기간이다.
    실제 비트코인 1분봉으로 재 보면 닮은 과거가 기간에 거의 비례해서 는다.

        7일 1개 · 30일 3개 · 90일 8개 · 1년 18개 · 2년 36개
    """
    best = max((r.samples for r in rows), default=0)
    length = rows[0].length if rows else 0
    found = (
        f"가장 많이 모인 조합도 {best}개뿐입니다 (최소 {ODDS_MIN_SAMPLES}개 필요)."
        if best
        else "닮은 과거 구간이 하나도 없습니다."
    )
    advice = []
    if length >= 60:
        advice.append(
            f"**직전 몇 개 봉**이 {length}개입니다. 20~40으로 줄여 보세요 — "
            "긴 모양이 과거에 그대로 반복될 일은 드물어서, 유사도를 낮추는 것보다 "
            "이쪽이 훨씬 크게 듣습니다."
        )
        advice.append("그래도 모자라면 **얼마나 과거까지**를 늘리세요.")
    else:
        advice.append(
            "**얼마나 과거까지**를 늘려 보세요 — 닮은 과거는 기간에 거의 "
            "비례해서 늘어납니다 (실제 비트코인 1분봉 기준 7일 1개 → 1년 18개 → 2년 36개)."
        )
        advice.append(
            "**얼마나 닮아야 같은 모양인가**를 0.80쯤으로 낮추면 표본이 크게 늡니다. "
            "다만 그만큼 덜 닮은 것까지 세게 됩니다."
        )
    return [found, *advice]


#: 판정 문구에서 "얼마를 넣었다면"의 기본값.
#:
#: 예전에는 이 숫자가 문장 안에 100만원으로 박혀 있었다. 그래서 화면에서
#: 금액을 바꿔도 판정은 늘 100만원 기준으로 말했다 — 넣을 금액을 물어 놓고
#: 답에는 반영하지 않은 셈이다. 자바스크립트 판과 같은 값을 쓴다.
DEFAULT_STAKE = 1_000_000


def _verdict(rows: list[Odds], cost: float, stake: float = DEFAULT_STAKE) -> dict[str, Any]:
    """지금 살지 말지.

    확률만 보여주기로 했지만, 사용자는 결국 "그래서 사?"를 묻는다.
    답하되 근거를 함께 낸다. 사려면 **넷을 모두** 넘겨야 한다.

      1. 표본이 충분할 것
      2. 불확실 범위가 '평소'를 넘을 것 (우연과 구분될 것)
      3. 수수료까지 넘길 확률이 평소보다 높을 것
      4. **중앙값 수익이 왕복 비용보다 클 것**

    4번이 없어서 실제로 사고가 났다. 확률 셋을 다 통과한 조합이
    "살 만합니다"로 나갔는데, 그 조합의 중앙값 수익은 +0.036%였고 왕복
    비용은 0.140%였다. 화면 오른쪽 '넣었다면' 칸에는 **−1,036원**이 찍혀
    있었다. 매수를 권하면서 그 옆에 손실을 적어 둔 셈이다.

    확률이 높은 것과 돈이 되는 것은 다른 문제다. 오를 확률 60%여도 오를
    때 조금 오르고 내릴 때 많이 내리면 잃는다. 그래서 마지막 관문은
    확률이 아니라 **금액**이어야 한다.
    """
    usable = [r for r in rows if r.samples >= ODDS_MIN_SAMPLES]
    if not usable:
        return {
            "buy": False,
            "headline": "판단할 수 없습니다",
            "reasons": _why_nothing_matched(rows),
        }

    winners = [
        r for r in usable
        if r.tells_us_anything and r.up_edge > 0 and r.beat_rate > r.base_beat
        and r.median_return > cost
    ]
    if not winners:
        # 확률 관문을 넘고 돈에서만 걸린 조합이 있으면 그걸 보여준다.
        # 사용자가 표에서 가장 좋아 보인다고 느낄 줄이 바로 그 줄이다.
        close = [
            r for r in usable
            if r.tells_us_anything and r.up_edge > 0 and r.beat_rate > r.base_beat
        ]
        best = max(close or usable, key=lambda r: r.median_return - cost)
        low, high = best.interval
        reasons = [
            f"가장 나은 조합은 {timeframe_label(best.timeframe)} {best.minutes}분 뒤로, "
            f"올라 있을 확률 {best.up_rate:.0%}입니다 (평소 {best.base_up:.0%}, "
            f"닮은 과거 {best.samples}개 기준)."
        ]
        # 실제로 걸린 관문만 말한다. 통과한 조건까지 실패로 적으면 거짓말이 된다.
        if not best.tells_us_anything:
            reasons.append(
                f"불확실 범위가 {low:.0%}~{high:.0%}로 평소({best.base_up:.0%})를 품고 있습니다 — "
                f"표본 {best.samples}개로는 이 차이가 우연인지 알 수 없습니다."
            )
        if best.up_edge <= 0:
            reasons.append("평소보다 높지도 않습니다.")
        if best.beat_rate <= best.base_beat:
            reasons.append(
                f"수수료까지 넘긴 경우가 {best.beat_rate:.0%}로 평소 {best.base_beat:.0%}보다 낮습니다."
            )
        if best.median_return <= cost:
            # 이게 대개 마지막까지 남는 이유다. 금액으로 적어야 와닿는다.
            loss = (best.median_return - cost) * stake
            reasons.append(
                f"확률이 평소보다 높아도 **돈이 되지는 않습니다** — 중앙값 수익 "
                f"{best.median_return:+.3%}가 왕복 비용 {cost:.3%}를 못 넘깁니다. "
                f"{stake:,.0f}원이면 {loss:+,.0f}원입니다."
            )
        elif best.tells_us_anything and best.beat_rate > best.base_beat:
            reasons.append("다른 조합들이 기준을 넘지 못했습니다.")
        reasons.append("근거가 기준을 넘길 때까지는 들어가지 않는 것이 기본값입니다.")
        return {"buy": False, "headline": "사지 마세요 — 근거가 없습니다", "reasons": reasons}

    top = max(winners, key=lambda r: r.beat_rate - r.base_beat)
    low, high = top.interval
    return {
        "buy": True,
        "headline": f"살 만합니다 — {timeframe_label(top.timeframe)} {top.minutes}분 뒤 기준",
        "reasons": [
            f"닮은 과거 {top.samples}개 중 {top.up}개가 올랐습니다 "
            f"({top.up_rate:.0%}, 평소 {top.base_up:.0%}).",
            f"불확실 범위 {low:.0%}~{high:.0%}가 평소를 넘습니다 — 우연으로 보기 어렵습니다.",
            f"왕복 비용 {cost:.2%}까지 넘긴 경우가 {top.beat_rate:.0%}로, "
            f"평소 {top.base_beat:.0%}보다 높습니다.",
            f"중앙값 수익 {top.median_return:+.3%}가 왕복 비용을 넘습니다 — "
            f"{stake:,.0f}원이면 {(top.median_return - cost) * stake:+,.0f}원입니다.",
            f"같은 기준을 통과한 조합이 {len(winners)}개입니다.",
        ],
    }


# ---------------------------------------------------------------- 직렬화
def _odds_json(row: Odds) -> dict[str, Any]:
    low, high = row.interval
    return {
        "timeframe": row.timeframe,
        "timeframeLabel": timeframe_label(row.timeframe),
        "length": row.length,
        "horizon": row.horizon,
        "minutes": row.minutes,
        "samples": row.samples,
        "up": row.up,
        "beatCost": row.beat_cost,
        "upRate": row.up_rate,
        "beatRate": row.beat_rate,
        "baseUp": row.base_up,
        "baseBeat": row.base_beat,
        "upEdge": row.up_edge,
        "beatEdge": row.beat_edge,
        "ciLow": low,
        "ciHigh": high,
        "tellsUsAnything": row.tells_us_anything,
        "minSimilarity": _finite(row.min_similarity),
        "linearity": row.query_linearity,
        "medianReturn": row.median_return,
        "best": row.best,
        "worst": row.worst,
    }


def _finite(value: float) -> float | None:
    """JSON에는 NaN이 없다. 그대로 흘리면 화면에서 조용히 깨진다."""
    number = float(value)
    return number if np.isfinite(number) else None


def _theory_json(analysis: Analysis) -> dict[str, Any]:
    """이론들이 지금 뭐라고 하는지, 그리고 **과거에 맞았는지**.

    둘을 반드시 함께 내보낸다. 앞의 것만 보내면 이 도구는 점집이 된다.
    """
    out: dict[str, Any] = {}
    for timeframe, readings in analysis.readings.items():
        ups, downs, flats = tally(readings)
        marks = {s.theory: s for s in analysis.scores.get(timeframe, [])}
        bars = len(analysis.series.get(timeframe, ())) if analysis.series else 0
        out[timeframe] = {
            "label": timeframe_label(timeframe),
            "up": ups, "down": downs, "flat": flats,
            # 채점을 **아예 못 한** 것과 '이 이론이 방향을 말한 적이 없다'는
            # 전혀 다른 이야기다. 둘을 같은 문구로 보여주면, 봉이 201개뿐이라
            # 채점이 통째로 불가능한 상황에서도 열한 줄 모두가 "이 이론은
            # 방향을 말한 적이 없습니다"로 나온다 — 이론 탓처럼 읽힌다.
            "scoring": {
                "ran": bars >= SCORE_NEEDS,
                "have": bars,
                "need": SCORE_NEEDS,
            },
            "readings": [
                {
                    "theory": r.theory,
                    "says": r.says,
                    "detail": r.detail,
                    "clarity": round(r.clarity, 2),
                    "past": _score_json(marks.get(r.theory)),
                }
                for r in readings
            ],
        }
    # 다우의 상호 확인 — 봉 간격끼리 같은 말을 하는지
    dows = {tf: dow(one) for tf, one in analysis.series.items()}
    agreed = dow_confirmation(dows)
    out["confirmation"] = {"says": agreed.says, "detail": agreed.detail}
    return out


def _score_json(mark: Score | None) -> dict[str, Any] | None:
    if mark is None or mark.calls == 0:
        return None
    return {
        "calls": mark.calls,
        "rate": mark.rate,
        "base": mark.base,
        "edge": mark.edge,
        "beatRate": mark.beat_rate,
        "enough": mark.enough,
        "worthBelieving": mark.worth_believing,
    }


def _level_json(one: Level) -> dict[str, Any]:
    return {
        "price": one.price,
        "touches": one.touches,
        "lastTouch": one.last_touch,
        "kind": one.kind,
        "strength": round(one.strength, 3),
    }


#: 예상 앞에 붙일 **실제 봉** 개수. 지나온 길이 없으면 그림이 허공에서
#: 시작해 진짜 차트로 안 보인다.
RECENT_BARS = 40

#: 이론 성적을 낼 때 몇 봉 뒤를 보는지.
SCORE_HORIZON = 10

#: 과거 성적을 내려면 최소 몇 봉이 있어야 하는지. theories.score는 시점마다
#: 그 이전 LOOKBACK봉을 보고, 결과를 보려고 horizon봉을 더 쓴다.
SCORE_NEEDS = THEORY_LOOKBACK + SCORE_HORIZON + 2


def _recent_candles(series: Series, count: int = RECENT_BARS) -> list[dict[str, float]]:
    """마지막 봉 몇 개를 그대로. 종가만 주면 꼬리가 사라져 밋밋해진다."""
    take = min(count, len(series))
    if take <= 0:
        return []
    base = float(series.close[-1])
    if base <= 0:
        return []
    out = []
    for i in range(len(series) - take, len(series)):
        out.append({
            "o": round(float(series.open[i]) / base - 1.0, 6),
            "h": round(float(series.high[i]) / base - 1.0, 6),
            "l": round(float(series.low[i]) / base - 1.0, 6),
            "c": round(float(series.close[i]) / base - 1.0, 6),
        })
    return out


def _projection_json(forward: Projection, series: Series | None = None) -> dict[str, Any]:
    return {
        "recent": _recent_candles(series) if series is not None else [],
        "walks": forward.walks,
        "timeframe": forward.timeframe,
        "label": timeframe_label(forward.timeframe),
        "samples": forward.samples,
        "minutes": forward.minutes,
        "priceNow": forward.price_now,
        "median": forward.median,
        "low": forward.low,
        "high": forward.high,
        "worst": forward.worst,
        "best": forward.best,
        "fan": forward.fan,
        "spread": forward.spread,
    }


def _analysis_json(analysis: Analysis) -> dict[str, Any]:
    spans = []
    for timeframe, series in analysis.series.items():
        span = series.span
        spans.append({
            "timeframe": timeframe,
            "label": timeframe_label(timeframe),
            "count": len(series),
            "gaps": series.gaps(),
            # 지금 값. 예전에는 화면이 이걸 '예상 그림'에서 꺼내 썼는데,
            # 닮은 과거를 못 찾으면 그림이 없어서 지지·저항선을 위아래로
            # 가를 수가 없었다. 값은 그림과 상관없이 늘 있다.
            "priceNow": float(series.close[-1]) if len(series) else None,
            "from": span[0].isoformat() if span else None,
            "to": span[1].isoformat() if span else None,
        })
    return {
        "market": analysis.market,
        "cost": analysis.cost,
        "similarity": analysis.similarity,
        "oddsLength": analysis.length,
        "minSamples": ODDS_MIN_SAMPLES,
        "series": spans,
        "missing": [
            {"timeframe": tf, "label": timeframe_label(tf)} for tf in analysis.missing
        ],
        "odds": [_odds_json(r) for r in analysis.odds],
        "theories": _theory_json(analysis),
        "levels": {
            tf: [_level_json(x) for x in found]
            for tf, found in analysis.levels.items()
        },
        "fibonacci": {
            tf: [_level_json(x) for x in found]
            for tf, found in analysis.fibonacci.items()
        },
        "projection": {
            tf: _projection_json(p, analysis.series.get(tf))
            for tf, p in analysis.projection.items()
        },
        "verdict": _verdict(analysis.odds, analysis.cost),
        "updatedAt": analysis.updated_at,
    }


def _examples_json(
    analysis: Analysis, timeframe: str, horizon: int, count: int = 3
) -> dict[str, Any]:
    """가장 닮은 과거 사례를, 올랐던 쪽과 떨어졌던 쪽에서 각각 몇 개.

    확률 숫자만 보면 '정말 닮았나'를 확인할 방법이 없다. 실제 사례를
    겹쳐 보여줘서 사용자가 직접 판단하게 한다.
    """
    series = analysis.series.get(timeframe)
    if series is None:
        raise KeyError(f"{timeframe} 시세가 없습니다")

    matches = analysis.matches.get(timeframe)
    if matches is None:
        return {"timeframe": timeframe, "rose": [], "fell": [], "query": [], "horizon": horizon}

    rose, fell = examples_for(series, matches, horizon, cost=analysis.cost, count=count)

    def pack(example: Any) -> dict[str, Any]:
        return {
            "at": example.at,
            "similarity": round(example.similarity, 4),
            "outcome": example.outcome,
            "shape": example.shape,
            "after": example.after,
        }

    return {
        "timeframe": timeframe,
        "timeframeLabel": timeframe_label(timeframe),
        "length": analysis.length,
        "horizon": horizon,
        "cost": analysis.cost,
        "query": [round(v, 4) for v in normalize_window(matches.query).tolist()],
        "queryAt": series.kst_at(len(series) - 1).strftime("%Y-%m-%d %H:%M"),
        "rose": [pack(e) for e in rose],
        "fell": [pack(e) for e in fell],
    }


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "patternscan"
    state: State

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    # -- 응답 도우미
    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def _bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, name: str, extra: dict[str, str] | None = None) -> None:
        path = (STATIC / name).resolve()
        # 경로 탈출 방지: static 밖은 절대 내주지 않는다.
        if not str(path).startswith(str(STATIC.resolve())) or not path.is_file():
            self._error("없는 파일입니다", 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        if path.suffix in CACHEABLE:
            self.send_header("Cache-Control", "public, max-age=604800")
        else:
            self.send_header("Cache-Control", "no-store")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    # -- 라우팅
    def do_GET(self) -> None:
        url = urlparse(self.path)
        route = url.path
        try:
            if route in ("/", "/index.html"):
                self._static("index.html")
            elif route == "/sw.js":
                # 서비스 워커의 활동 범위는 **자기가 놓인 경로**로 정해진다.
                # /static/sw.js 로 주면 /static/ 아래만 맡게 되어, 정작
                # 첫 화면('/')을 대신 띄워 주지 못한다. 그래서 뿌리에 놓는다.
                self._static("sw.js", {"Service-Worker-Allowed": "/"})
            elif route == "/manifest.webmanifest":
                self._static("manifest.webmanifest")
            elif route == "/apple-touch-icon.png":
                # iOS는 link 태그가 없으면 이 주소를 직접 찾아본다.
                self._static("apple-touch-icon.png")
            elif route == "/favicon.ico":
                self._bytes(FAVICON, "image/svg+xml")
            elif route.startswith("/static/"):
                self._static(route[len("/static/") :])
            elif route == "/api/state":
                self._json(self._state_payload())
            elif route == "/api/examples":
                self._examples(parse_qs(url.query))
            elif route == "/api/ticker":
                self._ticker(parse_qs(url.query))
            else:
                self._error("없는 주소입니다", 404)
        except (BrokenPipeError, ConnectionResetError):
            # 사용자가 다음 행을 빨리 누르면 브라우저가 앞 요청을 끊는다.
            # 정상적인 일이므로 역추적을 화면에 뿌리지 않는다.
            log.debug("클라이언트가 연결을 끊었습니다: %s", route)
        except KeyError as exc:
            self._error(str(exc), 404)
        except (ValueError, RuntimeError) as exc:
            self._error(str(exc), 400)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            payload = self._body()
            if route == "/api/fetch":
                self._start_fetch(payload)
            elif route == "/api/live":
                self._start_live(payload)
            elif route == "/api/scan":
                self._start_scan(payload)
            elif route == "/api/stop":
                self._json({"stopped": self.state.stop(), "job": self.state.job.snapshot()})
            else:
                self._error("없는 주소입니다", 404)
        except (BrokenPipeError, ConnectionResetError):
            log.debug("클라이언트가 연결을 끊었습니다: %s", route)
        except (ValueError, RuntimeError) as exc:
            self._error(str(exc), 400)

    # -- 처리
    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 64 * 1024:
            raise ValueError("요청이 너무 큽니다")
        try:
            parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"본문을 읽을 수 없습니다: {exc}") from None
        return parsed if isinstance(parsed, dict) else {}

    def _state_payload(self) -> dict[str, Any]:
        state = self.state
        payload: dict[str, Any] = {
            "market": state.market,
            "job": state.job.snapshot(),
            "marketLabel": market_label(state.market),
            "markets": [{"code": code, "label": name} for code, name in MARKETS.items()],
            "periods": PERIODS,
            "defaults": {
                "similarity": DEFAULT_SIMILARITY,
                "fee": DEFAULT_FEE,
                "slippage": DEFAULT_SLIPPAGE,
            },
            "analysisId": state.analysis_id,
            "analysis": None,
        }
        if state.analysis is not None:
            payload["analysis"] = _analysis_json(state.analysis)
        else:
            # 개수만 센다. 예전에는 CSV 전체를 파싱해 Candle을 다 만들고
            # 즉시 버렸는데, 이 응답은 수집이 도는 동안 0.5초마다 나간다.
            payload["cached"] = [_cached_json(state, tf) for tf in DEFAULT_COUNT]
        return payload

    def _start_fetch(self, payload: dict[str, Any]) -> None:
        market = _market(payload.get("market"), self.state.market)
        refresh = bool(payload.get("refresh"))
        self.state.market = market
        started = self.state.start("fetch", _do_fetch, self.state, market, refresh)
        self._json({"started": started, "job": self.state.job.snapshot()})

    def _start_live(self, payload: dict[str, Any]) -> None:
        """새 봉을 받고 곧바로 다시 계산 — 화면의 '지금 시세로 갱신'."""
        market = _market(payload.get("market"), self.state.market)
        self.state.market = market
        similarity = _number(payload.get("similarity"), DEFAULT_SIMILARITY, 0.0, 1.0)
        fee = _number(payload.get("fee"), DEFAULT_FEE, 0.0, 0.1)
        slippage = _number(payload.get("slippage"), DEFAULT_SLIPPAGE, 0.0, 0.1)
        length = int(_number(payload.get("oddsLength"), 180, 5, 180))
        count = int(_number(payload.get("count"), DEFAULT_COUNT["minute1"], 3_000, 4_500_000))

        started = self.state.start(
            "live", _do_live, self.state, market, similarity, fee, slippage, length, count
        )
        self._json({"started": started, "job": self.state.job.snapshot()})

    def _start_scan(self, payload: dict[str, Any]) -> None:
        market = _market(payload.get("market"), self.state.market)
        self.state.market = market
        similarity = _number(payload.get("similarity"), DEFAULT_SIMILARITY, 0.0, 1.0)
        fee = _number(payload.get("fee"), DEFAULT_FEE, 0.0, 0.1)
        slippage = _number(payload.get("slippage"), DEFAULT_SLIPPAGE, 0.0, 0.1)
        length = int(_number(payload.get("oddsLength"), 180, 5, 180))

        started = self.state.start(
            "scan", _do_odds, self.state, market, similarity, fee, slippage, length
        )
        self._json({"started": started, "job": self.state.job.snapshot()})

    def _ticker(self, query: dict[str, list[str]]) -> None:
        """지금 얼마인지. 화면 맨 위 숫자.

        **실패해도 200으로 답한다.** 이건 장식이지 분석이 아니다. 여기서
        오류를 올리면 시세 표시가 안 되는 것 때문에 화면 전체가 빨개진다.
        """
        market = _market(query.get("market", [None])[0], self.state.market)
        try:
            ticker = self.state.prices.get(market)
        except (UpbitError, OSError) as exc:
            self._json({"market": market, "ok": False, "why": str(exc)[:120]})
            return
        self._json({
            "market": ticker.market,
            "label": market_label(ticker.market),
            "ok": True,
            "price": ticker.price,
            "changeRate": ticker.change_rate,
            "changePrice": ticker.change_price,
            "direction": ticker.direction,
            "high": ticker.high,
            "low": ticker.low,
            "at": ticker.at.astimezone(KST).strftime("%H:%M:%S"),
        })

    def _examples(self, query: dict[str, list[str]]) -> None:
        analysis = self.state.analysis
        if analysis is None:
            raise KeyError("아직 계산한 결과가 없습니다")
        timeframe = (query.get("timeframe") or [""])[0]
        horizon = int((query.get("horizon") or ["1"])[0])
        self._json(_examples_json(analysis, timeframe, horizon))



#: 화면에서 고를 수 있는 '얼마나 과거까지'. 1분봉 개수 기준.
#: 업비트는 2017년 10월 개장이라 그 이전은 없다.
PERIODS = [
    {"label": "30일", "count": 43_200, "note": "금방 받습니다"},
    {"label": "90일", "count": 129_600, "note": "몇 분"},
    {"label": "1년", "count": 525_600, "note": "10분쯤"},
    {"label": "8년", "count": 4_204_800, "note": "처음 한 번 40분쯤"},
]


def _cached_json(state: State, timeframe: str) -> dict[str, Any]:
    """받아둔 게 얼마나 되는지. **파일 전체를 읽지 않는다.**"""
    span = span_cached(state.market, timeframe, state.data_dir)
    return {
        "timeframe": timeframe,
        "label": timeframe_label(timeframe),
        "count": count_cached(state.market, timeframe, state.data_dir),
        "from": span[0].astimezone(KST).strftime("%Y-%m-%d") if span else None,
        "to": span[1].astimezone(KST).strftime("%Y-%m-%d") if span else None,
    }


def _market(value: Any, default: str) -> str:
    """화면에서 온 종목 코드를 아는 것만 통과시킨다.

    아무거나 받으면 오타 하나가 "없는 종목입니다"로 돌아오는데, 그게
    오타 때문인지 업비트가 막힌 건지 사용자는 알 수가 없다. 게다가 이
    값은 파일 이름이 되므로(KRW-BTC_minute1.csv), 밖에서 온 문자열을
    그대로 쓰면 경로를 벗어나는 이름도 만들 수 있다.
    """
    text = str(value or "").strip().upper()
    return text if text in MARKETS else default


def _number(value: Any, default: float, low: float, high: float) -> float:
    """화면에서 온 숫자를 안전한 범위로 자른다."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(number):
        return default
    return min(high, max(low, number))


def phone_address(port: int, host: str) -> tuple[str | None, str]:
    """다른 기기(아이패드·폰)에서 열 주소와, 그 주소의 성격.

    "홈 화면에 어떤 주소를 추가하면 되냐"가 매번 막히는 자리였다. 답이
    실행 방법마다 다른데(Codespaces냐, 집 와이파이냐, 이 컴퓨터뿐이냐)
    화면에는 늘 127.0.0.1만 찍혀 있었다. **그 주소는 아이패드에서 열면
    아이패드 자신을 가리키므로 절대 안 된다.**
    """
    codespace = os.environ.get("CODESPACE_NAME")
    domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    if codespace and domain:
        # Codespaces는 포트를 https로 넘겨준다. 이 주소는 코드스페이스를
        # 껐다 켜도 그대로라, 홈 화면에 두기에 가장 안전하다.
        return f"https://{codespace}-{port}.{domain}/", "codespace"
    if host in ("127.0.0.1", "localhost", "::1"):
        return None, "local-only"
    lan = _lan_address()
    return (f"http://{lan}:{port}/", "lan") if lan else (None, "lan-unknown")


def _lan_address() -> str | None:
    """같은 네트워크에서 이 컴퓨터를 가리키는 주소. 못 찾으면 None."""
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 실제로 보내지는 않는다. 어느 인터페이스로 나갈지만 물어본다.
        probe.connect(("192.0.2.1", 9))
        return str(probe.getsockname()[0])
    except OSError:
        return None
    finally:
        probe.close()


def _home_screen_lines(port: int, host: str) -> list[str]:
    """"홈 화면에 뭘 추가하냐"에 대한 답을 시작할 때마다 찍어 준다."""
    address, kind = phone_address(port, host)
    if kind == "codespace":
        return [
            "  아이패드·폰의 홈 화면에 추가할 주소:",
            f"    {address}",
            "    (코드스페이스를 껐다 켜도 이 주소는 그대로입니다)",
        ]
    if kind == "lan":
        return [
            "  아이패드·폰의 홈 화면에 추가할 주소:",
            f"    {address}",
            "    공유기가 이 컴퓨터에 다른 IP를 주면 이 주소는 바뀝니다 —",
            "    아이콘이 안 열리면 여기 찍힌 새 주소로 다시 추가하세요.",
        ]
    if kind == "lan-unknown":
        return [
            "  이 컴퓨터의 네트워크 주소를 찾지 못했습니다.",
            "    다른 기기에서 열려면 IP를 직접 확인해 주세요.",
        ]
    return [
        "  지금은 이 컴퓨터에서만 열립니다.",
        "    아이패드·폰에서 보려면 --host 0.0.0.0 (또는 ./start.sh --lan)으로 다시 여세요.",
        "    127.0.0.1은 '이 기기 자신'이라는 뜻이라, 아이패드에 넣으면 아이패드를 가리킵니다.",
    ]


def serve(
    market: str = "KRW-BTC",
    data_dir: str = "data",
    port: int = 8765,
    open_browser: bool = True,
    host: str = "127.0.0.1",
) -> None:
    """기본은 127.0.0.1 — 이 컴퓨터에서만 열린다.

    `host="0.0.0.0"`을 주면 같은 네트워크(집 와이파이)의 다른 기기에서도
    열린다. 인터넷에 열리는 것은 아니지만, **같은 와이파이에 있는 누구나**
    볼 수 있게 되므로 그 사실을 화면에 분명히 적는다.
    """
    state = State(market, data_dir)
    handler = type("BoundHandler", (Handler,), {"state": state})
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://127.0.0.1:{port}/"

    print(f"  화면: {url}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print()
        print(f"  ⚠ 이 컴퓨터 밖에서도 열립니다 ({host}:{port}).")
        print("    인터넷에 공개되지는 않지만, 같은 와이파이의 누구나 볼 수 있습니다.")
        print("    카페·공용 와이파이에서는 쓰지 마세요.")

    print()
    for line in _home_screen_lines(port, host):
        print(line)
    print()
    print("  종료: Ctrl+C")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        httpd.server_close()
