"""업비트에서 봉을 받아 파일로 적어 둔다 — **서버에서, 미리.**

왜 이게 필요한가
---------------
브라우저에서 업비트를 직접 부르면 **분당 6번**밖에 못 받는다. 업비트가
브라우저 요청(Origin 헤더가 붙은 요청)을 `origin`이라는 별도 한도 묶음에
넣기 때문이다 — 서버에서 부르면 같은 주소가 분당 600번이다. 100배 차이고,
헤더에 대놓고 적혀 있다::

    Origin 없이   → remaining-req: group=candles; min=600; sec=9
    Origin 붙여서 → remaining-req: group=origin;  min=6;   sec=0

분당 6번이면 7일치(51번)를 받는 데 8분, 4년치(10,512번)는 **29시간**이다.
브라우저에서 받는 건 애초에 될 일이 아니었다.

그래서 여기서 받는다. 서버에는 CORS도 없고 한도도 100배 넉넉하다.

무엇을 어디에 적는가
------------------
바뀌는 것과 안 바뀌는 것을 나눈다. 이게 이 파일의 설계 전부다.

  ``tail/<종목>.json``     최근 2일. **10분마다** 새로 적는다.
  ``recent/<종목>.json``   최근 31일. **한 시간에 한 번쯤** 새로 적는다.
  ``<종목>/<YYYY-MM>.json`` 지나간 달. **한 번 적고 다시 안 건드린다.**

가운데를 '이번 달'이 아니라 **최근 31일**로 잡은 이유가 있다. 달로 자르면
1일 0시에 그 파일이 몇 줄로 줄어드는데, 지난달 조각은 과거 채우기가 돌기
전까지 아직 없다. 그 사이에 30일치를 고른 사람은 이틀치밖에 못 받는다 —
한 달에 한 번, 몇 시간짜리 구멍이다. 31일 창으로 두면 지난달을 늘 덮으므로
그 구멍이 아예 안 생긴다. 지나간 달 조각과 겹치는 건 아무 해가 없다
(같은 봉은 같은 자리에 저장된다).

앞의 둘은 `data` 브랜치에 덮어쓴다(force push). 늘 커밋 하나만 남으므로
저장소가 안 불어난다. 지나간 달은 `history` 브랜치에 쌓는다 — 다시 안
바뀌므로 쌓여도 각 파일이 딱 한 번씩만 올라간다.

앱은 **고른 기간만큼만** 내려받는다. 7일이면 꼬리 하나(90KB), 1년이면
열두 조각, 4년이면 마흔여덟 조각. 한 번 받은 조각은 그 기기에 남으므로
다시 안 받는다.

쓰는 법::

    python -m tools.candles --mode tail    --out data
    python -m tools.candles --mode history --out history --years 4
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
from tools.pack import pack, unpack

#: 미리 받아 둘 종목. 앱의 MARKETS와 같아야 한다 (web/core/models.js).
MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]

#: 꼬리에 담을 날 수. 10분마다 새로 적으므로 작아야 한다.
TAIL_DAYS = 2

#: 가운데 파일이 담는 날 수. 지난달을 늘 덮도록 31일로 둔다.
RECENT_DAYS = 31

#: 가운데 파일을 이보다 오래 안 건드렸으면 새로 적는다 (초).
RECENT_STALE = 3600

#: 몇 년치까지 거슬러 올라가는가.
DEFAULT_YEARS = 4

STEP = 60
MINUTE = 60


def _row(candle: Candle) -> list[float]:
    return [
        int(candle.ts.timestamp()),
        float(candle.open), float(candle.high),
        float(candle.low), float(candle.close), float(candle.volume),
    ]


def read_rows(path: Path) -> dict[int, list[float]]:
    """적어 둔 파일을 되읽는다. 없거나 깨졌으면 빈 것으로 친다."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    try:
        rows = unpack(payload)
    except (KeyError, TypeError, ValueError, IndexError):
        return {}
    return {int(r[0]): r for r in rows}


def write_rows(path: Path, market: str, rows: dict[int, list[float]]) -> int:
    ordered = [rows[ts] for ts in sorted(rows)]
    made = int(datetime.now(timezone.utc).timestamp())
    path.parent.mkdir(parents=True, exist_ok=True)
    # 줄바꿈도 공백도 없이 붙여 쓴다. 2만 줄짜리 파일에서 그건 자리만 먹는다.
    path.write_text(
        json.dumps(pack(market, ordered, STEP, made), separators=(",", ":")),
        encoding="utf-8",
    )
    return len(ordered)


def month_range(when: datetime) -> tuple[datetime, datetime]:
    """그 달의 시작과 다음 달의 시작 (UTC)."""
    start = when.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    following = (start + timedelta(days=32)).replace(day=1)
    return start, following


def months_back(years: int, now: datetime) -> list[str]:
    """이번 달을 빼고, 지나간 달을 **새것부터** 늘어놓는다."""
    out = []
    cursor, _ = month_range(now)
    for _ in range(years * 12):
        cursor = (cursor - timedelta(days=1)).replace(day=1)
        out.append(f"{cursor:%Y-%m}")
    return out


def fetch_span(client: UpbitClient, market: str, start: datetime, end: datetime,
               have: dict[int, list[float]] | None = None) -> dict[int, list[float]]:
    """`start` 이상 `end` 미만을 받아서 `have`에 합친다.

    이미 가진 것이 있으면 **그 뒤로만** 받는다. 10분마다 도는데 매번
    처음부터 받으면 업비트에 폐가 되고 우리도 느리다.
    """
    merged = dict(have or {})
    inside = [ts for ts in merged if start.timestamp() <= ts < end.timestamp()]
    merged = {ts: merged[ts] for ts in inside}

    stop_at = start
    if merged:
        # 마지막 몇 개는 다시 받는다 — 그 분이 끝나기 전에 받은 봉은 아직
        # 확정된 값이 아니다.
        stop_at = max(start, datetime.fromtimestamp(max(merged) - STEP * 3, tz=timezone.utc))

    need = int((end.timestamp() - stop_at.timestamp()) / STEP) + 5
    if need <= 0:
        return merged

    got = client.collect(market, "minute1", need, end=end, stop_at=stop_at)
    for candle in got:
        ts = int(candle.ts.timestamp())
        if start.timestamp() <= ts < end.timestamp():
            merged[ts] = _row(candle)
    return merged


# ------------------------------------------------------------ 꼬리와 이번 달
def do_tail(client: UpbitClient, out: Path, markets: list[str]) -> list[str]:
    """최근 2일과 최근 31일. **자주 바뀌는 쪽**이다."""
    now = datetime.now(timezone.utc)
    failed = []

    for market in markets:
        tail_path = out / "tail" / f"{market}.json"
        recent_path = out / "recent" / f"{market}.json"
        try:
            since = now - timedelta(days=TAIL_DAYS)
            rows = fetch_span(client, market, since, now, read_rows(tail_path))
            count = write_rows(tail_path, market, rows)
            print(f"  {market} 꼬리: {count:,}개 ({since:%m-%d %H:%M} → 지금)")

            # **가운데 파일은 자주 안 건드린다.** 1.2MB짜리를 10분마다 새로
            # 올리면 얻는 것 없이 오르내리는 양만 커진다. 한 시간쯤 묵었을
            # 때만 손댄다.
            have = read_rows(recent_path)
            fresh = max(have) if have else 0
            if now.timestamp() - fresh < RECENT_STALE:
                print(f"  {market} 최근 31일: 아직 새것입니다 ({len(have):,}개)")
                continue
            since = now - timedelta(days=RECENT_DAYS)
            rows = fetch_span(client, market, since, now, have)
            count = write_rows(recent_path, market, rows)
            print(f"  {market} 최근 31일: {count:,}개 ({since:%m-%d} → 지금)")
        except (UpbitError, OSError) as error:
            print(f"  {market}: 실패 — {error}", file=sys.stderr)
            failed.append(market)
    return failed


# ---------------------------------------------------------------- 지나간 달
def do_history(client: UpbitClient, out: Path, markets: list[str],
               years: int, budget: int, now: datetime | None = None) -> tuple[int, list[str]]:
    """지나간 달을 **새것부터** 채운다. 한 번 적은 달은 다시 안 건드린다.

    `(새로 적은 조각 수, 실패 목록)`을 돌려준다. 실패를 세는 것만으로는
    부족하다 — 100조각 중 하나가 실패해도 나머지 99를 올려야 하기 때문이다.
    """
    now = now or datetime.now(timezone.utc)
    wanted = months_back(years, now)
    failed: list[str] = []
    # 상장 전 구간에 닿은 종목. 더 내려가 봐야 빈 달만 나온다.
    done: set[str] = set()
    made = 0

    for name in wanted:
        if made >= budget:
            print(f"이번 판은 {budget}조각까지입니다. 남은 달은 다음 판에서 채웁니다.")
            break
        if len(done) == len(markets):
            print("모든 종목이 상장 전 구간에 닿았습니다. 여기까지입니다.")
            break
        start = datetime.strptime(name, "%Y-%m").replace(tzinfo=timezone.utc)
        _, end = month_range(start)
        for market in markets:
            if market in done:
                continue
            path = out / market / f"{name}.json"
            if path.is_file():
                continue
            try:
                rows = fetch_span(client, market, start, end)
            except (UpbitError, OSError) as error:
                print(f"  {market} {name}: 실패 — {error}", file=sys.stderr)
                failed.append(f"{market}/{name}")
                continue
            if not rows:
                # **상장 전이다. 이 종목은 더 안 내려간다.**
                #
                # 예전에는 여기서 그냥 넘어가서, 2년 전에 상장한 종목도
                # 4년치를 다 훑었다 — 빈 달마다 요청이 한 번씩 나가고
                # 아무것도 안 얻는다. 주석은 "더 내려가도 없다"고 적혀
                # 있었는데 코드가 그 말을 안 지키고 있었다.
                print(f"  {market} {name}: 봉이 없습니다 (상장 전으로 봅니다)")
                done.add(market)
                continue
            count = write_rows(path, market, rows)
            size = path.stat().st_size / 1024
            print(f"  {market} {name}: {count:,}개, {size:.0f}KB")
            made += 1
    return made, failed


def write_manifest(out: Path, markets: list[str]) -> None:
    """어느 달이 있는지 적어 둔다. 앱이 404를 더듬지 않게."""
    have: dict[str, Any] = {}
    for market in markets:
        months = sorted(p.stem for p in (out / market).glob("*.json")) if (out / market).is_dir() else []
        if months:
            have[market] = months
    (out / "manifest.json").write_text(
        json.dumps({"made": int(datetime.now(timezone.utc).timestamp()), "months": have},
                   separators=(",", ":")),
        encoding="utf-8",
    )
    for market, months in have.items():
        print(f"  {market}: {len(months)}조각 ({months[0]} → {months[-1]})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="업비트 봉을 미리 받아 파일로 적는다")
    parser.add_argument("--mode", choices=["tail", "history"], default="tail")
    parser.add_argument("--out", default="data", help="파일을 적을 폴더")
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS, help="몇 년치까지")
    parser.add_argument("--budget", type=int, default=12, help="한 판에 채울 조각 수")
    parser.add_argument("--markets", nargs="*", default=MARKETS)
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # 서버에서 부르므로 분당 600번을 쓸 수 있다. 8로 두면 여유가 넉넉하다.
    client = UpbitClient(per_second=8)

    if args.mode == "tail":
        failed = do_tail(client, out, args.markets)
        if failed and len(failed) >= len(args.markets):
            print("전부 실패했습니다. 옛 파일을 그대로 둡니다.", file=sys.stderr)
            return 1
        return 0

    # **하나라도 적었으면 성공으로 끝낸다.**
    #
    # 여기서 1을 돌려주면 판이 죽고, 그러면 뒤따르는 '올리기' 단계가 아예
    # 안 돈다 — 192조각 중 4개가 실패했다고 나머지 188조각을 통째로 버리는
    # 셈이다. 한 시간 넘게 받아 놓고 그걸 버릴 이유가 없다.
    made, failed = do_history(client, out, args.markets, args.years, args.budget)
    write_manifest(out, args.markets)
    if failed:
        print(f"{len(failed)}조각이 실패했습니다: {', '.join(failed[:5])}"
              f"{' …' if len(failed) > 5 else ''}", file=sys.stderr)
    if made == 0 and failed:
        print("한 조각도 못 적었습니다.", file=sys.stderr)
        return 1
    print(f"이번 판에 {made}조각을 새로 적었습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
