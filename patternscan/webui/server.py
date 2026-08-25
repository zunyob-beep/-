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
import threading
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np

from ..data import fetch, load_cached
from ..models import HORIZONS, WINDOW_LENGTHS, Series, timeframe_label
from ..scan import (
    DEFAULT_FEE,
    DEFAULT_SIMILARITY,
    DEFAULT_SLIPPAGE,
    ScanResult,
    round_trip_cost,
    scan_all,
)
from ..shape import normalize_window
from ..stats import MIN_SAMPLES, Finding, Verdict, decide, evaluate, qualifies
from ..upbit import UpbitClient, UpbitError

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
}

#: 탭 아이콘. 없으면 브라우저가 /favicon.ico를 찾다 404를 받고
#: 콘솔에 오류를 남긴다 — 진짜 오류를 찾을 때 방해가 된다.
FAVICON = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    b'<rect width="32" height="32" rx="7" fill="#0f1116"/>'
    b'<polyline points="5,21 11,13 16,17 21,7 27,12" fill="none" '
    b'stroke="#3d7eff" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
    b"</svg>"
)


# ---------------------------------------------------------------- 분석 상태
@dataclass
class Analysis:
    """마지막으로 끝난 분석 한 건. 화면이 되묻는 것들에 답하려면 통째로 들고 있어야 한다."""

    market: str
    scale: str
    cost: float
    similarity: float
    series: dict[str, Series]
    results: list[ScanResult]
    findings: list[Finding]
    verdict: Verdict
    #: 시세가 없어 판정에서 빠진 봉 간격. 조용히 빼면 3종을 다 본 줄 안다.
    missing: tuple[str, ...] = ()

    def result_for(self, timeframe: str, length: int) -> ScanResult | None:
        for result in self.results:
            if result.timeframe == timeframe and result.length == length:
                return result
        return None


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


class State:
    def __init__(self, market: str, data_dir: str) -> None:
        self.market = market
        self.data_dir = data_dir
        self.job = Job()
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
            self.job.update(kind=kind, running=True, message="시작하는 중…", done=0, total=0, error="")
        thread = threading.Thread(target=self._run, args=(target, args), daemon=True)
        thread.start()
        return True

    def _run(self, target: Any, args: tuple[Any, ...]) -> None:
        try:
            target(*args)
        except (UpbitError, RuntimeError, ValueError, KeyError, OSError) as exc:
            log.exception("작업 실패")
            self.job.update(error=str(exc), message="실패했습니다")
        finally:
            self.job.update(running=False)


# ---------------------------------------------------------------- 작업 본체
def _do_fetch(state: State, market: str, refresh: bool) -> None:
    client = UpbitClient()
    for timeframe, count in DEFAULT_COUNT.items():
        label = timeframe_label(timeframe)
        state.job.update(message=f"{label} 시세 받는 중…", done=0, total=count)

        def progress(done: int, total: int) -> None:
            state.job.update(done=done, total=total)

        fetch(
            client, market, timeframe, count,
            directory=state.data_dir, refresh=refresh, progress=progress,
        )
    state.job.update(message="시세 수집을 마쳤습니다", done=0, total=0)


def _do_scan(
    state: State, market: str, similarity: float, fee: float, slippage: float,
    scale: str, top_k: int, fdr: float,
) -> None:
    state.job.update(message="캐시에서 시세를 읽는 중…")
    series = {}
    for timeframe in DEFAULT_COUNT:
        loaded = load_cached(market, timeframe, state.data_dir)
        if len(loaded):
            series[timeframe] = loaded

    if not series:
        raise RuntimeError("시세가 없습니다. 먼저 '시세 받기'를 눌러 주세요.")

    cost = round_trip_cost(fee, slippage)
    total = len(series) * len(WINDOW_LENGTHS)
    state.job.update(message="과거에서 같은 모양을 찾는 중…", done=0, total=total)

    results: list[ScanResult] = []
    done = 0
    for one in series.values():
        chunk = scan_all(
            {one.timeframe: one}, WINDOW_LENGTHS,
            horizons=HORIZONS, top_k=top_k, similarity=similarity,
            scale=scale, fee=fee, slippage=slippage,
        )
        results.extend(chunk)
        done = min(total, done + len(WINDOW_LENGTHS))
        state.job.update(
            done=done,
            message=f"{timeframe_label(one.timeframe)} 분석 완료",
        )

    findings = evaluate(results, fdr=fdr)
    verdict = decide(findings, fdr=fdr, cost=cost)
    state.publish(
        Analysis(
            market=market, scale=scale, cost=cost, similarity=similarity,
            series=series, results=results, findings=findings, verdict=verdict,
            missing=tuple(tf for tf in DEFAULT_COUNT if tf not in series),
        )
    )
    state.job.update(message="판정을 마쳤습니다", done=total, total=total)


# ---------------------------------------------------------------- 직렬화
def _finding_json(finding: Finding, cost: float) -> dict[str, Any]:
    return {
        "timeframe": finding.timeframe,
        "timeframeLabel": timeframe_label(finding.timeframe),
        "length": finding.length,
        "horizon": finding.horizon,
        "label": finding.label,
        "samples": finding.samples,
        "up": finding.up,
        "flat": finding.flat,
        "down": finding.down,
        "upRate": finding.up_rate,
        "baseUpRate": finding.base_up_rate,
        "edge": finding.edge,
        "meanReturn": finding.mean_return,
        "medianReturn": finding.median_return,
        "worst": finding.worst,
        "pValue": finding.p_value,
        "qValue": finding.q_value,
        "ciLow": finding.ci_low,
        "ciHigh": finding.ci_high,
        "minSimilarity": _finite(finding.min_similarity),
        "firstHalfRate": finding.first_half_rate,
        "secondHalfRate": finding.second_half_rate,
        "holdsInBothHalves": finding.holds_in_both_halves,
        "significant": finding.significant,
        "enoughSamples": finding.enough_samples,
        "qualifies": qualifies(finding, cost),
    }


def _finite(value: float) -> float | None:
    """JSON에는 NaN이 없다. 그대로 흘리면 화면에서 조용히 깨진다."""
    number = float(value)
    return number if np.isfinite(number) else None


def _analysis_json(analysis: Analysis) -> dict[str, Any]:
    verdict = analysis.verdict
    spans = []
    for timeframe, series in analysis.series.items():
        span = series.span
        spans.append({
            "timeframe": timeframe,
            "label": timeframe_label(timeframe),
            "count": len(series),
            "gaps": series.gaps(),
            "from": span[0].isoformat() if span else None,
            "to": span[1].isoformat() if span else None,
        })

    skipped = sum(1 for r in analysis.results if r.note)
    thin = sum(1 for r in analysis.results if not r.note and r.sample_size < MIN_SAMPLES)

    return {
        "market": analysis.market,
        "cost": analysis.cost,
        "similarity": analysis.similarity,
        "minSamples": MIN_SAMPLES,
        "series": spans,
        "coverage": {"skipped": skipped, "thin": thin, "total": len(analysis.results)},
        "missing": [
            {"timeframe": tf, "label": timeframe_label(tf)} for tf in analysis.missing
        ],
        "verdict": {
            "enter": verdict.enter,
            "headline": verdict.headline,
            "reasons": verdict.reasons,
            "tested": verdict.tested,
            "significant": verdict.significant,
        },
        "findings": [_finding_json(f, analysis.cost) for f in analysis.findings if f.enough_samples],
    }


def _shape_json(
    analysis: Analysis, timeframe: str, length: int, horizon: int
) -> dict[str, Any]:
    """모양 겹쳐보기 + 직후 경로 데이터.

    승률 숫자만 보여주면 '정말 같은 모양인가'를 확인할 방법이 없다.
    직접 겹쳐 보게 해서 눈으로 판단할 수 있게 한다.
    """
    series = analysis.series.get(timeframe)
    result = analysis.result_for(timeframe, length)
    if series is None or result is None:
        raise KeyError(f"{timeframe} {length}개 결과가 없습니다")

    closes = series.close
    query_end = len(series) - 1
    query = closes[query_end - length + 1 : query_end + 1]

    shapes = []
    paths = []
    for match in result.matches[:CHART_MATCHES]:
        window = closes[match.end_index - length + 1 : match.end_index + 1]
        entry = float(closes[match.end_index])
        after = closes[match.end_index + 1 : match.end_index + 1 + horizon]
        outcome = float(after[-1] / entry - 1.0) if after.size else 0.0
        shapes.append({
            "similarity": round(match.similarity, 4),
            "values": [round(v, 4) for v in normalize_window(window, analysis.scale).tolist()],
        })
        paths.append({
            "similarity": round(match.similarity, 4),
            "outcome": outcome,
            "won": outcome > analysis.cost,
            "values": [0.0] + [round(float(v / entry - 1.0), 6) for v in after],
            "at": series.kst_at(match.end_index).strftime("%Y-%m-%d %H:%M"),
        })

    return {
        "timeframe": timeframe,
        "timeframeLabel": timeframe_label(timeframe),
        "length": length,
        "horizon": horizon,
        "cost": analysis.cost,
        "query": [round(v, 4) for v in normalize_window(query, analysis.scale).tolist()],
        "queryAt": series.kst_at(query_end).strftime("%Y-%m-%d %H:%M"),
        "shapes": shapes,
        "paths": paths,
        "shown": len(shapes),
        "matches": len(result.matches),
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

    def _static(self, name: str) -> None:
        path = (STATIC / name).resolve()
        # 경로 탈출 방지: static 밖은 절대 내주지 않는다.
        if not str(path).startswith(str(STATIC.resolve())) or not path.is_file():
            self._error("없는 파일입니다", 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # -- 라우팅
    def do_GET(self) -> None:
        url = urlparse(self.path)
        route = url.path
        try:
            if route in ("/", "/index.html"):
                self._static("index.html")
            elif route == "/favicon.ico":
                self._bytes(FAVICON, "image/svg+xml")
            elif route.startswith("/static/"):
                self._static(route[len("/static/") :])
            elif route == "/api/state":
                self._json(self._state_payload())
            elif route == "/api/shape":
                self._shape(parse_qs(url.query))
            else:
                self._error("없는 주소입니다", 404)
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
            elif route == "/api/scan":
                self._start_scan(payload)
            else:
                self._error("없는 주소입니다", 404)
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
            payload["cached"] = [
                {
                    "timeframe": tf,
                    "label": timeframe_label(tf),
                    "count": len(load_cached(state.market, tf, state.data_dir)),
                }
                for tf in DEFAULT_COUNT
            ]
        return payload

    def _start_fetch(self, payload: dict[str, Any]) -> None:
        market = str(payload.get("market") or self.state.market)
        refresh = bool(payload.get("refresh"))
        self.state.market = market
        started = self.state.start("fetch", _do_fetch, self.state, market, refresh)
        self._json({"started": started, "job": self.state.job.snapshot()})

    def _start_scan(self, payload: dict[str, Any]) -> None:
        market = str(payload.get("market") or self.state.market)
        self.state.market = market
        similarity = _number(payload.get("similarity"), DEFAULT_SIMILARITY, 0.0, 1.0)
        fee = _number(payload.get("fee"), DEFAULT_FEE, 0.0, 0.1)
        slippage = _number(payload.get("slippage"), DEFAULT_SLIPPAGE, 0.0, 0.1)
        scale = payload.get("scale") if payload.get("scale") in ("shape", "amplitude") else "shape"
        top_k = int(_number(payload.get("topK"), 60, 5, 500))
        fdr = _number(payload.get("fdr"), 0.10, 0.001, 0.5)

        started = self.state.start(
            "scan", _do_scan, self.state, market, similarity, fee, slippage, scale, top_k, fdr
        )
        self._json({"started": started, "job": self.state.job.snapshot()})

    def _shape(self, query: dict[str, list[str]]) -> None:
        analysis = self.state.analysis
        if analysis is None:
            raise KeyError("아직 판정한 결과가 없습니다")
        timeframe = (query.get("timeframe") or [""])[0]
        length = int((query.get("length") or ["0"])[0])
        horizon = int((query.get("horizon") or ["1"])[0])
        self._json(_shape_json(analysis, timeframe, length, horizon))


def _number(value: Any, default: float, low: float, high: float) -> float:
    """화면에서 온 숫자를 안전한 범위로 자른다."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(number):
        return default
    return min(high, max(low, number))


def serve(
    market: str = "KRW-BTC",
    data_dir: str = "data",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """127.0.0.1에만 붙는다 — 같은 네트워크의 다른 기기에서는 열리지 않는다."""
    state = State(market, data_dir)
    handler = type("BoundHandler", (Handler,), {"state": state})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/"

    print(f"  화면: {url}")
    print("  종료: Ctrl+C")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        httpd.server_close()
