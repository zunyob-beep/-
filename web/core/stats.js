// 통계 도구 중 화면이 쓰는 것만. patternscan/stats.py에서 가져왔다.

/**
 * 승률의 95% 신뢰구간 (Wilson).
 *
 * 표본이 적을 때 단순 근사(k/n ± z·SE)는 구간이 [0,1]을 벗어나거나
 * 지나치게 좁아진다. Wilson은 그 두 문제가 없다.
 */
export function wilsonInterval(k, n, z = 1.96) {
  if (n <= 0) return [0, 1];
  const phat = k / n;
  const denominator = 1 + (z * z) / n;
  const center = (phat + (z * z) / (2 * n)) / denominator;
  const margin =
    (z * Math.sqrt((phat * (1 - phat)) / n + (z * z) / (4 * n * n))) / denominator;
  return [Math.max(0, center - margin), Math.min(1, center + margin)];
}

/**
 * 백분위수. numpy.percentile의 기본(선형 보간)과 같은 값을 낸다.
 *
 * `values`는 정렬되어 있어야 한다 — 호출자가 이미 정렬한 배열을 여러
 * 백분위에 돌려 쓰기 때문에 여기서 다시 정렬하지 않는다.
 */
export function percentileSorted(sorted, q) {
  const n = sorted.length;
  if (n === 0) return NaN;
  if (n === 1) return sorted[0];
  const pos = ((q / 100) * (n - 1));
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

/** 중앙값. */
export function median(values) {
  const sorted = Float64Array.from(values).sort();
  return percentileSorted(sorted, 50);
}
