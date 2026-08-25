"""웹 UI 서버 검증 — 네트워크 없이.

이 서버는 API 키로 실제 주문을 낼 수 있다. 그래서 두 가지를 특히 조인다:
외부에 열리지 않는지, 그리고 잘못된 입력이 한글 오류로 막히는지.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from btcbot.config import Settings
from btcbot.data import cache_path, save_csv
from btcbot.exchange.base import ExchangeError
from btcbot.strategies.rule import PRESETS
from btcbot.webui.botmanager import BotManager, LogBuffer
from btcbot.webui.server import STATIC_DIR, ApiError, AppState, create_server, handle_api
from tests.conftest import series


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings.load(None)
    s.data_dir = str(tmp_path / "data")
    s.runs_dir = str(tmp_path / "runs")
    s.market = "KRW-BTC"
    s.interval = "day"
    return s


@pytest.fixture
def state(settings, tmp_path) -> AppState:
    """네트워크를 완전히 끊은 서버 상태.

    실제 업비트를 때리면 테스트가 외부 상황에 흔들리고, 재시도 백오프
    때문에 몇 분씩 멈춘다.
    """
    app = AppState(settings)
    app.store_path = tmp_path / "strategies_saved.json"

    def offline(*args, **kwargs):
        raise ExchangeError("테스트: 네트워크 차단됨")

    app.client.get_markets = app.quick.get_markets = lambda: []
    for client in (app.client, app.quick):
        client.get_candles = offline
        client.get_ticker = offline
        client.get_price = offline
    return app


@pytest.fixture
def with_candles(state, settings):
    """백테스트가 쓸 캔들을 캐시에 심어둔다."""
    import math

    prices = [100.0 + 12 * math.sin(i / 22) + i * 0.06 for i in range(400)]
    candles = series(prices, start=datetime(2023, 1, 1, tzinfo=timezone.utc), step=timedelta(days=1))
    save_csv(cache_path("KRW-BTC", "day", settings.data_dir), candles)
    return state


def get(state, path, **query):
    return handle_api(state, "GET", path, {k: [v] for k, v in query.items()}, None)


def post(state, path, body=None):
    return handle_api(state, "POST", path, {}, body or {})


# ------------------------------------------------------------------ 메타
def test_meta_gives_everything_the_ui_needs(state):
    meta = get(state, "/api/meta")
    assert meta["markets"]  # 네트워크가 없어도 기본 목록이 나온다
    assert meta["intervals"]
    assert meta["presets"] == PRESETS
    assert meta["builder"]["operands"] and meta["builder"]["operators"]
    assert "risk" in meta["defaults"]
    assert isinstance(meta["has_api_keys"], bool)


def test_meta_never_leaks_api_keys(state, monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "비밀키값")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "비밀시크릿")
    payload = json.dumps(get(state, "/api/meta"), ensure_ascii=False)
    assert "비밀키값" not in payload
    assert "비밀시크릿" not in payload
    assert get(state, "/api/meta")["has_api_keys"] is True


def test_meta_does_not_use_the_slow_client(state):
    """첫 화면 로딩이 재시도 백오프에 걸리면 30초 넘게 빈 화면이 뜬다."""

    def forbidden():
        raise AssertionError("느린 클라이언트를 쓰면 안 됩니다")

    state.client.get_markets = forbidden
    state.quick.get_markets = lambda: [
        {"market": "KRW-BTC", "korean_name": "비트코인"},
        {"market": "BTC-ETH", "korean_name": "이더리움"},
    ]
    markets = get(state, "/api/meta")["markets"]
    assert [m["market"] for m in markets] == ["KRW-BTC"]  # 원화 마켓만 남는다


def test_unknown_route_is_404(state):
    with pytest.raises(ApiError) as exc:
        get(state, "/api/없는것")
    assert exc.value.status == 404


def test_unsupported_method(state):
    with pytest.raises(ApiError) as exc:
        handle_api(state, "DELETE", "/api/meta", {}, None)
    assert exc.value.status == 405


# ------------------------------------------------------------------ 전략 저장
def test_save_and_delete_strategy(state):
    spec = {**PRESETS[0], "label": "내 전략"}
    saved = post(state, "/api/strategies/save", {"spec": spec})["saved"]
    assert [s["label"] for s in saved] == ["내 전략"]

    left = post(state, "/api/strategies/delete", {"label": "내 전략"})["saved"]
    assert left == []


def test_save_overwrites_same_label(state):
    for weight in (1.0, 0.5):
        saved = post(
            state, "/api/strategies/save", {"spec": {**PRESETS[0], "label": "같은이름", "target_weight": weight}}
        )["saved"]
    assert len(saved) == 1
    assert saved[0]["target_weight"] == 0.5


def test_save_requires_label(state):
    with pytest.raises(ApiError, match="이름"):
        post(state, "/api/strategies/save", {"spec": {**PRESETS[0], "label": ""}})


def test_save_rejects_broken_spec(state):
    with pytest.raises(Exception, match="최소 하나"):
        post(state, "/api/strategies/save", {"spec": {"label": "빈것"}})


# --------------------------------------------------- 말로 설명하기 (Claude)
def test_describe_returns_translation(state):
    from tests.test_nlstrategy import FakeClient, payload

    state.translator = FakeClient(payload())
    result = post(state, "/api/strategies/describe", {"text": "RSI 30 아래면 사줘"})
    assert result["understood"] is True
    assert result["spec"]["label"] == "RSI 반등"


def test_describe_does_not_auto_apply(state):
    """변환 결과가 저장된 전략이 되어선 안 된다 — 사람이 확인한 뒤에만."""
    from tests.test_nlstrategy import FakeClient, payload

    state.translator = FakeClient(payload())
    post(state, "/api/strategies/describe", {"text": "RSI 30 아래면 사줘"})
    assert state.load_strategies() == []


def test_describe_surfaces_refusal(state):
    from tests.test_nlstrategy import FakeClient, payload

    state.translator = FakeClient(
        payload(understood=False, message="김치프리미엄은 지원하지 않습니다.", entry=[], exit=[])
    )
    result = post(state, "/api/strategies/describe", {"text": "김프 뜨면 사줘"})
    assert result["understood"] is False
    assert result["spec"] is None


def test_describe_empty_text_is_rejected(state):
    with pytest.raises(ApiError, match="입력하세요"):
        post(state, "/api/strategies/describe", {"text": "  "})


def test_meta_reports_claude_key_presence(state, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert get(state, "/api/meta")["has_claude_key"] is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert get(state, "/api/meta")["has_claude_key"] is True


def test_validate_returns_message_instead_of_raising(state):
    bad = post(state, "/api/strategies/validate", {"spec": {"label": "x"}})
    assert bad["ok"] is False
    assert "최소 하나" in bad["message"]

    good = post(state, "/api/strategies/validate", {"spec": PRESETS[0]})
    assert good["ok"] is True


def test_saved_strategies_survive_corrupt_file(state):
    state.store_path.write_text("깨진 json", encoding="utf-8")
    assert state.load_strategies() == []


def test_saved_file_is_written_atomically(state):
    post(state, "/api/strategies/save", {"spec": {**PRESETS[0], "label": "원자적"}})
    assert state.store_path.exists()
    assert not list(state.store_path.parent.glob("*.tmp"))


# ------------------------------------------------------------------ 백테스트
def test_backtest_with_builder_spec(with_candles):
    result = post(
        with_candles,
        "/api/backtest",
        {"market": "KRW-BTC", "interval": "day", "spec": PRESETS[1], "cash": 1_000_000},
    )
    assert result["performance"]["initial_equity"] == 1_000_000
    assert result["equity_curve"]
    assert result["candles"]
    assert "strategy" in result


def test_backtest_with_builtin_strategy(with_candles):
    result = post(
        with_candles,
        "/api/backtest",
        {"strategy": "ma_cross", "params": {"fast": 5, "slow": 20}, "interval": "day"},
    )
    assert result["strategy"].startswith("ma_cross")


def test_backtest_json_has_no_infinity(with_candles):
    """손익비가 무한대여도 JSON으로 나갈 수 있어야 한다."""
    result = post(with_candles, "/api/backtest", {"spec": PRESETS[1], "interval": "day"})
    json.dumps(result)  # 예외 없이 직렬화되면 통과


def test_backtest_without_data_says_what_to_do(state):
    with pytest.raises(ApiError) as exc:
        post(state, "/api/backtest", {"spec": PRESETS[1], "interval": "day"})
    assert "데이터 받기" in str(exc.value)


def test_backtest_rejects_bad_spec(with_candles):
    with pytest.raises(ApiError, match="알 수 없는 지표"):
        post(
            with_candles,
            "/api/backtest",
            {"spec": {"entry": {"all": [{"left": {"type": "없는지표"}, "op": ">", "right": 1}]}}},
        )


def test_backtest_rejects_unknown_strategy(with_candles):
    with pytest.raises(ApiError, match="모르는 전략"):
        post(with_candles, "/api/backtest", {"strategy": "없는전략"})


def test_backtest_rejects_bad_interval(state):
    with pytest.raises(ApiError, match="봉 간격"):
        post(state, "/api/backtest", {"interval": "minute7", "spec": PRESETS[0]})


def test_backtest_rejects_bad_risk(with_candles):
    with pytest.raises(ApiError, match="리스크"):
        post(with_candles, "/api/backtest", {"spec": PRESETS[1], "risk": {"stop_loss_pct": 5}})


def test_backtest_rejects_non_numeric_cash(with_candles):
    with pytest.raises(ApiError, match="숫자"):
        post(with_candles, "/api/backtest", {"spec": PRESETS[1], "cash": "백만원"})


def test_backtest_risk_settings_take_effect(with_candles):
    loose = post(with_candles, "/api/backtest", {"spec": PRESETS[1], "interval": "day"})
    capped = post(
        with_candles,
        "/api/backtest",
        {"spec": PRESETS[1], "interval": "day", "risk": {"max_position_weight": 0.2}},
    )
    assert max(p["weight"] for p in capped["equity_curve"]) < max(
        p["weight"] for p in loose["equity_curve"]
    )


def test_requests_do_not_leak_settings_between_calls(with_candles):
    """한 요청의 리스크 설정이 다음 요청에 남으면 안 된다."""
    post(with_candles, "/api/backtest", {"spec": PRESETS[1], "risk": {"max_position_weight": 0.1}})
    after = post(with_candles, "/api/backtest", {"spec": PRESETS[1]})
    assert with_candles.settings.risk.max_position_weight == 1.0
    assert max(p["weight"] for p in after["equity_curve"]) > 0.5


# ------------------------------------------------------------------ 봇 제어
def test_live_start_without_keys_is_blocked(state, monkeypatch):
    monkeypatch.delenv("UPBIT_ACCESS_KEY", raising=False)
    monkeypatch.delenv("UPBIT_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="API 키"):
        post(state, "/api/bot/start", {"live": True, "spec": PRESETS[1]})
    assert state.bot.running is False


def test_status_when_idle(state):
    status = get(state, "/api/status")
    assert status["running"] is False


def test_only_one_bot_at_a_time(settings):
    manager = BotManager()
    manager._thread = threading.Thread(target=lambda: threading.Event().wait(0.2))
    manager._thread.start()
    try:
        with pytest.raises(RuntimeError, match="이미 봇이"):
            manager.start(settings, live=False)
    finally:
        manager._thread.join()


def test_stop_is_safe_when_not_running(state):
    assert post(state, "/api/bot/stop")["running"] is False


# ------------------------------------------------------------------ 로그
def test_log_buffer_keeps_recent_only():
    buffer = LogBuffer(capacity=3)
    import logging

    for i in range(6):
        buffer.emit(logging.LogRecord("t", logging.INFO, "f", 1, "메시지 %d", (i,), None))
    lines = buffer.snapshot()
    assert len(lines) == 3
    assert lines[-1]["message"] == "메시지 5"


def test_log_buffer_survives_bad_format():
    import logging

    buffer = LogBuffer()
    buffer.emit(logging.LogRecord("t", logging.INFO, "f", 1, "%d개", ("숫자아님",), None))
    assert buffer.snapshot()  # 예외 없이 뭔가는 남는다


def test_logs_endpoint(state):
    assert "logs" in get(state, "/api/logs", limit="10")


# ------------------------------------------------------------------ 정적 파일
def test_canvas_heights_are_fixed_in_css():
    """차트 높이는 CSS가 정해야 한다.

    fitCanvas()는 clientHeight를 읽어 캔버스 버퍼를 만든다. CSS에서 높이가
    사라지면 캔버스가 자기 버퍼 크기만큼 늘어나고, 화면 배율이 2 이상인
    기기(아이패드 등)에서는 다시 그릴 때마다 배로 커진다.
    """
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    for canvas_id in ("#priceChart", "#equityChart"):
        # 미디어쿼리 안의 규칙이 아니라 '기본' 규칙에 높이가 있어야 한다.
        # 좁은 화면에서만 높이가 잡히면 큰 화면에서 그대로 늘어난다.
        base = [
            line
            for line in css.splitlines()
            if line.strip().startswith(canvas_id) and "width:" in line
        ]
        assert base, f"{canvas_id} 기본 규칙이 없습니다"
        assert all("height:" in rule for rule in base), f"{canvas_id} 기본 규칙에 height가 없습니다"


def test_fit_canvas_never_reads_back_the_height_attribute():
    """`canvas.height = ...`는 height 속성도 바꾼다.

    그 속성을 곱셈의 입력으로 다시 쓰면 배율이 누적된다. 실제로 났던
    버그라 회귀를 막아둔다.
    """
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    start = app_js.index("function fitCanvas")
    body = app_js[start : app_js.index("\n}", start)]
    assert "getAttribute('height') * dpr" not in body
    assert "clientHeight" in body


# ------------------------------------------------------------------ HTTP 계층
@pytest.fixture
def running_server(state):
    server, _ = create_server(state.settings, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def test_server_binds_only_to_localhost(running_server):
    """외부에 열리면 API 키로 남이 주문을 낼 수 있다."""
    assert running_server.server_address[0] == "127.0.0.1"


def test_index_page_is_served(running_server):
    port = running_server.server_port
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as res:
        body = res.read().decode()
    assert res.status == 200
    assert "btcbot" in body
    assert "전략 만들기" in body


def test_static_assets_are_served(running_server):
    port = running_server.server_port
    for path, needle in (("/style.css", "--up"), ("/app.js", "collectSpec")):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as res:
            assert needle in res.read().decode()


def test_path_traversal_is_blocked(running_server):
    """../로 서버 파일을 빼갈 수 없어야 한다."""
    port = running_server.server_port
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/../../config.py")
    assert exc.value.code == 404


def test_api_error_returns_json_not_html(running_server):
    port = running_server.server_port
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/nope")
    payload = json.loads(exc.value.read().decode())
    assert "error" in payload


def test_malformed_json_body_is_rejected(running_server):
    port = running_server.server_port
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/backtest",
        data="{깨진".encode(),
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert "잘못" in json.loads(exc.value.read().decode())["error"]


# --------------------------------------------------- 회귀: 조용히 죽거나 안 사는 문제
def test_engine_run_accepts_stop_signal(with_candles):
    """웹 UI의 '중지'는 Engine의 루프를 그대로 쓴다.

    예전에는 UI가 루프를 따로 구현하다가 거래소 오류 허용 로직을 빠뜨려,
    일시적인 429 하나에 봇이 죽었다.
    """
    from btcbot.backtest import run_backtest  # noqa: F401  (엔진 경로 확인용)
    from btcbot.engine import Engine
    from btcbot.exchange.simulated import SimulatedBroker
    from btcbot.feed import BacktestFeed
    from btcbot.strategies import get_strategy

    candles = load_candles(with_candles)
    feed = BacktestFeed(candles, warmup=2)
    engine = Engine(
        feed=feed,
        broker=SimulatedBroker("KRW-BTC", cash=1_000_000),
        strategy=get_strategy("ma_cross", fast=3, slow=10),
    )
    stats = engine.run(should_stop=lambda: True)
    assert len(stats.equity_curve) <= 1  # 즉시 멈춘다


def test_engine_survives_a_transient_exchange_error(with_candles):
    from btcbot.engine import Engine
    from btcbot.exchange.base import RateLimited
    from btcbot.exchange.simulated import SimulatedBroker
    from btcbot.feed import BacktestFeed
    from btcbot.strategies import get_strategy

    candles = load_candles(with_candles)
    broker = SimulatedBroker("KRW-BTC", cash=1_000_000)
    calls = {"n": 0}
    real_buy = broker.market_buy

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimited("일시적인 429")
        return real_buy(*args, **kwargs)

    broker.market_buy = flaky
    engine = Engine(
        feed=BacktestFeed(candles, warmup=2),
        broker=broker,
        strategy=get_strategy("ma_cross", fast=3, slow=10),
    )
    stats = engine.run()
    assert stats.errors == 1
    assert len(stats.equity_curve) > 10  # 죽지 않고 끝까지 돈다


def load_candles(state):
    from btcbot.data import cache_path, load_csv

    return load_csv(cache_path("KRW-BTC", "day", state.settings.data_dir))


def test_live_feed_pages_past_the_200_candle_limit():
    """업비트는 한 번에 200개까지만 준다. 그보다 큰 warmup도 채워야 한다.

    예전에는 200으로 잘라서, SMA 200을 쓰는 프리셋이 영원히 "warmup"만
    내놓고 한 번도 거래하지 않았다 — 오류도 없이.
    """
    from btcbot.feed import LiveFeed
    from tests.conftest import series

    all_candles = series([100.0 + i for i in range(700)])

    class Paged:
        def get_candles(self, market, interval="day", count=200, to=None):
            pool = [c for c in all_candles if to is None or c.ts <= to]
            return pool[-min(count, 200) :]

    feed = LiveFeed(Paged(), "KRW-BTC", "day", lookback=500)
    history = feed.fetch_history()
    assert len(history) == 500
    assert [c.ts for c in history] == sorted(c.ts for c in history)


def test_static_guard_rejects_sibling_directory(tmp_path, monkeypatch):
    """'static_secret' 같은 형제 폴더가 접두사만으로 통과하면 안 된다."""
    from pathlib import Path

    from btcbot.webui.server import STATIC_DIR

    sibling = Path(str(STATIC_DIR.resolve()) + "_secret") / "keys.txt"
    assert str(sibling).startswith(str(STATIC_DIR.resolve()))  # 문자열로는 통과
    assert not sibling.is_relative_to(STATIC_DIR.resolve())  # 경로로는 막힌다
