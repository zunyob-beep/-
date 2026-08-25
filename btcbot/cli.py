"""명령줄 인터페이스.

    python -m btcbot backtest --strategy vb --interval day --start 2023-01-01
    python -m btcbot paper --strategy ma_cross --interval minute60
    python -m btcbot live --strategy vb --interval minute60   # 실제 주문!
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

from . import data as data_mod
from .backtest import grid_search, run_backtest, walk_forward
from .config import Settings, load_dotenv
from .exchange.upbit import INTERVALS, UpbitClient
from .models import KST
from .risk import RiskConfig
from .storage import Journal
from .strategies import available, get_strategy, strategy_class

log = logging.getLogger("btcbot")


# ---------------------------------------------------------------------- 파서
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="btcbot",
        description="업비트 비트코인 자동매매 봇 (백테스트 / 페이퍼 / 실거래)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  python -m btcbot strategies\n"
            "  python -m btcbot fetch --interval day --start 2020-01-01\n"
            "  python -m btcbot backtest --strategy vb -p k=0.5 --start 2023-01-01\n"
            "  python -m btcbot optimize --strategy vb -g k=0.3,0.4,0.5,0.6\n"
            "  python -m btcbot paper --strategy ma_cross --interval minute60\n"
        ),
    )
    parser.add_argument("--config", help="설정 파일 (.json 또는 .yaml)")
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING/ERROR")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("strategies", help="사용 가능한 전략과 파라미터 보기")

    fetch = sub.add_parser("fetch", help="과거 봉을 내려받아 CSV로 저장")
    _add_market_args(fetch)
    fetch.add_argument("--start", help="시작일 (예: 2020-01-01)")
    fetch.add_argument("--end", help="종료일")
    fetch.add_argument("--refresh", action="store_true", help="캐시를 무시하고 새로 받기")

    back = sub.add_parser("backtest", help="과거 데이터로 전략 검증")
    _add_market_args(back)
    _add_strategy_args(back)
    _add_sim_args(back)
    _add_risk_args(back)
    back.add_argument("--start", help="시작일")
    back.add_argument("--end", help="종료일")
    back.add_argument("--refresh", action="store_true", help="캐시 무시")
    back.add_argument("--show-trades", action="store_true", help="개별 거래 내역 출력")
    back.add_argument("--csv", help="자산 곡선을 CSV로 저장할 경로")

    opt = sub.add_parser("optimize", help="파라미터 격자 탐색 (+ 워크포워드 검증)")
    _add_market_args(opt)
    _add_strategy_args(opt)
    _add_sim_args(opt)
    _add_risk_args(opt)
    opt.add_argument("--start", help="시작일")
    opt.add_argument("--end", help="종료일")
    opt.add_argument(
        "-g",
        "--grid",
        action="append",
        default=[],
        metavar="KEY=V1,V2",
        help="탐색할 파라미터 (여러 번 지정 가능)",
    )
    opt.add_argument("--metric", default="sharpe", help="정렬 기준 (기본: sharpe)")
    opt.add_argument("--top", type=int, default=10, help="상위 몇 개를 볼지")
    opt.add_argument(
        "--walk-forward",
        action="store_true",
        help="앞 구간으로 고르고 뒤 구간으로 검증 (과최적화 점검)",
    )

    paper = sub.add_parser("paper", help="실시간 시세로 모의매매 (주문 안 나감)")
    _add_market_args(paper)
    _add_strategy_args(paper)
    _add_sim_args(paper)
    _add_risk_args(paper)
    _add_run_args(paper)

    live = sub.add_parser("live", help="실제 주문 실행 — 진짜 돈이 나갑니다")
    _add_market_args(live)
    _add_strategy_args(live)
    _add_risk_args(live)
    _add_run_args(live)
    live.add_argument("--fee", type=float, default=None, help="수수료율 (기본 0.0005)")
    live.add_argument(
        "--dry-run", action="store_true", help="주문 직전까지만 수행하고 실제 전송은 생략"
    )
    live.add_argument("--yes", action="store_true", help="확인 프롬프트 건너뛰기")

    status = sub.add_parser("status", help="계좌와 최근 거래 기록 보기")
    status.add_argument("--run-name", default=None, help="기록 이름")
    status.add_argument("--runs-dir", default=None)
    status.add_argument("--account", action="store_true", help="업비트 실계좌도 조회")
    status.add_argument("--limit", type=int, default=10, help="보여줄 거래 개수")

    return parser


def _add_market_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--market", default=None, help="마켓 코드 (기본 KRW-BTC)")
    parser.add_argument(
        "--interval", default=None, choices=list(INTERVALS), metavar="INTERVAL",
        help=f"봉 간격 ({', '.join(INTERVALS)})",
    )
    parser.add_argument("--data-dir", default=None, help="CSV 캐시 폴더")


def _add_strategy_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--strategy", default=None, choices=available(), metavar="NAME",
                        help=f"전략 ({', '.join(available())})")
    parser.add_argument(
        "-p", "--param", action="append", default=[], metavar="KEY=VALUE",
        help="전략 파라미터 (여러 번 지정 가능)",
    )


def _add_sim_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cash", type=float, default=None, help="초기 자금 (기본 1,000,000)")
    parser.add_argument("--fee", type=float, default=None, help="수수료율 (기본 0.0005)")
    parser.add_argument("--slippage", type=float, default=None, help="슬리피지 (기본 0.0005)")
    parser.add_argument("--band", type=float, default=None, help="재조정 허용 오차 (기본 0.05)")


def _add_risk_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("리스크 관리")
    group.add_argument("--max-weight", type=float, default=None, help="최대 코인 비중 (0~1)")
    group.add_argument("--stop-loss", type=float, default=None, help="손절 비율 (0.05 = -5%%)")
    group.add_argument("--take-profit", type=float, default=None, help="익절 비율")
    group.add_argument("--trailing-stop", type=float, default=None, help="트레일링 스탑 비율")
    group.add_argument("--daily-loss-limit", type=float, default=None, help="일일 손실 한도")
    group.add_argument("--max-drawdown", type=float, default=None, help="킬 스위치 낙폭 한도")
    group.add_argument("--cooldown", type=int, default=None, help="손절 후 쉬는 봉 개수")


def _add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-name", default=None, help="기록을 저장할 이름")
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--max-bars", type=int, default=None, help="이 개수만큼만 돌고 종료")
    parser.add_argument("--webhook", default=None, help="체결 알림 웹훅 URL")
    parser.add_argument("-v", "--verbose", action="store_true", help="매 봉 상태 출력")


# ------------------------------------------------------------------ 인자 파싱
def parse_params(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"파라미터는 KEY=VALUE 형식이어야 합니다: {pair!r}")
        key, _, value = pair.partition("=")
        out[key.strip()] = _coerce(value.strip())
    return out


def parse_grid(pairs: list[str]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"격자는 KEY=V1,V2 형식이어야 합니다: {pair!r}")
        key, _, values = pair.partition("=")
        out[key.strip()] = [_coerce(v.strip()) for v in values.split(",") if v.strip()]
    return out


def _coerce(text: str) -> Any:
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            continue
    return text


def settings_from_args(args: argparse.Namespace) -> Settings:
    overrides: dict[str, Any] = {
        "market": getattr(args, "market", None),
        "interval": getattr(args, "interval", None),
        "strategy": getattr(args, "strategy", None),
        "cash": getattr(args, "cash", None),
        "fee_rate": getattr(args, "fee", None),
        "slippage": getattr(args, "slippage", None),
        "rebalance_band": getattr(args, "band", None),
        "run_name": getattr(args, "run_name", None),
        "runs_dir": getattr(args, "runs_dir", None),
        "data_dir": getattr(args, "data_dir", None),
        "log_level": getattr(args, "log_level", None),
        "webhook_url": getattr(args, "webhook", None),
    }
    if getattr(args, "verbose", False):
        overrides["verbose"] = True

    settings = Settings.load(getattr(args, "config", None), **overrides)

    params = parse_params(getattr(args, "param", []) or [])
    if params:
        settings.strategy_params = {**settings.strategy_params, **params}

    risk_overrides = {
        "max_position_weight": getattr(args, "max_weight", None),
        "stop_loss_pct": getattr(args, "stop_loss", None),
        "take_profit_pct": getattr(args, "take_profit", None),
        "trailing_stop_pct": getattr(args, "trailing_stop", None),
        "daily_loss_limit_pct": getattr(args, "daily_loss_limit", None),
        "max_drawdown_pct": getattr(args, "max_drawdown", None),
        "cooldown_bars": getattr(args, "cooldown", None),
    }
    provided = {k: v for k, v in risk_overrides.items() if v is not None}
    if provided:
        current = {
            f: getattr(settings.risk, f) for f in RiskConfig.__dataclass_fields__
        }
        settings.risk = RiskConfig(**{**current, **provided})
    return settings


# -------------------------------------------------------------------- 커맨드
def cmd_strategies(_: argparse.Namespace) -> int:
    print("사용 가능한 전략:\n")
    for name in available():
        cls = strategy_class(name)
        doc = (cls.__doc__ or "").strip().splitlines()
        summary = doc[0] if doc else ""
        print(f"  {name:<10} {summary}")
        for key, value in sorted(cls.defaults().items()):
            print(f"{'':<14}- {key} = {value!r}")
        print()
    print("사용법:  --strategy vb -p k=0.5 -p dynamic_k=true")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    settings = settings_from_args(args)
    client = UpbitClient()
    start = data_mod.parse_date(args.start) if args.start else None
    end = data_mod.parse_date(args.end) if args.end else None

    candles = data_mod.load_or_fetch(
        client, settings.market, settings.interval,
        start=start, end=end, directory=settings.data_dir, refresh=args.refresh,
    )
    if not candles:
        print("받아온 봉이 없습니다.")
        return 1
    path = data_mod.cache_path(settings.market, settings.interval, settings.data_dir)
    print(f"{len(candles)}개 저장 → {path}")
    print(f"기간: {candles[0].ts:%Y-%m-%d %H:%M} ~ {candles[-1].ts:%Y-%m-%d %H:%M} (UTC)")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    settings = settings_from_args(args)
    candles = _load_candles(args, settings)
    if candles is None:
        return 1

    strategy = get_strategy(settings.strategy, **settings.strategy_params)
    result = run_backtest(
        candles, strategy,
        cash=settings.cash, fee_rate=settings.fee_rate, slippage=settings.slippage,
        risk_config=settings.risk, rebalance_band=settings.rebalance_band,
        interval=settings.interval, verbose=settings.verbose,
    )
    print(result.report())

    if args.show_trades and result.stats.trades:
        print("\n  거래 내역")
        print(f"  {'진입':<12}{'청산':<12}{'수익률':>10}{'손익':>14}  사유")
        for trade in result.stats.trades:
            print(
                f"  {trade.entry_ts.astimezone(KST):%y-%m-%d %H:%M}"
                f"  {trade.exit_ts.astimezone(KST):%y-%m-%d %H:%M}"
                f"{trade.pnl_pct:>+9.2%}{trade.pnl:>+14,.0f}  {trade.reason[:40]}"
            )

    if args.csv:
        _write_curve(args.csv, result)
        print(f"\n자산 곡선 저장 → {args.csv}")
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    settings = settings_from_args(args)
    candles = _load_candles(args, settings)
    if candles is None:
        return 1

    grid = parse_grid(args.grid)
    if not grid:
        print("탐색할 파라미터가 없습니다. 예: -g k=0.3,0.4,0.5")
        return 1

    kwargs = dict(
        cash=settings.cash, fee_rate=settings.fee_rate, slippage=settings.slippage,
        risk_config=settings.risk, rebalance_band=settings.rebalance_band,
        interval=settings.interval,
    )

    if args.walk_forward:
        test, train = walk_forward(
            candles, settings.strategy, grid, metric=args.metric, **kwargs
        )
        if test is None or train is None:
            print("검증 가능한 조합이 없습니다.")
            return 1
        print("── 학습 구간(in-sample) 최적 조합 ──")
        print(train.report())
        print("\n── 검증 구간(out-of-sample) 동일 파라미터 ──")
        print(test.report())
        gap = train.performance.sharpe - test.performance.sharpe
        print(f"\n샤프 하락폭: {gap:+.2f}", "  ⚠ 과최적화 의심" if gap > 0.5 else "")
        return 0

    results = grid_search(
        candles, settings.strategy, grid, metric=args.metric, top=args.top, **kwargs
    )
    if not results:
        print("유효한 조합이 없습니다.")
        return 1

    print(f"\n상위 {len(results)}개 ({args.metric} 기준)\n")
    print(f"  {'파라미터':<44}{'수익률':>10}{'MDD':>9}{'샤프':>8}{'거래':>7}")
    for result in results:
        params = ", ".join(f"{k}={v}" for k, v in sorted(result.params.items()))
        perf = result.performance
        print(
            f"  {params[:42]:<44}{perf.total_return:>+9.1%}"
            f"{perf.max_drawdown:>9.1%}{perf.sharpe:>8.2f}{perf.trades:>7}"
        )
    print("\n⚠ 같은 데이터로 고른 파라미터는 과최적화되기 쉽습니다. --walk-forward로 검증하세요.")
    return 0


def cmd_paper(args: argparse.Namespace) -> int:
    from .runner import run

    settings = settings_from_args(args)
    if not settings.run_name or settings.run_name == "default":
        settings.run_name = f"paper-{settings.market}-{settings.strategy}"
    print(f"페이퍼 모드 — 실제 주문은 나가지 않습니다. (초기 자금 {settings.cash:,.0f}원)")
    print("Ctrl+C로 중단합니다.\n")
    stats = run(settings, live=False, max_bars=args.max_bars)
    _print_run_summary(stats)
    return 0


def cmd_live(args: argparse.Namespace) -> int:
    from .runner import run

    settings = settings_from_args(args)
    if not settings.run_name or settings.run_name == "default":
        settings.run_name = f"live-{settings.market}-{settings.strategy}"

    try:
        Settings.require_api_keys()
    except RuntimeError as exc:
        print(f"오류: {exc}")
        return 1

    if not args.yes and not args.dry_run:
        print("=" * 62)
        print("  실거래 모드 — 실제 자산으로 주문이 나갑니다.")
        print(f"  마켓 {settings.market} / 봉 {settings.interval}")
        print(f"  전략 {settings.strategy} {settings.strategy_params or ''}")
        print(f"  최대 비중 {settings.risk.max_position_weight:.0%}"
              f" / 손절 {settings.risk.stop_loss_pct:.1%}"
              f" / 일일한도 {settings.risk.daily_loss_limit_pct:.1%}"
              f" / 킬스위치 {settings.risk.max_drawdown_pct:.1%}")
        print("=" * 62)
        if input("계속하려면 LIVE 를 입력하세요: ").strip() != "LIVE":
            print("취소했습니다.")
            return 1

    stats = run(settings, live=True, dry_run=args.dry_run, max_bars=args.max_bars)
    _print_run_summary(stats)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    settings = settings_from_args(args)
    journal = Journal(settings.runs_dir, settings.run_name)

    saved = journal.load_state()
    if saved is None:
        print(f"저장된 상태가 없습니다: {journal.state_path}")
    else:
        risk_state, account = saved
        print(f"기록: {journal.dir}")
        print(f"  현금       {account.get('cash', 0):,.0f}원")
        print(f"  보유       {account.get('volume', 0):.8f} @ {account.get('avg_price', 0):,.0f}")
        print(f"  평가자산   {account.get('equity', 0):,.0f}원")
        print(f"  최고자산   {risk_state.peak_equity:,.0f}원")
        if risk_state.halted:
            print(f"  ⛔ 정지    {risk_state.halt_reason}")
        if risk_state.blocked_day:
            print(f"  ⚠ 진입금지 {risk_state.blocked_day}")

    trades = journal.read_trades()[-args.limit :]
    if trades:
        print(f"\n최근 거래 {len(trades)}건")
        for trade in trades:
            print(
                f"  {trade['exit_ts'][:16]}  {trade['pnl']:>+12,.0f}원"
                f"  ({trade['pnl_pct']:+.2%})  {trade.get('reason', '')[:40]}"
            )
        total = sum(t["pnl"] for t in journal.read_trades())
        print(f"\n  누적 실현손익 {total:+,.0f}원")

    if args.account:
        access, secret = Settings.api_keys()
        if not access or not secret:
            print("\n(실계좌 조회에는 API 키가 필요합니다)")
            return 0
        client = UpbitClient(access, secret)
        print("\n업비트 실계좌")
        for row in client.get_accounts():
            balance = float(row.get("balance", 0))
            locked = float(row.get("locked", 0))
            if balance + locked <= 0:
                continue
            print(f"  {row['currency']:<8}{balance:>18.8f}  (묶임 {locked:.8f})")
    return 0


# -------------------------------------------------------------------- 도우미
def _load_candles(args: argparse.Namespace, settings: Settings):
    client = UpbitClient()
    start = data_mod.parse_date(args.start) if getattr(args, "start", None) else None
    end = data_mod.parse_date(args.end) if getattr(args, "end", None) else None
    candles = data_mod.load_or_fetch(
        client, settings.market, settings.interval,
        start=start, end=end, directory=settings.data_dir,
        refresh=getattr(args, "refresh", False),
    )
    if len(candles) < 2:
        print("봉이 부족합니다. 먼저 `python -m btcbot fetch`로 데이터를 받으세요.")
        return None
    return candles


def _write_curve(path: str, result) -> None:
    import csv

    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ts", "equity", "cash", "price", "weight"])
        for point in result.stats.equity_curve:
            writer.writerow(
                [point.ts.isoformat(), point.equity, point.cash, point.price, point.weight]
            )


def _print_run_summary(stats) -> None:
    print(f"\n체결 {len(stats.fills)}건 / 청산 {len(stats.trades)}건")
    if stats.trades:
        print(f"실현손익 {stats.realized_pnl:+,.0f}원")
    if stats.errors:
        print(f"오류 {stats.errors}건 (로그 확인)")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


COMMANDS = {
    "strategies": cmd_strategies,
    "fetch": cmd_fetch,
    "backtest": cmd_backtest,
    "optimize": cmd_optimize,
    "paper": cmd_paper,
    "live": cmd_live,
    "status": cmd_status,
}


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level or "INFO")

    try:
        return COMMANDS[args.command](args)
    except KeyboardInterrupt:
        print("\n중단했습니다.")
        return 130
    except BrokenPipeError:
        # `python -m btcbot strategies | head` 처럼 읽는 쪽이 먼저 닫은 경우.
        # 파이썬이 종료하면서 stdout을 한 번 더 flush하다 같은 오류를 다시
        # 내므로, stdout을 devnull로 바꿔두고 조용히 끝낸다.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141  # 128 + SIGPIPE
    except (RuntimeError, ValueError, KeyError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
