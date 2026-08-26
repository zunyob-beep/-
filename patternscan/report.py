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
        marks = ("~" if finding.mostly_a_trend else "") + ("★" if finding.significant else "")
        lines.append(
            f"  {combo:<30}{finding.samples:>5}{finding.up_rate:>8.1%}"
            f"{finding.base_up_rate:>8.1%}{finding.edge:>+8.1%}"
            f"{finding.mean_return:>+9.3%}{finding.min_similarity:>8.2f}"
            f"{finding.q_value:>8.3f}{' ' + marks if marks else ''}"
        )
    lines.append("")
    lines.append("  ★ = 다중비교 보정 후에도 유의함")
    lines.append("  ~ = 질의 모양이 직선에 가까움 (특이한 모양이 아니라 '추세 중'을 센 것)")
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
            f"      질의 모양의 직선성 {finding.query_linearity:.2f}"
            + (
                "  ← 직선에 가깝습니다. '모양'이 아니라 '추세'를 본 것에 가깝습니다"
                if finding.mostly_a_trend
                else " (1.00이면 그냥 직선)"
            ),
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


def format_validation(scores: list, cost: float, top: int = 20) -> str:
    """길이별 성적표.

    적중률만 크게 보여주면 안 된다. 1분 뒤에 수수료를 넘겨 오르는 경우는
    원래 20%도 안 되므로, 무조건 '안 오른다'고 찍어도 적중률 80%가 나온다.
    그래서 '찍기'(다수 쪽으로만 찍었을 때)와 '실력'(그 차이)을 나란히 둔다.
    """
    usable = [s for s in scores if s.predictions >= 30]
    if not usable:
        return "  예측을 30번 이상 낼 수 있었던 조합이 없습니다 — 데이터를 더 모으세요."

    lines = [
        "",
        f"  {'길이':>5}{'지평':>5}{'예측':>6}{'적중률':>8}{'찍기':>8}{'실력':>8}{'오차':>7}"
        f"{'부호':>8}{'상승예측 평균':>13}{'전체 평균':>11}",
        "  " + "─" * 80,
    ]
    for score in sorted(usable, key=lambda s: s.skill, reverse=True)[:top]:
        pays = score.mean_up_return > cost
        mark = " ←" if score.skill_is_real and pays else (" ·" if pays else "")
        lines.append(
            f"  {score.length:>5}{score.horizon:>5}{score.predictions:>6}"
            f"{score.accuracy:>8.1%}{score.base_accuracy:>8.1%}{score.skill:>+8.1%}"
            f"{'±' + format(score.skill_error, '.1%'):>7}{score.sign_accuracy:>8.1%}"
            f"{score.mean_up_return:>+13.4%}{score.mean_all_return:>+11.4%}{mark}"
        )
    lines.append("")
    lines.append("  실력 = 적중률 − 찍기. 0 이하면 그냥 찍는 것만 못합니다.")
    lines.append(
        "  오차 = 실력의 표준오차. 예측 150회면 ±4%이므로, **실력이 없어도 ±8%는 그냥 나옵니다.**"
    )
    lines.append("  실력이 오차의 2.5배를 넘지 못하면 우연과 구분되지 않습니다.")
    lines.append(f"  ← = 우연으로 설명 안 되면서 평균 수익도 왕복 비용 {cost:.3%}를 넘긴 조합")
    lines.append("  · = 평균 수익은 비용을 넘겼지만 실력이 우연과 구분되지 않는 조합")
    return "\n".join(lines)


def format_validation_verdict(scores: list, cost: float) -> str:
    """가장 나은 길이가 무엇이고, 그게 쓸 만한지."""
    usable = [s for s in scores if s.predictions >= 30]
    if not usable:
        return "  판정할 수 없습니다 — 예측을 충분히 내지 못했습니다."

    winners = [s for s in usable if s.skill_is_real and s.mean_up_return > cost]
    best = max(usable, key=lambda s: s.skill)
    lines = ["=" * 66]
    if winners:
        top = max(winners, key=lambda s: s.skill)
        lines.append(f"  🟢 가장 잘 맞은 길이: {top.length}개  ({top.label})")
        lines.append("=" * 66)
        lines.append(
            f"    · 예측 {top.predictions}회 중 적중률 {top.accuracy:.1%} "
            f"(찍기 {top.base_accuracy:.1%} → 실력 {top.skill:+.1%} ± {top.skill_error:.1%})"
        )
        lines.append(
            f"    · '상승' 예측 {top.said_up}회의 평균 수익 {top.mean_up_return:+.4%} "
            f"— 왕복 비용 {cost:.3%}를 넘습니다"
        )
        lines.append(f"    · 아무 때나 들어갔을 때({top.mean_all_return:+.4%}) 대비 {top.edge_return:+.4%}")
        lengths = sorted({s.length for s in winners})
        lines.append(
            f"    · 같은 기준을 통과한 조합 {len(winners)}개 — 길이 "
            f"{', '.join(str(x) for x in lengths)}"
        )
    else:
        lines.append("  🔴 어떤 길이도 우연을 넘지 못했습니다")
        lines.append("=" * 66)
        lines.append(
            f"    · 실력이 가장 높은 조합은 {best.label}로 {best.skill:+.1%}입니다."
        )
        if not best.skill_is_real:
            lines.append(
                f"    · 그런데 예측 {best.predictions}회의 표준오차가 ±{best.skill_error:.1%}라, "
                "실력이 없어도 이 정도는 그냥 나옵니다."
            )
        paying = [s for s in usable if s.mean_up_return > cost]
        if paying:
            lines.append(
                f"    · 평균 수익이 비용을 넘긴 조합은 {len(paying)}개 있지만, "
                "실력이 우연과 구분되지 않습니다."
            )
        else:
            lines.append(
                f"    · '상승' 예측 시 평균 수익이 왕복 비용 {cost:.3%}를 넘긴 조합이 "
                "하나도 없습니다."
            )
        lines.append("    · 이 데이터에서는 직전 봉 모양으로 방향을 맞힐 근거가 없습니다.")
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
    "format_validation",
    "format_validation_verdict",
    "format_verdict",
    "round_trip_cost",
    "summary_header",
]
