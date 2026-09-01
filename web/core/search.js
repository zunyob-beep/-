// 같은 모양 찾기를 빠르게 — 결과는 그대로. patternscan/search.py를 옮긴 것이다.
//
// 왜 필요한가
// -----------
// 8년치 1분봉은 420만 개다. 길이 180짜리 구간 하나를 비교하려면 420만 개
// 구간을 전부 정규화해야 한다. 브라우저 안에서는 더더욱 못 쓴다.
//
// 어떻게 줄이는가
// --------------
// 거리를 정확히 계산하기 전에 **확실히 먼 후보를 먼저 버린다**. 버릴 때는
// 반드시 '실제 거리가 이보다 작을 수 없다'는 하한을 쓴다. 하한이 임계값을
// 넘으면 실제 거리도 넘으므로, 버려도 결과가 변하지 않는다.
//
// 하한은 구간을 k토막(기본 8)으로 나눈 평균만으로 계산한다. 코시-슈바르츠에서
//
//     Σ_j L_j · (토막평균 차이)²  ≤  Σ_i (원소 차이)²
//
// 이므로
//
//     하한 = √( Σ_j L_j·Δ_j² / N )  ≤  실제 RMSE 거리
//
// 가 성립한다. 토막평균은 8개뿐이라 길이 180이든 5든 계산량이 같다.
//
// 그리고 **토막평균 자체를 누적합으로 O(1)에** 구한다.
//
// 수치 안정성
// -----------
// 누적합 차이는 큰 수에서 작은 수를 빼므로 자릿수를 잃는다. 그래서
// (1) 값을 중앙값으로 나눠 1 근처로 맞추고,
// (2) 누적합을 보정 덧셈으로 쌓고,
// (3) 하한에 여유(SAFETY)를 둬서 오차 때문에 진짜 매치를 버리지 않게 한다.

import {
  FLAT_EPS, compensatedCumsum, distancesTo, flatMask, normalizeInto, normalizeWindow,
} from './shape.js';

/** 하한을 계산할 때 쓸 토막 수. 늘리면 더 촘촘히 걸러내지만 계산이 는다. */
export const SEGMENTS = 8;

/** 하한에 둘 여유. 누적합 오차로 진짜 매치를 버리는 일을 막는다. */
export const SAFETY = 1e-6;

/** 후보가 이보다 적으면 그냥 정확히 계산한다 (거르는 게 더 비싸다). */
export const MIN_CANDIDATES = 20000;

/** 길이를 최대한 고르게 나눈 경계. 나머지는 앞쪽 토막에 하나씩. */
export function segmentBounds(length, segments = SEGMENTS) {
  const count = Math.max(1, Math.min(segments, length));
  const base = Math.floor(length / count);
  const extra = length % count;
  const bounds = new Int32Array(count + 1);
  for (let j = 0; j < count; j += 1) {
    bounds[j + 1] = bounds[j] + base + (j < extra ? 1 : 0);
  }
  return bounds;
}

/** 중앙값. 정렬 사본을 쓴다 — 원본을 흐트러뜨리면 시계열이 망가진다. */
function medianAbs(values) {
  const copy = Float64Array.from(values, Math.abs);
  copy.sort();
  const n = copy.length;
  if (n === 0) return 0;
  return n % 2 ? copy[(n - 1) / 2] : (copy[n / 2 - 1] + copy[n / 2]) / 2;
}

/**
 * 모든 길이-`length` 구간의 정규화 경로 토막평균과, 평평한 구간 표시.
 *
 * 누적합만 쓰므로 구간 하나당 O(토막 수)다. 길이에 비례하지 않는다.
 */
export function paaAll(values, length, bounds) {
  const count = values.length - length + 1;
  const segments = bounds.length - 1;
  if (count <= 0) return { paa: new Float64Array(0), flat: new Uint8Array(0), segments };

  const csum = compensatedCumsum(values, new Float64Array(values.length + 1));
  const squares = new Float64Array(values.length);
  for (let i = 0; i < values.length; i += 1) squares[i] = values[i] * values[i];
  const csq = compensatedCumsum(squares, new Float64Array(values.length + 1));

  const paa = new Float64Array(count * segments);
  const flat = new Uint8Array(count);

  for (let s = 0; s < count; s += 1) {
    const first = values[s];
    if (first === 0) {
      flat[s] = 1;
      continue;
    }
    const scaled = 1 / first;
    const total = csum[s + length] - csum[s];
    const totalSq = csq[s + length] - csq[s];

    // r = v/first - 1 의 평균과 표준편차
    const meanR = (total * scaled) / length - 1;
    const meanSq = (totalSq * scaled * scaled) / length - (2 * total * scaled) / length + 1;
    const varR = Math.max(meanSq - meanR * meanR, 0);
    const sdR = Math.sqrt(varR);

    if (!(sdR > FLAT_EPS)) {
      flat[s] = 1;
      continue;
    }
    for (let j = 0; j < segments; j += 1) {
      const lo = bounds[j];
      const hi = bounds[j + 1];
      const seg = ((csum[s + hi] - csum[s + lo]) * scaled) / (hi - lo) - 1;
      paa[s * segments + j] = (seg - meanR) / sdR;
    }
  }
  return { paa, flat, segments };
}

/**
 * `threshold` 이내인 구간만 {positions, distances}로 돌려준다.
 *
 * 임계값을 넘는 후보는 하한으로 미리 버리므로, 전부 정규화하는 것보다
 * 훨씬 빠르다. 돌려주는 것은 정확한 거리이며, 전수 계산과 결과가 같다.
 *
 * shape 모드('모양만')만 지원한다 — 하한 유도가 z정규화를 전제한다.
 */
export function distancesWithin(query, values, length, threshold, segments = SEGMENTS) {
  const count = values.length - length + 1;
  if (count <= 0) {
    return { positions: new Int32Array(0), distances: new Float64Array(0) };
  }
  if (count < MIN_CANDIDATES) return exactWithin(query, values, length, threshold);

  // 값이 1 근처가 되도록 맞춘다 (누적합의 자릿수 손실을 줄인다)
  const reference = medianAbs(values) || 1;
  const scaledValues = new Float64Array(values.length);
  for (let i = 0; i < values.length; i += 1) scaledValues[i] = values[i] / reference;

  const bounds = segmentBounds(length, segments);
  const { paa, flat, segments: parts } = paaAll(scaledValues, length, bounds);

  const target = normalizeWindow(query);
  const targetPaa = new Float64Array(parts);
  const widths = new Float64Array(parts);
  for (let j = 0; j < parts; j += 1) {
    const lo = bounds[j];
    const hi = bounds[j + 1];
    widths[j] = hi - lo;
    let sum = 0;
    for (let i = lo; i < hi; i += 1) sum += target[i];
    targetPaa[j] = sum / (hi - lo);
  }

  const limit = threshold + SAFETY;
  const kept = [];
  for (let s = 0; s < count; s += 1) {
    if (flat[s]) continue;
    let total = 0;
    for (let j = 0; j < parts; j += 1) {
      const d = paa[s * parts + j] - targetPaa[j];
      total += d * d * widths[j];
    }
    if (Math.sqrt(total / length) <= limit) kept.push(s);
  }
  if (kept.length === 0) {
    return { positions: new Int32Array(0), distances: new Float64Array(0) };
  }

  // 살아남은 후보에 대해서만 정확한 거리를 계산한다.
  const positions = [];
  const distances = [];
  const buffer = new Float64Array(length);
  for (const start of kept) {
    normalizeInto(values, start, length, buffer);
    let sum = 0;
    for (let i = 0; i < length; i += 1) {
      const d = buffer[i] - target[i];
      sum += d * d;
    }
    const exact = Math.sqrt(sum / length);
    if (exact <= threshold) {
      positions.push(start);
      distances.push(exact);
    }
  }
  return { positions: Int32Array.from(positions), distances: Float64Array.from(distances) };
}

/** 전수 계산. 구간이 적을 때는 거르는 것보다 이게 싸다. */
export function exactWithin(query, values, length, threshold) {
  const all = distancesTo(query, values, length);
  const flat = flatMask(values, length);
  const positions = [];
  const distances = [];
  for (let i = 0; i < all.length; i += 1) {
    if (flat[i]) continue;             // 평평한 구간은 거리가 0이라 전부 걸린다
    if (all[i] <= threshold) {
      positions.push(i);
      distances.push(all[i]);
    }
  }
  return { positions: Int32Array.from(positions), distances: Float64Array.from(distances) };
}
