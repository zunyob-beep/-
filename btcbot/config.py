"""설정 로딩.

우선순위: CLI 인자 > 환경변수 > 설정 파일 > 기본값.

API 키는 **설정 파일에 두지 않는다**. 환경변수나 .env로만 받는다 —
설정 파일은 커밋되기 쉽고, 커밋된 키는 대개 늦게 발견된다.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .exchange.base import DEFAULT_FEE_RATE, MIN_ORDER_KRW
from .risk import RiskConfig

log = logging.getLogger(__name__)

ENV_ACCESS_KEY = "UPBIT_ACCESS_KEY"
ENV_SECRET_KEY = "UPBIT_SECRET_KEY"


@dataclass
class Settings:
    market: str = "KRW-BTC"
    interval: str = "minute60"
    strategy: str = "vb"
    strategy_params: dict[str, Any] = field(default_factory=dict)

    cash: float = 1_000_000.0
    fee_rate: float = DEFAULT_FEE_RATE
    slippage: float = 0.0005
    min_order_krw: float = MIN_ORDER_KRW
    rebalance_band: float = 0.05

    risk: RiskConfig = field(default_factory=RiskConfig)

    run_name: str = "default"
    runs_dir: str = "runs"
    data_dir: str = "data"
    log_level: str = "INFO"
    verbose: bool = False

    #: 알림 웹훅 (선택). 환경변수 BTCBOT_WEBHOOK_URL로도 설정 가능
    webhook_url: str | None = None

    @classmethod
    def load(cls, path: str | Path | None = None, **overrides: Any) -> Settings:
        data: dict[str, Any] = {}
        if path:
            data = _read_file(Path(path))

        risk_data = data.pop("risk", {}) or {}
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            log.warning("설정 파일에 모르는 키가 있습니다: %s", sorted(unknown))
        data = {k: v for k, v in data.items() if k in known}

        for key, value in overrides.items():
            if value is not None and key in known:
                data[key] = value

        settings = cls(**data)
        settings.risk = RiskConfig(**_filter(RiskConfig, risk_data))
        settings.webhook_url = settings.webhook_url or os.getenv("BTCBOT_WEBHOOK_URL")
        return settings

    # ------------------------------------------------------------------ 키
    @staticmethod
    def api_keys() -> tuple[str | None, str | None]:
        return os.getenv(ENV_ACCESS_KEY), os.getenv(ENV_SECRET_KEY)

    @staticmethod
    def require_api_keys() -> tuple[str, str]:
        access, secret = Settings.api_keys()
        if not access or not secret:
            raise RuntimeError(
                f"실거래에는 API 키가 필요합니다. {ENV_ACCESS_KEY}와 {ENV_SECRET_KEY}를 "
                "환경변수로 설정하세요 (.env.example 참고)."
            )
        return access, secret

    def to_dict(self) -> dict[str, Any]:
        out = {f.name: getattr(self, f.name) for f in fields(self)}
        out["risk"] = {f.name: getattr(self.risk, f.name) for f in fields(self.risk)}
        return out


def _filter(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        log.warning("%s에 모르는 키가 있습니다: %s", cls.__name__, sorted(unknown))
    return {k: v for k, v in data.items() if k in known}


def _read_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"설정 파일이 없습니다: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        return _read_yaml(text, path)
    return json.loads(text)


def _read_yaml(text: str, path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError(
            f"{path}를 읽으려면 PyYAML이 필요합니다 (pip install pyyaml). "
            "또는 같은 내용을 .json으로 저장하세요."
        ) from None
    return yaml.safe_load(text) or {}


def load_dotenv(path: str | Path = ".env") -> int:
    """아주 단순한 .env 로더. 이미 설정된 환경변수는 덮어쓰지 않는다."""
    path = Path(path)
    if not path.exists():
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded
