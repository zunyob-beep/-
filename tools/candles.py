"""업비트에서 봉을 받아 파일로 적어 둔다 — **서버에서, 미리.**

왜 이게 필요한가
---------------
브라우저에서 업비트를 직접 부르는 것은 믿을 수 없다. 업비트의 REST API는
서버끼리 쓰라고 만든 것이라, 거절할 때(한도 초과 같은) 돌려주는 응답에는
브라우저가 요구하는 허용 표시(CORS 헤더)가 없다. 그러면 브라우저는 그 답을
**읽지도 못하고** 그냥 "실패"로 처리한다 — 인터넷이 끊긴 것과 구분이 안 된다.

여기에 더해 한도는 **주소(IP) 단위**로 걸린다. 휴대폰 데이터(5G)는 수백 명이
한 주소를 나눠 쓰므로, 내가 아무것도 안 해도 남이 쓴 몫 때문에 막힌다.

그래서 공개 우회 서버로 돌아가게 만들었는데, 그건 문제를 옮긴 것뿐이었다.
우회 서버의 주소도 수천 명이 같이 쓰므로 똑같이 막히고, 게다가 느리고,
어느 날 유료로 바뀌면(실제로 그랬다) 그날부터 앱이 죽는다.

그러니 **브라우저가 업비트를 안 부르게 한다.** 이 스크립트가 깃허브 액션
위에서 — 서버에서, CORS도 없고 한도도 넉넉한 자리에서 — 미리 받아 파일로
적어 둔다. 앱은 그 파일 하나만 내려받으면 된다. 요청 한 번, 몇백 KB,
CORS 없음, 한도 없음.

파일 모양
--------
한 종목에 한 파일. 자리를 아끼려고 봉 하나를 배열 하나로 적는다::

    {"market": "KRW-BTC", "timeframe": "minute1", "step": 60,
     "made": 1788330000, "days": 14,
     "rows": [[ts, 시가, 고가, 저가, 종가, 거래량], ...]}

`rows`는 **오래된 것부터**다. 시각은 유닉스 초.

쓰는 법::

    python tools/candles.py --out web/data --days 14
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from patternscan.models import Candle
from patternscan.upbit import UpbitClient, UpbitError

#: 미리 받아 둘 종목. 앱의 MARKETS와 같아야 한다 (web/core/models.js).
MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]

#: 며칠치를 담을지. 1분봉은 하루가 1,440개다.
#:
#: 14일이면 20,160개, 파일 하나가 1MB 남짓이고 압축하면 300KB쯤 된다.
#: 앱의 기본값(7일)을 넉넉히 덮으면서 5G에서도 몇 초면 받아진다.
DEFAULT_DAYS = 14

STEP = 60


def _round(value: float) -> float:
    """소수점 끝의 부동소수 찌꺼기를 자른다.

    0.30000000000000004 같은 것이 파일에 그대로 들어가면 자리만 먹는다.
    가격은 원 단위라 소수 둘이면 충분하고, 거래량은 여덟 자리까지 남긴다.
    """
    return round(value, 8)


def _row(candle: Candle) -> list[float]:
    return [
        int(candle.ts.timestamp()),
        _round(candle.open),
        _round(candle.high),
        _round(candle.low),
        _round(candle.close),
        _round(candle.volume),
    ]


def read_existing(path: Path) -> dict[int, list[float]]:
    """이미 적어 둔 것을 읽는다. 없거나 깨졌으면 빈 것으로 친다.

    **매번 14일치를 처음부터 받지 않기 위한 것이다.** 20분마다 도는데
    그때마다 101쪽씩 받으면 업비트에 폐가 되고, 우리도 느리다. 지난번
    파일에 이어 붙이면 보통 한 쪽이면 끝난다.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return {}
    out: dict[int, list[float]] = {}
    for row in rows:
        if isinstance(row, list) and len(row) == 6 and all(isinstance(v, (int, float)) for v in row):
            out[int(row[0])] = [int(row[0]), *(float(v) for v in row[1:])]
    return out


def fetch(client: UpbitClient, market: str, have: dict[int, list[float]], days: int,
          say: Any = print) -> dict[int, list[float]]:
    """모자란 만큼만 받아서 합친다."""
    now = datetime.now(timezone.utc)
    oldest_wanted = now - timedelta(days=days)
    want = days * 24 * 60

    # 가진 것 중 오래된 것은 버린다. 안 그러면 파일이 끝없이 자란다.
    merged = {ts: row for ts, row in have.items() if ts >= oldest_wanted.timestamp()}

    # 어디까지 받아야 하는가. 가진 게 있으면 그 뒤로만, 없으면 전부.
    stop_at = None
    if merged:
        newest = max(merged)
        # 마지막 몇 개는 다시 받는다 — 그 분이 끝나기 전에 받은 봉은
        # 아직 확정된 값이 아니다.
        stop_at = datetime.fromtimestamp(newest - STEP * 3, tz=timezone.utc)
        need = int((now.timestamp() - newest) / STEP) + 5
    else:
        need = want

    need = max(1, min(need, want))
    say(f"  {market}: 가진 것 {len(merged):,}개, 받을 것 {need:,}개")

    candles = client.collect(market, "minute1", need, stop_at=stop_at)
    for candle in candles:
        row = _row(candle)
        merged[row[0]] = row

    # 다시 한 번 자른다 — 방금 받은 것 때문에 늘어났을 수 있다.
    merged = {ts: row for ts, row in merged.items() if ts >= oldest_wanted.timestamp()}
    return merged


def write(path: Path, market: str, rows: dict[int, list[float]], days: int) -> int:
    ordered = [rows[ts] for ts in sorted(rows)]
    payload = {
        "market": market,
        "timeframe": "minute1",
        "step": STEP,
        "days": days,
        "made": int(datetime.now(timezone.utc).timestamp()),
        "rows": ordered,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # 줄바꿈 없이 붙여 쓴다. 2만 줄짜리 파일에서 들여쓰기는 자리만 먹는다.
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return len(ordered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="업비트 봉을 미리 받아 파일로 적는다")
    parser.add_argument("--out", default="data", help="파일을 적을 폴더")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="며칠치를 담을지")
    parser.add_argument("--markets", nargs="*", default=MARKETS, help="종목")
    args = parser.parse_args(argv)

    out = Path(args.out)
    client = UpbitClient(per_second=5)
    failed: list[str] = []

    for market in args.markets:
        path = out / f"{market}.min1.json"
        try:
            rows = fetch(client, market, read_existing(path), args.days)
        except (UpbitError, OSError) as error:
            # **한 종목이 실패해도 나머지는 적는다.** 전부 실패했을 때만
            # 판을 실패로 끝낸다 — 그래야 옛 파일이 그대로 남는다.
            print(f"  {market}: 실패 — {error}", file=sys.stderr)
            failed.append(market)
            continue
        if not rows:
            failed.append(market)
            continue
        count = write(path, market, rows, args.days)
        first = datetime.fromtimestamp(min(rows), tz=timezone.utc)
        last = datetime.fromtimestamp(max(rows), tz=timezone.utc)
        print(f"  {market}: {count:,}개 적었습니다 ({first:%m-%d %H:%M} → {last:%m-%d %H:%M} UTC)")

    if len(failed) == len(args.markets):
        print("전부 실패했습니다. 옛 파일을 그대로 둡니다.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
