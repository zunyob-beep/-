// '모양'을 숫자로 만들고 비교한다. 파이썬 patternscan/shape.py를 옮긴 것이다.
//
// 같은 모양이란 무엇인가 — 이 프로그램에서 제일 중요한 정의다.
//
// 가격을 그대로 비교하면 4,000만원 구간과 9,000만원 구간은 아무리 모양이
// 같아도 절대 안 겹친다. 그래서 두 단계로 정규화한다:
//
//   1. **첫 봉 대비 수익률 경로**로 바꾼다 (가격대 무관)
//   2. **표준편차로 나눈다** (변동성 무관 — 오르내린 '모양'만 남는다)
//
// 2번은 취향이 갈린다. 변동성까지 같아야 같은 모양이라고 볼 수도 있다.
// 그래서 scale='shape'(기본, 변동성 무시)와 scale='amplitude'(변동폭도
// 같아야 함)를 모두 제공한다.
//
// 거리는 정규화한 경로 사이의 RMSE다. 0이면 완전히 같은 모양이다.

/** 표준편차가 이보다 작으면 '움직임이 없는 구간'으로 본다(0으로 나누기 방지). */
export const FLAT_EPS = 1e-12;

/**
 * 보정 덧셈(Neumaier). 부동소수점 덧셈에서 잃는 자릿수를 따로 모아 마지막에
 * 더한다.
 *
 * 왜 쓰는가: 8년치 1분봉은 420만 개다. 그냥 더하면 누적 오차가 합의
 * 1e-9배까지 커지는데, search.js는 **누적합의 차이**로 구간합을 구하므로
 * 그 오차가 짧은 구간(20봉)의 합에 그대로 실린다 — 상대오차가 1e-4까지
 * 튄다. 한 번 더 도는 값이 아니라 한 번만 도는 초기화 비용이므로 싸다.
 */
export function compensatedCumsum(values, out) {
  let sum = 0;
  let carry = 0;
  out[0] = 0;
  for (let i = 0; i < values.length; i += 1) {
    const v = values[i];
    const t = sum + v;
    carry += Math.abs(sum) >= Math.abs(v) ? (sum - t) + v : (v - t) + sum;
    sum = t;
    out[i + 1] = sum + carry;
  }
  return out;
}

/** 평균. 파이썬 numpy.mean과 같은 값을 목표로 보정 덧셈을 쓴다. */
export function mean(values, start = 0, length = values.length - start) {
  if (length <= 0) return 0;
  let sum = 0;
  let carry = 0;
  for (let i = 0; i < length; i += 1) {
    const v = values[start + i];
    const t = sum + v;
    carry += Math.abs(sum) >= Math.abs(v) ? (sum - t) + v : (v - t) + sum;
    sum = t;
  }
  return (sum + carry) / length;
}

/** 모표준편차(ddof=0). numpy.std의 기본과 같다. */
export function std(values, start = 0, length = values.length - start) {
  if (length <= 0) return 0;
  const m = mean(values, start, length);
  let sum = 0;
  let carry = 0;
  for (let i = 0; i < length; i += 1) {
    const d = values[start + i] - m;
    const v = d * d;
    const t = sum + v;
    carry += Math.abs(sum) >= Math.abs(v) ? (sum - t) + v : (v - t) + sum;
    sum = t;
  }
  return Math.sqrt(Math.max(0, (sum + carry) / length));
}

/**
 * `values[start .. start+length)`를 정규화해 `out`에 쓴다.
 *
 * 배열을 새로 만들지 않는다 — 420만 봉을 훑을 때 창마다 할당하면 그것만으로
 * 시간이 다 간다. 호출자가 버퍼 하나를 만들어 돌려 쓴다.
 */
export function normalizeInto(values, start, length, out, scale = 'shape') {
  const base = values[start];
  if (base === 0) {
    out.fill(0, 0, length);
    return out;
  }
  // 1) 첫 값 대비 상대 변화 (가격대 제거)
  for (let i = 0; i < length; i += 1) out[i] = values[start + i] / base - 1;

  // 2) 평균을 빼서 '수준'을 없앤다
  const m = mean(out, 0, length);
  for (let i = 0; i < length; i += 1) out[i] -= m;

  if (scale === 'shape') {
    // 3) 표준편차로 나눠 '크기'도 없앤다
    const sd = std(out, 0, length);
    if (sd > FLAT_EPS) {
      for (let i = 0; i < length; i += 1) out[i] /= sd;
    } else {
      out.fill(0, 0, length);
    }
  } else if (scale !== 'amplitude') {
    throw new Error(`scale은 'shape' 또는 'amplitude'여야 합니다: ${scale}`);
  }
  return out;
}

/** 창 하나를 정규화한 새 배열. 편의용이다 — 반복문 안에서는 normalizeInto를 쓸 것. */
export function normalizeWindow(window, scale = 'shape') {
  const values = window instanceof Float64Array ? window : Float64Array.from(window);
  return normalizeInto(values, 0, values.length, new Float64Array(values.length), scale);
}

/**
 * 움직임이 거의 없는 구간인지.
 *
 * 호가가 한 번도 안 움직인 창은 정규화하면 전부 0이 되고, 그런 창끼리는
 * 거리가 0이라 '완벽히 같은 모양'으로 잡힌다. 통계에 넣으면 의미 없는
 * 표본이 잔뜩 들어오므로 걸러낸다.
 */
export function isFlat(window) {
  const values = window instanceof Float64Array ? window : Float64Array.from(window);
  if (values.length === 0) return true;
  const base = values[0];
  if (base === 0) return true;
  const path = new Float64Array(values.length);
  for (let i = 0; i < values.length; i += 1) path[i] = values[i] / base - 1;
  return std(path) <= FLAT_EPS;
}

/** 각 구간이 '움직임 없는 구간'인지 표시하는 배열. */
export function flatMask(values, length) {
  const count = values.length - length + 1;
  if (count <= 0) return new Uint8Array(0);
  const out = new Uint8Array(count);
  const buffer = new Float64Array(length);
  for (let start = 0; start < count; start += 1) {
    const base = values[start];
    if (base === 0) {
      out[start] = 1;
      continue;
    }
    for (let i = 0; i < length; i += 1) buffer[i] = values[start + i] / base - 1;
    out[start] = std(buffer) <= FLAT_EPS ? 1 : 0;
  }
  return out;
}

/**
 * `values`의 모든 길이-`length` 구간과 `query` 사이의 거리(RMSE).
 *
 * 전수 계산이다. 구간이 많으면 search.js의 하한 거르기를 쓸 것.
 */
export function distancesTo(query, values, length, scale = 'shape') {
  const count = values.length - length + 1;
  if (count <= 0) return new Float64Array(0);

  const target = normalizeWindow(query, scale);
  const out = new Float64Array(count);
  const buffer = new Float64Array(length);
  for (let start = 0; start < count; start += 1) {
    normalizeInto(values, start, length, buffer, scale);
    let sum = 0;
    for (let i = 0; i < length; i += 1) {
      const d = buffer[i] - target[i];
      sum += d * d;
    }
    out[start] = Math.sqrt(sum / length);
  }
  return out;
}

/**
 * 이 모양이 '그냥 곧게 오르내리는 추세선'에 얼마나 가까운지 (0~1).
 *
 * 정규화한 경로에 직선을 맞췄을 때의 결정계수 R²다. 1에 가까우면 굴곡이
 * 거의 없는 직선이다.
 *
 * 왜 재는가: 긴 모양일수록 똑같은 게 과거에 잘 없다. 그런데도 표본이 20개
 * 넘게 나온다면 대개 "같은 모양"이 아니라 **"같은 방향으로 추세 중"**이다.
 * 그걸 알려주지 않으면 사용자는 훨씬 강한 주장으로 읽는다.
 */
export function linearity(window) {
  const path = normalizeWindow(window);
  if (path.length < 3) return 1;
  if (std(path) <= FLAT_EPS) return 1;

  const n = path.length;
  const xMean = (n - 1) / 2;
  const yMean = mean(path);
  let sxy = 0;
  let sxx = 0;
  let syy = 0;
  for (let i = 0; i < n; i += 1) {
    const dx = i - xMean;
    const dy = path[i] - yMean;
    sxy += dx * dy;
    sxx += dx * dx;
    syy += dy * dy;
  }
  if (sxx <= 0 || syy <= 0) return 1;
  const r = sxy / Math.sqrt(sxx * syy);
  return r * r;
}

/**
 * '유사도'(상관계수)를 거리 임계값으로 바꾼다.
 *
 * shape 모드에서는 두 경로가 평균 0, 표준편차 1로 정규화되므로
 *
 *     거리² = 2 × (1 − 상관계수)
 *
 * 가 정확히 성립한다. 그래서 거리는 이렇게 읽으면 된다:
 *
 *     거리 0.00 → 상관 1.00  (완전히 같은 모양)
 *     거리 0.45 → 상관 0.90
 *     거리 0.63 → 상관 0.80
 *     거리 1.00 → 상관 0.50
 *     거리 1.41 → 상관 0.00  (아무 관계 없음)
 *
 * 이 변환이 없으면 사용자는 "거리 1.78"이 얼마나 나쁜지 알 수 없다.
 */
export function similarityToDistance(similarity) {
  const clamped = Math.max(-1, Math.min(1, Number(similarity)));
  return Math.sqrt(Math.max(0, 2 * (1 - clamped)));
}

/** 거리를 상관계수로. similarityToDistance의 역. */
export function distanceToSimilarity(distance) {
  if (!Number.isFinite(distance)) return NaN;
  return 1 - (distance * distance) / 2;
}
