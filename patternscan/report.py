"""결과를 사람이 읽을 수 있게 찍는다."""

from __future__ import annotations

from .models import timeframe_label
from .scan import ScanResult, round_trip_cost
from .stats import MIN_SAMPLES, Finding, Verdict


def format_verdict(verdict: Verdict, cost: float) -> str:
    mark = "🟢" if verdict.enter else "🔴"
    lines = [
        "=" * 66,
        f"  {mark} {verdict.headline}",
        "=" * 66,
    ]
    for reason in verdict.reasons:
        lines.append(f"    · {reason}")
    lines.append("")
    lines.append(
        f"    상승 기준: 왕복 비용 {cost:.3%}를 넘긴 경우만 상승으로 셉니다."
    )
    lines.append(
        f"    검정한 조합 {verdict.tested}개 중 보정 후 유의한 것 {verdict.significant}개."
    )
    return "\n".join(lines)


def format_table(findings: list[Finding], top: int = 15) -> str:
    """상위 조합 표.

    승률만 보면 안 되므로 표본 수·기준 대비·보정 후 q값을 같이 보여준다.
    """
    usable = [f for f in findings if f.enough_samples]
    if not usable:
        return "  표본이 충분한 조합이 없습니다."

    lines = [
        "",
        f"  {'조합':<30}{'표본':>5}{'승률':>8}{'기준':>8}{'초과':>8}"
        f"{'평균':>9}{'유사도':>8}{'q값':>8}",
        "  " + "─" * 84,
    ]
    for finding in usable[:top]:
        combo = f"{timeframe_label(finding.timeframe)} {finding.length}개 → {finding.horizon}봉"
        star = " ★" if finding.significant else ""
        lines.append(
            f"  {combo:<30}{finding.samples:>5}{finding.up_rate:>8.1%}"
            f"{finding.base_up_rate:>8.1%}{finding.edge:>+8.1%}"
            f"{finding.mean_return:>+9.3%}{finding.min_similarity:>8.2f}"
            f"{finding.q_value:>8.3f}{star}"
        )
    lines.append("")
    lines.append("  ★ = 다중비교 보정 후에도 유의함")
    lines.append("  유사도 = 표본에 들어온 것 중 가장 덜 닮은 모양의 상관계수")
    lines.append(f"  표본 {MIN_SAMPLES}개 미만 조합은 표에서 제외했습니다.")
    return "\n".join(lines)


def format_detail(finding: Finding) -> str:
    return "\n".join(
        [
            "",
            f"  ▸ {finding.label}",
            f"      과거 같은 모양 {finding.samples}건",
            f"      상승 {finding.up}건 / 보합 {finding.flat}건 / 하락 {finding.down}건",
            f"      승률 {finding.up_rate:.1%} (95% 구간 {finding.ci_low:.1%}~{finding.ci_high:.1%})",
            f"      같은 기간 기준 승률 {finding.base_up_rate:.1%} → 초과 {finding.edge:+.1%}",
            f"      평균 {finding.mean_return:+.3%} · 중앙값 {finding.median_return:+.3%}"
            f" · 최악 {finding.worst:+.2%}",
            f"      p={finding.p_value:.4f}, 보정 후 q={finding.q_value:.4f}",
            f"      시간순 앞쪽 {finding.first_half_rate:.0%} / 뒤쪽 {finding.second_half_rate:.0%}"
            + ("" if finding.holds_in_both_halves else "  ← 한쪽 구간에서만 나타납니다"),
            f"      가장 덜 닮은 매치의 유사도 {finding.min_similarity:.3f}"
            " (1.00이면 완전히 같은 모양, 0.00이면 무관)",
        ]
    )


def format_coverage(results: list[ScanResult]) -> str:
    """어떤 조합이 왜 빠졌는지."""
    skipped = [r for r in results if r.note]
    thin = [r for r in results if not r.note and r.sample_size < MIN_SAMPLES]
    lines = []
    if skipped:
        lines.append(f"  데이터 부족으로 건너뛴 조합 {len(skipped)}개")
    if thin:
        lines.append(
            f"  같은 모양을 {MIN_SAMPLES}개 미만으로 찾은 조합 {len(thin)}개"
            " (모양이 길수록 잘 안 겹칩니다)"
        )
    return "\n".join(lines)


def summary_header(
    market: str,
    series_info: list[tuple[str, int, int]],
    cost: float,
    similarity: float | None = None,
) -> str:
    lines = [
        "",
        f"  종목 {market} · 왕복 비용 {cost:.3%}",
    ]
    if similarity is not None:
        lines.append(f"  '같은 모양' 기준: 상관계수 {similarity:.2f} 이상")
    for timeframe, count, gaps in series_info:
        gap_note = f" (끊긴 구간 {gaps}곳)" if gaps else ""
        lines.append(f"    {timeframe_label(timeframe)}: 봉 {count:,}개{gap_note}")
    return "\n".join(lines)


__all__ = [
    "format_coverage",
    "format_detail",
    "format_table",
    "format_verdict",
    "round_trip_cost",
    "summary_header",
]
