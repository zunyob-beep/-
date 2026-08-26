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
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

from patternscan.data import cache_path, save
from patternscan.models import Candle
from patternscan.odds import MIN_SAMPLES as ODDS_MIN_SAMPLES_FOR_TEST
from patternscan.webui import server as webui


def _odds_stub(samples, length):
    """표본만 있으면 되는 가짜 확률 행."""
    from patternscan.odds import Odds

    return Odds(
        timeframe="minute1", length=length, horizon=1, samples=samples,
        up=0, beat_cost=0, base_up=0.5, base_beat=0.2,
        median_return=0.0, best=0.0, worst=0.0,
        min_similarity=0.9, query_linearity=0.3,
    )


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


# ------------------------------------------------------------ 앱처럼 설치
#
# 홈 화면 아이콘이 되려면 브라우저가 세 가지를 **정확한 자리에서** 찾을 수
# 있어야 한다. 하나라도 어긋나면 설치가 조용히 안 될 뿐 오류는 안 난다 —
# 그래서 눈으로는 못 잡고, 여기서 잡아야 한다.
def test_manifest_is_served_as_a_manifest(client):
    status, body, headers = client[0].get("/static/manifest.webmanifest")
    assert status == 200
    # text/plain으로 나가면 크롬이 매니페스트로 안 읽고 설치가 안 뜬다.
    assert "manifest" in headers["Content-Type"]
    manifest = json.loads(body.decode("utf-8"))
    for key in ("name", "short_name", "start_url", "display", "icons"):
        assert manifest[key], f"매니페스트에 {key}가 없습니다"
    assert manifest["display"] == "standalone"


def test_every_icon_the_manifest_promises_actually_exists(client):
    """매니페스트가 없는 파일을 가리키면 설치가 통째로 실패한다."""
    manifest = client[0].get_json("/static/manifest.webmanifest")
    for icon in manifest["icons"]:
        status, body, headers = client[0].get(icon["src"])
        assert status == 200, icon["src"]
        assert headers["Content-Type"] == "image/png"
        assert body[:8] == b"\x89PNG\r\n\x1a\n", f"{icon['src']}가 PNG가 아닙니다"
        width = int.from_bytes(body[16:20], "big")
        assert f"{width}x" in icon["sizes"], f"{icon['src']}의 실제 크기가 {width}"


def test_a_maskable_icon_is_offered(client):
    """안드로이드는 마스크용 아이콘이 없으면 흰 테두리를 둘러버린다."""
    manifest = client[0].get_json("/static/manifest.webmanifest")
    assert any("maskable" in i.get("purpose", "") for i in manifest["icons"])


def test_the_service_worker_sits_at_the_root(client):
    """/static/sw.js 로 주면 관할이 /static/ 아래로 좁아져 첫 화면을 못 맡는다."""
    status, body, headers = client[0].get("/sw.js")
    assert status == 200
    assert "javascript" in headers["Content-Type"]
    assert headers.get("Service-Worker-Allowed") == "/"
    assert b"addEventListener" in body


def test_apple_looks_for_the_touch_icon_at_the_root(client):
    """iOS는 link 태그를 못 찾으면 /apple-touch-icon.png 를 직접 찔러 본다."""
    status, body, _ = client[0].get("/apple-touch-icon.png")
    assert status == 200
    assert body[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_page_declares_what_ios_needs(client):
    """iOS는 매니페스트의 display를 안 읽는다. meta 태그가 있어야 전체 화면이 된다."""
    body = client[0].get("/")[1].decode("utf-8")
    assert 'rel="manifest"' in body
    assert 'name="apple-mobile-web-app-capable"' in body
    assert 'rel="apple-touch-icon"' in body


def test_the_service_worker_never_caches_prices(client):
    """3시간 전 가격을 지금 가격이라고 보여주는 것보다 실패하는 편이 낫다.

    서비스 워커가 /api/* 를 캐시하면 정확히 그 일이 벌어진다. 화면에는
    아무 표시도 안 나므로 사용자는 옛 숫자를 보고 돈을 넣는다.
    """
    source = client[0].get("/sw.js")[1].decode("utf-8")
    guard = "if (url.pathname.startsWith('/api/')) return;"
    assert guard in source
    # 그 방어선이 fetch 처리보다 앞에 있어야 의미가 있다.
    assert source.index(guard) < source.index("event.respondWith")


def test_icons_may_be_cached_but_code_never_is(client):
    """아이콘은 안 바뀐다. 화면 코드는 바뀌는데, 옛것이 남으면 못 고친다."""
    assert "no-store" in client[0].get("/static/app.js")[2]["Cache-Control"]
    assert "no-store" in client[0].get("/")[2]["Cache-Control"]
    assert "max-age" in client[0].get("/static/icon-192.png")[2]["Cache-Control"]


def test_the_page_keeps_checking_that_the_server_is_alive(client):
    """놀 때 폴링을 멈추면 컴퓨터가 꺼져도 화면은 멀쩡해 보인다.

    실제로 그랬다. 서버를 죽여도 안내가 안 뜨고, 버튼을 눌러야만
    그제서야 안 된다는 걸 알 수 있었다. 홈 화면 아이콘으로 띄워 두고
    쓰는 물건이라 이건 특히 나쁘다.
    """
    source = client[0].get("/static/app.js")[1].decode("utf-8")
    assert "HEARTBEAT" in source
    # 작업 중일 때만 다음 폴링을 잡는 옛 구조로 돌아가면 안 된다.
    assert "if (job.running) timer =" not in source
    assert "job.running ? 500 : HEARTBEAT" in source


def test_a_dead_server_is_told_apart_from_a_rejected_request(client):
    """둘을 같은 빨간 줄로 보여주면 어느 쪽인지 알 수가 없다.

    앞의 것은 컴퓨터를 켜야 하고, 뒤의 것은 화면에서 조건을 바꿔야 한다.
    """
    source = client[0].get("/static/app.js")[1].decode("utf-8")
    assert "class Unreachable" in source
    assert "instanceof Unreachable" in source


def test_the_ios_hint_points_at_the_right_corner(client):
    """공유 버튼 자리가 기기마다 다르다.

    아이패드는 위쪽 주소창 옆, 아이폰은 아래쪽 가운데다. 한쪽만 적어두면
    다른 쪽 사용자는 없는 곳을 쳐다보게 된다 — 그리고 그 버튼을 못 찾으면
    홈 화면 추가는 영영 못 한다.
    """
    source = client[0].get("/static/app.js")[1].decode("utf-8")
    assert "화면 아래 가운데" in source     # 아이폰
    assert "오른쪽 위 주소창 옆" in source   # 아이패드
    # 둘을 갈라놓지 않으면 위 두 문구가 있어도 소용없다.
    assert "iphone ?" in source


# ------------------------------------------------- 홈 화면에 넣을 주소
#
# "어떤 주소를 추가하냐"가 매번 막히는 자리다. 답이 실행 방법마다 다른데
# 화면에는 늘 127.0.0.1만 찍혀 있었다.
def test_localhost_is_never_offered_as_the_home_screen_address(monkeypatch):
    """127.0.0.1을 아이패드에 넣으면 아이패드 자신을 가리킨다 — 절대 안 열린다."""
    monkeypatch.delenv("CODESPACE_NAME", raising=False)
    address, kind = webui.phone_address(8765, "127.0.0.1")
    assert address is None
    assert kind == "local-only"
    lines = "\n".join(webui._home_screen_lines(8765, "127.0.0.1"))
    assert "127.0.0.1" in lines and "이 기기 자신" in lines


def test_a_codespace_is_recognised_and_gets_its_https_address(monkeypatch):
    """Codespaces는 포트를 https로 넘긴다. IP를 찍어 주면 아무 데도 안 닿는다."""
    monkeypatch.setenv("CODESPACE_NAME", "fuzzy-space-1234")
    monkeypatch.setenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    address, kind = webui.phone_address(8765, "0.0.0.0")
    assert kind == "codespace"
    assert address == "https://fuzzy-space-1234-8765.app.github.dev/"


def test_the_codespace_address_wins_even_on_localhost(monkeypatch):
    """코드스페이스 안에서는 127.0.0.1에 붙어 있어도 밖에서 열 주소가 있다."""
    monkeypatch.setenv("CODESPACE_NAME", "fuzzy-space-1234")
    monkeypatch.setenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    assert webui.phone_address(8765, "127.0.0.1")[1] == "codespace"


def test_a_lan_address_comes_with_the_warning_that_it_can_change(monkeypatch):
    """IP가 바뀌면 홈 화면 아이콘이 죽는다. 죽고 나서 알면 늦다."""
    monkeypatch.delenv("CODESPACE_NAME", raising=False)
    monkeypatch.setattr(webui, "_lan_address", lambda: "192.168.0.7")
    address, kind = webui.phone_address(8765, "0.0.0.0")
    assert (address, kind) == ("http://192.168.0.7:8765/", "lan")
    assert "바뀝니다" in "\n".join(webui._home_screen_lines(8765, "0.0.0.0"))


def test_an_undiscoverable_network_says_so_instead_of_guessing(monkeypatch):
    monkeypatch.delenv("CODESPACE_NAME", raising=False)
    monkeypatch.setattr(webui, "_lan_address", lambda: None)
    assert webui.phone_address(8765, "0.0.0.0") == (None, "lan-unknown")


# ---------------------------------------------------------- 맨 위 지금 시세
class FakeTickerClient:
    """업비트 현재가 API 흉내. 몇 번 불렸는지도 센다."""

    def __init__(self, prices=None, fail=False):
        self.prices = prices or {"KRW-BTC": 158_320_000.0, "KRW-SOL": 268_000.0}
        self.fail = fail
        self.calls = []

    def get_ticker(self, market):
        from patternscan.upbit import Ticker, UpbitError

        self.calls.append(market)
        if self.fail:
            raise UpbitError("업비트에 닿지 않습니다")
        price = self.prices[market]
        return Ticker(
            market=market, price=price, change_rate=0.0124,
            change_price=price * 0.0124, high=price * 1.03, low=price * 0.97,
            at=datetime(2026, 8, 26, tzinfo=timezone.utc),
        )


def test_the_ticker_reports_price_and_direction(client):
    api, state = client
    state.prices.client = FakeTickerClient()
    data = api.get_json("/api/ticker?market=KRW-BTC")
    assert data["ok"] is True
    assert data["price"] == 158_320_000.0
    assert data["label"] == "비트코인"
    assert data["direction"] == "up"


def test_a_failed_ticker_does_not_turn_the_whole_page_red(client):
    """맨 위 숫자는 장식이다. 못 받았다고 분석 화면이 오류가 되면 안 된다."""
    api, state = client
    state.prices.client = FakeTickerClient(fail=True)
    status, body, _ = api.get("/api/ticker?market=KRW-BTC")
    assert status == 200, "시세 실패가 HTTP 오류로 나가면 화면 전체가 빨개진다"
    assert json.loads(body)["ok"] is False


def test_an_unknown_market_falls_back_instead_of_becoming_a_filename(client):
    """종목 코드는 파일 이름이 된다 (KRW-BTC_minute1.csv).

    밖에서 온 문자열을 그대로 쓰면 폴더를 벗어나는 이름도 만들 수 있다.
    """
    api, state = client
    state.prices.client = FakeTickerClient()
    for bad in ("../../../etc/passwd", "KRW-DOGE", "", "KRW-BTC; rm -rf /"):
        data = api.get_json(f"/api/ticker?market={urllib.parse.quote(bad)}")
        assert data["market"] == "KRW-BTC", f"{bad!r}가 그대로 통과했습니다"


def test_a_bad_market_never_reaches_a_job(client):
    api, _ = client
    started = api.post_json("/api/scan", {"market": "../../etc/passwd"})
    assert started["started"]
    assert not list(Path(".").glob("**/etc*")), "폴더 밖에 파일을 만들었습니다"


def test_prices_are_reused_for_a_moment(client):
    """창을 여러 개 열어두면 그만큼 업비트를 두드린다.

    수집이 도는 중이면 같은 한도를 나눠 쓰게 되어 수집이 느려진다.
    화면 숫자 하나 때문에 본업이 밀리면 안 된다.
    """
    api, state = client
    fake = FakeTickerClient()
    state.prices.client = fake
    for _ in range(10):
        api.get_json("/api/ticker?market=KRW-BTC")
    assert len(fake.calls) == 1, f"10번 물어봤더니 업비트를 {len(fake.calls)}번 불렀습니다"


def test_each_market_is_cached_separately(client):
    api, state = client
    fake = FakeTickerClient()
    state.prices.client = fake
    assert api.get_json("/api/ticker?market=KRW-BTC")["price"] == 158_320_000.0
    assert api.get_json("/api/ticker?market=KRW-SOL")["price"] == 268_000.0


def test_the_cache_expires(client):
    """영원히 들고 있으면 '지금 시세'가 아니라 '아까 시세'다."""
    api, state = client
    fake = FakeTickerClient()
    state.prices.client = fake
    state.prices.ttl = 0.0
    api.get_json("/api/ticker?market=KRW-BTC")
    api.get_json("/api/ticker?market=KRW-BTC")
    assert len(fake.calls) == 2


def test_the_page_is_told_which_coins_exist(client):
    """화면이 종목 목록을 따로 들고 있으면 언젠가 서버와 갈라진다."""
    state = _run_scan(client)
    codes = [m["code"] for m in state["markets"]]
    assert codes == ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
    assert [m["label"] for m in state["markets"]] == ["비트코인", "이더리움", "엑스알피", "솔라나"]


def test_direction_matches_upbit_colours():
    """업비트는 오르면 빨강, 내리면 파랑이다. 여기서만 뒤집으면 안 된다."""
    from patternscan.upbit import Ticker

    def at(rate):
        return Ticker("KRW-BTC", 100.0, rate, 1.0, 1.0, 1.0,
                      datetime(2026, 1, 1, tzinfo=timezone.utc)).direction

    assert at(0.01) == "up"
    assert at(-0.01) == "down"
    assert at(0.0) == "flat"


# ------------------------------------------------------- 얼마나 과거까지
def test_the_page_can_ask_for_more_than_thirty_days(client):
    """예전에는 이 선택이 없어서 화면 버튼이 늘 30일치만 요청했다.

    명령줄로 8년치를 받아둔 사람이 화면에서 버튼을 누르면 그때부터
    30일만 갱신됐고, 왜 숫자가 그것밖에 안 되는지 알 방법이 없었다.
    """
    state = _run_scan(client)
    counts = [p["count"] for p in state["periods"]]
    assert 43_200 in counts, "30일치가 없습니다"
    assert max(counts) >= 4_000_000, "8년치를 고를 수 없습니다"
    assert counts == sorted(counts), "짧은 것부터 나와야 고르기 쉽다"


def test_the_requested_period_actually_reaches_the_fetch(client, monkeypatch):
    """화면에서 8년을 골랐는데 서버가 30일만 받으면 아무 소용이 없다."""
    api, state = client
    asked = []

    def spy(client_, market, timeframe, count, **kwargs):
        asked.append((timeframe, count))
        raise webui.UpbitError("여기서 멈춘다")

    monkeypatch.setattr(webui, "fetch", spy)
    api.post_json("/api/live", {"market": "KRW-BTC", "count": 4_204_800})
    for _ in range(200):
        if not state.job.snapshot()["running"]:
            break
        threading.Event().wait(0.05)

    by_timeframe = dict(asked)
    assert by_timeframe["minute1"] == 4_204_800
    # 같은 기간이 되도록 나눠야 한다. 3분봉을 420만 개 받으면 24년치다.
    assert by_timeframe["minute3"] == 4_204_800 // 3
    assert by_timeframe["minute5"] == 4_204_800 // 5


def test_the_cache_report_says_when_not_just_how_many(client):
    """개수만으로는 '작다'는 느낌만 들 뿐 왜 작은지 알 수 없다."""
    api, _ = client
    cached = api.get_json("/api/state")["cached"]
    minute1 = next(c for c in cached if c["timeframe"] == "minute1")
    assert minute1["count"] > 0
    assert minute1["from"] and minute1["to"], "기간이 없으면 개수가 많은지 적은지 모른다"
    assert minute1["from"] <= minute1["to"]


def test_fetch_progress_is_finer_than_three_steps(client, monkeypatch):
    """8년치는 40분이 걸린다. 3칸 막대로는 멈춘 것과 구분이 안 된다."""
    api, state = client
    seen = []

    def spy(client_, market, timeframe, count, **kwargs):
        progress = kwargs.get("progress")
        assert progress is not None, f"{timeframe} 수집에 진행 표시가 없습니다"
        progress(count // 2, count)
        seen.append(state.job.snapshot())
        raise webui.UpbitError("여기까지")

    monkeypatch.setattr(webui, "fetch", spy)
    api.post_json("/api/live", {"market": "KRW-BTC", "count": 43_200})
    for _ in range(200):
        if not state.job.snapshot()["running"]:
            break
        threading.Event().wait(0.05)

    assert seen and seen[0]["total"] > 3, "진행이 봉 개수 단위여야 한다"


# ------------------------------------------------------- 숨기라면 숨겨야 한다
def test_hidden_really_hides(client):
    """`hidden` 속성은 브라우저 기본 스타일의 `[hidden]{display:none}`으로
    동작하는데, 그건 **클래스 선택자 하나에도 진다.**

    실제로 그랬다. `.install-hint`에 display:flex를 준 순간, hidden을 붙여
    둔 안내가 내용도 없이 화면에 떴다 — "앱처럼 쓰기:"만 덩그러니.
    """
    css = client[0].get("/static/style.css")[1].decode("utf-8")
    assert "[hidden]" in css and "display: none !important" in css


def test_an_error_without_a_reason_is_treated_as_unreachable(client):
    """이 서버는 오류에 반드시 이유를 붙인다.

    그러니 이유 없는 오류가 왔다면 답한 쪽이 이 서버가 아니다 —
    코드스페이스가 잠들어 깃허브 프록시가 대신 답한 경우다. 그때
    "오류 404"라고만 띄우면 사용자는 뭘 해야 할지 알 수가 없다.
    """
    source = client[0].get("/static/app.js")[1].decode("utf-8")
    assert "if (!response.ok && !said)" in source
    assert "서버가 아닌 곳에서" in source


def test_every_server_error_carries_a_reason(client):
    """위 규칙이 성립하려면 서버가 그 약속을 지켜야 한다."""
    for path in ("/api/examples?timeframe=nope&horizon=1", "/no-such-route", "/static/no-such.js"):
        try:
            client[0].get(path)
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert body.get("error"), f"{path}가 이유 없이 실패했습니다"


# ------------------------------------------------- 표본이 안 모였을 때의 안내
def test_no_matches_says_how_many_were_found(client):
    """"기준을 낮춰 보세요"만 말하면, 이미 낮춘 사람에게는 아무 말도 안 한 것이다."""
    from patternscan.webui.server import _why_nothing_matched

    rows = [_odds_stub(samples=4, length=180), _odds_stub(samples=1, length=180)]
    said = " ".join(_why_nothing_matched(rows))
    assert "4개" in said, "몇 개가 모였는지 숫자로 말해야 한다"
    assert str(ODDS_MIN_SAMPLES_FOR_TEST) in said


def test_a_long_window_is_named_as_the_thing_to_change(client):
    """유사도보다 직전 봉 개수가 훨씬 크게 듣는다. 그걸 먼저 말해야 한다."""
    from patternscan.webui.server import _why_nothing_matched

    said = " ".join(_why_nothing_matched([_odds_stub(samples=2, length=180)]))
    assert "180개" in said
    assert "직전 몇 개 봉" in said


def test_with_nothing_at_all_it_still_advises(client):
    from patternscan.webui.server import _why_nothing_matched

    said = _why_nothing_matched([])
    assert said and any("직전 몇 개 봉" in line for line in said)


# ------------------------------------------------------- 한 번 죽으면 끝이었다
def test_polling_survives_any_error(client):
    """오류가 나도 **다음 폴링은 반드시 잡아야** 한다.

    예전에는 오류 종류를 보고 어떤 경우에만 다시 잡았다. 그래서 예상 못 한
    오류가 한 번 나면 그 자리에서 폴링이 죽었고, 마지막 응답에서 잠갔던
    단추가 영영 잠긴 채로 남았다. 새로고침 말고는 되살릴 방법이 없었다.
    브라우저로 재현하니 오류 뒤 7초 동안 재시도가 0회였다.
    """
    source = client[0].get("/static/app.js")[1].decode("utf-8")
    body = source[source.index("async function refreshState()"):]
    body = body[: body.index("\nfunction unlock")]
    assert "finally" in body, "다음 폴링을 finally에서 잡아야 한다"
    assert "timer = setTimeout(refreshState, next)" in body
    # 조건부로 다시 잡던 옛 구조로 돌아가면 안 된다
    assert "if (err instanceof Unreachable) { stopPolling();" not in source


def test_buttons_unlock_when_the_server_stops_answering(client):
    """서버가 답을 못 하는 동안 단추까지 잠겨 있으면 손쓸 방법이 없어진다."""
    source = client[0].get("/static/app.js")[1].decode("utf-8")
    assert "function unlock()" in source
    assert "unlock();" in source


# ------------------------------------------------------------------ 멈추기
def test_a_long_job_can_be_stopped(client):
    """8년치 수집은 40분이 걸린다. 잘못 눌렀을 때 빠져나올 길이 있어야 한다."""
    api, state = client
    entered = threading.Event()

    def slow(state_):
        entered.set()
        for _ in range(10_000):
            state_.checkpoint()
            threading.Event().wait(0.01)

    assert state.start("slow", slow, state)
    assert entered.wait(3)

    answer = api.post_json("/api/stop", {})
    assert answer["stopped"] is True

    for _ in range(300):
        if not state.job.snapshot()["running"]:
            break
        threading.Event().wait(0.02)
    job = state.job.snapshot()
    assert not job["running"], "멈추라고 했는데 계속 돕니다"
    # 사용자가 멈춘 것은 고장이 아니다 — 빨간 오류로 띄우면 안 된다
    assert not job["error"]
    assert "멈췄" in job["message"]


def test_stopping_when_nothing_runs_is_harmless(client):
    assert client[0].post_json("/api/stop", {})["stopped"] is False


def test_a_stopped_job_lets_the_next_one_start(client):
    """멈춘 뒤에도 못 돌리면 멈추기가 무슨 소용인가."""
    api, state = client

    def slow(state_):
        for _ in range(10_000):
            state_.checkpoint()
            threading.Event().wait(0.01)

    state.start("slow", slow, state)
    threading.Event().wait(0.2)
    api.post_json("/api/stop", {})
    for _ in range(300):
        if not state.job.snapshot()["running"]:
            break
        threading.Event().wait(0.02)

    assert state.start("again", lambda: None), "멈춘 뒤에 다시 시작하지 못합니다"


def test_an_unexpected_failure_still_reports_itself(client):
    """예상 못 한 예외가 조용히 사라지면 원인을 영영 못 찾는다."""
    api, state = client

    def broken():
        raise ZeroDivisionError("0으로 나눴습니다")

    state.start("broken", broken)
    for _ in range(200):
        if not state.job.snapshot()["running"]:
            break
        threading.Event().wait(0.02)
    job = state.job.snapshot()
    assert "ZeroDivisionError" in job["error"]


def test_pressing_start_twice_says_so(client):
    """시작이 안 됐는데 아무 말도 안 하면, 눌러도 반응이 없는 것처럼 보인다."""
    source = client[0].get("/static/app.js")[1].decode("utf-8")
    assert "answer.started === false" in source
    assert "이미 하고 있습니다" in source
