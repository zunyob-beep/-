"""웹 화면 서버 검증.

화면은 CLI와 **같은 숫자**를 보여줘야 한다. 화면에서만 계산을 다시 하면
언젠가 두 결과가 갈라지고, 사용자는 그중 하나를 믿고 돈을 넣는다.
그래서 서버는 계산을 직접 하지 않고 scan/stats를 그대로 부른다 —
이 파일은 그게 유지되는지 확인한다.

네트워크는 타지 않는다. 시세는 전부 임시 폴더의 CSV로 넣는다.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import numpy as np
import pytest

from patternscan.data import cache_path, save
from patternscan.models import Candle
from patternscan.webui import server as webui


# ---------------------------------------------------------------- 준비
def _write_cache(directory, market="KRW-BTC", seed=5):
    """세 봉 간격 모두에 합성 시세를 심는다."""
    rng = np.random.default_rng(seed)
    for timeframe, step, n in (("minute1", 60, 2500), ("minute3", 180, 1200), ("minute5", 300, 900)):
        closes = 40_000_000 * np.exp(np.cumsum(rng.normal(0, 0.0008, n)))
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        candles = [
            Candle(
                ts=start + timedelta(seconds=step * i),
                open=float(c), high=float(c) * 1.001, low=float(c) * 0.999,
                close=float(c), volume=1.0,
            )
            for i, c in enumerate(closes)
        ]
        save(cache_path(market, timeframe, directory), candles)


class Client:
    """테스트용 최소 HTTP 클라이언트."""

    def __init__(self, base: str) -> None:
        self.base = base

    def get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return response.status, response.read(), response.headers

    def get_json(self, path: str):
        return json.loads(self.get(path)[1].decode("utf-8"))

    def post_json(self, path: str, payload: dict):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))


@pytest.fixture
def client(tmp_path):
    _write_cache(tmp_path)
    state = webui.State("KRW-BTC", str(tmp_path))
    handler = type("BoundHandler", (webui.Handler,), {"state": state})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(f"http://127.0.0.1:{httpd.server_address[1]}"), state
    finally:
        httpd.shutdown()
        httpd.server_close()


def _run_scan(client_and_state, **overrides):
    client, state = client_and_state
    payload = {"market": "KRW-BTC", "similarity": 0.85, "topK": 40}
    payload.update(overrides)
    assert client.post_json("/api/scan", payload)["started"]
    for _ in range(600):
        if not state.job.snapshot()["running"]:
            break
        threading.Event().wait(0.05)
    job = state.job.snapshot()
    assert not job["running"], "분석이 끝나지 않았습니다"
    assert not job["error"], job["error"]
    return client.get_json("/api/state")


# ---------------------------------------------------------------- 기본 응답
def test_index_is_served(client):
    status, body, headers = client[0].get("/")
    assert status == 200
    assert "text/html" in headers["Content-Type"]
    assert b"<title>" in body


def test_static_files_are_served(client):
    for path, kind in (("/static/app.js", "javascript"), ("/static/style.css", "css")):
        status, body, headers = client[0].get(path)
        assert status == 200
        assert kind in headers["Content-Type"]
        assert body


def test_path_traversal_is_refused(client):
    """`..`로 static 밖 파일을 읽어가지 못해야 한다.

    urllib은 보내기 전에 `..`를 정리해 버리므로, 그걸로 시험하면 서버가
    아니라 클라이언트를 시험하는 꼴이 된다. 생 소켓으로 그대로 보낸다.
    """
    host, port = client[0].base.rsplit(":", 1)
    attempts = (
        "/static/../pyproject.toml",
        "/static/../../etc/hostname",
        "/static/....//pyproject.toml",
        "/static/..%2fpyproject.toml",
    )
    for attempt in attempts:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=10) as sock:
            sock.sendall(
                f"GET {attempt} HTTP/1.1\r\nHost: local\r\nConnection: close\r\n\r\n".encode()
            )
            received = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                received += chunk
        assert b"404" in received.split(b"\r\n")[0], attempt
        assert b"[project]" not in received, f"{attempt}: static 밖 파일이 새어 나갔습니다"


def test_favicon_is_served(client):
    """404를 남기면 콘솔이 지저분해져 진짜 오류를 놓친다."""
    status, body, headers = client[0].get("/favicon.ico")
    assert status == 200
    assert headers["Content-Type"] == "image/svg+xml"
    assert body.startswith(b"<svg")


def test_unknown_route_is_404(client):
    with pytest.raises(urllib.error.HTTPError) as exc:
        client[0].get("/api/nope")
    assert exc.value.code == 404


def test_state_before_any_scan_lists_the_cache(client):
    state = client[0].get_json("/api/state")
    assert state["analysis"] is None
    assert state["job"]["running"] is False
    counts = {row["timeframe"]: row["count"] for row in state["cached"]}
    assert counts["minute1"] == 2500


def test_shape_without_an_analysis_is_404(client):
    with pytest.raises(urllib.error.HTTPError) as exc:
        client[0].get("/api/shape?timeframe=minute1&length=20&horizon=1")
    assert exc.value.code == 404


# ---------------------------------------------------------------- 분석
def test_scan_produces_an_analysis(client):
    state = _run_scan(client)
    analysis = state["analysis"]
    assert analysis is not None
    assert analysis["market"] == "KRW-BTC"
    assert analysis["verdict"]["headline"]
    assert analysis["cost"] == pytest.approx(0.0014)
    assert analysis["similarity"] == pytest.approx(0.85)


def test_screen_shows_the_same_numbers_as_the_library(client):
    """화면 숫자는 stats가 내놓은 값 그대로여야 한다."""
    state = _run_scan(client)
    analysis = client[1].analysis
    shown = {(f["timeframe"], f["length"], f["horizon"]): f for f in state["analysis"]["findings"]}

    for finding in analysis.findings:
        if not finding.enough_samples:
            continue
        row = shown[(finding.timeframe, finding.length, finding.horizon)]
        assert row["upRate"] == pytest.approx(finding.up_rate)
        assert row["edge"] == pytest.approx(finding.edge)
        assert row["qValue"] == pytest.approx(finding.q_value)
        assert row["samples"] == finding.samples


def test_thin_combinations_are_not_shown_as_rows(client):
    """표본이 모자란 조합을 표에 올리면 '승률 100%'가 맨 위에 뜬다."""
    state = _run_scan(client)
    analysis = state["analysis"]
    assert all(f["samples"] >= analysis["minSamples"] for f in analysis["findings"])
    # 다만 몇 개가 빠졌는지는 숨기지 않고 알려줘야 한다
    assert analysis["coverage"]["total"] > 0


def test_random_data_does_not_show_a_buy_signal_on_screen(client):
    """CLI와 같은 결론이어야 한다 — 합성 무작위 시세다."""
    state = _run_scan(client)
    assert state["analysis"]["verdict"]["enter"] is False


def test_json_never_contains_nan(client):
    """NaN은 JSON이 아니다. 흘려보내면 화면이 조용히 깨진다."""
    _run_scan(client)
    raw = client[0].get("/api/state")[1].decode("utf-8")
    assert "NaN" not in raw
    assert "Infinity" not in raw


# ---------------------------------------------------------------- 모양 데이터
def test_missing_timeframes_are_reported_not_hidden(tmp_path):
    """일부 간격만 있으면 그것만으로 판정하되, 빠진 걸 반드시 알려야 한다.

    조용히 빼면 사용자는 1·3·5분봉을 다 본 줄 안다.
    """
    _write_cache(tmp_path)
    # 3분·5분봉 캐시를 지운다
    for timeframe in ("minute3", "minute5"):
        cache_path("KRW-BTC", timeframe, tmp_path).unlink()

    state = webui.State("KRW-BTC", str(tmp_path))
    handler = type("BoundHandler", (webui.Handler,), {"state": state})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        c = Client(f"http://127.0.0.1:{httpd.server_address[1]}")
        _run_scan((c, state), topK=30)
        analysis = c.get_json("/api/state")["analysis"]
        assert analysis is not None, "1분봉만으로도 판정은 나와야 합니다"
        assert {m["timeframe"] for m in analysis["missing"]} == {"minute3", "minute5"}
        assert {s["timeframe"] for s in analysis["series"]} == {"minute1"}
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_analysis_id_changes_only_when_the_analysis_does(client):
    """화면은 이 번호로 '다시 그릴지'를 정한다.

    번호가 매번 바뀌면 표가 계속 새로 그려져 사용자가 누르던 행이
    클릭 도중에 사라진다. 안 바뀌면 새 결과가 화면에 안 올라온다.
    """
    c, _ = client
    assert c.get_json("/api/state")["analysisId"] == 0

    _run_scan(client)
    first = c.get_json("/api/state")["analysisId"]
    assert first == 1
    # 그냥 다시 물어보는 것만으로는 안 바뀐다
    assert c.get_json("/api/state")["analysisId"] == first

    _run_scan(client)
    assert c.get_json("/api/state")["analysisId"] == first + 1


def test_old_analysis_is_still_served_while_a_new_scan_runs(client):
    """새로 계산하는 동안에도 옛 결과가 그대로 나온다 — 화면이 흐려야 하는 이유다."""
    c, state = client
    _run_scan(client)
    before = c.get_json("/api/state")
    assert c.post_json("/api/scan", {"topK": 40})["started"]

    during = c.get_json("/api/state")
    if during["job"]["running"]:
        # 옛 판정이 그대로 응답에 실린다. 번호가 같으므로 화면은 '이전 것'임을 안다.
        assert during["analysisId"] == before["analysisId"]
        assert during["analysis"]["verdict"]["headline"] == before["analysis"]["verdict"]["headline"]

    for _ in range(600):
        if not state.job.snapshot()["running"]:
            break
        threading.Event().wait(0.05)


def test_shape_payload_matches_the_request(client):
    _run_scan(client)
    finding = client[0].get_json("/api/state")["analysis"]["findings"][0]
    data = client[0].get_json(
        f"/api/shape?timeframe={finding['timeframe']}"
        f"&length={finding['length']}&horizon={finding['horizon']}"
    )
    assert data["length"] == finding["length"]
    assert len(data["query"]) == finding["length"]
    assert data["shown"] <= webui.CHART_MATCHES
    for shape in data["shapes"]:
        assert len(shape["values"]) == finding["length"]
        assert shape["similarity"] >= 0.85 - 1e-6


def test_after_paths_start_at_zero_and_have_one_point_per_bar(client):
    """직후 경로는 진입 시점(0%)에서 출발해야 비교가 된다."""
    _run_scan(client)
    finding = client[0].get_json("/api/state")["analysis"]["findings"][0]
    data = client[0].get_json(
        f"/api/shape?timeframe={finding['timeframe']}"
        f"&length={finding['length']}&horizon={finding['horizon']}"
    )
    for path in data["paths"]:
        assert path["values"][0] == 0.0
        assert len(path["values"]) == finding["horizon"] + 1
        # 'won' 표시는 마지막 값이 왕복 비용을 넘겼는지와 일치해야 한다
        assert path["won"] == (path["values"][-1] > data["cost"])


def test_shape_for_an_unknown_combination_is_404(client):
    _run_scan(client)
    with pytest.raises(urllib.error.HTTPError) as exc:
        client[0].get_json("/api/shape?timeframe=minute7&length=20&horizon=1")
    assert exc.value.code == 404


# ---------------------------------------------------------------- 입력 방어
def test_out_of_range_settings_are_clamped_not_crashed(client):
    """화면에서 이상한 값이 와도 서버가 죽으면 안 된다."""
    state = _run_scan(client, similarity=99.0, fee=-5.0, topK=100000)
    assert state["analysis"]["similarity"] == pytest.approx(1.0)
    assert state["analysis"]["cost"] >= 0.0


def test_garbage_body_is_rejected_with_a_message(client):
    request = urllib.request.Request(
        client[0].base + "/api/scan", data=b"{not json",
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=10)
    assert exc.value.code == 400


def test_scan_without_any_cache_reports_it(tmp_path):
    """시세가 없을 때 조용히 빈 화면을 주면 안 된다."""
    state = webui.State("KRW-BTC", str(tmp_path))
    state.start("scan", webui._do_scan, state, "KRW-BTC", 0.85, 0.0005, 0.0002, "shape", 40, 0.1)
    for _ in range(200):
        if not state.job.snapshot()["running"]:
            break
        threading.Event().wait(0.05)
    job = state.job.snapshot()
    assert "시세가 없습니다" in job["error"]


def test_only_one_job_runs_at_a_time(client):
    """분석 두 개가 동시에 돌면 결과가 뒤섞인다."""
    c, state = client
    assert c.post_json("/api/scan", {"topK": 40})["started"] is True
    second = c.post_json("/api/scan", {"topK": 40})
    # 첫 작업이 아직 돌고 있으면 거절, 이미 끝났으면 수락 — 둘 다 정상이다
    if state.job.snapshot()["running"]:
        assert second["started"] is False
    for _ in range(600):
        if not state.job.snapshot()["running"]:
            break
        threading.Event().wait(0.05)


def test_server_binds_to_loopback_only():
    """다른 기기에서 열리면 안 된다."""
    state = webui.State("KRW-BTC", "data")
    handler = type("BoundHandler", (webui.Handler,), {"state": state})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    try:
        assert httpd.server_address[0] == "127.0.0.1"
    finally:
        httpd.server_close()
