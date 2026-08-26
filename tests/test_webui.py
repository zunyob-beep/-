"""웹 화면 서버 검증.

화면은 CLI와 **같은 숫자**를 보여줘야 한다. 화면에서만 계산을 다시 하면
언젠가 두 결과가 갈라지고, 사용자는 그중 하나를 믿고 돈을 넣는다.
그래서 서버는 계산을 직접 하지 않고 odds를 그대로 부른다 —
이 파일은 그게 유지되는지 확인한다.

그리고 확률은 절대 혼자 나가면 안 된다. 평소 확률과 불확실 범위가
반드시 함께 실려야, 화면이 "56%"를 홀로 띄우는 일이 없다.

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


def test_examples_without_an_analysis_is_404(client):
    with pytest.raises(urllib.error.HTTPError) as exc:
        client[0].get("/api/examples?timeframe=minute1&horizon=1")
    assert exc.value.code == 404


# ---------------------------------------------------------------- 분석
def test_scan_produces_an_analysis(client):
    state = _run_scan(client)
    analysis = state["analysis"]
    assert analysis is not None
    assert analysis["market"] == "KRW-BTC"
    assert analysis["cost"] == pytest.approx(0.0014)
    assert analysis["similarity"] == pytest.approx(0.85)
    assert analysis["odds"]


def test_screen_shows_the_same_numbers_as_the_library(client):
    """화면 숫자는 odds가 내놓은 값 그대로여야 한다."""
    state = _run_scan(client)
    analysis = client[1].analysis
    shown = {(r["timeframe"], r["horizon"]): r for r in state["analysis"]["odds"]}

    for row in analysis.odds:
        seen = shown[(row.timeframe, row.horizon)]
        assert seen["upRate"] == pytest.approx(row.up_rate)
        assert seen["baseUp"] == pytest.approx(row.base_up)
        assert seen["samples"] == row.samples
        assert seen["tellsUsAnything"] == row.tells_us_anything


def test_every_probability_ships_with_its_baseline(client):
    """확률만 보내면 화면이 '56%'를 혼자 띄우게 된다 — 평소가 반드시 따라와야 한다."""
    state = _run_scan(client)
    for row in state["analysis"]["odds"]:
        assert "baseUp" in row and "baseBeat" in row
        assert "ciLow" in row and "ciHigh" in row
        assert row["ciLow"] <= row["upRate"] <= row["ciHigh"]


def test_verdict_refuses_when_nothing_is_distinguishable(client):
    """잡음에서는 '사세요'가 나오면 안 된다."""
    state = _run_scan(client)
    verdict = state["analysis"]["verdict"]
    assert verdict["buy"] is False
    assert verdict["reasons"], "왜 안 되는지 말해야 한다"


def test_verdict_only_blames_the_gate_that_actually_failed(client):
    """통과한 조건까지 실패로 적으면 거짓말이 된다."""
    state = _run_scan(client)
    analysis = client[1].analysis
    usable = [r for r in analysis.odds if r.samples >= 20]
    if not usable:
        pytest.skip("표본이 모자라 판정할 수 없습니다")
    best = max(usable, key=lambda r: r.up_edge)
    text = " ".join(state["analysis"]["verdict"]["reasons"])
    if best.beat_rate > best.base_beat:
        assert "보다 낮습니다" not in text, "넘긴 조건을 못 넘겼다고 적었습니다"


def test_verdict_says_buy_when_the_evidence_is_real():
    """진짜 신호에서는 '사세요'가 나와야 한다 — 무조건 거절하는 도구는 쓸모없다."""
    from patternscan.odds import Odds
    from patternscan.webui.server import _verdict

    strong = [
        Odds("minute1", 5, h, samples=100, up=91, beat_cost=90, base_up=0.52,
             base_beat=0.19, median_return=0.003, best=0.01, worst=0.001,
             min_similarity=0.99, query_linearity=0.1)
        for h in (1, 3, 5)
    ]
    out = _verdict(strong, 0.001)
    assert out["buy"] is True
    assert "살 만합니다" in out["headline"]


def test_verdict_without_samples_says_so():
    from patternscan.odds import Odds
    from patternscan.webui.server import _verdict

    thin = [Odds("minute1", 5, 1, samples=3, up=3, beat_cost=3, base_up=0.5,
                 base_beat=0.2, median_return=0.0, best=0.0, worst=0.0,
                 min_similarity=0.9, query_linearity=0.3)]
    out = _verdict(thin, 0.001)
    assert out["buy"] is False
    assert "판단할 수 없습니다" in out["headline"]


def test_noise_is_marked_as_indistinguishable(client):
    """합성 무작위 시세이므로 '평소와 구분됨'이 나오면 안 된다."""
    state = _run_scan(client)
    informative = [r for r in state["analysis"]["odds"] if r["tellsUsAnything"]]
    assert not informative, f"잡음인데 의미있다고 표시된 조합: {len(informative)}개"


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
        assert during["analysis"]["odds"] == before["analysis"]["odds"]

    for _ in range(600):
        if not state.job.snapshot()["running"]:
            break
        threading.Event().wait(0.05)


def test_examples_come_from_both_outcomes(client):
    """올랐던 사례와 떨어졌던 사례를 함께 보여줘야 한쪽만 보고 속지 않는다."""
    _run_scan(client)
    row = client[0].get_json("/api/state")["analysis"]["odds"][0]
    data = client[0].get_json(
        f"/api/examples?timeframe={row['timeframe']}&horizon={row['horizon']}"
    )
    assert data["query"], "지금 모양이 없습니다"
    for example in data["rose"]:
        assert example["outcome"] > data["cost"]
        assert len(example["shape"]) == data["length"]
        assert len(example["after"]) == row["horizon"] + 1
        assert example["after"][0] == 0.0
    for example in data["fell"]:
        assert example["outcome"] < 0.0


def test_examples_are_the_most_similar_not_the_most_profitable(client):
    """가장 많이 오른 사례를 보여주면 실제보다 좋아 보인다."""
    _run_scan(client)
    row = client[0].get_json("/api/state")["analysis"]["odds"][0]
    data = client[0].get_json(
        f"/api/examples?timeframe={row['timeframe']}&horizon={row['horizon']}"
    )
    for group in ("rose", "fell"):
        sims = [e["similarity"] for e in data[group]]
        assert sims == sorted(sims, reverse=True), f"{group}이 유사도 순이 아닙니다"


def test_examples_for_an_unknown_timeframe_is_404(client):
    _run_scan(client)
    with pytest.raises(urllib.error.HTTPError) as exc:
        client[0].get_json("/api/examples?timeframe=minute7&horizon=1")
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
    state.start("scan", webui._do_odds, state, "KRW-BTC", 0.85, 0.0005, 0.0002, 180)
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


def test_server_binds_to_loopback_by_default():
    """기본은 이 컴퓨터에서만 열려야 한다 — 밖에 여는 건 사용자가 정한다."""
    import inspect

    assert inspect.signature(webui.serve).parameters["host"].default == "127.0.0.1"

    state = webui.State("KRW-BTC", "data")
    handler = type("BoundHandler", (webui.Handler,), {"state": state})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    try:
        assert httpd.server_address[0] == "127.0.0.1"
    finally:
        httpd.server_close()


def test_cli_defaults_to_loopback():
    """--host를 빼먹었을 때 밖으로 열리면 안 된다."""
    from patternscan.cli import build_parser

    args = build_parser().parse_args(["ui"])
    assert args.host == "127.0.0.1"


def test_lan_address_is_a_plain_address_or_none():
    """주소를 못 찾아도 서버가 죽으면 안 된다."""
    found = webui._lan_address()
    assert found is None or (isinstance(found, str) and found.count(".") == 3)
