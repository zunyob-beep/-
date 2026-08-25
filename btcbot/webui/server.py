"""로컬 웹 서버.

표준 라이브러리 `http.server`만 쓴다. 코딩을 모르는 사용자에게 프레임워크
설치를 시키는 순간 거기서 막히기 때문이다.

**127.0.0.1에만 바인딩한다.** 이 서버는 API 키로 실제 주문을 낼 수 있으므로
외부에 열리면 안 된다. 인증이 없는 것도 같은 전제 위에 있다.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import traceback
from dataclasses import replace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import data as data_mod
from ..backtest import run_backtest
from ..config import Settings
from ..exchange.upbit import INTERVALS, UpbitClient
from ..models import Side
from ..risk import RiskConfig
from ..storage import Journal
from ..strategies import available, get_strategy, strategy_class
from ..strategies.rule import PRESETS, SpecError, builder_metadata, validate_spec
from .botmanager import BotManager

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
STRATEGY_STORE = Path("strategies_saved.json")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class ApiError(Exception):
    """사용자에게 그대로 보여줄 오류. 메시지는 한글로 쓴다."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class AppState:
    """서버가 들고 있는 것들. 요청 스레드 여러 개가 같이 쓴다."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bot = BotManager()
        #: 오래 걸려도 되는 작업용(과거 데이터 수집). 재시도를 넉넉히 한다.
        self.client = UpbitClient()
        #: 화면 갱신용. 실패하면 즉시 포기한다 — 현재가 위젯 하나 때문에
        #: 브라우저가 30초씩 멈춰 있으면 봇이 고장난 것처럼 보인다.
        self.quick = UpbitClient(timeout=4.0, max_retries=0)
        self.store_path = Path(settings.data_dir).parent / STRATEGY_STORE
        self._lock = threading.Lock()

    # -------------------------------------------------------- 전략 저장소
    def load_strategies(self) -> list[dict[str, Any]]:
        if not self.store_path.exists():
            return []
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("저장된 전략 파일을 읽지 못했습니다: %s", self.store_path)
            return []
        return data if isinstance(data, list) else []

    def save_strategy(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        clean = validate_spec(spec)
        if not clean["label"]:
            raise ApiError("전략 이름을 입력하세요")
        # 원본의 설명은 검증 결과에 없으므로 따로 옮긴다.
        clean["note"] = str(spec.get("note") or "")
        clean["saved_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

        with self._lock:
            items = [s for s in self.load_strategies() if s.get("label") != clean["label"]]
            items.append(clean)
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.store_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.store_path)
        return items

    def delete_strategy(self, label: str) -> list[dict[str, Any]]:
        with self._lock:
            items = [s for s in self.load_strategies() if s.get("label") != label]
            self.store_path.write_text(
                json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return items


# ------------------------------------------------------------------ 라우팅
def handle_api(state: AppState, method: str, path: str, query: dict, body: Any) -> Any:
    if method == "GET":
        return _handle_get(state, path, query)
    if method == "POST":
        return _handle_post(state, path, body or {})
    raise ApiError(f"지원하지 않는 방식입니다: {method}", 405)


def _handle_get(state: AppState, path: str, query: dict) -> Any:
    if path == "/api/meta":
        return {
            "markets": _markets(state),
            "intervals": [{"value": k, "label": _interval_label(k)} for k in INTERVALS],
            "strategies": _strategy_catalog(),
            "builder": builder_metadata(),
            "presets": PRESETS,
            "saved": state.load_strategies(),
            "has_api_keys": all(Settings.api_keys()),
            "defaults": {
                "market": state.settings.market,
                "interval": state.settings.interval,
                "cash": state.settings.cash,
                "fee_rate": state.settings.fee_rate,
                "slippage": state.settings.slippage,
                "rebalance_band": state.settings.rebalance_band,
                "risk": {
                    field: getattr(state.settings.risk, field)
                    for field in RiskConfig.__dataclass_fields__
                },
            },
        }

    if path == "/api/status":
        return state.bot.status()

    if path == "/api/logs":
        return {"logs": state.bot.logs.snapshot(int(query.get("limit", ["200"])[0]))}

    if path == "/api/candles":
        market = query.get("market", [state.settings.market])[0]
        interval = query.get("interval", [state.settings.interval])[0]
        return {"candles": _candles_payload(state, market, interval, count=200)}

    if path == "/api/ticker":
        market = query.get("market", [state.settings.market])[0]
        return _ticker(state, market)

    if path == "/api/journal":
        journal = Journal(state.settings.runs_dir, query.get("run", ["default"])[0])
        trades = journal.read_trades()
        return {
            "trades": trades[-100:],
            "total_pnl": sum(t.get("pnl", 0) for t in trades),
            "count": len(trades),
        }

    raise ApiError(f"없는 주소입니다: {path}", 404)


def _handle_post(state: AppState, path: str, body: dict) -> Any:
    if path == "/api/backtest":
        return _run_backtest(state, body)

    if path == "/api/strategies/save":
        return {"saved": state.save_strategy(body.get("spec") or {})}

    if path == "/api/strategies/delete":
        label = str(body.get("label") or "")
        if not label:
            raise ApiError("삭제할 전략 이름이 없습니다")
        return {"saved": state.delete_strategy(label)}

    if path == "/api/strategies/validate":
        try:
            validate_spec(body.get("spec") or {})
        except SpecError as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "message": "조건이 올바릅니다"}

    if path == "/api/data/fetch":
        return _fetch_data(state, body)

    if path == "/api/bot/start":
        settings = _settings_from_body(state, body)
        live = bool(body.get("live"))
        return state.bot.start(settings, live=live, dry_run=bool(body.get("dry_run")))

    if path == "/api/bot/stop":
        return state.bot.stop()

    raise ApiError(f"없는 주소입니다: {path}", 404)


# ------------------------------------------------------------------ 동작
def _run_backtest(state: AppState, body: dict) -> dict[str, Any]:
    settings = _settings_from_body(state, body)
    candles = _load_candles(state, settings, body)
    strategy = get_strategy(settings.strategy, **settings.strategy_params)

    result = run_backtest(
        candles,
        strategy,
        cash=settings.cash,
        fee_rate=settings.fee_rate,
        slippage=settings.slippage,
        risk_config=settings.risk,
        rebalance_band=settings.rebalance_band,
        interval=settings.interval,
    )
    perf = result.performance
    return {
        "market": result.market,
        "interval": result.interval,
        "strategy": result.strategy,
        "performance": {
            field: getattr(perf, field)
            for field in perf.__dataclass_fields__
            if _is_finite(getattr(perf, field))
        },
        "equity_curve": [
            {"ts": p.ts.isoformat(), "equity": p.equity, "price": p.price, "weight": p.weight}
            for p in result.stats.equity_curve
        ],
        "trades": [
            {
                "entry_ts": t.entry_ts.astimezone().strftime("%Y-%m-%d %H:%M"),
                "exit_ts": t.exit_ts.astimezone().strftime("%Y-%m-%d %H:%M"),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "reason": t.reason,
            }
            for t in result.stats.trades
        ],
        "fills": [
            {
                "ts": f.ts.isoformat(),
                "side": "buy" if f.side is Side.BUY else "sell",
                "price": f.price,
            }
            for f in result.stats.fills
        ],
        "candles": _serialize_candles(candles),
    }


def _fetch_data(state: AppState, body: dict) -> dict[str, Any]:
    market = str(body.get("market") or state.settings.market)
    interval = str(body.get("interval") or state.settings.interval)
    start = _parse_date(body.get("start"))

    candles = data_mod.load_or_fetch(
        state.client,
        market,
        interval,
        start=start,
        directory=state.settings.data_dir,
        refresh=bool(body.get("refresh")),
    )
    if not candles:
        raise ApiError("데이터를 받지 못했습니다. 인터넷 연결을 확인하세요.")
    return {
        "count": len(candles),
        "first": candles[0].ts.isoformat(),
        "last": candles[-1].ts.isoformat(),
    }


def _load_candles(state: AppState, settings: Settings, body: dict):
    start = _parse_date(body.get("start"))
    end = _parse_date(body.get("end"))
    try:
        candles = data_mod.load_or_fetch(
            state.client,
            settings.market,
            settings.interval,
            start=start,
            end=end,
            directory=settings.data_dir,
        )
    except Exception as exc:
        raise ApiError(
            f"시세를 불러오지 못했습니다: {exc}. '데이터 받기'를 먼저 눌러보세요."
        ) from exc

    if len(candles) < 2:
        raise ApiError(
            "저장된 봉이 없습니다. 위의 '데이터 받기' 버튼을 먼저 눌러 시세를 받아오세요."
        )
    return candles


def _settings_from_body(state: AppState, body: dict) -> Settings:
    settings = replace(state.settings)
    # RiskConfig는 참조가 공유되므로 새로 만들어 둔다(요청끼리 섞이면 안 된다).
    settings.risk = RiskConfig(
        **{f: getattr(state.settings.risk, f) for f in RiskConfig.__dataclass_fields__}
    )
    settings.market = str(body.get("market") or settings.market)
    settings.interval = str(body.get("interval") or settings.interval)

    if settings.interval not in INTERVALS:
        raise ApiError(f"지원하지 않는 봉 간격입니다: {settings.interval}")

    for key in ("cash", "fee_rate", "slippage", "rebalance_band"):
        if body.get(key) is not None:
            setattr(settings, key, _number(body[key], key))

    spec = body.get("spec")
    if spec:
        try:
            validate_spec(spec)
        except SpecError as exc:
            raise ApiError(str(exc)) from exc
        settings.strategy = "rule"
        settings.strategy_params = {"spec": spec}
    else:
        name = str(body.get("strategy") or settings.strategy)
        if name not in available():
            raise ApiError(f"모르는 전략입니다: {name}")
        settings.strategy = name
        settings.strategy_params = dict(body.get("params") or {})

    risk = dict(body.get("risk") or {})
    if risk:
        current = {f: getattr(settings.risk, f) for f in RiskConfig.__dataclass_fields__}
        merged = {**current, **{k: v for k, v in risk.items() if k in current and v is not None}}
        try:
            settings.risk = RiskConfig(**merged)
        except ValueError as exc:
            raise ApiError(f"리스크 설정이 올바르지 않습니다: {exc}") from exc

    if body.get("run_name"):
        settings.run_name = str(body["run_name"])
    else:
        mode = "live" if body.get("live") else "paper"
        settings.run_name = f"{mode}-{settings.market}"
    return settings


# ------------------------------------------------------------------ 보조
def _markets(state: AppState) -> list[dict[str, str]]:
    """원화 마켓 목록. 네트워크가 막히면 기본 목록으로 대체한다."""
    fallback = [
        {"market": "KRW-BTC", "name": "비트코인"},
        {"market": "KRW-ETH", "name": "이더리움"},
        {"market": "KRW-XRP", "name": "리플"},
        {"market": "KRW-SOL", "name": "솔라나"},
        {"market": "KRW-DOGE", "name": "도지코인"},
    ]
    try:
        # 화면을 띄우는 데 필요한 목록이므로 오래 매달리지 않는다.
        # 여기서 재시도 백오프를 타면 첫 화면이 30초 넘게 안 뜬다.
        rows = state.quick.get_markets()
    except Exception:
        log.info("마켓 목록 조회 실패 — 기본 목록을 씁니다")
        return fallback

    krw = [
        {"market": row["market"], "name": row.get("korean_name", row["market"])}
        for row in rows
        if str(row.get("market", "")).startswith("KRW-")
    ]
    return krw or fallback


def _ticker(state: AppState, market: str) -> dict[str, Any]:
    try:
        row = state.quick.get_ticker(market)
    except Exception as exc:
        raise ApiError(f"현재가를 가져오지 못했습니다: {exc}") from exc
    return {
        "market": market,
        "price": float(row.get("trade_price", 0)),
        "change_rate": float(row.get("signed_change_rate", 0)),
        "change_price": float(row.get("signed_change_price", 0)),
        "high": float(row.get("high_price", 0)),
        "low": float(row.get("low_price", 0)),
        "volume": float(row.get("acc_trade_volume_24h", 0)),
        "value": float(row.get("acc_trade_price_24h", 0)),
    }


def _candles_payload(state: AppState, market: str, interval: str, count: int):
    try:
        candles = state.quick.get_candles(market, interval, count=count)
    except Exception:
        candles = data_mod.load_csv(
            data_mod.cache_path(market, interval, state.settings.data_dir)
        )[-count:]
    if not candles:
        raise ApiError("봉 데이터가 없습니다. '데이터 받기'를 먼저 눌러주세요.")
    return _serialize_candles(candles)


def _serialize_candles(candles) -> list[dict[str, Any]]:
    return [
        {
            "ts": c.ts.isoformat(),
            "o": c.open,
            "h": c.high,
            "l": c.low,
            "c": c.close,
            "v": c.volume,
        }
        for c in candles
    ]


def _interval_label(key: str) -> str:
    labels = {
        "minute1": "1분", "minute3": "3분", "minute5": "5분", "minute10": "10분",
        "minute15": "15분", "minute30": "30분", "minute60": "1시간",
        "minute240": "4시간", "day": "일", "week": "주", "month": "월",
    }
    return labels.get(key, key)


def _strategy_catalog() -> list[dict[str, Any]]:
    out = []
    for name in available():
        cls = strategy_class(name)
        doc = (cls.__doc__ or "").strip().splitlines()
        out.append(
            {
                "name": name,
                "label": doc[0] if doc else name,
                "params": cls.defaults(),
            }
        )
    return out


def _parse_date(value: Any):
    if not value:
        return None
    try:
        return data_mod.parse_date(str(value))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc


def _number(value: Any, key: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ApiError(f"{key} 값이 숫자가 아닙니다: {value!r}") from None


def _is_finite(value: Any) -> bool:
    """JSON은 inf를 표현하지 못한다(손익비가 무한대일 수 있음)."""
    import math

    if isinstance(value, float):
        return math.isfinite(value)
    return True


# ------------------------------------------------------------------ HTTP
def make_handler(state: AppState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "btcbot"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self._api("GET", parsed.path, parse_qs(parsed.query), None)
            else:
                self._static(parsed.path)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._json({"error": "요청 형식이 잘못되었습니다"}, 400)
                return
            self._api("POST", parsed.path, {}, body)

        # ------------------------------------------------------------- 처리
        def _api(self, method: str, path: str, query: dict, body: Any) -> None:
            try:
                self._json(handle_api(state, method, path, query, body))
            except (ApiError, SpecError) as exc:
                status = getattr(exc, "status", 400)
                self._json({"error": str(exc)}, status)
            except (ValueError, KeyError, RuntimeError, FileNotFoundError) as exc:
                self._json({"error": str(exc)}, 400)
            except Exception as exc:  # pragma: no cover - 예상 못 한 오류
                log.error("처리 실패 %s %s: %s", method, path, traceback.format_exc())
                self._json({"error": f"서버 오류: {exc}"}, 500)

        def _static(self, path: str) -> None:
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (STATIC_DIR / name).resolve()
            # 경로 탈출 방지: static 폴더 밖은 절대 내보내지 않는다.
            if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
                self.send_error(404, "Not Found")
                return

            payload = target.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type", CONTENT_TYPES.get(target.suffix, "application/octet-stream")
            )
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, payload: Any, status: int = 200) -> None:
            raw = json.dumps(payload, ensure_ascii=False, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            with contextlib.suppress(BrokenPipeError):  # 사용자가 탭을 닫은 경우
                self.wfile.write(raw)

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("%s - %s", self.address_string(), fmt % args)

    return Handler


def create_server(settings: Settings, port: int = 8765) -> tuple[ThreadingHTTPServer, AppState]:
    state = AppState(settings)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    return server, state


def serve(settings: Settings, port: int = 8765, open_browser: bool = True) -> None:
    server, state = create_server(settings, port)
    url = f"http://127.0.0.1:{server.server_port}"

    print("=" * 58)
    print("  btcbot 웹 화면이 열렸습니다")
    print(f"  {url}")
    print("  브라우저가 안 열리면 위 주소를 직접 입력하세요.")
    print("  종료: 이 창에서 Ctrl+C")
    print("=" * 58)

    if open_browser:
        import webbrowser

        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료 중...")
    finally:
        if state.bot.running:
            print("봇을 멈추는 중입니다...")
            state.bot.stop()
        server.shutdown()
        server.server_close()


__all__ = ["ApiError", "AppState", "create_server", "handle_api", "serve"]
