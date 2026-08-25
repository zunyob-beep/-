"""거래 기록과 상태 저장.

실거래 봇은 언젠가 반드시 죽는다(배포, OOM, 네트워크). 죽고 나서 다시
켰을 때 "일일 손실 한도를 이미 썼는지", "트레일링 고점이 얼마였는지"를
기억하지 못하면 리스크 규칙이 매번 초기화된다. 그래서 체결이 일어날
때마다 상태를 디스크에 남긴다.

기록은 JSONL이다 — 한 줄이 한 사건이라 이어쓰기가 안전하고, 중간에
프로세스가 죽어도 앞부분은 그대로 읽힌다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import AccountState, Fill, TradeRecord
from .risk import RiskState

log = logging.getLogger(__name__)


class Journal:
    def __init__(self, directory: Path | str = "runs", name: str = "default") -> None:
        self.dir = Path(directory) / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fills_path = self.dir / "fills.jsonl"
        self.trades_path = self.dir / "trades.jsonl"
        self.state_path = self.dir / "state.json"

    # ------------------------------------------------------------------ 쓰기
    def write_fill(self, fill: Fill, state: AccountState | None = None) -> None:
        record: dict[str, Any] = {
            "ts": fill.ts.isoformat(),
            "market": fill.market,
            "side": fill.side.value,
            "price": fill.price,
            "volume": fill.volume,
            "fee": fill.fee,
            "reason": fill.reason,
            "order_id": fill.order_id,
        }
        if state is not None:
            record |= {"cash": state.cash, "equity": state.equity, "weight": state.weight}
        self._append(self.fills_path, record)

    def write_trade(self, trade: TradeRecord) -> None:
        record = asdict(trade)
        record["entry_ts"] = trade.entry_ts.isoformat()
        record["exit_ts"] = trade.exit_ts.isoformat()
        self._append(self.trades_path, record)

    def save_state(self, risk_state: RiskState, account: AccountState) -> None:
        payload = {
            "saved_at": datetime.now().astimezone().isoformat(),
            "risk": risk_state.to_dict(),
            "account": {
                "cash": account.cash,
                "volume": account.position.volume,
                "avg_price": account.position.avg_price,
                "price": account.price,
                "equity": account.equity,
            },
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # 원자적 교체 — 쓰는 도중 죽어도 기존 상태 파일이 깨지지 않는다
        tmp.replace(self.state_path)

    # ------------------------------------------------------------------ 읽기
    def load_state(self) -> tuple[RiskState, dict[str, Any]] | None:
        if not self.state_path.exists():
            return None
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("상태 파일을 읽지 못했습니다 (%s) — 새로 시작합니다", exc)
            return None
        return RiskState.from_dict(payload.get("risk", {})), payload.get("account", {})

    def read_fills(self) -> list[dict[str, Any]]:
        return self._read(self.fills_path)

    def read_trades(self) -> list[dict[str, Any]]:
        return self._read(self.trades_path)

    def _append(self, path: Path, record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _read(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("%s: 깨진 줄을 건너뜁니다", path.name)
        return rows
