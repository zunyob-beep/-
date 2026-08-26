"""막혔을 때 어디가 문제인지 알려준다.

"안 돼요"만큼 고치기 어려운 말이 없다. 파이썬이 낮은 건지, 업비트가 막힌
건지, 시세를 아직 안 받은 건지에 따라 할 일이 전혀 다른데, 화면에는 똑같이
아무것도 안 나온다. 그래서 하나씩 짚어 준다.
"""

from __future__ import annotations

import logging
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .data import cache_path, load
from .models import TIMEFRAMES, timeframe_label
from .upbit import UpbitClient, UpbitError

OK = "✅"
WARN = "⚠️ "
BAD = "❌"


@dataclass
class Check:
    mark: str
    title: str
    detail: str
    fix: str = ""

    def render(self) -> str:
        lines = [f"  {self.mark} {self.title}", f"       {self.detail}"]
        if self.fix:
            lines.append(f"       → {self.fix}")
        return "\n".join(lines)


def check_python() -> Check:
    version = ".".join(str(x) for x in sys.version_info[:3])
    # ruff는 "3.10 미만은 어차피 못 돌아오니 죽은 코드"라고 하지만, 이 파일은
    # 3.9에서도 파싱된다(from __future__ import annotations 덕분에). 낮은 버전으로
    # 들어온 사람에게 이유를 말해 주는 게 이 함수의 존재 이유다.
    if sys.version_info < (3, 10):  # noqa: UP036
        return Check(
            BAD, f"파이썬 {version}", "3.10 이상이 필요합니다.",
            "python.org에서 최신 버전을 설치한 뒤 다시 실행하세요.",
        )
    return Check(OK, f"파이썬 {version}", f"{platform.system()} · 3.10 이상 확인")


def check_numpy() -> Check:
    try:
        import numpy
    except ImportError:
        return Check(
            BAD, "numpy 없음", "계산에 꼭 필요합니다.",
            "pip install -e . 을 실행하세요.",
        )
    return Check(OK, f"numpy {numpy.__version__}", "설치됨")


def _because(exc: BaseException) -> str:
    """진짜 원인까지 따라간다.

    UpbitError는 "재시도 1회 실패"까지만 말한다. 정작 알아야 하는 건 그 밑에
    깔린 이유 — 프록시가 막았는지, 이름을 못 찾았는지, 업비트가 거절했는지 —
    인데, 그게 __cause__에 들어 있다.
    """
    parts = [str(exc)]
    seen = {id(exc)}
    cause = exc.__cause__
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        parts.append(f"{type(cause).__name__}: {cause}")
        cause = cause.__cause__
    return " ← ".join(p for p in parts if p)


def check_upbit(market: str = "KRW-BTC") -> Check:
    """실제로 한 번 불러 본다. 되는지 안 되는지가 전부이므로."""
    client = UpbitClient(max_retries=1, timeout=8.0)
    started = time.time()
    # 재시도 경고까지 찍히면 진단 화면이 지저분해진다. 결과는 아래에서 말한다.
    upbit_log = logging.getLogger("patternscan.upbit")
    before = upbit_log.level
    upbit_log.setLevel(logging.ERROR)
    try:
        candles = client.get_candles(market, "minute1", 1)
    except UpbitError as exc:
        return Check(
            BAD, "업비트에 닿지 않습니다", _because(exc)[:240],
            "회사·학교 네트워크나 VPN이 막고 있을 수 있습니다. "
            "다른 네트워크에서 시도하거나, 이미 받아둔 시세로 돌리세요.",
        )
    except Exception as exc:  # 네트워크 계층에서 나는 예외까지
        return Check(
            BAD, "업비트에 닿지 않습니다", f"{type(exc).__name__}: {_because(exc)}"[:240],
            "인터넷 연결을 확인하세요.",
        )
    finally:
        upbit_log.setLevel(before)
    if not candles:
        return Check(
            WARN, "업비트가 빈 응답을 줬습니다", f"{market}이 없는 종목일 수 있습니다.",
            "종목 코드를 확인하세요 (예: KRW-BTC, KRW-ETH).",
        )
    took = time.time() - started
    last = candles[-1]
    return Check(
        OK, "업비트 연결됨",
        f"{market} 최근 1분봉 {last.kst:%Y-%m-%d %H:%M} KST · "
        f"{last.close:,.0f}원 · 응답 {took:.1f}초",
    )


def check_cache(market: str, directory: str) -> list[Check]:
    out: list[Check] = []
    for timeframe in TIMEFRAMES:
        path = cache_path(market, timeframe, directory)
        label = timeframe_label(timeframe)
        if not Path(path).exists():
            out.append(Check(WARN, f"{label} 시세 없음", f"{path} 가 아직 없습니다.",
                             "python -m patternscan fetch 를 실행하세요."))
            continue
        try:
            candles = load(path)
        except ValueError as exc:
            out.append(Check(BAD, f"{label} 파일이 깨졌습니다", str(exc)[:120],
                             f"{path} 를 지우고 다시 받으세요."))
            continue
        if not candles:
            out.append(Check(WARN, f"{label} 비어 있음", f"{path} 에 봉이 없습니다.",
                             "python -m patternscan fetch 를 실행하세요."))
            continue
        first, last = candles[0], candles[-1]
        days = (last.ts - first.ts).days
        enough = len(candles) >= 2000
        out.append(Check(
            OK if enough else WARN,
            f"{label} 봉 {len(candles):,}개",
            f"{first.kst:%Y-%m-%d} ~ {last.kst:%Y-%m-%d %H:%M} KST ({days}일치)",
            "" if enough else "직전 180개를 보려면 최소 2,000개는 있어야 합니다. 더 받으세요.",
        ))
    return out


def run(market: str = "KRW-BTC", directory: str = "data") -> int:
    """전부 점검하고 사람이 읽을 수 있게 찍는다. 문제가 있으면 1을 돌려준다."""
    checks = [check_python(), check_numpy(), check_upbit(market)]
    checks += check_cache(market, directory)

    print()
    print(f"  자가진단 · 종목 {market} · 캐시 폴더 {directory}")
    print("  " + "─" * 64)
    for check in checks:
        print(check.render())
    print()

    broken = [c for c in checks if c.mark == BAD]
    warned = [c for c in checks if c.mark == WARN]
    if broken:
        print(f"  {BAD} 먼저 고쳐야 할 것이 {len(broken)}개 있습니다 (위의 → 를 따라가세요).")
        return 1
    if warned:
        print(f"  {WARN}당장 돌릴 수는 있지만 {len(warned)}개는 챙기는 게 좋습니다.")
        return 0
    print("  모두 정상입니다. `python -m patternscan ui` 로 화면을 여세요.")
    return 0
