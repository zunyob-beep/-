// 확률만 알려준다 — 사라 말라를 정하지 않는다. patternscan/odds.py를 옮겼다.
//
// 왜 판정을 그만두고 확률만 보여주는가
// -----------------------------------
// 실제 비트코인 105만 봉으로 재보면, 모양 비교는 적중률을 기준의 1.2~2배로
// 올린다. 정보가 분명히 있다. 그런데 그 우위가 왕복 수수료보다 작다 —
// 20분 움직임이 0.117%인데 수수료가 0.100%라, 여유가 0.017%뿐이다.
//
// 그래서 "사세요"라고 하면 거짓말이 된다. 하지만 "과거에 이런 모양 뒤에는
// 이랬습니다"는 참말이고, 판단은 사람이 하면 된다.
//
// 반드시 같이 보여줘야 하는 것
// --------------------------
// **"56%"만 보여주면 안 된다.** 평소가 55%라면 56%는 아무 의미가 없다.
// 그래서 이 모듈은 확률을 낼 때 항상 셋을 함께 낸다.
//
//     1. 이 모양 뒤의 확률
//     2. **평소 확률** (같은 기간 아무 때나 들어갔을 때)
//     3. **불확실성** (표본이 적으면 확률은 흔들린다)
//
// 셋 중 하나라도 빠지면 사용자는 숫자를 실제보다 믿게 된다.
//
// 이 파일은 값을 반올림하지 않는다. 반올림은 화면에 내보낼 때만 한다 —
// 계산 중간에 자르면 파이썬 쪽 결과와 대조할 때 어디서 갈렸는지 알 수 없다.

import { HORIZONS, timeframeLabel, timeframeSeconds } from './models.js';
import { distancesWithin } from './search.js';
import { isFlat, linearity, normalizeWindow, similarityToDistance } from './shape.js';
import { percentileSorted, wilsonInterval } from './stats.js';

/** 이보다 표본이 적으면 확률을 말하지 않는다. */
export const MIN_SAMPLES = 20;

/** 업비트 수수료(편도). */
export const DEFAULT_FEE = 0.0005;

/** 매수/매도 사이 호가 차이로 더 잃는 몫(편도). */
export const DEFAULT_SLIPPAGE = 0.0002;

export const DEFAULT_SIMILARITY = 0.85;

/** 한 번 사고 파는 데 드는 총비용(비율). */
export function roundTripCost(fee = DEFAULT_FEE, slippage = DEFAULT_SLIPPAGE) {
  return 2 * (fee + slippage);
}

/** 한 조합(봉 간격 × 길이 × 지평)에 대한 확률. */
export class Odds {
  constructor(fields) {
    Object.assign(this, fields);
  }

  /** 실제 몇 분 뒤인지. 5분봉의 '1봉 뒤'는 5분 뒤다. */
  get minutes() {
    return this.horizon * Math.floor(timeframeSeconds(this.timeframe) / 60);
  }

  get upRate() { return this.samples ? this.up / this.samples : 0; }

  get beatRate() { return this.samples ? this.beatCost / this.samples : 0; }

  /** 평소보다 얼마나 높은가. 이게 0 근처면 이 모양은 아무 말도 안 하고 있다. */
  get upEdge() { return this.upRate - this.baseUp; }

  get beatEdge() { return this.beatRate - this.baseBeat; }

  /** 오를 확률의 95% 신뢰구간. 표본이 적으면 넓어진다. */
  get interval() { return wilsonInterval(this.up, this.samples); }

  /**
   * 평소와 구분되는가.
   *
   * 신뢰구간이 평소 확률을 품고 있으면, 이 모양은 아무 정보도 주지 않는
   * 것과 구분되지 않는다.
   */
  get tellsUsAnything() {
    const [low, high] = this.interval;
    return !(low <= this.baseUp && this.baseUp <= high);
  }
}

/** 찾은 과거 구간들. 확률과 사례가 모두 이걸 쓴다. */
export class Matches {
  constructor(ends, distances, query, limit) {
    this.ends = ends;
    this.distances = distances;
    this.query = query;
    /** 기준 승률을 잴 수 있는 범위 (미래 참조 없음). */
    this.limit = limit;
  }
}

/** `length`개 모양과 닮은 과거 구간을 찾는다 (미래 참조 없음). */
export function findMatches(series, length, options = {}) {
  const {
    queryEnd = series.length - 1,
    maxHorizon = Math.max(...HORIZONS),
    similarity = DEFAULT_SIMILARITY,
    topK = 100,
  } = options;

  const closes = series.close;
  const queryStart = queryEnd - length + 1;
  const lastAllowed = queryStart - 1 - maxHorizon;
  if (queryStart < 0 || lastAllowed < length - 1) return null;

  const query = closes.subarray(queryStart, queryEnd + 1);
  if (isFlat(query)) return null;

  const usable = closes.subarray(0, lastAllowed + 1);
  const threshold = similarityToDistance(similarity);
  const { positions, distances } = distancesWithin(query, usable, length, threshold);
  if (positions.length === 0) return null;

  // 빠진 봉이 있는 구간은 버린다 — 실제로는 떨어져 있는 두 시점을
  // 연속된 모양으로 착각하게 된다.
  const step = timeframeSeconds(series.timeframe);
  const want = step * (length - 1);
  const candidates = [];
  for (let i = 0; i < positions.length; i += 1) {
    const start = positions[i];
    const end = start + length - 1;
    if (series.ts[end] - series.ts[start] !== want) continue;
    candidates.push({ end, distance: distances[i] });
  }
  if (candidates.length === 0) return null;

  // 가까운 것부터, 겹치지 않게. 정렬은 안정 정렬이어야 파이썬(kind='stable')과
  // 같은 순서가 나온다 — 자바스크립트 Array.sort는 명세상 안정 정렬이다.
  candidates.sort((a, b) => a.distance - b.distance);
  const chosen = [];
  const kept = [];
  for (const one of candidates) {
    if (chosen.some((other) => Math.abs(one.end - other) < length)) continue;
    chosen.push(one.end);
    kept.push(one.distance);
    if (chosen.length >= topK) break;
  }

  return new Matches(
    Int32Array.from(chosen), Float64Array.from(kept), query, lastAllowed + 1,
  );
}

/**
 * 올랐던 사례와 떨어졌던 사례를, **가장 닮은 것부터** 각각 `count`개.
 *
 * 닮은 정도 순으로 고르는 이유: 사용자가 보고 싶은 건 '가장 비슷했던
 * 과거가 어떻게 됐나'이지, '가장 많이 오른 과거'가 아니다. 후자를 보여주면
 * 실제보다 좋아 보인다.
 */
export function examplesFor(series, matches, horizon, { cost, count = 3 } = {}) {
  const closes = series.close;
  const length = matches.query.length;
  const returns = Array.from(matches.ends, (end) => closes[end + horizon] / closes[end] - 1);

  const build = (index) => {
    const end = matches.ends[index];
    const entry = closes[end];
    const after = [];
    for (let i = end; i <= end + horizon; i += 1) after.push(closes[i] / entry - 1);
    return {
      endIndex: end,
      at: series.kstAt(end),
      similarity: 1 - (matches.distances[index] ** 2) / 2,
      outcome: returns[index],
      shape: Array.from(normalizeWindow(closes.subarray(end - length + 1, end + 1))),
      after,
    };
  };

  // matches는 이미 닮은 순이므로 앞에서부터 고르면 된다
  const rose = [];
  const fell = [];
  for (let i = 0; i < returns.length; i += 1) {
    if (returns[i] > cost && rose.length < count) rose.push(build(i));
    if (returns[i] < 0 && fell.length < count) fell.push(build(i));
  }
  return { rose, fell };
}

/** 같은 기간 아무 때나 들어갔을 때의 확률. 비교 기준이 없으면 숫자는 의미가 없다. */
function baseRates(closes, limit, horizon, cost) {
  const end = limit - horizon;
  if (end <= 0) return [0, 0];
  let up = 0;
  let beat = 0;
  for (let i = 0; i < end; i += 1) {
    const value = closes[i + horizon] / closes[i] - 1;
    if (value > 0) up += 1;
    if (value > cost) beat += 1;
  }
  return [up / end, beat / end];
}

/**
 * `length`개 모양과 닮은 과거 구간을 찾아 지평별 확률을 낸다.
 *
 * 미래를 보지 않는다: 후보 구간과 그 직후 관측까지 전부 질의 구간이
 * 시작하기 전에 끝나야 한다.
 */
export function oddsFor(series, length, options = {}) {
  const {
    horizons = HORIZONS,
    queryEnd,
    similarity = DEFAULT_SIMILARITY,
    topK = 100,
    fee = DEFAULT_FEE,
    slippage = DEFAULT_SLIPPAGE,
    matches = undefined,
  } = options;

  const closes = series.close;
  const cost = roundTripCost(fee, slippage);
  const found = matches !== undefined ? matches : findMatches(series, length, {
    queryEnd, maxHorizon: Math.max(...horizons), similarity, topK,
  });
  if (found === null) return [];

  let worstDistance = 0;
  for (const d of found.distances) if (d > worstDistance) worstDistance = d;
  const shapeLinearity = linearity(found.query);

  const out = [];
  for (const horizon of horizons) {
    const returns = Float64Array.from(
      found.ends, (end) => closes[end + horizon] / closes[end] - 1,
    );
    const [baseUp, baseBeat] = baseRates(closes, found.limit, horizon, cost);
    let up = 0;
    let beatCost = 0;
    let best = -Infinity;
    let worst = Infinity;
    for (const value of returns) {
      if (value > 0) up += 1;
      if (value > cost) beatCost += 1;
      if (value > best) best = value;
      if (value < worst) worst = value;
    }
    const sorted = Float64Array.from(returns).sort();
    out.push(new Odds({
      timeframe: series.timeframe,
      length,
      horizon,
      samples: returns.length,
      up,
      beatCost,
      baseUp,
      baseBeat,
      medianReturn: percentileSorted(sorted, 50),
      best,
      worst,
      minSimilarity: 1 - (worstDistance * worstDistance) / 2,
      queryLinearity: shapeLinearity,
    }));
  }
  return out;
}

// ------------------------------------------------------------- 앞으로의 모양
//
// "그래서 앞으로 어떻게 되는데"에 답하는 그림이다. 다만 **선 하나를 그으면
// 거짓말이 된다** — 실제로 일어날 일은 하나지만, 우리가 아는 건 비슷했던
// 과거들이 제각각 흩어졌다는 사실뿐이다. 그래서 가운뎃값과 함께 **퍼진
// 정도**를 띠로 그린다. 띠가 넓으면 그건 "모른다"는 뜻이고, 그 사실이
// 화면에 보여야 한다.

/** 실제 경로를 몇 개나 겹쳐 그릴지. */
export const SHOWN_WALKS = 8;

/**
 * **앞으로를 몇 겹의 띠로 그릴지.** 각 줄은 [아래 백분위, 위 백분위]다.
 *
 * 예전에는 두 겹뿐이었다(10~90, 25~75). 그러면 흩어진 모양이 네모 두 개로만
 * 보여서, 어디가 빽빽하고 어디가 성긴지가 안 드러난다 — 실제로 화면을 찍어
 * 보니 가운뎃값이 그냥 직선 하나로 읽혔다.
 *
 * 네 겹으로 늘린다. 바깥일수록 옅게 칠하면 **분포 자체가 모양으로** 보인다.
 * 백분위는 이미 정렬해 둔 배열에서 꺼내므로 겹을 늘려도 계산이 거의 안 는다.
 */
export const FAN = [[5, 95], [10, 90], [25, 75], [40, 60]];

/**
 * 닮았던 과거 구간들의 **직후 경로**를 모아 가운뎃값과 띠를 낸다.
 *
 * 새 이론을 들이지 않는다. 이 도구가 원래 하던 일(닮은 과거 찾기)을
 * 그대로 앞으로 이어 그릴 뿐이다. 그래서 이 그림이 맞을 확률은 확률
 * 표와 정확히 같은 근거를 가진다 — 더도 덜도 아니다.
 */
export function project(series, matches, ahead) {
  const closes = series.close;
  const n = series.length;
  // 인덱스를 경로와 함께 들고 다닌다. 하나라도 건너뛰면 '가장 닮은 것'을
  // 고를 때 엉뚱한 경로를 집게 된다.
  const paths = [];
  for (let i = 0; i < matches.ends.length; i += 1) {
    const end = matches.ends[i];
    if (end + ahead >= n) continue;
    const entry = closes[end];
    if (entry <= 0) continue;
    const path = new Float64Array(ahead);
    for (let k = 0; k < ahead; k += 1) path[k] = closes[end + 1 + k] / entry - 1;
    paths.push({ index: i, path });
  }

  if (paths.length < MIN_SAMPLES) return null;

  // 시점별로 모아 정렬해 두고 백분위를 뽑는다. 0에서 시작한다 — 지금 값이
  // 기준점이라 그래야 선이 이어진다.
  const column = new Float64Array(paths.length);
  const median = [0];
  const fan = FAN.map(([lo, hi]) => ({ at: lo, with: hi, lo: [0], hi: [0] }));
  for (let k = 0; k < ahead; k += 1) {
    for (let i = 0; i < paths.length; i += 1) column[i] = paths[i].path[k];
    const sorted = Float64Array.from(column).sort();
    median.push(percentileSorted(sorted, 50));
    for (const band of fan) {
      band.lo.push(percentileSorted(sorted, band.at));
      band.hi.push(percentileSorted(sorted, band.with));
    }
  }
  // 화면 오른쪽 글에 그대로 쓰는 두 겹은 이름을 붙여 둔다.
  const named = (at) => fan.find((b) => b.at === at);
  const bands = {
    median,
    fan,
    low: named(25).lo,
    high: named(25).hi,
    worst: named(10).lo,
    best: named(10).hi,
  };

  // 가장 닮았던 것부터 몇 개만 실제 경로로 겹쳐 그린다. 너무 많으면
  // 선이 뭉개져서 띠와 구분이 안 된다.
  const walks = [...paths]
    .sort((a, b) => matches.distances[a.index] - matches.distances[b.index])
    .slice(0, SHOWN_WALKS)
    .map((one) => [0, ...one.path]);

  return {
    timeframe: series.timeframe,
    label: timeframeLabel(series.timeframe),
    length: matches.query.length,
    samples: paths.length,
    ...bands,
    priceNow: closes[n - 1],
    walks,
    minutes: ahead * Math.floor(timeframeSeconds(series.timeframe) / 60),
    spread: bands.high[bands.high.length - 1] - bands.low[bands.low.length - 1],
  };
}
