"""명령줄 인터페이스.

    python -m patternscan fetch --count 40000
    python -m patternscan scan
    python -m patternscan ui
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .data import cache_path, fetch, load_cached, save
from .models import HORIZONS, KST, WINDOW_LENGTHS, Series, timeframe_label
from .odds import format_odds, odds_all
from .report import (
    format_coverage,
    format_detail,
    format_table,
    format_validation,
    format_validation_verdict,
    format_verdict,
    summary_header,
)
from .scan import (
    DEFAULT_FEE,
    DEFAULT_SIMILARITY,
    DEFAULT_SLIPPAGE,
    round_trip_cost,
    scan_all,
)
from .stats import decide, evaluate
from .upbit import UpbitClient, UpbitError
from .validate import validate

log = logging.getLogger("patternscan")

#: 기본 수집량. 1분봉 30일치. 3분/5분봉은 같은 기간이면 1/3, 1/5면 충분하다.
DEFAULT_COUNT = {"minute1": 43_200, "minute3": 14_400, "minute5": 8_640}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patternscan",
        description="과거에 같은 모양이 있었는지 찾아 단타 진입 여부를 판정합니다",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  python -m patternscan fetch                 # 시세 받기 (처음 한 번, 몇 분 걸림)\n"
            "  python -m patternscan odds                  # 오를 확률만 보기 (판단은 안 함)\n"
            "  python -m patternscan scan                  # 지금 들어갈지 판정\n"
            "  python -m patternscan scan --detail         # 조합별 상세\n"
            "  python -m patternscan validate              # 어느 길이가 잘 맞았는지 측정\n"
            "  python -m patternscan import a.csv          # 외부 CSV 들여오기\n"
            "  python -m patternscan ui                    # 웹 화면\n"
        ),
    )
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("fetch", "시세를 받아 캐시에 저장"),
        ("scan", "지금 시점의 모양을 과거와 비교해 판정"),
        ("odds", "지금 모양과 닮은 과거를 찾아 오를 확률만 알려줌 (판단은 안 함)"),
        ("validate", "과거 여러 시점으로 돌아가 길이별 적중률을 측정"),
        ("import", "남이 만든 OHLCV CSV를 들여오기"),
        ("ui", "웹 화면 열기"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--market", default="KRW-BTC", help="마켓 코드 (기본 KRW-BTC)")
        p.add_argument("--data-dir", default="data", help="CSV 캐시 폴더")
        if name == "fetch":
            p.add_argument("--count", type=int, default=None, help="1분봉 기준 받을 봉 개수")
            p.add_argument("--refresh", action="store_true", help="캐시를 무시하고 새로 받기")
        if name == "scan":
            p.add_argument("--top-k", type=int, default=60, help="쓸 매치 개수 (기본 60)")
            p.add_argument(
                "--similarity", type=float, default=DEFAULT_SIMILARITY,
                help=(
                    "'같은 모양'으로 인정할 최소 상관계수 "
                    f"(기본 {DEFAULT_SIMILARITY}, 1.00=완전히 같음, 0.00=무관). "
                    "낮추면 표본이 늘지만 안 닮은 구간까지 섞입니다"
                ),
            )
            p.add_argument(
                "--max-distance", type=float, default=None,
                help="유사도 대신 거리로 자르고 싶을 때 (0에 가까울수록 똑같은 모양)",
            )
            p.add_argument(
                "--scale", default="shape", choices=["shape", "amplitude"],
                help="shape=변동폭 무시하고 모양만, amplitude=변동폭도 같아야 함",
            )
            p.add_argument("--fee", type=float, default=DEFAULT_FEE, help="편도 수수료율")
            p.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE, help="편도 슬리피지")
            p.add_argument("--fdr", type=float, default=0.10, help="허용 거짓발견율")
            p.add_argument("--detail", action="store_true", help="상위 조합 상세 보기")
            p.add_argument("--refresh", action="store_true", help="시세를 새로 받고 판정")
        if name == "odds":
            p.add_argument("--length", type=int, default=180, help="직전 몇 개 봉을 볼지 (기본 180)")
            p.add_argument(
                "--similarity", type=float, default=DEFAULT_SIMILARITY,
                help=f"'닮았다'로 볼 최소 상관계수 (기본 {DEFAULT_SIMILARITY})",
            )
            p.add_argument("--top-k", type=int, default=100, help="쓸 과거 구간 개수 (기본 100)")
            p.add_argument("--fee", type=float, default=DEFAULT_FEE, help="편도 수수료율")
            p.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE, help="편도 슬리피지")
            p.add_argument("--refresh", action="store_true", help="시세를 새로 받고 계산")
        if name == "validate":
            p.add_argument(
                "--timeframe", default="minute1", choices=list(DEFAULT_COUNT),
                help="어느 봉 간격으로 검증할지 (기본 1분봉)",
            )
            p.add_argument("--points", type=int, default=500, help="평가할 시점 수 (기본 500)")
            p.add_argument("--top-k", type=int, default=60, help="쓸 매치 개수")
            p.add_argument(
                "--similarity", type=float, default=DEFAULT_SIMILARITY,
                help=f"'같은 모양' 최소 상관계수 (기본 {DEFAULT_SIMILARITY})",
            )
            p.add_argument("--fee", type=float, default=DEFAULT_FEE, help="편도 수수료율")
            p.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE, help="편도 슬리피지")
            p.add_argument(
                "--lengths", default=None,
                help="쉼표로 구분한 길이 목록 (기본: 5,10,20,…,180 전부)",
            )
            p.add_argument(
                "--buckets", type=int, default=0,
                help=(
                    "0이면 연속 비교(상관계수), 2 이상이면 '구간별로 자르기'. "
                    "예: 5면 급등/상승/보합/하락/급락으로 잘라 기호가 같은지로 비교. "
                    "이때 --similarity는 '기호가 몇 %%까지 같아야 하는지'가 됩니다"
                ),
            )
            p.add_argument("--seed", type=int, default=0, help="평가 시점 추출 난수 씨앗")
        if name == "import":
            p.add_argument("csv", help="들여올 CSV 경로")
            p.add_argument(
                "--timeframe", default="minute1", choices=list(DEFAULT_COUNT),
                help="이 CSV가 몇 분봉인지 (기본 1분봉)",
            )
            p.add_argument(
                "--resample", action="store_true",
                help="1분봉이면 3분봉·5분봉도 같이 만들기",
            )
            p.add_argument("--limit", type=int, default=None, help="앞에서 이만큼만 읽기")
        if name == "ui":
            p.add_argument("--port", type=int, default=8765)
            p.add_argument("--no-browser", action="store_true")

    return parser


def _counts(count: int | None) -> dict[str, int]:
    if count is None:
        return dict(DEFAULT_COUNT)
    # 1분봉 기준으로 주면 나머지는 같은 기간이 되도록 나눈다
    return {"minute1": count, "minute3": max(count // 3, 300), "minute5": max(count // 5, 300)}


def cmd_fetch(args: argparse.Namespace) -> int:
    client = UpbitClient()
    wanted = _counts(args.count)
    for timeframe, count in wanted.items():
        print(f"{timeframe} 수집 중… (최대 {count:,}개)")

        def progress(done: int, total: int, tf: str = timeframe) -> None:
            print(f"  {tf}: {done:,}/{total:,}", end="\r", flush=True)

        series = fetch(
            client, args.market, timeframe, count,
            directory=args.data_dir, refresh=args.refresh, progress=progress,
        )
        span = series.span
        window = ""
        if span:
            window = f"  {span[0]:%Y-%m-%d %H:%M} ~ {span[1]:%Y-%m-%d %H:%M} UTC"
        print(f"  {timeframe}: 봉 {len(series):,}개 확보{window}          ")
    return 0


def _load_series(args: argparse.Namespace, refresh: bool) -> dict[str, Series]:
    """봉 간격별로 시세를 확보한다.

    한 간격을 못 받아도 나머지로 계속 간다. 예전에는 여기서 예외가 그대로
    올라가 **1분봉이 멀쩡히 있는데도 3분봉을 못 받았다는 이유로 판정 전체가
    죽었다**. 수집이 중간에 끊겼거나 업비트가 잠깐 죽으면 바로 그 상황이 된다.
    """
    client = UpbitClient()
    out: dict[str, Series] = {}
    for timeframe, count in DEFAULT_COUNT.items():
        series = Series.empty(args.market, timeframe) if refresh else load_cached(
            args.market, timeframe, args.data_dir
        )
        if refresh or len(series) == 0:
            try:
                series = fetch(
                    client, args.market, timeframe, count,
                    directory=args.data_dir, refresh=refresh,
                )
            except UpbitError as exc:
                log.warning("%s 시세를 받지 못했습니다 (%s) — 이 간격은 건너뜁니다", timeframe, exc)
                series = Series.empty(args.market, timeframe)
        out[timeframe] = series
    return out


def cmd_scan(args: argparse.Namespace) -> int:
    series_by_tf = _load_series(args, args.refresh)
    usable = {tf: s for tf, s in series_by_tf.items() if len(s) > 0}
    if not usable:
        print("시세가 없습니다. 먼저 `python -m patternscan fetch`를 실행하세요.")
        return 1

    missing = [tf for tf, s in series_by_tf.items() if len(s) == 0]
    if missing:
        # 조용히 빼면 사용자는 3종을 다 본 줄 안다.
        print(
            f"  ※ {', '.join(timeframe_label(tf) for tf in missing)} 시세가 없어 "
            "이 간격은 판정에서 빠집니다."
        )

    cost = round_trip_cost(args.fee, args.slippage)
    print(
        summary_header(
            args.market,
            [(tf, len(s), s.gaps()) for tf, s in usable.items()],
            cost,
            None if args.max_distance is not None else args.similarity,
        )
    )

    results = scan_all(
        usable,
        WINDOW_LENGTHS,
        horizons=HORIZONS,
        top_k=args.top_k,
        similarity=args.similarity,
        max_distance=args.max_distance,
        scale=args.scale,
        fee=args.fee,
        slippage=args.slippage,
    )
    findings = evaluate(results, fdr=args.fdr)
    verdict = decide(findings, fdr=args.fdr)

    print()
    print(format_verdict(verdict, cost))
    print(format_table(findings))

    coverage = format_coverage(results)
    if coverage:
        print()
        print(coverage)

    if args.detail:
        for finding in [f for f in findings if f.enough_samples][:5]:
            print(format_detail(finding))

    return 0


def cmd_odds(args: argparse.Namespace) -> int:
    series_by_tf = _load_series(args, args.refresh)
    usable = {tf: s for tf, s in series_by_tf.items() if len(s) > 0}
    if not usable:
        print("시세가 없습니다. 먼저 `python -m patternscan fetch`를 실행하세요.")
        return 1

    cost = round_trip_cost(args.fee, args.slippage)
    newest = max((s.span[1] for s in usable.values() if s.span), default=None)
    when = f"{newest.astimezone(KST):%Y-%m-%d %H:%M} KST 기준" if newest else ""
    print(f"\n  {args.market} · {when}")
    print(f"  직전 {args.length}개 봉의 모양과 닮은 과거를 찾아, 그 뒤에 무슨 일이 있었는지 셉니다.")

    rows = odds_all(
        usable, args.length,
        horizons=HORIZONS, similarity=args.similarity, top_k=args.top_k,
        fee=args.fee, slippage=args.slippage,
    )
    print(format_odds(rows, cost, expected=list(usable)))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    series = load_cached(args.market, args.timeframe, args.data_dir)
    if len(series) == 0:
        print("시세가 없습니다. 먼저 `python -m patternscan fetch`를 실행하세요.")
        return 1

    lengths = (
        tuple(int(x) for x in args.lengths.split(",") if x.strip())
        if args.lengths
        else WINDOW_LENGTHS
    )
    cost = round_trip_cost(args.fee, args.slippage)
    span = series.span
    window = f"  {span[0]:%Y-%m-%d} ~ {span[1]:%Y-%m-%d}" if span else ""
    how = (
        f"{args.buckets}구간으로 잘라 기호 비교 (유사도 = 기호 일치율)"
        if args.buckets
        else "연속 — 상관계수"
    )
    print(
        f"\n  {args.market} {timeframe_label(args.timeframe)} 봉 {len(series):,}개{window}\n"
        f"  평가 시점 {args.points}개 · 길이 {len(lengths)}종 · 왕복 비용 {cost:.3%}\n"
        f"  비교 방식: {how}\n"
        f"  각 시점에서 그 이전 데이터만 보고 예측한 뒤, 실제 결과와 맞춰봅니다.\n"
    )

    def progress(done: int, total: int) -> None:
        print(f"  검증 중… {done:,}/{total:,}", end="\r", flush=True)

    scores = validate(
        series, lengths,
        horizons=HORIZONS, points=args.points, similarity=args.similarity,
        top_k=args.top_k, fee=args.fee, slippage=args.slippage,
        buckets=args.buckets, seed=args.seed, progress=progress,
    )
    print(" " * 40, end="\r")

    print(format_validation_verdict(scores, cost))
    print(format_validation(scores, cost))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from .importer import describe, read_csv, resample

    print(f"\n  {args.csv} 읽는 중…")
    candles = read_csv(args.csv, limit=args.limit)
    print(describe(candles, args.timeframe))

    saved = [(args.timeframe, candles)]
    if args.resample and args.timeframe == "minute1":
        for timeframe, factor in (("minute3", 3), ("minute5", 5)):
            grouped = resample(candles, factor)
            saved.append((timeframe, grouped))

    print()
    for timeframe, group in saved:
        path = save(cache_path(args.market, timeframe, args.data_dir), group)
        print(f"  {timeframe_label(timeframe):>5}: {len(group):>10,}개 → {path}")

    print("\n  이제 이렇게 쓰면 됩니다:")
    print(f"    python -m patternscan validate --market {args.market} --data-dir {args.data_dir}")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .webui.server import serve

    serve(
        market=args.market,
        data_dir=args.data_dir,
        port=args.port,
        open_browser=not args.no_browser,
    )
    return 0


COMMANDS = {
    "fetch": cmd_fetch,
    "scan": cmd_scan,
    "odds": cmd_odds,
    "validate": cmd_validate,
    "import": cmd_import,
    "ui": cmd_ui,
}


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)
    try:
        return COMMANDS[args.command](args)
    except KeyboardInterrupt:
        print("\n중단했습니다.")
        return 130
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141
    except (RuntimeError, ValueError, KeyError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
