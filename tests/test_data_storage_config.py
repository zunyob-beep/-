from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from btcbot import data as data_mod
from btcbot.config import Settings, load_dotenv
from btcbot.models import AccountState, Fill, Position, Side, TradeRecord
from btcbot.risk import RiskState
from btcbot.storage import Journal
from tests.conftest import series

TS = datetime(2024, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------- 데이터
def test_csv_round_trip_preserves_values(tmp_path):
    candles = series([100.0, 101.5, 99.25])
    path = data_mod.save_csv(tmp_path / "c.csv", candles)
    loaded = data_mod.load_csv(path)
    assert loaded == candles


def test_csv_preserves_float_precision(tmp_path):
    from btcbot.models import Candle

    original = Candle("KRW-BTC", TS, 0.123456789, 1.1, 0.9, 1.0000000001, 3.14159265)
    path = data_mod.save_csv(tmp_path / "p.csv", [original])
    assert data_mod.load_csv(path)[0] == original


def test_load_csv_missing_file_is_empty(tmp_path):
    assert data_mod.load_csv(tmp_path / "nope.csv") == []


def test_load_csv_rejects_wrong_header(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        data_mod.load_csv(path)


def test_merge_dedupes_by_timestamp():
    first = series([100.0, 101.0])
    second = series([200.0, 201.0])  # 같은 시각, 다른 값
    merged = data_mod.merge(first, second)
    assert len(merged) == 2
    assert merged[0].close == 200.0  # 뒤쪽이 우선


def test_parse_date_accepts_common_formats():
    assert data_mod.parse_date("2024-01-01") == TS
    assert data_mod.parse_date("2024-01-01T00:00:00") == TS
    with pytest.raises(ValueError):
        data_mod.parse_date("01/01/2024")


class StubClient:
    """`to`를 따라 과거로 거슬러 가는 업비트 페이지네이션을 흉내낸다."""

    def __init__(self, candles, page=200):
        self.candles = sorted(candles, key=lambda c: c.ts)
        self.page = page
        self.calls = 0

    def get_candles(self, market, interval="day", count=200, to=None):
        self.calls += 1
        pool = [c for c in self.candles if to is None or c.ts <= to]
        return pool[-min(count, self.page) :]


def test_fetch_history_paginates_backwards():
    candles = series([100.0 + i for i in range(500)])
    client = StubClient(candles, page=200)
    fetched = data_mod.fetch_history(client, "KRW-BTC", "day", start=candles[0].ts)
    assert len(fetched) == 500
    assert client.calls >= 3
    assert [c.ts for c in fetched] == sorted(c.ts for c in fetched)


def test_fetch_history_stops_when_exhausted():
    """더 줄 데이터가 없으면 무한 루프에 빠지지 않아야 한다."""
    candles = series([100.0 + i for i in range(10)])
    client = StubClient(candles)
    fetched = data_mod.fetch_history(
        client, "KRW-BTC", "day", start=TS - timedelta(days=3650)
    )
    assert len(fetched) == 10
    assert client.calls < 10


def test_load_or_fetch_uses_cache_without_calling_api(tmp_path):
    candles = series([100.0 + i for i in range(20)])
    data_mod.save_csv(data_mod.cache_path("KRW-BTC", "day", tmp_path), candles)

    client = StubClient([])
    result = data_mod.load_or_fetch(
        client, "KRW-BTC", "day",
        start=candles[0].ts, end=candles[-1].ts, directory=tmp_path,
    )
    assert len(result) == 20
    assert client.calls == 0


def test_load_or_fetch_refresh_ignores_cache(tmp_path):
    candles = series([100.0 + i for i in range(20)])
    data_mod.save_csv(data_mod.cache_path("KRW-BTC", "day", tmp_path), candles)

    client = StubClient(candles)
    data_mod.load_or_fetch(client, "KRW-BTC", "day", directory=tmp_path, refresh=True)
    assert client.calls > 0


# ---------------------------------------------------------------------- 저널
def test_journal_appends_fills(tmp_path):
    journal = Journal(tmp_path, "run")
    state = AccountState(cash=100.0, position=Position("KRW-BTC", 1.0, 90.0), price=100.0)
    for i in range(3):
        journal.write_fill(
            Fill("KRW-BTC", Side.BUY, price=100 + i, volume=1, fee=0.5, ts=TS, reason="r"), state
        )
    rows = journal.read_fills()
    assert len(rows) == 3
    assert rows[0]["side"] == "buy"
    assert rows[0]["equity"] == pytest.approx(200.0)


def test_journal_state_round_trip(tmp_path):
    journal = Journal(tmp_path, "run")
    risk_state = RiskState(day="2024-01-01", peak_equity=123.0, halted=True, halt_reason="x")
    account = AccountState(cash=50.0, position=Position("KRW-BTC", 2.0, 10.0), price=20.0)

    journal.save_state(risk_state, account)
    restored_risk, restored_account = journal.load_state()
    assert restored_risk == risk_state
    assert restored_account["cash"] == 50.0
    assert restored_account["volume"] == 2.0


def test_journal_state_write_is_atomic(tmp_path):
    """임시 파일이 남거나 본 파일이 반쯤 쓰인 상태로 남으면 안 된다."""
    journal = Journal(tmp_path, "run")
    account = AccountState(cash=1.0, position=Position("KRW-BTC"), price=1.0)
    journal.save_state(RiskState(), account)
    assert journal.state_path.exists()
    assert not list(journal.dir.glob("*.tmp"))
    json.loads(journal.state_path.read_text(encoding="utf-8"))


def test_journal_survives_corrupt_state(tmp_path):
    journal = Journal(tmp_path, "run")
    journal.state_path.write_text("{깨진 json", encoding="utf-8")
    assert journal.load_state() is None


def test_journal_skips_corrupt_lines(tmp_path):
    journal = Journal(tmp_path, "run")
    journal.fills_path.write_text('{"ts": "x"}\n깨진줄\n{"ts": "y"}\n', encoding="utf-8")
    assert len(journal.read_fills()) == 2


def test_journal_writes_trades(tmp_path):
    journal = Journal(tmp_path, "run")
    journal.write_trade(
        TradeRecord("KRW-BTC", TS, 100.0, TS + timedelta(days=1), 110.0, 1.0, 10.0, 0.1, "익절")
    )
    rows = journal.read_trades()
    assert rows[0]["pnl"] == 10.0
    assert rows[0]["reason"] == "익절"


def test_journal_missing_state_returns_none(tmp_path):
    assert Journal(tmp_path, "fresh").load_state() is None


# ---------------------------------------------------------------------- 설정
def test_settings_defaults():
    settings = Settings.load()
    assert settings.market == "KRW-BTC"
    assert settings.risk.max_position_weight == 1.0


def test_settings_from_json_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "market": "KRW-ETH",
                "interval": "minute30",
                "cash": 500_000,
                "risk": {"stop_loss_pct": 0.05, "max_drawdown_pct": 0.2},
            }
        ),
        encoding="utf-8",
    )
    settings = Settings.load(path)
    assert settings.market == "KRW-ETH"
    assert settings.interval == "minute30"
    assert settings.cash == 500_000
    assert settings.risk.stop_loss_pct == 0.05


def test_cli_overrides_beat_config_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"market": "KRW-ETH"}), encoding="utf-8")
    settings = Settings.load(path, market="KRW-BTC")
    assert settings.market == "KRW-BTC"


def test_none_overrides_are_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"market": "KRW-ETH"}), encoding="utf-8")
    assert Settings.load(path, market=None).market == "KRW-ETH"


def test_unknown_config_keys_are_dropped(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"market": "KRW-BTC", "정체불명": 1}), encoding="utf-8")
    assert Settings.load(path).market == "KRW-BTC"  # 예외 없이 무시


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Settings.load(tmp_path / "없음.json")


def test_api_keys_come_from_env(monkeypatch):
    monkeypatch.delenv("UPBIT_ACCESS_KEY", raising=False)
    monkeypatch.delenv("UPBIT_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="API 키"):
        Settings.require_api_keys()

    monkeypatch.setenv("UPBIT_ACCESS_KEY", "a")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "b")
    assert Settings.require_api_keys() == ("a", "b")


def test_dotenv_does_not_override_existing_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('UPBIT_ACCESS_KEY="from-file"\nOTHER=42\n# 주석\n', encoding="utf-8")
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "from-shell")
    monkeypatch.delenv("OTHER", raising=False)

    load_dotenv(env)
    import os

    assert os.environ["UPBIT_ACCESS_KEY"] == "from-shell"  # 기존 값 유지
    assert os.environ["OTHER"] == "42"


def test_settings_never_reads_keys_from_config_file(tmp_path):
    """설정 파일에 키를 적어도 무시된다 — 커밋 사고를 막기 위한 규칙."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"market": "KRW-BTC", "access_key": "leaked", "secret_key": "leaked"}),
        encoding="utf-8",
    )
    settings = Settings.load(path)
    assert not hasattr(settings, "access_key")
    assert "leaked" not in json.dumps(settings.to_dict(), default=str)
